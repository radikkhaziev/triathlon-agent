"""MCP tools for resting heart rate analysis."""

from data.database import RhrAnalysisRow, get_session
from mcp_server.app import mcp


@mcp.tool()
async def get_rhr_analysis(date: str) -> dict:
    """Get resting heart rate analysis for a given date.

    Returns RHR status with 7-day, 30-day, and 60-day baselines.
    Bounds are ±0.5 SD of 30-day mean.
    Inverted vs HRV: elevated RHR = under-recovered (red), low RHR = well-recovered (green).

    Args:
        date: Date in YYYY-MM-DD format
    """
    async with get_session() as session:
        row = await session.get(RhrAnalysisRow, date)

    if not row:
        return {"date": date, "status": "insufficient_data"}

    delta_30d = None
    if row.rhr_today and row.rhr_30d:
        delta_30d = round(row.rhr_today - row.rhr_30d, 1)

    return {
        "date": date,
        "status": row.status,
        "today": row.rhr_today,
        "mean_7d": row.rhr_7d,
        "sd_7d": row.rhr_sd_7d,
        "mean_30d": row.rhr_30d,
        "sd_30d": row.rhr_sd_30d,
        "mean_60d": row.rhr_60d,
        "sd_60d": row.rhr_sd_60d,
        "delta_30d": delta_30d,
        "lower_bound": row.lower_bound,
        "upper_bound": row.upper_bound,
        "cv_7d": row.cv_7d,
        "days_available": row.days_available,
        "trend": (
            {
                "direction": row.trend_direction,
                "slope": row.trend_slope,
                "r_squared": row.trend_r_squared,
            }
            if row.trend_direction
            else None
        ),
    }
