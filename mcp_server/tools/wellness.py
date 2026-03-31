"""MCP tools for wellness data."""

from sqlalchemy import select

from data.database import WellnessRow, get_session
from mcp_server.app import mcp


def _row_to_dict(row: WellnessRow) -> dict:
    """Convert WellnessRow to a flat dict for MCP response."""
    return {
        "date": row.date,
        "ctl": row.ctl,
        "atl": row.atl,
        "ramp_rate": row.ramp_rate,
        "ctl_load": row.ctl_load,
        "atl_load": row.atl_load,
        "sport_info": row.sport_info,
        "weight": row.weight,
        "resting_hr": row.resting_hr,
        "hrv": row.hrv,
        "sleep_secs": row.sleep_secs,
        "sleep_score": row.sleep_score,
        "sleep_quality": row.sleep_quality,
        "body_fat": row.body_fat,
        "vo2max": row.vo2max,
        "steps": row.steps,
        "ess_today": row.ess_today,
        "banister_recovery": row.banister_recovery,
        "recovery_score": row.recovery_score,
        "recovery_category": row.recovery_category,
        "recovery_recommendation": row.recovery_recommendation,
        "readiness_score": row.readiness_score,
        "readiness_level": row.readiness_level,
    }


@mcp.tool()
async def get_wellness(date: str) -> dict:
    """Get all wellness fields for a given date.

    Returns Intervals.icu synced data (CTL, ATL, HRV, sleep, body metrics)
    plus computed fields (recovery score, readiness).

    Args:
        date: Date in YYYY-MM-DD format
    """
    async with get_session() as session:
        result = await session.execute(
            select(WellnessRow).where(WellnessRow.user_id == 1, WellnessRow.date == date)  # TODO: per-user
        )
        row = result.scalar_one_or_none()
    if not row:
        return {"error": f"No data for {date}"}
    return _row_to_dict(row)


@mcp.tool()
async def get_wellness_range(from_date: str, to_date: str) -> dict:
    """Get wellness data for a date range (inclusive).

    Useful for trend analysis — returns a list of daily wellness records.

    Args:
        from_date: Start date in YYYY-MM-DD format
        to_date: End date in YYYY-MM-DD format
    """
    async with get_session() as session:
        result = await session.execute(
            select(WellnessRow)
            .where(WellnessRow.user_id == 1)  # TODO: per-user
            .where(WellnessRow.date >= from_date, WellnessRow.date <= to_date)
            .order_by(WellnessRow.date)
        )
        rows = result.scalars().all()

    if not rows:
        return {"error": f"No data for range {from_date} to {to_date}", "count": 0}

    return {
        "from_date": from_date,
        "to_date": to_date,
        "count": len(rows),
        "data": [_row_to_dict(r) for r in rows],
    }
