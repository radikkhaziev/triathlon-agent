"""MCP tool — `get_taper_plan`.

Thin wrapper over :func:`data.taper_service.get_taper_plan_for_user`
(TAPER_PLANNER_SPEC Phase 2). All resolution and refusal-gate logic lives in
the service, shared with `GET /api/taper-plan` (Phase 4 LoadDetail overlay).
"""

from __future__ import annotations

from data.taper_service import get_taper_plan_for_user
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id
from mcp_server.sentry import sentry_tool


@mcp.tool()
@sentry_tool
async def get_taper_plan(goal_id: int | None = None, race_date: str = "", race_distance_class: str = "") -> dict:
    """Deterministic pre-race taper plan: daily TSS targets + race-day form projection.

    Resolves the target race from `goal_id`, else the primary upcoming goal
    (RACE_A first); `race_date` (ISO YYYY-MM-DD) overrides both for what-if
    questions. `race_distance_class` is `long` (IM / 70.3 / marathon) /
    `standard` (default) / `short` (sprint, ≤5k) — inferred from the goal's
    event name when omitted.

    Returns `{available: False, reason, hint}` when there is no future race or
    no wellness data; otherwise the plan (daily TSS targets, race-day form
    projection, rules, warnings). `confidence="early"` (race >21d out): start
    date is an estimate, daily targets and projection are withheld — re-call
    closer to the race. Read-only: schedule the actual sessions via
    `suggest_workout` against the daily TSS budget.
    """
    return await get_taper_plan_for_user(
        get_current_user_id(),
        goal_id=goal_id,
        race_date=race_date,
        race_distance_class=race_distance_class,
    )
