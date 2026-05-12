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
    race_date, inflates CI by `min(sqrt(days_to_race / 30), 1.8)`.

    `race_date` (ISO YYYY-MM-DD) is auto-filled from the user's RACE_A goal when empty.
    Distances and target HR are passed through to the model; missing distance prunes
    the discipline with a warning. Cold-start models report `not_available[]`.

    Returns:
        Top-level: `{mode, race_date, days_to_race, splits, not_available,
        below_acceptance, warnings, generated_at, ci_level, inflation,
        inflation_raw, inflation_capped, bias_correction_applied, bias_fit_method}`.
        In `race_day` mode with projection available also: `{projected_ctl,
        projected_atl}`.

        CI metadata (always present, both modes — issue #361):
        * `ci_level` — 0.90 (90% PI from 5/95 percentile residuals).
        * `inflation` — multiplier applied to CI bounds.
        * `inflation_raw` — pre-cap sqrt(days/30) value.
        * `inflation_capped: bool` — True iff cap engaged (~97d+).

        Bias correction (Phase 2.0β2 / issue #363 β2):
        * `bias_correction_applied: float` — Run-only sec/km shift subtracted
          from `pred` (0.0 if Run not requested OR legacy bundle).
        * `bias_fit_method` — `per_athlete_linear` / `pool_fallback` /
          `out_of_scope` / null.

        Per-discipline `splits[<run|ride|swim>]`: `{pred, ci_low, ci_high,
        units, total_sec?, total_sec_ci_low?, total_sec_ci_high?,
        total_sec_unavailable?, total_sec_reason?}`.
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
