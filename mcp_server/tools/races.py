"""MCP tools for race tagging and analytics."""

from datetime import date, timedelta

from sqlalchemy import select

from data.db import Activity, Race, Wellness, get_session
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


def _fmt_time(secs: int | None) -> str | None:
    if not secs:
        return None
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_pace(secs_km: float | None) -> str | None:
    if not secs_km:
        return None
    m, s = divmod(int(secs_km), 60)
    return f"{m}:{s:02d}/km"


@mcp.tool()
async def get_races(days_back: int = 365, sport: str = "") -> dict:
    """Get race history with fitness context, results, and activity metrics."""
    user_id = get_current_user_id()
    end = date.today()
    start = end - timedelta(days=days_back)

    races = await Race.get_range(user_id, str(start), str(end))

    # Batch-fetch all activities
    act_map: dict = {}
    if races:
        async with get_session() as session:
            act_ids = [r.activity_id for r in races]
            rows = (
                (await session.execute(select(Activity).where(Activity.id.in_(act_ids), Activity.user_id == user_id)))
                .scalars()
                .all()
            )
            act_map = {a.id: a for a in rows}

    if sport:
        sport_lower = sport.lower()
        races = [
            r
            for r in races
            if (act_map.get(r.activity_id) and (act_map[r.activity_id].type or "").lower() == sport_lower)
        ]

    entries = []
    for r in races:
        act = act_map.get(r.activity_id)

        entry = {
            "date": act.start_date_local if act else None,
            "name": r.name,
            "race_type": r.race_type,
            "sport": act.type if act else None,
            "distance_km": round(r.distance_m / 1000, 1) if r.distance_m else None,
            "finish_time": _fmt_time(r.finish_time_sec),
            "finish_time_sec": r.finish_time_sec,
            "goal_time": _fmt_time(r.goal_time_sec),
            "avg_pace": _fmt_pace(r.avg_pace_sec_km),
            "placement": r.placement,
            "surface": r.surface,
            "weather": r.weather,
            "rpe": r.rpe,
            "notes": r.notes,
            "fitness_context": {
                "ctl": r.race_day_ctl,
                "atl": r.race_day_atl,
                "tsb": r.race_day_tsb,
                "hrv_status": r.race_day_hrv_status,
                "recovery_score": r.race_day_recovery_score,
                "weight": r.race_day_weight,
            },
            "activity": {
                "id": r.activity_id,
                "duration_min": act.moving_time // 60 if act and act.moving_time else None,
                "avg_hr": act.average_hr if act else None,
                "tss": act.icu_training_load if act else None,
            },
        }
        entries.append(entry)

    return {"count": len(entries), "races": entries}


@mcp.tool()
async def tag_race(
    activity_id: str,
    name: str,
    race_type: str = "C",
    distance_m: float | None = None,
    finish_time_sec: int | None = None,
    goal_time_sec: int | None = None,
    placement: int | None = None,
    placement_total: int | None = None,
    surface: str | None = None,
    weather: str | None = None,
    rpe: int | None = None,
    notes: str | None = None,
) -> dict:
    """Tag activity as race. Auto-fills fitness context from wellness data."""
    user_id = get_current_user_id()

    async with get_session() as session:
        activity = (
            await session.execute(select(Activity).where(Activity.id == activity_id, Activity.user_id == user_id))
        ).scalar_one_or_none()

        if not activity:
            return {"error": f"Activity {activity_id} not found."}

        activity.is_race = True
        activity.sub_type = "RACE"

        # Auto-fill fitness context
        wellness = (
            await session.execute(
                select(Wellness).where(
                    Wellness.user_id == user_id,
                    Wellness.date == activity.start_date_local,
                )
            )
        ).scalar_one_or_none()

        # Calculate pace
        avg_pace = None
        if distance_m and activity.moving_time and distance_m > 0:
            avg_pace = round(activity.moving_time / (distance_m / 1000), 1)

        if not finish_time_sec and activity.moving_time:
            finish_time_sec = activity.moving_time

        # Check existing race
        existing = (await session.execute(select(Race).where(Race.activity_id == activity_id))).scalar_one_or_none()

        race_data = {
            "user_id": user_id,
            "activity_id": activity_id,
            "name": name,
            "race_type": race_type,
            "distance_m": distance_m,
            "finish_time_sec": finish_time_sec,
            "goal_time_sec": goal_time_sec,
            "placement": placement,
            "placement_total": placement_total,
            "surface": surface,
            "weather": weather,
            "avg_pace_sec_km": avg_pace,
            "rpe": rpe,
            "notes": notes,
            "race_day_ctl": wellness.ctl if wellness else None,
            "race_day_atl": wellness.atl if wellness else None,
            "race_day_tsb": (wellness.ctl - wellness.atl) if wellness and wellness.ctl and wellness.atl else None,
            "race_day_recovery_score": wellness.recovery_score if wellness else None,
            "race_day_weight": wellness.weight if wellness else None,
        }

        if existing:
            for k, v in race_data.items():
                if v is not None:
                    setattr(existing, k, v)
        else:
            session.add(Race(**race_data))

        await session.commit()

    return {
        "status": "tagged",
        "activity_id": activity_id,
        "name": name,
        "race_type": race_type,
        "distance_km": round(distance_m / 1000, 1) if distance_m else None,
        "finish_time": _fmt_time(finish_time_sec),
        "avg_pace": _fmt_pace(avg_pace),
        "fitness_context": {
            "ctl": race_data["race_day_ctl"],
            "tsb": race_data["race_day_tsb"],
            "recovery": race_data["race_day_recovery_score"],
        },
    }


@mcp.tool()
async def update_race(
    activity_id: str,
    name: str | None = None,
    race_type: str | None = None,
    distance_m: float | None = None,
    finish_time_sec: int | None = None,
    goal_time_sec: int | None = None,
    placement: int | None = None,
    placement_total: int | None = None,
    surface: str | None = None,
    weather: str | None = None,
    rpe: int | None = None,
    notes: str | None = None,
) -> dict:
    """Update race details (placement, notes, RPE, weather, etc.)."""
    user_id = get_current_user_id()

    async with get_session() as session:
        race = (
            await session.execute(select(Race).where(Race.activity_id == activity_id, Race.user_id == user_id))
        ).scalar_one_or_none()

        if not race:
            return {"error": f"No race found for activity {activity_id}."}

        updates = {}
        for field, value in [
            ("name", name),
            ("race_type", race_type),
            ("distance_m", distance_m),
            ("finish_time_sec", finish_time_sec),
            ("goal_time_sec", goal_time_sec),
            ("placement", placement),
            ("placement_total", placement_total),
            ("surface", surface),
            ("weather", weather),
            ("rpe", rpe),
            ("notes", notes),
        ]:
            if value is not None:
                setattr(race, field, value)
                updates[field] = value

        # Recalculate pace if distance changed
        if distance_m:
            act = (await session.execute(select(Activity.moving_time).where(Activity.id == activity_id))).scalar()
            if act and distance_m > 0:
                race.avg_pace_sec_km = round(act / (distance_m / 1000), 1)
                updates["avg_pace"] = _fmt_pace(race.avg_pace_sec_km)

        await session.commit()

    return {"status": "updated", "activity_id": activity_id, "updates": updates}
