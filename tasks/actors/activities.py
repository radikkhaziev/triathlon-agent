"""Dramatiq actors — activity pipeline: fetch, FIT processing, DFA a1, notifications."""

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import dramatiq
import numpy as np
import sentry_sdk
from dramatiq import group, pipeline
from fitparse import FitFile
from pydantic import validate_call
from sqlalchemy import select

from data.db import Activity, ActivityDetail, ActivityHrv, PaBaseline, Race, UserDTO, get_sync_session
from data.hrv_activity import (
    calculate_dfa_timeseries,
    calculate_durability_da,
    calculate_readiness_ra,
    correct_rr_artifacts,
    detect_hrv_thresholds,
)
from data.intervals.client import IntervalsSyncClient
from data.intervals.dto import ActivityDTO
from data.utils import HRV_ELIGIBLE_TYPES
from tasks.dto import ORMDTO, DateDTO, FitProcessingResultDTO, PaBaselineDTO
from tasks.formatter import build_post_activity_message, build_rpe_keyboard
from tasks.tools import TelegramTool

from .common import actor_after_activity_update

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_download_fit_file(
    user: UserDTO,
    activity_id: str,
    force: bool = False,
):
    with get_sync_session() as session:
        activity = session.get(Activity, activity_id)
        if not activity:
            return
        if activity.fit_file_path and not force:
            return

        if activity.type not in HRV_ELIGIBLE_TYPES:
            return

        if activity.moving_time is None or activity.moving_time < 900:  # ≥15 min
            return

        try:
            with IntervalsSyncClient.for_user(user) as client:
                fit_bytes = client.download_fit(activity_id)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Failed to download FIT file for activity %s", activity_id)
            return

        if fit_bytes is None:
            logger.debug("No FIT file for activity %s, skipping", activity_id)
            return

        fit_dir = Path("static/fit-files")
        fit_dir.mkdir(parents=True, exist_ok=True)
        fit_path = fit_dir / f"{activity_id}.fit"
        try:
            fit_path.write_bytes(fit_bytes)
        except OSError as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Failed to write FIT file %s", fit_path)
            return

        activity.fit_file_path = str(fit_path)
        session.commit()
        logger.info("Saved FIT file %s (%d bytes)", fit_path, len(fit_bytes))

    return activity.fit_file_path


@dramatiq.actor(queue_name="default", time_limit=30 * 60 * 1000)
@validate_call
def _actor_process_fit_file(prev: str | None):
    """Parse FIT file once, extracting both RR intervals and Record messages.

    Returns:
        (rr_ms, records) where:
        - rr_ms: list of RR intervals in milliseconds (from HRV messages)
        - records: list of dicts with timestamp_s, heart_rate, power, speed
    """

    if prev is None:
        return
    try:
        fit = FitFile(prev)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("Failed to parse FIT file: %s", prev)
        return
    rr_ms: list[float] = []
    records: list[dict] = []
    start_ts = None

    for msg in fit.get_messages():
        msg_name = msg.name
        if msg_name == "hrv":
            for field in msg.fields:
                if field.name == "time" and field.value is not None:
                    values = field.value if isinstance(field.value, (list, tuple)) else [field.value]
                    for v in values:
                        if v is not None and v < 60.0:
                            rr_ms.append(v * 1000.0)
        elif msg_name == "record":
            rec: dict[str, Any] = {}
            for field in msg.fields:
                if field.name == "timestamp" and field.value is not None:
                    if start_ts is None:
                        start_ts = field.value
                    rec["timestamp_s"] = (field.value - start_ts).total_seconds()
                elif field.name == "heart_rate":
                    rec["heart_rate"] = field.value
                elif field.name == "power":
                    rec["power"] = field.value
                elif field.name in ("speed", "enhanced_speed"):
                    rec["speed"] = field.value
            if "timestamp_s" in rec:
                records.append(rec)

    return rr_ms, records


