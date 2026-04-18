"""Dramatiq actors — activity pipeline: fetch, FIT processing, DFA a1, notifications."""

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import anthropic
import dramatiq
import numpy as np
import sentry_sdk
from dramatiq import group, pipeline
from fitparse import FitFile
from pydantic import validate_call
from sqlalchemy import select

from bot.i18n import _, set_language
from config import settings
from data.db import (
    Activity,
    ActivityDetail,
    ActivityHrv,
    AthleteSettings,
    PaBaseline,
    Race,
    ScheduledWorkout,
    UserDTO,
    Wellness,
    get_sync_session,
)
from data.hrv_activity import (
    calculate_dfa_timeseries,
    calculate_durability_da,
    calculate_readiness_ra,
    correct_rr_artifacts,
    detect_hrv_thresholds,
    diagnose_hrv_thresholds,
)
from data.intervals.client import IntervalsSyncClient
from data.intervals.dto import ActivityDTO
from data.utils import HRV_ELIGIBLE_TYPES
from tasks.dto import ORMDTO, DateDTO, FitProcessingResultDTO, PaBaselineDTO
from tasks.formatter import build_post_activity_message, build_ramp_test_message, build_rpe_keyboard
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

    # Threshold detection — restrict regression to WORK intervals when available,
    # otherwise warm-up / cool-down / recovery data pollutes the fit.
    thresholds = detect_hrv_thresholds(
        timeseries,
        activity_type=activity.type,
        work_segments=_load_work_segments(activity_id),
    )

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
        activity_row: Activity | None = session.get(Activity, activity_id)
        hrv_row: ActivityHrv | None = session.get(ActivityHrv, activity_id)

    if activity_row is None or hrv_row is None:
        return

    if activity_row.start_date_local != DateDTO.today().isoformat():
        return  # only notify for today's activities

    set_language(user.language or "ru")
    tg = TelegramTool(user=user)

    if _is_ramp_test_activity(user.id, activity_row):
        failure_reason = None
        if hrv_row.hrvt1_hr is None:
            failure_reason = diagnose_hrv_thresholds(
                hrv_row.dfa_timeseries or [],
                work_segments=_load_work_segments(activity_id),
            )
        sport = activity_row.type or "Run"
        settings = AthleteSettings.get(user.id, sport)
        config_lthr = settings.lthr if settings else None
        hrvt1_sample_count = ActivityHrv.count_hrvt1_samples(user.id, sport)
        summary, show_update_zones = build_ramp_test_message(
            activity_row,
            hrv_row,
            config_lthr=config_lthr,
            failure_reason=failure_reason,
            hrvt1_sample_count=hrvt1_sample_count,
        )
        reply_markup = (
            {"inline_keyboard": [[{"text": _("Обновить зоны"), "callback_data": "update_zones"}]]}
            if show_update_zones
            else None
        )
        tg.send_message(text=summary, reply_markup=reply_markup)
        return

    race_row = Race.get_by_activity(user.id, activity_id) if activity_row.is_race else None
    summary = build_post_activity_message(activity_row, hrv_row, race=race_row)
    # Show the RPE rating keyboard only when the value is still unset.
    # After the first tap the bot handler clears the markup on the message;
    # we don't re-attach it here.
    reply_markup = build_rpe_keyboard(activity_id) if activity_row.rpe is None else None
    tg.send_message(text=summary, reply_markup=reply_markup)


def _is_ramp_test_activity(user_id: int, activity: Activity) -> bool:
    """True when a Ramp Test scheduled workout exists for the activity's date+sport.

    Only catches pre-planned ramp tests (created via /workout or create_ramp_test_tool).
    Ad-hoc ramp-style workouts without a matching ScheduledWorkout do not trigger
    the ramp-specific notification — by design, to avoid false positives on
    interval sessions that happen to look like ramps.
    """
    sport = activity.type
    if not sport:
        return False
    dt = date.fromisoformat(activity.start_date_local)
    scheduled = ScheduledWorkout.get_for_date(user_id, dt)
    return any(w.type == sport and w.name and "ramp test" in w.name.lower() for w in scheduled)


def _load_work_segments(activity_id: str) -> list[tuple[int, int]]:
    with get_sync_session() as session:
        detail = session.get(ActivityDetail, activity_id)
    if not detail or not detail.intervals:
        return []
    icu_intervals = (detail.intervals or {}).get("icu_intervals") or []
    return [
        (int(iv["start_time"]), int(iv["end_time"]))
        for iv in icu_intervals
        if iv.get("type") == "WORK" and iv.get("start_time") is not None and iv.get("end_time") is not None
    ]


