import zoneinfo
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from api.deps import get_data_user_id, require_viewer
from config import settings
from data.db import AthleteSettings, ScheduledWorkout, User, get_session
from data.utils import format_duration

router = APIRouter()

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@router.get("/api/scheduled-workouts")
async def scheduled_workouts(
    week_offset: int = Query(default=0, ge=-52, le=52),
    user: User = Depends(require_viewer),
) -> dict:
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()

    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    sunday = monday + timedelta(days=6)

    uid = get_data_user_id(user)
    workouts, last_synced_at = await ScheduledWorkout.get_range(uid, monday, sunday)

    by_date: dict[str, list] = {}
    for w in workouts:
        by_date.setdefault(w.start_date_local, []).append(w)

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        d_str = str(d)
        day_workouts = []
        for w in by_date.get(d_str, []):
            day_workouts.append(
                {
                    "id": w.id,
                    "type": w.type,
                    "name": w.name,
                    "category": w.category,
                    "duration": format_duration(w.moving_time),
                    "duration_secs": w.moving_time,
                    # Stored in METERS (Intervals native) — convert for the UI.
                    "distance_km": w.distance / 1000 if w.distance is not None else None,
                    "description": w.description,
                }
            )
        days.append({"date": d_str, "weekday": _WEEKDAYS[i], "workouts": day_workouts})

    next_monday = monday + timedelta(weeks=1)
    prev_sunday = monday - timedelta(days=1)

    async with get_session() as session:
        has_next_result = await session.execute(
            select(func.count())
            .select_from(ScheduledWorkout)
            .where(ScheduledWorkout.user_id == uid, ScheduledWorkout.start_date_local >= str(next_monday))
        )
        has_next = has_next_result.scalar_one() > 0

        has_prev_result = await session.execute(
            select(func.count())
            .select_from(ScheduledWorkout)
            .where(ScheduledWorkout.user_id == uid, ScheduledWorkout.start_date_local <= str(prev_sunday))
        )
        has_prev = has_prev_result.scalar_one() > 0

    return {
        "week_start": str(monday),
        "week_end": str(sunday),
        "week_offset": week_offset,
        "today": str(today),
        "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
        "has_prev": has_prev,
        "has_next": has_next,
        "role": user.role,
        "days": days,
    }


@router.get("/api/scheduled-workout/{workout_id}")
async def scheduled_workout_detail(
    workout_id: int,
    user: User = Depends(require_viewer),
) -> dict:
    """Single scheduled workout with structured steps + athlete thresholds +
    Intervals.icu enrichment (estimated TSS, normalized power, zone times, etc.)
    + per-sport zone boundaries.

    Thresholds are bundled so the frontend can render absolute target ranges
    (% → bpm / watts / sec-per-km) without a second roundtrip; zones drive the
    timeline-chart colouring.

    Enrichment-fields are populated by Intervals.icu on `POST /events` (see
    `docs/WORKOUT_ABSOLUTE_TARGETS_SPEC.md` §13.6 B — `update_event` does NOT
    re-trigger enrichment). Some fields may be null for sports lacking the
    relevant signal (e.g. `normalized_power` is 0/null for Swim).
    """
    uid = get_data_user_id(user)
    async with get_session() as session:
        w = await session.get(ScheduledWorkout, workout_id)
        if w is None or w.user_id != uid:
            raise HTTPException(status_code=404, detail="Workout not found")

    t = await AthleteSettings.get_thresholds(uid)
    sport_settings = await AthleteSettings.get(uid, w.type) if w.type else None

    wd = w.workout_doc or {}

    return {
        "id": w.id,
        "type": w.type,
        "name": w.name,
        "category": w.category,
        "date": w.start_date_local,
        "duration": format_duration(w.moving_time),
        "duration_secs": w.moving_time,
        # Stored in METERS (Intervals native) — convert for the UI.
        "distance_km": w.distance / 1000 if w.distance is not None else None,
        "description": w.description,
        "steps": wd.get("steps"),
        "rationale": wd.get("description"),
        "enrichment": {
            # TSS comes from `event.icu_training_load` (top-level). The
            # workout_doc-internal `strain_score` field is always None for
            # planned events — Intervals only populates it for completed
            # activities. Verified empirically 2026-05-13.
            "tss": w.icu_training_load,
            "normalized_power": wd.get("normalized_power") or None,
            "variability_index": wd.get("variability_index"),
            "polarization_index": wd.get("polarization_index"),
            # icu_intensity is emitted as event top-level by Intervals.icu
            # (NOT inside workout_doc — see ScheduledWorkoutDTO docstring).
            # Value is 0-100 percent; frontend renders verbatim with %.
            "intensity_pct": w.icu_intensity,
            "zone_times": wd.get("zoneTimes"),
        },
        "thresholds": {
            "lthr_run": t.lthr_run,
            "lthr_bike": t.lthr_bike,
            "ftp": t.ftp,
            "threshold_pace_run_sec_per_km": t.threshold_pace_run,
            "css_sec_per_100m": t.css,
        },
        "zones": {
            "hr": sport_settings.hr_zones if sport_settings else None,
            "power": sport_settings.power_zones if sport_settings else None,
            "pace": sport_settings.pace_zones if sport_settings else None,
        },
    }
