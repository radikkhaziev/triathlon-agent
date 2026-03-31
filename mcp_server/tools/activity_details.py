"""MCP tool for detailed activity statistics (Phase 2)."""

from data.db import Activity, ActivityDetail, ActivityHrv, get_session
from data.utils import format_duration, serialize_activity_details, serialize_activity_hrv
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_activity_details(activity_id: str) -> dict:
    """Get detailed statistics for a specific activity.

    Returns summary metrics (power, HR, pace, efficiency), zone distributions
    (HR/power/pace), interval breakdown, and DFA alpha 1 analysis if available.
    Combines data from activity_details and activity_hrv tables.

    Note: CTL/ATL/TSB and training load values come from Intervals.icu and
    thresholds are calibrated for its model, not TrainingPeaks.

    Args:
        activity_id: Intervals.icu activity ID (e.g. "i12345")
    """
    user_id = get_current_user_id()
    async with get_session() as session:
        activity = await session.get(Activity, activity_id)
        if activity is None or activity.user_id != user_id:
            return {"error": f"Activity {activity_id} not found."}

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
        "details": serialize_activity_details(detail) if detail else None,
        "hrv": serialize_activity_hrv(hrv) if hrv and hrv.processing_status == "processed" else None,
    }