@dramatiq.actor(queue_name="default", time_limit=30 * 60 * 1000)
@validate_call
def _actor_post_process_fit_file(
    parsed_fit_data: tuple[list[float], list[dict]] | None,
    user: UserDTO,
    activity_id: str,
) -> FitProcessingResultDTO | None:
    if not parsed_fit_data:
        return

    with get_sync_session() as session:
        activity: Activity = session.get(Activity, activity_id)

        baseline_pa: float | None = PaBaseline.get_average(
            user=user,
            activity_type=activity.type,
            as_of=activity.start_date_local,
            session=session,
        )

    rr_ms, records = parsed_fit_data
    if len(rr_ms) < 300:  # < ~5 min of data
        status = "too_short" if rr_ms else "no_rr_data"
        return FitProcessingResultDTO(status=status, rr_count=len(rr_ms)).model_dump()

    # Artifact correction
    corrected = correct_rr_artifacts(rr_ms)
    if corrected["quality"] == "poor":
        return FitProcessingResultDTO(
            status="low_quality",
            hrv_quality="poor",
            artifact_pct=corrected["artifact_pct"],
            rr_count=len(rr_ms),
        ).model_dump()
    # DFA timeseries
    timeseries = calculate_dfa_timeseries(
        corrected["rr_corrected"],
        records=records,
    )

    if not timeseries:
        return FitProcessingResultDTO(
            status="too_short",
            hrv_quality=corrected["quality"],
            artifact_pct=corrected["artifact_pct"],
            rr_count=len(rr_ms),
        ).model_dump()

    # DFA a1 summary
    a1_values = [p["dfa_a1"] for p in timeseries]
    dfa_a1_mean = float(np.mean(a1_values))

    # Warmup a1 (first 15 min)
    warmup_points = [p for p in timeseries if p["time_sec"] <= 900]
    dfa_a1_warmup = float(np.mean([p["dfa_a1"] for p in warmup_points])) if warmup_points else None

    # Threshold detection
    thresholds = detect_hrv_thresholds(timeseries, activity_type=activity.type)

    # Readiness (Ra)
    ra_result = None
    pa_today = None
    if baseline_pa is not None:
        ra_result = calculate_readiness_ra(timeseries, baseline_pa, activity_type=activity.type)
        if ra_result:
            pa_today = ra_result["pa_today"]

    # Pa baseline data for saving
    pa_baseline_data = None
    if warmup_points:
        if activity.type == "Ride":
            warmup_perf = [
                p["power"]
                for p in warmup_points
                if p.get("power") is not None and p["power"] > 0 and 0.6 <= p.get("dfa_a1", 0) <= 1.1
            ]
        else:
            warmup_perf = [
                p["speed"]
                for p in warmup_points
                if p.get("speed") is not None and p["speed"] > 0 and 0.6 <= p.get("dfa_a1", 0) <= 1.1
            ]
        if len(warmup_perf) >= 3:
            pa_baseline_data = PaBaselineDTO(
                pa_value=float(np.mean(warmup_perf)),
                dfa_a1_ref=dfa_a1_warmup,
                quality=corrected["quality"],
            )

    # Durability (Da)
    da_result = calculate_durability_da(timeseries, activity_type=activity.type)
    # Trim timeseries for storage (keep every 30s instead of 5s)
    stored_timeseries = [p for p in timeseries if p["time_sec"] % 30 == 0]
    return FitProcessingResultDTO(
        status="processed",
        hrv_quality=corrected["quality"],
        artifact_pct=corrected["artifact_pct"],
        rr_count=len(rr_ms),
        dfa_a1_mean=round(dfa_a1_mean, 3),
        dfa_a1_warmup=round(dfa_a1_warmup, 3) if dfa_a1_warmup is not None else None,
        dfa_timeseries=stored_timeseries,
        thresholds=thresholds,
        ra_result=ra_result,
        pa_today=pa_today,
        pa_baseline_data=pa_baseline_data,
        da_result=da_result,
    ).model_dump()


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_update_analityc_tables(
    resultDTO: FitProcessingResultDTO | None,
    user: UserDTO,
    activity_id: str,
):
    if resultDTO is None:
        return

    with get_sync_session() as session:
        activity: Activity = session.get(Activity, activity_id)

        if resultDTO.pa_baseline_data:
            pa = resultDTO.pa_baseline_data
            pa_row = session.execute(
                select(PaBaseline).where(
                    PaBaseline.user_id == user.id,
                    PaBaseline.activity_type == activity.type,
                    PaBaseline.date == activity.start_date_local,
                )
            ).scalar_one_or_none()
            if pa_row is None:
                pa_row = PaBaseline(
                    user_id=user.id,
                    activity_type=activity.type,
                    date=activity.start_date_local,
                )
                session.add(pa_row)
            pa_row.pa_value = pa.pa_value
            pa_row.dfa_a1_ref = pa.dfa_a1_ref
            pa_row.quality = pa.quality

        hrv_row = session.get(ActivityHrv, activity_id)
        if hrv_row is None:
            hrv_row = ActivityHrv(activity_id=activity_id)
            session.add(hrv_row)

        hrv_row.activity_type = activity.type
        hrv_row.processing_status = resultDTO.status
        hrv_row.hrv_quality = resultDTO.hrv_quality
        hrv_row.artifact_pct = resultDTO.artifact_pct
        hrv_row.rr_count = resultDTO.rr_count
        hrv_row.dfa_a1_mean = resultDTO.dfa_a1_mean
        hrv_row.dfa_a1_warmup = resultDTO.dfa_a1_warmup
        hrv_row.dfa_timeseries = resultDTO.dfa_timeseries
        hrv_row.pa_today = resultDTO.pa_today

        if thresholds := resultDTO.thresholds:
            hrv_row.hrvt1_hr = thresholds.hrvt1_hr
            hrv_row.hrvt1_power = thresholds.hrvt1_power
            hrv_row.hrvt1_pace = thresholds.hrvt1_pace
            hrv_row.hrvt2_hr = thresholds.hrvt2_hr
            hrv_row.threshold_r_squared = thresholds.r_squared
            hrv_row.threshold_confidence = thresholds.confidence

        if ra_result := resultDTO.ra_result:
            hrv_row.ra_pct = ra_result.ra_pct

        session.commit()

    return resultDTO.model_dump()


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_send_activity_notification(
    resultDTO: FitProcessingResultDTO | None,
    user: UserDTO,
    activity_id: str,
):
    if resultDTO is None or resultDTO.status != "processed":
        return

    with get_sync_session() as session:
        activity_row: Activity = session.get(Activity, activity_id)
        hrv_row: ActivityHrv = session.get(ActivityHrv, activity_id)
        race_row = Race.get_by_activity(user.id, activity_id) if activity_row.is_race else None

    if activity_row.start_date_local != DateDTO.today().isoformat():
        return  # only notify for today's activities

    tg = TelegramTool(user=user)
    summary = build_post_activity_message(activity_row, hrv_row, race=race_row)
    # Show the RPE rating keyboard only when the value is still unset.
    # After the first tap the bot handler clears the markup on the message;
    # we don't re-attach it here.
    reply_markup = build_rpe_keyboard(activity_id) if activity_row.rpe is None else None
    tg.send_message(text=summary, reply_markup=reply_markup)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_update_activity_details(
    user: UserDTO,
    activity_id: str,
    force: bool = False,
):
    with IntervalsSyncClient.for_user(user) as client:
        detail_data = client.get_activity_detail(activity_id)
        intervals_data = client.get_activity_intervals(activity_id)

    if not detail_data:
        return

    with get_sync_session() as session:
        result: ORMDTO = ActivityDetail.save(
            activity_id,
            detail_data,
            intervals_data,
            session=session,
        )
        if not result.is_changed and not force:
            return

        activity_row: Activity = session.get(Activity, result.row.activity_id)

    pipeline(
        [
            _actor_download_fit_file.message(user=user, activity_id=activity_id, force=force),
            _actor_process_fit_file.message(),
            _actor_post_process_fit_file.message(user=user, activity_id=activity_id),
            _actor_update_analityc_tables.message(user=user, activity_id=activity_id),
            _actor_send_activity_notification.message(user=user, activity_id=activity_id),
        ]
    ).run()

    actor_after_activity_update.send(user=user, dt=activity_row.start_date_local)


@dramatiq.actor(queue_name="default")
@validate_call
def actor_fetch_user_activities(
    user: UserDTO,
    oldest: DateDTO | None = None,
    newest: DateDTO | None = None,
    force: bool = False,
):
    today = DateDTO.today()
    _newest = newest or today
    _oldest = oldest or (today - timedelta(days=30))

    with IntervalsSyncClient.for_user(user) as client:
        activities: list[ActivityDTO] = client.get_activities(oldest=_oldest, newest=_newest)

    # Strava activities cannot be read via Intervals.icu API (licensing).
    # Skip them entirely so they never enter the DB or trigger downstream fetches.
    before = len(activities)
    activities = [a for a in activities if (a.source or "").upper() != "STRAVA"]
    skipped = before - len(activities)
    if skipped:
        logger.info("Skipped %d Strava activity(ies) for user %s — Intervals.icu API blocks them", skipped, user.id)

    if not activities:
        return

    activity_ids = Activity.save_bulk(user, activities=activities)

    if force:
        activity_ids = [a.id for a in activities]

    if not activity_ids:
        return

    g = group([_actor_update_activity_details.message(user=user, activity_id=aid, force=force) for aid in activity_ids])
    g.run()
