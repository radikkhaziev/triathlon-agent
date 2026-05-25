"""Dramatiq actor — FITNESS_UPDATED handler.

The FITNESS_UPDATED webhook fires after Intervals.icu recomputes the fitness
curve following an activity. Payload contains a mix of dates:
 - past + today: the recomputed actual CTL/ATL after Intervals finalizes
   yesterday at midnight (strips planned-bake from yesterday's ctl_load,
   see `recompute_today_loads` in data/metrics.py for the matching read-
   time fix). Without writing these to wellness, our DB stays stuck on
   yesterday's morning-of-yesterday value forever.
 - future: the projected curve under zero-load assumption (out to the
   next race). Stored in `fitness_projection` for race-plan / dashboard
   forecast rendering. NEVER written to wellness — wellness is "what was",
   not "what might be".

This actor:
 1. Saves the full batch to ``fitness_projection`` (used by race-plan etc.).
 2. Updates wellness for every record with `id <= today` — past finalisations
    + today's anchor — via `Wellness.update_loads` (no-op if the wellness
    row doesn't exist; regular sync materializes new rows). Skips the
    Intervals.icu API round-trip entirely — the payload already carries the
    recalculated ctl/atl/rampRate. Downstream recovery-score recompute is
    left to the next regular wellness sync so this actor doesn't race rolling
    HRV/RHR baselines (we only write load columns, not hrv/resting_hr).
"""

import logging
from datetime import date

import dramatiq
from pydantic import validate_call

from data.db import FitnessProjection, UserDTO, Wellness
from tasks.dto import local_today

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default")
@validate_call
def actor_save_fitness_projection(user: UserDTO, records: list[dict]) -> None:
    if not records:
        return

    valid_records = [r for r in records if r.get("id")]
    if not valid_records:
        return
    sorted_records = sorted(valid_records, key=lambda r: r["id"])
    FitnessProjection.save_bulk(user_id=user.id, records=sorted_records)

    today_iso = local_today().isoformat()
    wellness_updated_count = 0

    for r in sorted_records:
        rec_iso = r["id"]
        # ISO 8601 is lex-sortable, so string `>` matches chronological order.
        if rec_iso > today_iso:
            continue  # future projection — don't pollute wellness with "what might be"
        try:
            rec_date = date.fromisoformat(rec_iso)
        except ValueError:
            # Malformed `id` from Intervals (never seen in production, but
            # crashing the actor on a single bad row would lose the good ones
            # via Dramatiq retry → DLQ).
            logger.warning("Skipping fitness record with bad id=%r user_id=%d", rec_iso, user.id)
            continue
        if Wellness.update_loads(
            user_id=user.id,
            dt=rec_date,
            ctl=r.get("ctl"),
            atl=r.get("atl"),
            ramp_rate=r.get("rampRate"),
            ctl_load=r.get("ctlLoad"),
            atl_load=r.get("atlLoad"),
        ):
            wellness_updated_count += 1

    logger.info(
        "Saved %d fitness projection records, wellness rows updated=%d user_id=%d",
        len(sorted_records),
        wellness_updated_count,
        user.id,
    )
