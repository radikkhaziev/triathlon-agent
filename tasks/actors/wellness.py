"""Dramatiq actors — wellness pipeline: HRV, RHR, Banister, recovery."""

import logging
import statistics
from typing import Literal

import dramatiq
from dramatiq import group, pipeline
from pydantic import validate_call

from data.db import HrvAnalysis, RhrAnalysis, UserDTO, Wellness, get_sync_session
from data.intervals.client import IntervalsSyncClient
from data.intervals.dto import RecoveryScoreDTO, RhrStatusDTO, RmssdStatusDTO, WellnessDTO
from data.metrics import (
    TREND_THRESHOLDS,
    calculate_trend,
    combined_recovery_score,
    rmssd_ai_endurance,
    rmssd_flatt_esco,
)
from tasks.dto import ORMDTO, DateDTO

from .common import CATEGORY_TO_READINESS, actor_after_activity_update

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_calculate_rhr(
    user: UserDTO,
    dt: DateDTO,
) -> RhrStatusDTO:
    """Resting HR baseline analysis.

    Compares today's RHR vs 30-day rolling baseline.
    Inverted vs RMSSD: elevated RHR = under-recovered.
    Computes 7d, 30d, and 60d baselines.
    """

    MIN_DAYS = 7

    rhr_rows: list[float] = Wellness.get_rhr_history(user_id=user.id, dt=dt)
    n = len(rhr_rows)

    if n < MIN_DAYS:
        return RhrStatusDTO(
            status="insufficient_data",
            days_available=n,
            days_needed=MIN_DAYS - n,
        )

    today_rhr = rhr_rows[-1]

    # 7-day baseline
    last_7 = rhr_rows[-7:]
    mean_7 = statistics.mean(last_7)
    sd_7 = statistics.stdev(last_7) if len(last_7) >= 2 else 1.0

    # 30-day baseline (used for status bounds)
    last_30 = rhr_rows[-30:] if n >= 30 else rhr_rows
    mean_30 = statistics.mean(last_30)
    sd_30 = statistics.stdev(last_30) if len(last_30) >= 2 else 1.0

    lower_bound = mean_30 - 0.5 * sd_30
    upper_bound = mean_30 + 0.5 * sd_30

    # 60-day baseline (context only)
    rhr_60d = statistics.mean(rhr_rows[-60:]) if n >= 60 else None
    rhr_sd_60d = statistics.stdev(rhr_rows[-60:]) if n >= 60 else None

    # Inverted: high RHR = red, low RHR = green
    if today_rhr > upper_bound:
        status = "red"
    elif today_rhr < lower_bound:
        status = "green"
    else:
        status = "yellow"

    cv_7d = (sd_7 / mean_7 * 100) if mean_7 > 0 else None
    trend = calculate_trend(last_7, window=7, **TREND_THRESHOLDS["resting_hr"])

    return RhrStatusDTO(
        status=status,
        days_available=n,
        days_needed=0,
        rhr_today=round(today_rhr, 1),
        rhr_7d=round(mean_7, 1),
        rhr_sd_7d=round(sd_7, 2),
        rhr_30d=round(mean_30, 1),
        rhr_sd_30d=round(sd_30, 2),
        rhr_60d=round(rhr_60d, 1) if rhr_60d else None,
        rhr_sd_60d=round(rhr_sd_60d, 2) if rhr_sd_60d else None,
        lower_bound=round(lower_bound, 1),
        upper_bound=round(upper_bound, 1),
        cv_7d=round(cv_7d, 1) if cv_7d is not None else None,
        trend=trend,
    )


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_calculate_hrv(
    user: UserDTO,
    dt: DateDTO,
) -> dict[Literal["flatt_esco", "ai_endurance"], RmssdStatusDTO]:
    """Dispatcher: loads HRV history from DB, delegates to selected algorithm."""

    MIN_DAYS = 14

    hrv_rows: list[float] = Wellness.get_hrv_history(user_id=user.id, dt=dt)
    n = len(hrv_rows)

    if n < MIN_DAYS:
        _status = RmssdStatusDTO(
            status="insufficient_data",
            days_available=n,
            days_needed=MIN_DAYS - n,
        )
        return {
            "flatt_esco": _status,
            "ai_endurance": _status,
        }

    return {
        "flatt_esco": rmssd_flatt_esco(hrv_rows),
        "ai_endurance": rmssd_ai_endurance(hrv_rows),
    }


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_update_rhr_analysis(
    prev: RhrStatusDTO,
    *,
    user: UserDTO,
    dt: DateDTO,
) -> RhrStatusDTO:
    """Persist RHR baseline result to rhr_analysis table (upsert)."""
    if prev.status == "insufficient_data":
        return prev  # no DB update if not enough data

    _dt = dt.isoformat()
    with get_sync_session() as session:
        _rhr_row = session.get(RhrAnalysis, (user.id, _dt))
        if _rhr_row is None:
            _rhr_row = RhrAnalysis(user_id=user.id, date=_dt)
            session.add(_rhr_row)

        _rhr_row.status = prev.status
        _rhr_row.rhr_today = prev.rhr_today
        _rhr_row.rhr_7d = prev.rhr_7d
        _rhr_row.rhr_sd_7d = prev.rhr_sd_7d
        _rhr_row.rhr_30d = prev.rhr_30d
        _rhr_row.rhr_sd_30d = prev.rhr_sd_30d
        _rhr_row.rhr_60d = prev.rhr_60d
        _rhr_row.rhr_sd_60d = prev.rhr_sd_60d
        _rhr_row.lower_bound = prev.lower_bound
        _rhr_row.upper_bound = prev.upper_bound
        _rhr_row.cv_7d = prev.cv_7d
        _rhr_row.days_available = prev.days_available
        if prev.trend:
            _rhr_row.trend_direction = prev.trend.direction
            _rhr_row.trend_slope = prev.trend.slope
            _rhr_row.trend_r_squared = prev.trend.r_squared
        session.commit()

    return prev


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_update_hrv_analysis(
    prev: dict[Literal["flatt_esco", "ai_endurance"], RmssdStatusDTO],
    *,
    user: UserDTO,
    dt: DateDTO,
) -> RmssdStatusDTO:
    """Persist dual-algorithm HRV results to hrv_analysis table (upsert). Returns primary algorithm result."""
    for algorithm, rmssd in prev.items():
        if rmssd.status == "insufficient_data":
            continue  # skip if not enough data

        _dt = dt.isoformat()
        with get_sync_session() as session:
            _hrv_row = session.get(HrvAnalysis, (user.id, _dt, algorithm))
            if _hrv_row is None:
                _hrv_row = HrvAnalysis(user_id=user.id, date=_dt, algorithm=algorithm)
                session.add(_hrv_row)

            _hrv_row.status = rmssd.status
            _hrv_row.rmssd_7d = rmssd.rmssd_7d
            _hrv_row.rmssd_sd_7d = rmssd.rmssd_sd_7d
            _hrv_row.rmssd_60d = rmssd.rmssd_60d
            _hrv_row.rmssd_sd_60d = rmssd.rmssd_sd_60d
            _hrv_row.lower_bound = rmssd.lower_bound
            _hrv_row.upper_bound = rmssd.upper_bound
            _hrv_row.cv_7d = rmssd.cv_7d
            _hrv_row.swc = rmssd.swc
            _hrv_row.days_available = rmssd.days_available
            if rmssd.trend:
                _hrv_row.trend_direction = rmssd.trend.direction
                _hrv_row.trend_slope = rmssd.trend.slope
                _hrv_row.trend_r_squared = rmssd.trend.r_squared
            session.commit()

    return prev["flatt_esco"]


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_update_recovery_score(
    user: UserDTO,
    dt: DateDTO,
    force: bool = False,
):
    with get_sync_session() as session:
        _wellness_row = Wellness.get(user_id=user.id, dt=dt, session=session)
        _hrv_row = HrvAnalysis.get(user_id=user.id, dt=dt, algorithm="flatt_esco", session=session)
        _rhr_row = RhrAnalysis.get(user_id=user.id, dt=dt, session=session)

        if not _wellness_row or not _hrv_row or not _rhr_row:
            return

        recovery: RecoveryScoreDTO = combined_recovery_score(
            rmssd=_hrv_row,
            rhr=_rhr_row,
            banister_recovery=_wellness_row.banister_recovery,
            sleep_score=_wellness_row.sleep_score,
        )

        _wellness_row.recovery_score = recovery.score
        _wellness_row.recovery_category = recovery.category
        _wellness_row.recovery_recommendation = recovery.recommendation
        _wellness_row.readiness_score = int(recovery.score)  # legacy alias
        _wellness_row.readiness_level = CATEGORY_TO_READINESS.get(recovery.category, "yellow")

        session.commit()


