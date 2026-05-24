"""Dramatiq actor — FITNESS_UPDATED handler.

The FITNESS_UPDATED webhook fires after Intervals.icu recomputes the fitness
curve following an activity. `records[0]` is today (the recomputed anchor);
`records[1..N]` are the projected curve into the future (out to the next
race), under zero-load assumption. Past dates do NOT appear in this payload.

This actor:
 1. Saves the full batch to ``fitness_projection`` (today + the projected
    future curve used by race-plan / dashboard rendering).
 2. Updates today's wellness row in place with the new CTL/ATL/rampRate —
    no Intervals.icu round-trip, the payload already has the numbers.
    Downstream recovery-score recompute is left to the next regular
    wellness sync so this actor doesn't race rolling HRV/RHR baselines.
"""

import logging

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
    today_record = next((r for r in sorted_records if r["id"] == today_iso), None)
    wellness_updated = False
    if today_record is not None:
        wellness_updated = Wellness.update_loads(
            user_id=user.id,
            dt=today_iso,
            ctl=today_record.get("ctl"),
            atl=today_record.get("atl"),
            ramp_rate=today_record.get("rampRate"),
            ctl_load=today_record.get("ctlLoad"),
            atl_load=today_record.get("atlLoad"),
        )

    logger.info(
        "Saved %d fitness projection records, wellness today updated=%s user_id=%d",
        len(sorted_records),
        wellness_updated,
        user.id,
    )