@dramatiq.actor(queue_name="default")
@validate_call
def actor_update_activity_details(
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

    g = group([actor_update_activity_details.message(user=user, activity_id=aid, force=force) for aid in activity_ids])
    g.run()


@dramatiq.actor(queue_name="default")
@validate_call
def actor_send_achievement_notification(user: UserDTO, activity: dict) -> None:
    """Send Telegram notification about activity achievements (PR, FTP update)."""
    set_language(user.language or "ru")

    lines: list[str] = [f"🏆 {_('Новое достижение!')}"]

    activity_name = activity.get("name") or activity.get("type") or ""
    if activity_name:
        lines.append(activity_name)

    # FTP update
    ftp = activity.get("icu_rolling_ftp")
    ftp_delta = activity.get("icu_rolling_ftp_delta")
    if ftp is not None:
        delta_str = f" (+{ftp_delta})" if ftp_delta and ftp_delta > 0 else ""
        lines.append(f"⚡ FTP: {ftp}W{delta_str}")

    # Power PRs from icu_achievements array
    for ach in activity.get("icu_achievements", []):
        if ach.get("type") == "BEST_POWER" and ach.get("watts"):
            secs = int(ach.get("secs") or 0)
            if secs <= 0:
                continue
            label = f"{secs // 60}m" if secs >= 60 else f"{secs}s"
            lines.append(f"💪 {label} {_('рекорд')}: {ach['watts']}W")

    # CTL context
    ctl = activity.get("icu_ctl")
    if ctl is not None:
        lines.append(f"📊 CTL: {ctl:.0f}")

    # Don't send empty notification (only header + name, no achievements)
    if len(lines) <= 2:
        return

    tg = TelegramTool(user=user)
    tg.send_message(text="\n".join(lines))


_SIGNATURE_MARKERS = ("endurai.me", "Readiness")


def _already_signed(name: str) -> bool:
    return any(m in name for m in _SIGNATURE_MARKERS)


def _generate_signature_prompt(activity: Activity, wellness: Wellness | None) -> str:
    """Build a short prompt for Claude to generate title + description."""
    lines = [
        "Generate a short activity title and description for Strava feed.",
        "Style: like Whoop or Garmin Coach — metric-first, not ads.",
        f"Sport: {activity.type or 'Activity'}",
        f"Duration: {(activity.moving_time or 0) // 60} min",
    ]
    if activity.icu_training_load:
        lines.append(f"TSS: {activity.icu_training_load:.0f}")
    if activity.average_hr:
        lines.append(f"Avg HR: {activity.average_hr:.0f}")
    if wellness:
        if wellness.recovery_score is not None:
            lines.append(f"Recovery: {wellness.recovery_score:.0f}/100 ({wellness.recovery_category or ''})")
        if wellness.ctl is not None:
            lines.append(f"CTL: {wellness.ctl:.0f}")
        tsb = (wellness.ctl - wellness.atl) if wellness.ctl and wellness.atl else None
        if tsb is not None:
            lines.append(f"TSB: {tsb:+.0f}")

    lines.append("")
    lines.append("Rules:")
    lines.append("- Title: max 60 chars. Format: '{original_name} · {metric or insight}'. No emojis in title.")
    lines.append("- Description: 3-5 lines. Include key metrics. End with: '→ endurai.me'")
    lines.append("- Language: English")
    lines.append("- Do NOT include hashtags")
    lines.append("")
    lines.append('Respond as JSON: {"title": "...", "description": "..."}')
    return "\n".join(lines)


def _fallback_signature(activity: Activity, wellness: Wellness | None) -> tuple[str, str]:
    """Template-based fallback when Claude is unavailable."""
    name = activity.type or "Activity"
    if wellness and wellness.recovery_score is not None:
        title = f"{name} · Readiness {wellness.recovery_score:.0f}/100"
    else:
        title = f"{name} · endurai.me"

    desc_lines = ["🧠 Coached by endurai.me — AI triathlon coach"]
    if wellness and wellness.recovery_score is not None:
        desc_lines.append(f"Readiness: {wellness.recovery_score:.0f}/100")
    if wellness and wellness.ctl is not None:
        tsb = (wellness.ctl - wellness.atl) if wellness.atl else 0
        desc_lines.append(f"CTL {wellness.ctl:.0f} · TSB {tsb:+.0f}")
    desc_lines.append("")
    desc_lines.append("→ endurai.me")
    return title, "\n".join(desc_lines)


@dramatiq.actor(queue_name="default")
@validate_call
def actor_rename_activity(user: UserDTO, activity_id: str) -> None:
    """Rename activity with AI-generated promo title/description.

    Called with a 5-minute delay from ACTIVITY_UPLOADED webhook.
    Uses Claude for unique text, falls back to template on error.
    """
    if not settings.STRAVA_SIGNATURE_ENABLED:
        return

    with get_sync_session() as session:
        activity = session.get(Activity, activity_id)
        if not activity:
            return
        if activity.type == "Other":
            return  # skip yoga/mobility/strength

        # Get wellness for context
        dt = str(activity.start_date_local)[:10]
        wellness = session.execute(
            select(Wellness).where(Wellness.user_id == user.id, Wellness.date == dt)
        ).scalar_one_or_none()

    # Idempotency: fetch current name from Intervals.icu and check if already signed
    try:
        with IntervalsSyncClient.for_user(user) as client:
            detail = client.get_activity_detail(activity_id)
    except Exception:
        logger.warning("Failed to fetch activity detail for rename check %s", activity_id)
        return
    if not detail:
        return
    current_name = detail.get("name", "")
    if _already_signed(current_name):
        return

    # Try Claude, fallback to template
    title, description = _fallback_signature(activity, wellness)
    try:
        prompt = _generate_signature_prompt(activity, wellness)
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY.get_secret_value())
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        parsed = json.loads(text)
        title = parsed.get("title", title)[:60]
        description = parsed.get("description", description)
    except Exception:
        logger.warning("Claude signature generation failed for %s, using template", activity_id)

    # Push to Intervals.icu
    try:
        with IntervalsSyncClient.for_user(user) as client:
            client.update_activity(activity_id, {"name": title, "description": description})
        logger.info("Renamed activity %s for user %d: %s", activity_id, user.id, title)
    except Exception:
        logger.exception("Failed to rename activity %s for user %d", activity_id, user.id)
