"""Taper plan service — input resolution + envelope around `build_taper_plan`.

Shared by the `get_taper_plan` MCP tool and `GET /api/taper-plan` (LoadDetail
taper overlay), same pattern as `race_plan_service.py`: one resolution path so
chat and webapp can never disagree on the plan. See TAPER_PLANNER_SPEC Phase 2/4.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from sqlalchemy import select

from data.db import Activity, AthleteGoal, Wellness, get_session
from data.metrics import build_taper_plan, recompute_today_loads
from tasks.dto import local_today

_HISTORY_DAYS = 42
_MIN_HISTORY_DAYS = 14

# Heuristic event-name markers → race_distance_class (§5). Checked lowercase;
# the caller can always override via the explicit parameter. Long markers are
# checked first on purpose — they're the more specific signal when both match.
_LONG_MARKERS = ("70.3", "140.6", "ironman", "half-distance", "marathon", "марафон", "ultra", "ультра")
_SHORT_MARKERS = ("sprint", "спринт", "5k", "5к", "parkrun", "паркран")
_VALID_CLASSES = ("long", "standard", "short")


def _distance_class_from_name(event_name: str | None) -> str:
    name = (event_name or "").lower()
    if any(m in name for m in _LONG_MARKERS):
        return "long"
    if any(m in name for m in _SHORT_MARKERS):
        return "short"
    return "standard"


async def _resolve_loads(user_id: int) -> tuple[float, float] | None:
    """Today's (CTL, ATL): de-planned via `recompute_today_loads`, falling back
    to the latest wellness row (Intervals values, planned workouts baked in)."""
    loads = await recompute_today_loads(user_id)
    if loads is not None:
        return loads[0], loads[1]  # [2] is TSB, unused here
    async with get_session() as session:
        row = (
            await session.execute(
                select(Wellness.ctl, Wellness.atl)
                .where(Wellness.user_id == user_id, Wellness.ctl.isnot(None), Wellness.atl.isnot(None))
                .order_by(Wellness.date.desc())
                .limit(1)
            )
        ).first()
    if row is None:
        return None
    return float(row.ctl), float(row.atl)


async def _resolve_peak_daily_load(user_id: int, today: date, ctl_now: float) -> tuple[float, bool]:
    """`max(ctl_now, median daily TSS of the best rolling 7-day window)` over
    the last 42 days (spec §7 candidate). Returns (value, used_fallback) —
    fallback is plain `ctl_now` when history is shorter than 2 weeks (§6)."""
    activities, _ = await Activity.get_range(user_id, today - timedelta(days=_HISTORY_DAYS), today)
    daily: dict[date, float] = {}
    for act in activities:
        if act.icu_training_load is None:
            continue
        day = date.fromisoformat(str(act.start_date_local)[:10])
        daily[day] = daily.get(day, 0.0) + float(act.icu_training_load)

    if not daily or (today - min(daily)).days < _MIN_HISTORY_DAYS:
        return ctl_now, True

    days = [today - timedelta(days=_HISTORY_DAYS - i) for i in range(_HISTORY_DAYS + 1)]
    series = [daily.get(d, 0.0) for d in days]
    best_i = max(range(len(series) - 6), key=lambda i: sum(series[i : i + 7]))
    peak_week_median = statistics.median(series[best_i : best_i + 7])
    return max(ctl_now, peak_week_median), False


async def get_taper_plan_for_user(
    user_id: int,
    *,
    goal_id: int | None = None,
    race_date: str = "",
    race_distance_class: str = "",
) -> dict:
    """Resolve inputs and build the taper-plan envelope for `user_id`.

    Refusal gates (§6) return `{available: False, reason, hint?}`; otherwise
    `{available: True, ...build_taper_plan output}` with dates serialised to
    ISO strings. Read-only.
    """
    today = local_today()

    goal = None
    if goal_id is not None:
        goals = await AthleteGoal.get_all(user_id)
        goal = next((g for g in goals if g.id == goal_id and g.is_active), None)
        if goal is None:
            return {"available": False, "reason": "goal_not_found", "hint": "Check goal_id via get_races."}

    if race_date:
        try:
            race_dt = date.fromisoformat(race_date)
        except ValueError:
            return {"available": False, "reason": "invalid_race_date", "hint": "Use ISO format YYYY-MM-DD."}
        event_name = goal.event_name if goal else None
    elif goal is not None:
        race_dt, event_name = goal.event_date, goal.event_name
    else:
        goal_dto = await AthleteGoal.get_goal_dto(user_id)
        if goal_dto is None:
            return {
                "available": False,
                "reason": "no_future_race",
                "hint": "Create a race goal via /race or pass race_date explicitly.",
            }
        race_dt, event_name = goal_dto.event_date, goal_dto.event_name

    if race_dt <= today:
        return {"available": False, "reason": "race_date_in_past"}

    if race_distance_class and race_distance_class not in _VALID_CLASSES:
        return {
            "available": False,
            "reason": "invalid_distance_class",
            "hint": f"Use one of {', '.join(_VALID_CLASSES)}.",
        }
    distance_class = race_distance_class or _distance_class_from_name(event_name)

    loads = await _resolve_loads(user_id)
    if loads is None:
        return {"available": False, "reason": "no_wellness_data", "hint": "No CTL/ATL history yet — sync first."}
    ctl_now, atl_now = loads

    peak_daily_load, peak_fallback = await _resolve_peak_daily_load(user_id, today, ctl_now)
    if peak_daily_load <= 0:
        return {"available": False, "reason": "no_training_history", "hint": "No activity load in the last 6 weeks."}

    plan = build_taper_plan(
        race_date=race_dt,
        today=today,
        ctl_now=ctl_now,
        atl_now=atl_now,
        peak_daily_load=peak_daily_load,
        race_distance_class=distance_class,
    )
    if peak_fallback:
        plan["warnings"].append("peak_load_fallback_ctl")

    return {
        "available": True,
        "race_date": race_dt.isoformat(),
        "days_to_race": (race_dt - today).days,
        "event_name": event_name,
        "race_distance_class": distance_class,
        "inputs": {
            "ctl_now": round(ctl_now, 1),
            "atl_now": round(atl_now, 1),
            "peak_daily_load": round(peak_daily_load, 1),
        },
        **plan,
        "taper_start_date": plan["taper_start_date"].isoformat(),
        "daily_targets": [{**t, "date": t["date"].isoformat()} for t in plan["daily_targets"]],
    }