@dramatiq.actor(queue_name="default")
@validate_call
def actor_user_wellness(
    user: UserDTO,
    dt: DateDTO | None = None,
    wellnessDTO: WellnessDTO | None = None,
    force: bool = False,
):
    from .athlets import actor_sync_athlete_settings
    from .reports import actor_compose_user_morning_report

    today = DateDTO.today()
    dt = dt or today

    if wellnessDTO is None:
        with IntervalsSyncClient.for_user(user) as client:
            wellnessDTO: WellnessDTO = client.get_wellness(dt)

    if not wellnessDTO:
        logger.info("No wellness data found for user %s on %s", user.id, dt)
        return

    result: ORMDTO = Wellness.save(user_id=user.id, wellness=wellnessDTO)

    _dt = wellnessDTO.id

    if not result.is_changed and not force:
        logger.debug("Wellness unchanged for user %s on %s, skipping pipelines", user.id, _dt)
        return

    # all independent tasks run in parallel
    group(
        [
            actor_sync_athlete_settings.message(user=user),
            actor_after_activity_update.message(user=user, dt=_dt),
        ]
    ).run()

    g = group(
        [
            pipeline(
                [
                    _actor_calculate_rhr.message(user=user, dt=_dt),
                    _actor_update_rhr_analysis.message(user=user, dt=_dt),
                ]
            ),
            pipeline(
                [
                    _actor_calculate_hrv.message(user=user, dt=_dt),
                    _actor_update_hrv_analysis.message(user=user, dt=_dt),
                ]
            ),
        ]
    )
    g.add_completion_callback(_actor_update_recovery_score.message(user=user, dt=_dt, force=force))
    g.run()

    _row: Wellness = result.row
    if _dt == DateDTO.today().isoformat() and not _row.ai_recommendation and _row.sleep_score and _row.recovery_score:
        actor_compose_user_morning_report.send(user=user)
