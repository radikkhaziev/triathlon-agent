"""MCP tools for scheduled workouts from Intervals.icu calendar."""

from datetime import date, timedelta

from sqlalchemy import select

from data.db import ScheduledWorkout, get_session
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_scheduled_workouts(target_date: str = "", days_ahead: int = 0) -> dict:
    """Get planned workouts from Intervals.icu calendar.

    Returns scheduled workouts for a specific date or a date range.
    Includes workout name, sport type, duration, distance, and full description
    with interval structure (zones, power targets) from HumanGo.

    Args:
        target_date: Date in YYYY-MM-DD format. Default: today.
        days_ahead: Number of days ahead to include (0 = single day, 7 = week, 14 = two weeks).
    """
    start = date.fromisoformat(target_date) if target_date else date.today()
    end = start + timedelta(days=days_ahead)

    user_id = get_current_user_id()
    async with get_session() as session:
        query = (
            select(ScheduledWorkout)
            .where(
                ScheduledWorkout.user_id == user_id,
                ScheduledWorkout.start_date_local >= str(start),
                ScheduledWorkout.start_date_local <= str(end),
            )
            .order_by(ScheduledWorkout.start_date_local)
        )
        rows = (await session.execute(query)).scalars().all()

    if not rows:
        return {"count": 0, "from": str(start), "to": str(end), "workouts": []}

    workouts = []
    for r in rows:
        duration = None
        if r.moving_time:
            h, m = divmod(r.moving_time // 60, 60)
            duration = f"{h}h {m}m" if h else f"{m}m"

        workouts.append(
            {
                "date": r.start_date_local,
                "type": r.type,
                "name": r.name,
                "category": r.category,
                "duration": duration,
                "duration_secs": r.moving_time,
                "distance_km": r.distance,
                "description": r.description,
            }
        )

    return {
        "count": len(workouts),
        "from": str(start),
        "to": str(end),
        "workouts": workouts,
    }
