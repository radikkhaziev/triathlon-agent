"""MCP tool for workout compliance analysis: planned vs actual."""

from sqlalchemy import select

from data.db import Activity, ActivityDetail, ScheduledWorkout, TrainingLog, get_session
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_workout_compliance(activity_id: str) -> dict:
    """Compare completed activity against scheduled workout. Returns compliance rating."""
    user_id = get_current_user_id()

    # Read all ORM attributes inside session to avoid DetachedInstanceError
    async with get_session() as session:
        activity = (
            await session.execute(select(Activity).where(Activity.id == activity_id, Activity.user_id == user_id))
        ).scalar_one_or_none()

        if not activity:
            return {"error": f"Activity {activity_id} not found."}

        detail = (
            await session.execute(select(ActivityDetail).where(ActivityDetail.activity_id == activity_id))
        ).scalar_one_or_none()

        # Extract values inside session
        act_date = activity.start_date_local
        act_type = activity.type
        act_moving_time = activity.moving_time
        act_avg_hr = activity.average_hr
        act_tss = activity.icu_training_load
        det_avg_power = detail.avg_power if detail else None

    workouts = await ScheduledWorkout.get_for_date(user_id, act_date)
    matched = next((w for w in workouts if w.type == act_type), None)

    logs = await TrainingLog.get_for_date(user_id, act_date)
    log_entry = next((entry for entry in logs if str(entry.actual_activity_id) == str(activity_id)), None)

    planned = None
    if matched:
        planned = {
            "name": matched.name,
            "duration_min": matched.moving_time // 60 if matched.moving_time else None,
            "description": (matched.description or "")[:200] if matched.description else None,
        }

    actual = {
        "sport": act_type,
        "duration_min": act_moving_time // 60 if act_moving_time else None,
        "avg_hr": act_avg_hr,
        "avg_power": det_avg_power,
        "tss": act_tss,
        "max_zone": log_entry.actual_max_zone_time if log_entry else None,
    }

    compliance = _compute_compliance(planned, actual)

    return {
        "activity_id": activity_id,
        "date": act_date,
        "planned": planned,
        "actual": actual,
        "compliance": compliance,
        "training_log_compliance": log_entry.compliance if log_entry else None,
    }


def _compute_compliance(planned: dict | None, actual: dict) -> dict:
    if not planned:
        return {"overall": "unplanned", "note": "No scheduled workout found for this date/sport."}

    result: dict = {}

    if planned["duration_min"] and actual["duration_min"]:
        duration_pct = round(actual["duration_min"] / planned["duration_min"] * 100)
        result["duration_pct"] = duration_pct
    else:
        duration_pct = None

    if duration_pct is None:
        result["overall"] = "unknown"
        result["note"] = "Cannot determine — missing duration data."
    elif 90 <= duration_pct <= 110:
        result["overall"] = "excellent"
    elif 70 <= duration_pct <= 130:
        result["overall"] = "good"
    elif 50 <= duration_pct <= 150:
        result["overall"] = "partial"
    else:
        result["overall"] = "off_target"
        result["note"] = f"Duration was {duration_pct}% of planned."

    return result
