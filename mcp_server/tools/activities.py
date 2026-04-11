"""MCP tools for completed activities from Intervals.icu."""

from datetime import date, timedelta

from sqlalchemy import select

from data.db import Activity, ActivityHrv, get_session
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_activities(target_date: str = "", days_back: int = 7) -> dict:
    """Get completed activities with sport type, training load, duration, and DFA a1 availability."""
    end = date.fromisoformat(target_date) if target_date else date.today()
    start = end - timedelta(days=days_back)

    user_id = get_current_user_id()
    async with get_session() as session:
        query = (
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.start_date_local >= str(start),
                Activity.start_date_local <= str(end),
            )
            .order_by(Activity.start_date_local.desc())
        )
        rows = (await session.execute(query)).scalars().all()

        # Fetch HRV analysis for these activities
        if rows:
            activity_ids = [r.id for r in rows]
            hrv_rows = (
                (await session.execute(select(ActivityHrv).where(ActivityHrv.activity_id.in_(activity_ids))))
                .scalars()
                .all()
            )
            hrv_map = {h.activity_id: h for h in hrv_rows}
        else:
            hrv_map = {}

    if not rows:
        return {"count": 0, "from": str(start), "to": str(end), "activities": []}

    activities = []
    for r in rows:
        duration = None
        if r.moving_time:
            h, m = divmod(r.moving_time // 60, 60)
            duration = f"{h}h {m}m" if h else f"{m}m"

        entry = {
            "id": r.id,
            "date": r.start_date_local,
            "type": r.type,
            "training_load": r.icu_training_load,
            "duration": duration,
            "duration_secs": r.moving_time,
        }

        hrv = hrv_map.get(r.id)
        if hrv and hrv.processing_status == "processed":
            entry["has_hrv_analysis"] = True
            entry["dfa_a1_mean"] = hrv.dfa_a1_mean
        else:
            entry["has_hrv_analysis"] = False

        activities.append(entry)

    total_load = sum(a["training_load"] or 0 for a in activities)

    return {
        "count": len(activities),
        "from": str(start),
        "to": str(end),
        "total_training_load": round(total_load, 1),
        "activities": activities,
    }
