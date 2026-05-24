"""MCP tools for training load data (CTL/ATL/TSB)."""

from sqlalchemy import select

from data.db import Wellness, get_session
from data.metrics import recompute_today_loads
from data.utils import extract_sport_ctl
from data.utils import tsb_zone as _tsb_zone
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id
from tasks.dto import local_today


@mcp.tool()
async def get_training_load(date: str) -> dict:
    """Get CTL/ATL/TSB, ramp rate, and per-sport CTL from Intervals.icu."""
    user_id = get_current_user_id()
    async with get_session() as session:
        result = await session.execute(select(Wellness).where(Wellness.user_id == user_id, Wellness.date == date))
        row = result.scalar_one_or_none()

    if not row:
        return {"error": f"No data for {date}"}

    ctl, atl = row.ctl, row.atl
    # Intervals.icu bakes today's planned workouts into ctl/atl, so morning
    # reads look as if today's session is already done. Recompute from
    # yesterday + actually-completed activities. sport_ctl/ramp_rate untouched.
    if date == local_today().isoformat():
        recomputed = await recompute_today_loads(user_id)
        if recomputed is not None:
            ctl, atl, _ = recomputed

    tsb = round(ctl - atl, 1) if ctl is not None and atl is not None else None
    sport_ctl = extract_sport_ctl(row.sport_info)

    return {
        "date": date,
        "ctl": ctl,
        "atl": atl,
        "tsb": tsb,
        "ramp_rate": row.ramp_rate,
        "sport_ctl": sport_ctl,
        "interpretation": {
            "tsb_zone": _tsb_zone(tsb),
            "ramp_safe": row.ramp_rate <= 7 if row.ramp_rate else None,
        },
    }
