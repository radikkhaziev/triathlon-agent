"""Dramatiq actors — wellness pipeline: HRV, RHR, Banister, recovery."""

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Literal

import dramatiq
from dramatiq import group, pipeline
from pydantic import validate_call
from sqlalchemy import select

from data.db import (
    Activity,
    ActivityHrv,
    AiWorkout,
    AthleteConfig,
    HrvAnalysis,
    RhrAnalysis,
    ScheduledWorkout,
    TrainingLog,
    UserDTO,
    Wellness,
    get_sync_session,
)
from data.intervals.client import IntervalsSyncClient
from data.intervals.dto import RecoveryScoreDTO, RhrStatusDTO, RmssdStatusDTO, WellnessDTO
from data.metrics import (
    TREND_THRESHOLDS,
    calculate_banister_for_date,
    calculate_sport_ctl,
    calculate_trend,
    combined_recovery_score,
    rmssd_ai_endurance,
    rmssd_flatt_esco,
)
from tasks.dto import DateDTO

from ._common import _CATEGORY_TO_READINESS, TZ
from .reports import actor_user_scheduled_workouts

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_enrich_wellness_sport_info(
    user: UserDTO,
    dt: DateDTO,
) -> None:
    """Enrich wellness with per-sport CTL from DB (not API)."""
    with get_sync_session() as session:
        activity_row: list[Activity] = Activity.get_for_ctl(user_id=user.id, as_of=dt, session=session)

        sport_ctl: dict[str, float] = calculate_sport_ctl(activity_row)

        Wellness.update_sport_ctl(
            user_id=user.id,
            dt=dt,
            sport_ctl=sport_ctl,
            session=session,
        )


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
):
    _dt = dt.isoformat()
    with get_sync_session() as session:
        _wellness_row = session.execute(
            select(Wellness).where(
                Wellness.user_id == user.id,
                Wellness.date == _dt,
            )
        ).scalar_one_or_none()
        _hrv_row = session.get(HrvAnalysis, (user.id, _dt, "flatt_esco"))
        _rhr_row = session.get(RhrAnalysis, (user.id, _dt))

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
        _wellness_row.readiness_level = _CATEGORY_TO_READINESS.get(recovery.category, "yellow")

        session.commit()

    _actor_record_training_log.send(user=user, dt=dt)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_record_training_log(
    user: UserDTO,
    dt: DateDTO,
):
    """Record training log pre (today) + fill post (yesterday). Runs after recovery score."""

    _dt = dt.isoformat()

    with get_sync_session() as session:
        wellness_row = session.execute(
            select(Wellness).where(Wellness.user_id == user.id, Wellness.date == _dt)
        ).scalar_one_or_none()

    if not wellness_row:
        return

    # --- PRE: record today's training context ---
    existing = TrainingLog.get_for_date(dt, user_id=user.id)
    if not existing:
        workouts = ScheduledWorkout.get_for_date(user.id, dt)
        ai_workouts = AiWorkout.get_for_date(user.id, dt)

        hrv_flatt = HrvAnalysis.get(user_id=user.id, dt=dt, algorithm="flatt_esco")
        rhr_row = RhrAnalysis.get(user_id=user.id, dt=dt)

        hrv_status = hrv_flatt.status if hrv_flatt else "insufficient_data"
        hrv_7d = hrv_flatt.rmssd_7d if hrv_flatt else 0
        hrv_today = float(wellness_row.hrv) if wellness_row.hrv else 0
        hrv_delta = ((hrv_today - hrv_7d) / hrv_7d * 100) if hrv_today and hrv_7d else 0.0
        tsb = (
            (wellness_row.ctl - wellness_row.atl)
            if wellness_row.ctl is not None and wellness_row.atl is not None
            else 0
        )

        yesterday = dt - timedelta(days=1)
        ra = None
        for h in ActivityHrv.get_for_date(user.id, yesterday):
            if h.ra_pct is not None:
                ra = h.ra_pct
                break

        pre_kwargs = dict(
            pre_recovery_score=wellness_row.recovery_score,
            pre_recovery_category=wellness_row.recovery_category,
            pre_hrv_status=hrv_status,
            pre_hrv_delta_pct=round(hrv_delta, 1),
            pre_rhr_today=rhr_row.rhr_today if rhr_row else None,
            pre_rhr_status=rhr_row.status if rhr_row else None,
            pre_tsb=round(tsb, 1),
            pre_ctl=wellness_row.ctl,
            pre_atl=wellness_row.atl,
            pre_ra_pct=ra,
            pre_sleep_score=wellness_row.sleep_score,
        )

        if workouts:
            for w in workouts:
                adapted = next(
                    (a for a in ai_workouts if a.sport == w.type and "adapted" in (a.rationale or "").lower()),
                    None,
                )
                TrainingLog.create(
                    user_id=user.id,
                    date=_dt,
                    sport=w.type,
                    source="adapted" if adapted else "humango",
                    original_name=w.name,
                    original_description=(w.description or "")[:500],
                    original_duration_sec=w.moving_time,
                    adapted_name=adapted.name if adapted else None,
                    adapted_description=adapted.description if adapted else None,
                    adapted_duration_sec=(adapted.duration_minutes * 60) if adapted else None,
                    adaptation_reason=adapted.rationale if adapted else None,
                    **pre_kwargs,
                )
        elif ai_workouts:
            for a in ai_workouts:
                TrainingLog.create(
                    user_id=user.id,
                    date=_dt,
                    sport=a.sport,
                    source="ai",
                    original_name=a.name,
                    original_duration_sec=(a.duration_minutes or 0) * 60,
                    **pre_kwargs,
                )
        else:
            TrainingLog.create(
                user_id=user.id,
                date=_dt,
                source="none",
                **pre_kwargs,
            )

        logger.info("Training log PRE recorded for user %d on %s", user.id, dt)

    # --- POST: fill yesterday's outcome using today's wellness ---
    yesterday = dt - timedelta(days=1)
    unfilled = TrainingLog.get_unfilled_post(user_id=user.id)
    targets = [r for r in unfilled if r.date == str(yesterday)]

    if targets:
        hrv_flatt_today = HrvAnalysis.get(user_id=user.id, dt=dt, algorithm="flatt_esco")
        hrv_7d = hrv_flatt_today.rmssd_7d if hrv_flatt_today else 0
        hrv_today = float(wellness_row.hrv) if wellness_row.hrv else 0
        hrv_delta = ((hrv_today - hrv_7d) / hrv_7d * 100) if hrv_today and hrv_7d else 0.0
        rhr_today = RhrAnalysis.get(user_id=user.id, dt=dt)

        post_ra = None
        yesterday_hrv = ActivityHrv.get_for_date(user.id, yesterday)
        for h in yesterday_hrv:
            if h.ra_pct is not None:
                post_ra = h.ra_pct
                break

        for log in targets:
            pre_score = log.pre_recovery_score or 0
            post_score = wellness_row.recovery_score or 0
            TrainingLog.update(
                log.id,
                user_id=user.id,
                post_recovery_score=post_score,
                post_hrv_delta_pct=round(hrv_delta, 1),
                post_rhr_today=rhr_today.rhr_today if rhr_today else None,
                post_sleep_score=wellness_row.sleep_score,
                post_ra_pct=post_ra,
                recovery_delta=round(post_score - pre_score, 1),
            )

        logger.info("Training log POST filled for user %d, yesterday=%s (%d entries)", user.id, yesterday, len(targets))


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_update_banister_ess(
    user: UserDTO,
    dt: DateDTO,
):
    """Calculate Banister model and update wellness.banister_recovery + wellness.ess_today.

    Requires resting_hr to be already saved in the wellness row before calculation.
    """

    activity_rows: list[Activity] = Activity.get_for_banister(user_id=user.id, as_of=dt)
    if not activity_rows:
        logger.info("No activities found for Banister ESS calculation for user %s on %s", user.id, dt)
        return

    activities_by_date: dict[str, list] = defaultdict(list)
    for act in activity_rows:
        activities_by_date[act.start_date_local].append(act)

    with get_sync_session() as session:
        _wellness_row = session.execute(
            select(Wellness).where(Wellness.user_id == user.id, Wellness.date == dt.isoformat())
        ).scalar_one_or_none()

        if _wellness_row is None or not _wellness_row.resting_hr:
            logger.warning(
                "Cannot update Banister ESS: Wellness row not found or resting_hr missing for user %s on %s",
                user.id,
                dt,
            )
            return

        thresholds = AthleteConfig.get_thresholds(user.id)
        banister_r, ess_today = calculate_banister_for_date(
            activities_by_date=activities_by_date,
            dt=dt,
            hr_rest=_wellness_row.resting_hr,
            hr_max=thresholds.max_hr or 179,
            lthr=thresholds.lthr_run or 153,
        )

        _wellness_row.banister_recovery = banister_r
        _wellness_row.ess_today = ess_today
        session.commit()


@dramatiq.actor(queue_name="default")
@validate_call
def actor_user_wellness(
    user: UserDTO,
    dt: DateDTO | None = None,
):
    from .athlets import actor_sync_athlete_settings

    today = datetime.now(TZ).date()
    dt = dt or today
    # is_today = dt == today

    with IntervalsSyncClient.for_user(user) as client:
        _wellnessDTO: WellnessDTO = client.get_wellness(dt)

    if not _wellnessDTO:
        logger.info("No wellness data found for user %s on %s", user.id, dt)
        return

    result = Wellness.save(user_id=user.id, wellness=_wellnessDTO)

    _dt = _wellnessDTO.id

    if not result.is_changed:
        logger.debug("Wellness unchanged for user %s on %s, skipping pipelines", user.id, _dt)
        return

    # all independent tasks run in parallel
    g = group(
        [
            _actor_enrich_wellness_sport_info.message(user=user, dt=_dt),
            actor_user_scheduled_workouts.message(user=user),
            _actor_update_banister_ess.message(user=user, dt=_dt),
            actor_sync_athlete_settings.message(user=user),
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
    g.add_completion_callback(_actor_update_recovery_score.message(user=user, dt=_dt))
    g.run()
