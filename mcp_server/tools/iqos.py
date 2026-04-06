"""MCP tools for IQOS stick tracking."""

from datetime import date, timedelta

from data.db import IqosDaily
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_iqos_sticks(target_date: str = "", days_back: int = 0) -> dict:
    """Get IQOS stick count for a single day or a date range.

    Returns the number of IQOS sticks smoked per day.
    Use days_back=0 for a single day, or days_back>0 to get a range.

    Args:
        target_date: Date in YYYY-MM-DD format. Default: today.
        days_back: Number of days to look back (0 = single day, 7 = last week, 30 = last month).
    """
    ref = date.fromisoformat(target_date) if target_date else date.today()

    user_id = get_current_user_id()
    if days_back == 0:
        row = await IqosDaily.get(user_id=user_id, target_date=ref)
        return {
            "date": str(ref),
            "count": row.count if row else 0,
        }

    rows = await IqosDaily.get_range(user_id=user_id, target_date=str(ref), days_back=days_back)
    from_date = ref - timedelta(days=days_back - 1)

    rows_by_date = {r.date: r.count for r in rows}
    total = sum(rows_by_date.values())

    return {
        "period": {"from": str(from_date), "to": str(ref)},
        "total": total,
        "days_with_data": len(rows),
        "average_per_day": round(total / max(len(rows), 1), 1),
        "daily": [{"date": r.date, "count": r.count} for r in rows],
    }
