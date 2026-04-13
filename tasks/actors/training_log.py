"""Dramatiq actors — training log lifecycle: PRE + ACTUAL (same day), POST (next day)."""

import logging
from datetime import timedelta

import dramatiq
from pydantic import validate_call

from data.db import (
    Activity,
    ActivityHrv,
    AiWorkout,
    HrvAnalysis,
    Race,
    RhrAnalysis,
    ScheduledWorkout,
    TrainingLog,
    UserDTO,
    Wellness,
    get_sync_session,
)
from tasks.dto import DateDTO
from tasks.utils import compute_max_zone_sync, detect_compliance

logger = logging.getLogger(__name__)


def _build_pre_kwargs(user_id: int, dt, wellness_row) -> dict:
    """Build pre-context kwargs from wellness/HRV/RHR data."""
    hrv_flatt = HrvAnalysis.get(user_id=user_id, dt=dt, algorithm="flatt_esco")
    rhr_row = RhrAnalysis.get(user_id=user_id, dt=dt)

    hrv_status = hrv_flatt.status if hrv_flatt else "insufficient_data"
    hrv_7d = hrv_flatt.rmssd_7d if hrv_flatt else 0
    hrv_today = float(wellness_row.hrv) if wellness_row.hrv else 0
    hrv_delta = ((hrv_today - hrv_7d) / hrv_7d * 100) if hrv_today and hrv_7d else 0.0
    tsb = (wellness_row.ctl - wellness_row.atl) if wellness_row.ctl is not None and wellness_row.atl is not None else 0

    yesterday = dt - timedelta(days=1)
    ra = None
    for h in ActivityHrv.get_for_date(user_id, yesterday):
        if h.ra_pct is not None:
            ra = h.ra_pct
            break

    return dict(
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


def _match_plan(activity, workouts: list, ai_workouts: list) -> dict:
    """Match activity to a scheduled or AI workout. Returns plan kwargs.

    Consumes matched workout/ai_workout from lists so each plan is used only once.
    """
    # Check scheduled workout by sport
    scheduled = next((w for w in workouts if w.type == activity.type), None)
    if scheduled:
        workouts.remove(scheduled)
        adapted = next(
            (a for a in ai_workouts if a.sport == scheduled.type and "adapted" in (a.rationale or "").lower()),
            None,
        )
        if adapted:
            ai_workouts.remove(adapted)
        return dict(
            source="adapted" if adapted else "humango",
            original_name=scheduled.name,
            original_description=(scheduled.description or "")[:500],
            original_duration_sec=scheduled.moving_time,
            adapted_name=adapted.name if adapted else None,
            adapted_description=adapted.description if adapted else None,
            adapted_duration_sec=(adapted.duration_minutes * 60) if adapted and adapted.duration_minutes else None,
            adaptation_reason=adapted.rationale if adapted else None,
        )

    # Check AI workout by sport
    ai = next((a for a in ai_workouts if a.sport == activity.type), None)
    if ai:
        ai_workouts.remove(ai)
        return dict(
            source="ai",
            original_name=ai.name,
            original_duration_sec=(ai.duration_minutes or 0) * 60,
        )

    # Unplanned
    return dict(source="none")


@dramatiq.actor(queue_name="default")
@validate_call
def actor_fill_training_log(user: UserDTO, dt: DateDTO):
    """Create PRE + fill ACTUAL for each activity not yet in training_log.

    Starts from Activity as source of truth. For each activity:
    1. Check if already linked in training_log (by actual_activity_id)
    2. If not — match to ScheduledWorkout/AiWorkout, create entry with PRE + ACTUAL
    """
    activities = Activity.get_for_date(user.id, dt)
    if not activities:
        return

    wellness_row = Wellness.get(user_id=user.id, dt=dt)
    if not wellness_row:
        return

    # Existing activity IDs already in training_log
    existing = TrainingLog.get_for_date(user.id, dt)
    linked_ids = {str(e.actual_activity_id) for e in existing if e.actual_activity_id}

    new_activities = [a for a in activities if str(a.id) not in linked_ids]
    if not new_activities:
        return

    pre_kwargs = _build_pre_kwargs(user.id, dt, wellness_row)
    workouts = ScheduledWorkout.get_for_date(user.id, dt)
    ai_workouts = AiWorkout.get_for_date(user.id, dt)

    for activity in new_activities:
        plan_kwargs = _match_plan(activity, workouts, ai_workouts)
        compliance = "unplanned" if plan_kwargs["source"] == "none" else None

        log_obj = TrainingLog.create(
            user_id=user.id,
            date=dt.isoformat(),
            sport=activity.type,
            is_race=getattr(activity, "is_race", False),
            **plan_kwargs,
            **pre_kwargs,
        )

        # Fill ACTUAL
        if compliance is None:
            compliance = detect_compliance(log_obj, activity)

        max_zone = compute_max_zone_sync(activity.id, activity.type)
        TrainingLog.update(
            user.id,
            log_obj.id,
            actual_activity_id=activity.id,
            actual_sport=activity.type,
            actual_duration_sec=activity.moving_time,
            actual_avg_hr=activity.average_hr,
            actual_tss=activity.icu_training_load,
            actual_max_zone_time=max_zone,
            compliance=compliance,
        )

        # Auto-create Race record if activity is flagged as race
        if getattr(activity, "is_race", False) is True:
            race_id = _ensure_race_record(user, activity, wellness_row)
            if race_id:
                TrainingLog.update(user.id, log_obj.id, race_id=race_id)

    logger.info("Training log PRE+ACTUAL for user %d on %s: %d entries", user.id, dt, len(new_activities))


def _ensure_race_record(user: UserDTO, activity, wellness_row) -> int | None:
    """Create Race record if not exists for a race activity. Returns race id."""
    from data.db import ActivityDetail

    existing = Race.get_by_activity(user.id, activity.id)
    if existing:
        return existing.id

    with get_sync_session() as session:
        detail = session.get(ActivityDetail, activity.id)

    distance = detail.distance if detail else None
    avg_pace = None
    if distance and activity.moving_time and distance > 0:
        avg_pace = round(activity.moving_time / (distance / 1000), 1)

    tsb = None
    if wellness_row and wellness_row.ctl is not None and wellness_row.atl is not None:
        tsb = round(wellness_row.ctl - wellness_row.atl, 1)

    with get_sync_session() as session:
        race = Race(
            user_id=user.id,
            activity_id=activity.id,
            name=activity.type or "Race",
            race_type="C",
            distance_m=distance,
            finish_time_sec=activity.moving_time,
            avg_pace_sec_km=avg_pace,
            race_day_ctl=wellness_row.ctl if wellness_row else None,
            race_day_atl=wellness_row.atl if wellness_row else None,
            race_day_tsb=tsb,
            race_day_recovery_score=wellness_row.recovery_score if wellness_row else None,
            race_day_weight=wellness_row.weight if wellness_row else None,
        )
        session.add(race)
        session.commit()
        race_id = race.id

    logger.info("Race auto-created for user %d activity %s", user.id, activity.id)
    return race_id


@dramatiq.actor(queue_name="default")
@validate_call
def actor_fill_training_log_post(user: UserDTO, dt: DateDTO):
    """Fill POST outcome for yesterday's entries using today's wellness."""
    wellness_row = Wellness.get(user_id=user.id, dt=dt)
    if not wellness_row:
        return

    yesterday = dt - timedelta(days=1)
    targets = TrainingLog.get_unfilled_post(user_id=user.id, dt=yesterday)
    if not targets:
        return

    hrv_flatt = HrvAnalysis.get(user_id=user.id, dt=dt, algorithm="flatt_esco")
    rhr_row = RhrAnalysis.get(user_id=user.id, dt=dt)
    if not hrv_flatt or not rhr_row:
        return

    hrv_7d = hrv_flatt.rmssd_7d if hrv_flatt else 0
    hrv_today = float(wellness_row.hrv) if wellness_row.hrv else 0
    hrv_delta = ((hrv_today - hrv_7d) / hrv_7d * 100) if hrv_today and hrv_7d else 0.0

    post_ra = None
    for h in ActivityHrv.get_for_date(user.id, yesterday):
        if h.ra_pct is not None:
            post_ra = h.ra_pct
            break

    for log in targets:
        pre_score = log.pre_recovery_score or 0
        post_score = wellness_row.recovery_score or 0
        TrainingLog.update(
            user.id,
            log.id,
            post_recovery_score=post_score,
            post_hrv_delta_pct=round(hrv_delta, 1),
            post_rhr_today=rhr_row.rhr_today if rhr_row else None,
            post_sleep_score=wellness_row.sleep_score,
            post_ra_pct=post_ra,
            recovery_delta=round(post_score - pre_score, 1),
        )

    logger.info("Training log POST filled for user %d, yesterday=%s (%d entries)", user.id, yesterday, len(targets))
