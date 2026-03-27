"""MCP tools for training load data (CTL/ATL/TSB)."""

from data.database import WellnessRow, get_session
from data.utils import extract_sport_ctl
from mcp_server.app import mcp


def _tsb_zone(tsb: float | None) -> str | None:
    if tsb is None:
        return None
    if tsb > 10:
        return "under_training"
    if tsb >= -10:
        return "optimal"
    if tsb >= -25:
        return "productive_overreach"
    return "overtraining_risk"


@mcp.tool()
async def get_training_load(date: str) -> dict:
    """Get CTL/ATL/TSB and per-sport CTL for a given date.

    All values come from Intervals.icu (impulse-response model, tau_CTL=42d, tau_ATL=7d).
    Thresholds are calibrated for Intervals.icu, NOT TrainingPeaks.
    TSB zones: >+10 under-training, -10..+10 optimal, -10..-25 productive overreach, <-25 overtraining risk.

    Args:
        date: Date in YYYY-MM-DD format
    """
    async with get_session() as session:
        row = await session.get(WellnessRow, date)

    if not row:
        return {"error": f"No data for {date}"}

    tsb = round(row.ctl - row.atl, 1) if row.ctl is not None and row.atl is not None else None
    sport_ctl = extract_sport_ctl(row.sport_info)

    return {
        "date": date,
        "ctl": row.ctl,
        "atl": row.atl,
        "tsb": tsb,
        "ramp_rate": row.ramp_rate,
        "sport_ctl": sport_ctl,
        "interpretation": {
            "tsb_zone": _tsb_zone(tsb),
            "ramp_safe": row.ramp_rate <= 7 if row.ramp_rate else None,
        },
    }
