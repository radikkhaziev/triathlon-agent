import zoneinfo
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, select

from api.deps import get_data_user_id, require_viewer
from config import settings
from data.db import Activity, ActivityDetail, ActivityHrv, User, get_session
from data.utils import format_duration, serialize_activity_details, serialize_activity_hrv
from mcp_server.tools.progress import compute_efficiency_trend

router = APIRouter()

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@router.get("/api/activities-week")
async def activities_week(
    week_offset: int = Query(default=0, ge=-52, le=52),
    user: User = Depends(require_viewer),
) -> dict:
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()

    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    sunday = monday + timedelta(days=6)

    uid = get_data_user_id(user)
    activities, last_synced_at = await Activity.get_range(uid, monday, sunday)

    by_date: dict[str, list] = {}
    for a in activities:
        by_date.setdefault(a.start_date_local, []).append(a)

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        d_str = str(d)
        day_activities = []
        for a in by_date.get(d_str, []):
            day_activities.append(
                {
                    "id": a.id,
                    "type": a.type,
                    "moving_time": a.moving_time,
                    "duration": format_duration(a.moving_time),
                    "icu_training_load": round(a.icu_training_load, 1) if a.icu_training_load is not None else None,
                    "average_hr": round(a.average_hr) if a.average_hr is not None else None,
                    "is_race": a.is_race,
                }
            )
        days.append({"date": d_str, "weekday": _WEEKDAYS[i], "activities": day_activities})

    prev_sunday = monday - timedelta(days=1)
    async with get_session() as session:
        prev_result = await session.execute(
            select(exists().where(Activity.user_id == uid, Activity.start_date_local <= str(prev_sunday)))
        )
        has_prev = prev_result.scalar_one()

    return {
        "week_start": str(monday),
        "week_end": str(sunday),
        "week_offset": week_offset,
        "today": str(today),
        "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
        "has_prev": has_prev,
        "role": user.role,
        "days": days,
    }


@router.get("/api/activity/{activity_id}/details")
async def activity_details(
    activity_id: str,
    user: User = Depends(require_viewer),
) -> dict:
    if not activity_id or not activity_id.startswith("i") or not activity_id[1:].isdigit():
        raise HTTPException(status_code=400, detail="Invalid activity ID format")

    async with get_session() as session:
        activity = await session.get(Activity, activity_id)
        uid = get_data_user_id(user)
        if activity is None or activity.user_id != uid:
            raise HTTPException(status_code=404, detail="Activity not found")

        detail = await session.get(ActivityDetail, activity_id)
        hrv = await session.get(ActivityHrv, activity_id)

    return {
        "activity_id": activity.id,
        "type": activity.type,
        "date": activity.start_date_local,
        "moving_time": activity.moving_time,
        "duration": format_duration(activity.moving_time),
        "icu_training_load": round(activity.icu_training_load, 1) if activity.icu_training_load is not None else None,
        "average_hr": round(activity.average_hr) if activity.average_hr is not None else None,
        "is_race": activity.is_race,
        "details": serialize_activity_details(detail) if detail else None,
        "hrv": serialize_activity_hrv(hrv) if hrv and hrv.processing_status == "processed" else None,
    }


@router.get("/api/progress")
async def progress(
    sport: str = Query(default="", description="bike, run, or swim. Empty = all"),
    days: int = Query(default=90, ge=7, le=365),
    strict_filter: bool = Query(default=False, description="Strict decoupling filter"),
    user: User = Depends(require_viewer),
) -> dict:
    return await compute_efficiency_trend(
        user_id=get_data_user_id(user),
        sport=sport,
        days_back=days,
        strict_filter=strict_filter,
    )
