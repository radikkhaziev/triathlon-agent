"""MCP tools for race goal progress."""

from datetime import date

from sqlalchemy import select

from data.db import AthleteGoal, Wellness, get_session
from data.db.dto import AthleteGoalDTO
from data.utils import extract_sport_ctl
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id
from tasks.dto import local_today


def _pct(current: float | None, target: float | None) -> float | None:
    # Integer rounding matches the webapp gauge (api/routers/dashboard.py) so
    # the number Claude quotes in chat lines up with what the athlete sees.
    if current is None or not target or target <= 0:
        return None
    return round(current / target * 100)


def _goal_entry(g: AthleteGoalDTO, today: date, current_ctl: float | None, sport_ctl: dict) -> dict:
    """Build a single goal's progress block. Structure is identical to the
    legacy single-goal payload plus `goal_id`/`category` so multi-goal callers
    can disambiguate. Targets are read per goal row — per-sport CTL targets
    differ per race (see issue #473)."""
    days_remaining = (g.event_date - today).days
    weeks_remaining = round(days_remaining / 7, 1)
    targets = g.per_sport_targets or {}
    return {
        "goal_id": g.id,
        "category": g.category,
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
        "ride": {
            "current_ctl": sport_ctl["ride"],
            "target_ctl": targets.get("ride"),
            "pct": _pct(sport_ctl["ride"], targets.get("ride")),
        },
        "run": {
            "current_ctl": sport_ctl["run"],
            "target_ctl": targets.get("run"),
            "pct": _pct(sport_ctl["run"], targets.get("run")),
        },
    }


@mcp.tool()
async def get_goal_progress(goal_id: int | None = None) -> dict:
    """Get race goal progress for ALL active future goals: overall and per-sport
    CTL vs targets with weeks remaining.

    Athletes routinely run multiple A-races per season (e.g. Ironman 70.3 in
    September + Oceanlava in October), so this returns a `goals` array sorted by
    event_date ascending — one block per active future race, each with its own
    per-sport CTL targets. Pass `goal_id` to narrow the response to a single
    goal.
    """
    user_id = get_current_user_id()
    today = local_today()
    goals: list[AthleteGoalDTO] = await AthleteGoal.get_goals_for_settings(user_id, today)

    if goal_id is not None:
        goals = [g for g in goals if g.id == goal_id]
        if not goals:
            return {"error": f"No active goal with id={goal_id} for this user."}

    if not goals:
        return {"goals": [], "error": "No active goal set for this user."}

    # Current fitness is a single athlete-wide snapshot — the same latest CTL /
    # per-sport CTL applies to every goal; only the targets differ per race.
    async with get_session() as session:
        result = await session.execute(
            select(Wellness)
            .where(Wellness.user_id == user_id, Wellness.ctl.isnot(None))
            .order_by(Wellness.date.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()

    current_ctl = row.ctl if row else None
    sport_ctl = extract_sport_ctl(row.sport_info) if row else {"swim": None, "ride": None, "run": None}

    return {"goals": [_goal_entry(g, today, current_ctl, sport_ctl) for g in goals]}
