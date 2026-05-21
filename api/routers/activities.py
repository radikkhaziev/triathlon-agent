import zoneinfo
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, select

from api.deps import get_data_user_id, require_viewer
from config import settings
from data.db import Activity, ActivityDetail, ActivityHrv, ActivityWeather, Race, ScheduledWorkout, User, get_session
from data.utils import format_duration, serialize_activity_details, serialize_activity_hrv
from mcp_server.tools.polarization import get_polarization_multi_window
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
                    # Intervals.icu's planned-vs-actual compliance (0-100 %).
                    # Surfaced on the Week tab as the «N% on plan» chip on
                    # past activity rows (design direction-b-halo.jsx:1489).
                    # NULL when the activity wasn't paired with a planned event.
                    "compliance": round(a.compliance, 1) if a.compliance is not None else None,
                    # FK-less reference to `scheduled_workouts.id` (Intervals'
                    # native pairing). Drives the day-card merge logic: a
                    # planned session whose id appears here is «covered» by the
                    # actual and shouldn't render twice on multi-session days.
                    "paired_event_id": a.paired_event_id,
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
        "today": str(today),
        "has_prev": has_prev,
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
        weather = await session.get(ActivityWeather, activity_id)
        race = None
        if activity.is_race:
            race = (
                await session.execute(select(Race).where(Race.user_id == uid, Race.activity_id == activity_id))
            ).scalar_one_or_none()

        # Paired planned workout — surfaced for the Activity detail page's
        # «PLAN | <name> ›» breadcrumb (design BActivityWorkout, direction-b-
        # halo.jsx:2081-2096). Lets the user jump back to the workout the
        # activity executed without a second roundtrip. NULL when Intervals
        # didn't pair or the paired workout was deleted.
        paired_workout = None
        if activity.paired_event_id is not None:
            paired = await session.get(ScheduledWorkout, activity.paired_event_id)
            if paired is not None and paired.user_id == uid:
                paired_workout = {
                    "id": paired.id,
                    "name": paired.name,
                    "duration_secs": paired.moving_time,
                    "icu_training_load": (
                        round(paired.icu_training_load, 1) if paired.icu_training_load is not None else None
                    ),
                }

    return {
        "activity_id": activity.id,
        "type": activity.type,
        "date": activity.start_date_local,
        "moving_time": activity.moving_time,
        "duration": format_duration(activity.moving_time),
        "icu_training_load": round(activity.icu_training_load, 1) if activity.icu_training_load is not None else None,
        "average_hr": round(activity.average_hr) if activity.average_hr is not None else None,
        "rpe": activity.rpe,
        # Intervals.icu's native workout compliance % (planned vs actual,
        # 0-100). NULL when activity had no scheduled workout to compare.
        "compliance": round(activity.compliance, 1) if activity.compliance is not None else None,
        # Intervals.icu's native planned-vs-actual pairing (FK-less reference to
        # scheduled_workouts.id). Drives the «open planned workout» link on the
        # activity detail page. NULL when Intervals didn't pair or pairing was
        # cleaned up (planned event deleted).
        "paired_event_id": activity.paired_event_id,
        # Resolved paired workout (name + planned duration + planned TSS) for
        # the «PLAN | <name> ›» breadcrumb and Plan vs Actual mini-table. NULL
        # when paired_event_id is null OR the pointed-at workout was deleted.
        "paired_workout": paired_workout,
        "is_race": activity.is_race,
        "race": (
            {
                "name": race.name,
                "distance_km": round(race.distance_m / 1000, 2) if race.distance_m else None,
                "finish_time_sec": race.finish_time_sec,
                "goal_time_sec": race.goal_time_sec,
                "placement": race.placement,
                "placement_total": race.placement_total,
                "placement_ag": race.placement_ag,
                "surface": race.surface,
                "weather": race.weather,
                "avg_pace_sec_km": race.avg_pace_sec_km,
                "rpe": race.rpe,
                "notes": race.notes,
                "race_day_ctl": race.race_day_ctl,
                "race_day_tsb": race.race_day_tsb,
                "race_day_recovery_score": race.race_day_recovery_score,
                "race_day_hrv_status": race.race_day_hrv_status,
            }
            if race
            else None
        ),
        "details": serialize_activity_details(detail) if detail else None,
        "hrv": serialize_activity_hrv(hrv) if hrv and hrv.processing_status == "processed" else None,
        # Outdoor weather block — populated from ACTIVITY_UPLOADED webhook when
        # `dto.has_weather=True`. Indoor / virtual rides have no row.
        "weather": (
            {
                "avg_temp_c": weather.avg_temp_c,
                "avg_feels_like_c": weather.avg_feels_like_c,
                "avg_wind_speed_mps": weather.avg_wind_speed_mps,
                "prevailing_wind_deg": weather.prevailing_wind_deg,
                "headwind_pct": weather.headwind_pct,
                "avg_clouds": weather.avg_clouds,
                "max_rain_mm": weather.max_rain_mm,
                "max_snow_mm": weather.max_snow_mm,
            }
            if weather
            else None
        ),
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


@router.get("/api/polarization")
async def polarization(
    sport: str = Query(default="run", description="run or ride"),
    days: int = Query(default=28, description="Primary window: 7, 14, 28, or 56"),
    user: User = Depends(require_viewer),
) -> dict:
    """Get Polarization Index with multi-window analysis and trend signals."""
    windows, signals = await get_polarization_multi_window(get_data_user_id(user), sport)

    return {
        "windows": {str(d): w for d, w in windows.items()},
        "signals": signals,
    }
