"""MCP tools for race goal progress."""

from datetime import date

from sqlalchemy import select

from data.db import AthleteGoal, Wellness, get_session
from data.db.dto import AthleteGoalDTO
from data.utils import extract_sport_ctl
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


def _pct(current: float | None, target: float | None) -> float | None:
    if current is None or not target or target <= 0:
        return None
    return round(current / target * 100, 1)


@mcp.tool()
async def get_goal_progress() -> dict:
    """Get race goal progress — overall and per-sport CTL vs targets.

    Shows event name, date, weeks remaining, and percentage of target CTL achieved
    for total, swim, bike, and run. CTL values come from Intervals.icu.
    """
    user_id = get_current_user_id()
    g: AthleteGoalDTO | None = await AthleteGoal.get_goal_dto(user_id)

    if not g:
        return {"error": "No active goal set for this user."}

    today = date.today()
    days_remaining = (g.event_date - today).days
    weeks_remaining = round(days_remaining / 7, 1)

    async with get_session() as session:
        result = await session.execute(
            select(Wellness)
            .where(Wellness.user_id == user_id, Wellness.ctl.isnot(None))
            .order_by(Wellness.date.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()

    current_ctl = row.ctl if row else None
    sport_ctl = extract_sport_ctl(row.sport_info) if row else {"swim": None, "bike": None, "run": None}
    targets = g.per_sport_targets or {}

    return {
        "event": g.event_name,
        "event_date": str(g.event_date),
        "sport_type": g.sport_type,
        "days_remaining": days_remaining,
        "weeks_remaining": weeks_remaining,
        "overall": {
            "current_ctl": current_ctl,
            "target_ctl": g.ctl_target,
            "pct": _pct(current_ctl, g.ctl_target),
        },
        "swim": {
            "current_ctl": sport_ctl["swim"],
            "target_ctl": targets.get("swim"),
            "pct": _pct(sport_ctl["swim"], targets.get("swim")),
        },
        "bike": {
            "current_ctl": sport_ctl["bike"],
            "target_ctl": targets.get("bike"),
            "pct": _pct(sport_ctl["bike"], targets.get("bike")),
        },
        "run": {
            "current_ctl": sport_ctl["run"],
            "target_ctl": targets.get("run"),
            "pct": _pct(sport_ctl["run"], targets.get("run")),
        },
    }
