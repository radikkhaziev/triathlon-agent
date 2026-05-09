"""Compute & persist post-race compliance metrics (PR3, spec §14).

Define-not-ship: this service exists so post-race data starts collecting in
the right shape from day one. The auto-trigger (on the ``ACTIVITY_UPLOADED``
webhook when an activity matches a goal's race) is Phase 3 work.

PR3 implementation is a **whole-activity-averages** approximation (per spec
§14: "Для PR3 — fallback на whole-activity HR vs whole-leg ceiling"). Per
per-second streams aren't stored in the DB, and per-leg segmentation needs
``legs[].target_split_time_sec`` (PR4 schema extension) or ``lap_indexes``
parsing — neither in scope for PR3.

Concretely: the function reads ``Activity.average_hr`` /
``ActivityDetail.normalized_power`` / ``ActivityDetail.pace`` and compares
to each leg's ceiling/corridor. Result is binary (100% or 0%) per leg, with
``notes`` flagging the approximation. Fueling compliance is also computed
once over the whole activity (not per-leg) — same value cloned to every leg
row so future per-leg refinement (Phase 3) doesn't change the column shape.

Multi-tenant: all reads scope by ``user_id``; cross-tenant ``race_plan_id``
returns ``[]`` rather than raising.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select

from data.db import Activity, ActivityDetail, Race, RacePlan, RacePlanCompliance, get_session
from data.race_plan_service import _parse_corridor_value

logger = logging.getLogger(__name__)

_PR3_NOTES = (
    "PR3 fallback: whole-activity averages (no per-leg segmentation yet — " "Phase 3 work, see RACE_PLAN_SPEC §14)."
)


def _compute_hr_compliance(leg: dict[str, Any], avg_hr: float | None) -> Decimal | None:
    """Whole-activity proxy for the time-in-corridor metric.

    Returns 100.0 when avg_hr ≤ ceiling, 0.0 when above, None if either side
    missing. Per-second time-in-zone is the Phase 3 metric (we don't store
    streams in the DB; reading FIT files would be a separate workstream)."""
    ceiling = leg.get("hr_ceiling_bpm")
    if not isinstance(ceiling, (int, float)) or avg_hr is None:
        return None
    return Decimal("100.00") if avg_hr <= ceiling else Decimal("0.00")


def _compute_band_compliance(
    leg: dict[str, Any],
    activity_detail: ActivityDetail | None,
) -> Decimal | None:
    """Whole-activity proxy: is the activity's average pace/power inside the
    [low, cap] corridor? Returns 100.0/0.0/None.

    Uses ``_parse_corridor_value`` from the plan service — same effort-space
    normalization (pace negated so larger=harder) the validator uses, so the
    comparison stays consistent with how the corridor was originally checked."""
    pacing = leg.get("pacing") or {}
    low_parsed = _parse_corridor_value(pacing.get("low"))
    cap_parsed = _parse_corridor_value(pacing.get("cap"))
    if low_parsed is None or cap_parsed is None or low_parsed[1] != cap_parsed[1]:
        # Corridor unparseable or mixed-units — skip rather than guess.
        return None
    low_val, unit = low_parsed
    cap_val, _ = cap_parsed
    if activity_detail is None:
        return None

    # Map unit-kind → activity field. ``pace`` is m/s in Intervals; the
    # corridor uses sec/km/100m parsed by _parse_corridor_value (effort-space:
    # negated seconds). Convert m/s → sec/km, then negate to match.
    if unit == "power":
        actual_w = activity_detail.normalized_power or activity_detail.avg_power
        if actual_w is None:
            return None
        actual = float(actual_w)
    elif unit == "pace_km":
        ms = activity_detail.pace
        if ms is None or ms <= 0:
            return None
        actual = -1.0 * (1000.0 / ms)  # negate sec/km to match corridor effort-space
    elif unit == "pace_100m":
        ms = activity_detail.pace
        if ms is None or ms <= 0:
            return None
        actual = -1.0 * (100.0 / ms)
    elif unit == "pace_mi":
        ms = activity_detail.pace
        if ms is None or ms <= 0:
            return None
        actual = -1.0 * (1609.344 / ms)
    else:
        return None

    return Decimal("100.00") if low_val <= actual <= cap_val else Decimal("0.00")


def _compute_fueling_compliance(
    plan_payload: dict[str, Any],
    race_carbs_consumed_g: int | None,
    activity_moving_time_sec: int | None,
) -> Decimal | None:
    """``min(actual_g_hr, plan_g_hr) / plan_g_hr * 100`` — capped at 100% so an
    over-fueling athlete (200g/hr vs 75g/hr plan) doesn't show as 267% (the
    metric is "did you HIT the plan", not "by how much did you exceed").

    Returns None when any input is missing — fueling without
    ``Race.carbs_consumed_g`` (manual entry) or activity duration can't be
    estimated, and we'd rather record NULL than fabricate."""
    plan_carbs = ((plan_payload.get("plan") or {}).get("fueling") or {}).get("carbs_g_per_hour")
    if (
        race_carbs_consumed_g is None
        or activity_moving_time_sec is None
        or activity_moving_time_sec <= 0
        or not isinstance(plan_carbs, (int, float))
        or plan_carbs <= 0
    ):
        return None
    # Decimal end-to-end: avoids float artifacts (e.g. 200/4*100/75 round-trip)
    # and gives deterministic ROUND_HALF_UP for the NUMERIC(5,2) column —
    # tests assert exact decimals like 66.67, so the quantize policy has to
    # be explicit rather than relying on str(round(...)) banker's-rounding mix.
    plan_carbs_d = Decimal(str(plan_carbs))
    carbs_consumed_d = Decimal(race_carbs_consumed_g)
    moving_hours = Decimal(activity_moving_time_sec) / Decimal("3600")
    actual_g_hr = carbs_consumed_d / moving_hours
    pct = min(actual_g_hr, plan_carbs_d) / plan_carbs_d * Decimal("100")
    return pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def compute_compliance(
    *,
    race_plan_id: int,
    race_id: int | None,
    user_id: int,
) -> list[RacePlanCompliance]:
    """Compute and persist per-leg compliance for a race plan.

    Caller responsibility:
    - Pre-clear existing compliance rows for the same plan if recomputing
      (no UNIQUE constraint on the table — keeps multiple compute runs
      addressable; auto-actor in Phase 3 will own the upsert policy).

    Returns the persisted rows in leg order. Empty list if the plan can't
    be loaded or has no legs.
    """
    # Resolve plan — scoped to user_id so a leaked plan_id can't surface
    # another tenant's data.
    async with get_session() as session:
        plan_row = (
            await session.execute(select(RacePlan).where(RacePlan.id == race_plan_id, RacePlan.user_id == user_id))
        ).scalar_one_or_none()
    if plan_row is None:
        logger.info("compute_compliance: plan_id=%d not found for user=%d", race_plan_id, user_id)
        return []

    payload = plan_row.payload or {}
    legs = (payload.get("plan") or {}).get("legs") or []
    if not legs:
        return []

    # Resolve race + activity (optional — race_id can be None for ad-hoc plans).
    race_row: Race | None = None
    activity: Activity | None = None
    activity_detail: ActivityDetail | None = None
    if race_id is not None:
        async with get_session() as session:
            race_row = (
                await session.execute(select(Race).where(Race.id == race_id, Race.user_id == user_id))
            ).scalar_one_or_none()
            if race_row is not None and race_row.activity_id:
                activity = (
                    await session.execute(
                        select(Activity).where(Activity.id == race_row.activity_id, Activity.user_id == user_id)
                    )
                ).scalar_one_or_none()
                if activity is not None:
                    activity_detail = (
                        await session.execute(select(ActivityDetail).where(ActivityDetail.activity_id == activity.id))
                    ).scalar_one_or_none()

    # Fueling compliance is computed ONCE on the whole activity. Per-leg
    # carbs intake isn't recordable today (athlete logs total post-race),
    # so the same value lands on every leg row; Phase 3 per-leg fueling
    # would replace this without schema change.
    fueling_pct = _compute_fueling_compliance(
        payload,
        race_row.carbs_consumed_g if race_row else None,
        activity.moving_time if activity else None,
    )

    # Build per-leg dicts in memory first, then write the whole batch in ONE
    # session/commit via save_many_for_legs. The previous loop called
    # save_for_leg per leg → N commits per plan; for a triathlon (3 legs) that
    # was 3 round-trips where 1 suffices, and for future ultra plans it scales
    # linearly. Atomicity also matters: a partial write here would leave the
    # plan with some leg-rows missing, which the dashboard couldn't tell apart
    # from "compute hasn't run yet".
    avg_hr = activity.average_hr if activity else None
    leg_duration_sec = activity.moving_time if activity else None  # whole-activity proxy
    rows_data = [
        {
            "user_id": user_id,
            "race_plan_id": plan_row.id,
            "race_id": race_id,
            "leg_name": leg.get("leg") or "?",
            "hr_compliance_pct": _compute_hr_compliance(leg, avg_hr),
            "band_compliance_pct": _compute_band_compliance(leg, activity_detail),
            "fueling_compliance_pct": fueling_pct,
            "leg_duration_sec": leg_duration_sec,
            "notes": _PR3_NOTES,
        }
        for leg in legs
    ]
    rows = await RacePlanCompliance.save_many_for_legs(rows_data)

    logger.info(
        "compute_compliance: persisted %d compliance rows for plan_id=%d (user=%d, race_id=%s)",
        len(rows),
        plan_row.id,
        user_id,
        race_id,
    )
    return rows
