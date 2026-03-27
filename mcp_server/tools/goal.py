"""MCP tools for race goal progress."""

from datetime import date

from sqlalchemy import select

from config import settings
from data.database import WellnessRow, get_session
from data.utils import extract_sport_ctl
from mcp_server.app import mcp


def _pct(current: float | None, target: float) -> float | None:
    if current is None or target <= 0:
        return None
    return round(current / target * 100, 1)


@mcp.tool()
async def get_goal_progress() -> dict:
    """Get race goal progress — overall and per-sport CTL vs targets.

    Shows event name, date, weeks remaining, and percentage of target CTL achieved
    for total, swim, bike, and run. CTL values come from Intervals.icu.
    """
    today = date.today()
    days_remaining = (settings.GOAL_EVENT_DATE - today).days
    weeks_remaining = round(days_remaining / 7, 1)

    # Get latest wellness row
    async with get_session() as session:
        result = await session.execute(
            select(WellnessRow).where(WellnessRow.ctl.isnot(None)).order_by(WellnessRow.id.desc()).limit(1)
        )
        row = result.scalar_one_or_none()

    current_ctl = row.ctl if row else None
    sport_ctl = extract_sport_ctl(row.sport_info) if row else {"swim": None, "bike": None, "run": None}

    return {
        "event": settings.GOAL_EVENT_NAME,
        "event_date": str(settings.GOAL_EVENT_DATE),
        "days_remaining": days_remaining,
        "weeks_remaining": weeks_remaining,
        "overall": {
            "current_ctl": current_ctl,
            "target_ctl": settings.GOAL_CTL_TARGET,
            "pct": _pct(current_ctl, settings.GOAL_CTL_TARGET),
        },
        "swim": {
            "current_ctl": sport_ctl["swim"],
            "target_ctl": settings.GOAL_SWIM_CTL_TARGET,
            "pct": _pct(sport_ctl["swim"], settings.GOAL_SWIM_CTL_TARGET),
        },
        "bike": {
            "current_ctl": sport_ctl["bike"],
            "target_ctl": settings.GOAL_BIKE_CTL_TARGET,
            "pct": _pct(sport_ctl["bike"], settings.GOAL_BIKE_CTL_TARGET),
        },
        "run": {
            "current_ctl": sport_ctl["run"],
            "target_ctl": settings.GOAL_RUN_CTL_TARGET,
            "pct": _pct(sport_ctl["run"], settings.GOAL_RUN_CTL_TARGET),
        },
    }
