"""MCP tools for training load data (CTL/ATL/TSB)."""

from data.database import WellnessRow, get_session
from mcp_server.app import mcp


def _extract_sport_ctl(sport_info) -> dict:
    """Extract per-sport CTL from Intervals.icu sport_info JSON."""
    result = {"swim": None, "bike": None, "run": None}
    if not sport_info:
        return result
    info = sport_info if isinstance(sport_info, list) else []
    for entry in info:
        sport = (entry.get("type") or entry.get("sport") or "").lower()
        ctl_val = entry.get("ctl") or entry.get("ctlLoad")
        if ctl_val is None:
            continue
        if sport in ("swim", "swimming"):
            result["swim"] = round(float(ctl_val), 1)
        elif sport in ("ride", "bike", "cycling"):
            result["bike"] = round(float(ctl_val), 1)
        elif sport in ("run", "running"):
            result["run"] = round(float(ctl_val), 1)
    return result


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
    sport_ctl = _extract_sport_ctl(row.sport_info)

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
