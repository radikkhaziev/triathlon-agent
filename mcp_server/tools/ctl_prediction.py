"""MCP tool for CTL target date prediction."""

from datetime import date, timedelta

from sqlalchemy import select

from data.db import Wellness, get_session
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


def _extract_sport_ctl(sport_info: list[dict] | None, sport: str) -> float | None:
    """Extract CTL for a specific sport from sport_info list."""
    if not sport_info:
        return None
    sport_lower = sport.lower()
    for entry in sport_info:
        if entry.get("type", "").lower() == sport_lower:
            return entry.get("ctl")
    return None


@mcp.tool()
async def predict_ctl(target_ctl: float, sport: str = "") -> dict:
    """Predict when CTL will reach target value based on recent ramp rate."""
    user_id = get_current_user_id()
    today = date.today()

    async with get_session() as session:
        rows = (
            await session.execute(
                select(Wellness.date, Wellness.ctl, Wellness.sport_info)
                .where(Wellness.user_id == user_id, Wellness.ctl.isnot(None))
                .order_by(Wellness.date.desc())
                .limit(15)
            )
        ).all()

    if len(rows) < 2:
        return {"error": "Not enough CTL data (need at least 2 days)."}

    newest = rows[0]
    oldest = rows[-1]

    # Current CTL
    if sport:
        current_ctl = None
        for dt, ctl, sport_info in rows:
            current_ctl = _extract_sport_ctl(sport_info, sport)
            if current_ctl is not None:
                break
        if current_ctl is None:
            return {"error": f"No CTL data for sport '{sport}'."}
    else:
        current_ctl = newest[1]

    # Ramp rate from available span
    days_span = (date.fromisoformat(newest[0]) - date.fromisoformat(oldest[0])).days
    if days_span < 7:
        return {"error": "Not enough history for ramp rate (need 7+ days)."}

    if sport:
        oldest_sport_ctl = None
        for dt, ctl, sport_info in reversed(rows):
            oldest_sport_ctl = _extract_sport_ctl(sport_info, sport)
            if oldest_sport_ctl is not None:
                break
        if oldest_sport_ctl is None:
            return {"error": f"No historical CTL for sport '{sport}'."}
        ctl_delta = current_ctl - oldest_sport_ctl
    else:
        ctl_delta = newest[1] - oldest[1]

    ramp_per_week = ctl_delta / (days_span / 7)
    gap = target_ctl - current_ctl

    if ramp_per_week <= 0 and gap > 0:
        return {
            "current_ctl": round(current_ctl, 1),
            "target_ctl": target_ctl,
            "ramp_rate_per_week": round(ramp_per_week, 2),
            "estimated_date": None,
            "note": "CTL is declining or flat — target cannot be reached at current rate.",
        }

    if gap <= 0:
        return {
            "current_ctl": round(current_ctl, 1),
            "target_ctl": target_ctl,
            "ramp_rate_per_week": round(ramp_per_week, 2),
            "estimated_date": None,
            "note": "Target already reached!",
        }

    weeks_to_target = gap / ramp_per_week
    estimated_date = today + timedelta(weeks=weeks_to_target)

    confidence = "high" if days_span >= 14 else "medium"
    if ramp_per_week > 7:
        confidence = "low"

    return {
        "current_ctl": round(current_ctl, 1),
        "target_ctl": target_ctl,
        "sport": sport or "total",
        "ramp_rate_per_week": round(ramp_per_week, 2),
        "data_days": days_span,
        "estimated_weeks": round(weeks_to_target, 1),
        "estimated_date": str(estimated_date),
        "confidence": confidence,
    }
