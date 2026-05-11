"""MCP tool — `get_race_projection`.

Thin wrapper over :func:`data.ml.race_predict.predict_splits_with_ci`. Handles
input validation (§9.3 error cases), auto-fills ``race_date`` from RACE_A goal,
and returns the §9.2 envelope.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Literal

from data.db import AthleteGoal
from data.ml.race_predict import predict_splits_with_ci
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id
from mcp_server.sentry import sentry_tool

logger = logging.getLogger(__name__)


@mcp.tool()
@sentry_tool
async def get_race_projection(
    mode: Literal["today", "race_day"] = "today",
    race_date: str = "",
    race_distance_swim_m: int | None = None,
    race_distance_ride_m: int | None = None,
    race_distance_run_m: int | None = None,
    target_hr_ride: int | None = None,
    target_hr_run: int | None = None,
) -> dict:
    """Predict race splits for current state or race-day projected state.

    `mode="today"`: per-discipline predictions from current wellness + Intervals state.
    `mode="race_day"`: overrides CTL/ATL + per-sport eFTP with `fitness_projection` at
    race_date, inflates CI by sqrt(days_to_race / 30).

    `race_date` (ISO YYYY-MM-DD) is auto-filled from the user's RACE_A goal when empty.
    Distances and target HR are passed through to the model; missing distance prunes
    the discipline with a warning. Cold-start models report `not_available[]`.

    Returns: `{mode, race_date, days_to_race, splits, not_available, warnings, generated_at}`
    plus `{projected_ctl, projected_atl, inflation}` in `race_day` mode.
    """
    user_id = get_current_user_id()

    # --- Validate race_date ---
    if not race_date:
        goal = await AthleteGoal.get_by_category(user_id, "RACE_A")
        if not goal or not goal.event_date:
            return {
                "available": False,
                "reason": "no_race_date",
                "hint": "Pass race_date or create a RACE_A goal via /race first.",
            }
        _race_iso = goal.event_date.isoformat() if hasattr(goal.event_date, "isoformat") else str(goal.event_date)
    else:
        _race_iso = race_date

    try:
        target_dt = date.fromisoformat(_race_iso)
    except ValueError:
        return {"available": False, "reason": "invalid_race_date", "race_date": _race_iso}

    if target_dt < date.today():
        return {"available": False, "reason": "race_date_in_past", "race_date": _race_iso}

    if not any([race_distance_swim_m, race_distance_ride_m, race_distance_run_m]):
        return {
            "available": False,
            "reason": "no_distance",
            "hint": "Provide at least one of race_distance_{swim,ride,run}_m.",
        }

    envelope = await predict_splits_with_ci(
        user_id=user_id,
        mode=mode,
        race_date=_race_iso,
        race_distance_run_m=race_distance_run_m,
        race_distance_ride_m=race_distance_ride_m,
        race_distance_swim_m=race_distance_swim_m,
        target_hr_run=target_hr_run,
        target_hr_ride=target_hr_ride,
    )

    if not envelope.get("splits"):
        envelope["available"] = False
        # Pick the most informative reason from the buckets we filled. When
        # the call had mixed outcomes (one discipline trained but quality-gated,
        # another simply absent) we still emit the gate reason — Claude can
        # explain «calibrating» более точно чем «not trained».
        if envelope.get("below_acceptance"):
            envelope["reason"] = "model_below_acceptance"
        elif envelope.get("not_available"):
            envelope["reason"] = "model_not_trained"
        else:
            envelope["reason"] = "no_splits"
    else:
        envelope["available"] = True
    return envelope
