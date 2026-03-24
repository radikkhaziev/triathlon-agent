"""MCP tools for recovery score data."""

from data.database import WellnessRow, get_session
from mcp_server.app import mcp


@mcp.tool()
async def get_recovery(date: str) -> dict:
    """Get composite recovery score and training recommendation.

    Recovery score (0-100) combines: RMSSD 35%, Banister 25%, RHR 20%, Sleep 20%.
    Categories: excellent >85, good 70-85, moderate 40-70, low <40.
    Recommendations: zone2_ok, zone1_long, zone1_short, skip.

    Args:
        date: Date in YYYY-MM-DD format
    """
    async with get_session() as session:
        row = await session.get(WellnessRow, date)

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
    }
