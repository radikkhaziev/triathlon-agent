"""MCP tools for recovery score data."""

from sqlalchemy import select

from data.db import Wellness, get_session
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_recovery(date: str) -> dict:
    """Get composite recovery score and training recommendation.

    Recovery score (0-100) combines: RMSSD 35%, Banister 25%, RHR 20%, Sleep 20%.
    Categories: excellent >85, good 70-85, moderate 40-70, low <40.
    Recommendations: zone2_ok, zone1_long, zone1_short, skip.

    Args:
        date: Date in YYYY-MM-DD format
    """
    user_id = get_current_user_id()
    async with get_session() as session:
        result = await session.execute(select(Wellness).where(Wellness.user_id == user_id, Wellness.date == date))
        row = result.scalar_one_or_none()

    if not row:
        return {"error": f"No data for {date}"}

    sleep_duration = None
    if row.sleep_secs:
        h, m = divmod(row.sleep_secs // 60, 60)
        sleep_duration = f"{h}h {m}m" if h else f"{m}m"

    return {
        "date": date,
        "score": row.recovery_score,
        "category": row.recovery_category,
        "recommendation": row.recovery_recommendation,
        "readiness_score": row.readiness_score,
        "readiness_level": row.readiness_level,
        "sleep_score": row.sleep_score,
        "sleep_duration": sleep_duration,
        "ess_today": row.ess_today,
        "banister_recovery": row.banister_recovery,
        "ai_recommendation": row.ai_recommendation,
    }
