import zoneinfo
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from api.deps import get_data_user_id, require_viewer
from config import settings
from data.db import ScheduledWorkout, User, get_session
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
                    "distance_km": w.distance,
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
