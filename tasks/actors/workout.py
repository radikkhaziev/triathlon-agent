import logging

import dramatiq
from pydantic import validate_call

from bot.formatter import build_workout_pushed_message
from data.db import AiWorkout, AthleteSettings, UserDTO
from data.intervals.client import IntervalsAccessError, IntervalsSyncClient
from data.intervals.dto import EventExDTO, PlannedWorkoutDTO, ScheduledWorkoutDTO, render_native_description
from data.workout_adapter import humango_to_intervals_steps, is_humango_event
from tasks.dto import DateDTO

from ..tools import TelegramTool

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_send_workout_notification(
    user: UserDTO,
    workout: PlannedWorkoutDTO,
    intervals_id: int,
    dt: DateDTO,
):
    tg = TelegramTool(user=user)
    summary = build_workout_pushed_message(
        sport=workout.sport,
        name=workout.name,
        duration_minutes=workout.duration_minutes,
        target_tss=workout.target_tss,
        suffix=workout.suffix,
        intervals_id=intervals_id,
        athlete_id=user.athlete_id,
        target_date=dt,
    )
    tg.send_message(text=summary)


@dramatiq.actor(queue_name="default")
@validate_call
def actor_push_workout(
    user: UserDTO,
    workout: PlannedWorkoutDTO,
    dt: DateDTO,
):
    existing = AiWorkout.get_by_external_id(
        user_id=user.id,
        external_id=workout.external_id,
    )
    if existing and existing.status == "active":
        logger.info(f"Workout {workout.external_id} already exists for user {user.id}, skipping push.")
        return

    try:
        with IntervalsSyncClient.for_user(user) as client:
            _event: EventExDTO = workout.to_intervals_event()
            data_event: ScheduledWorkoutDTO = client.create_event(_event)
    except IntervalsAccessError as e:
        logger.info("Skipping workout push for user %d: %s", user.id, e)
        return

    intervals_id = data_event.id

    AiWorkout.save(
        user_id=user.id,
        date_str=dt.isoformat(),
        sport=workout.sport,
        slot=workout.slot,
        external_id=workout.external_id,
        intervals_id=intervals_id,
        name=workout.name,
        description="; ".join(s.text for s in workout.steps if s.text),
        duration_minutes=workout.duration_minutes,
        target_tss=workout.target_tss,
        rationale=workout.rationale,
    )

    _actor_send_workout_notification.send(user, workout, intervals_id, dt)


@dramatiq.actor(queue_name="default")
@validate_call
def actor_enrich_humango_workout(user: UserDTO, intervals_event_id: int):
    """Enrich a HumanGo-sourced Intervals.icu event with structured ``workout_doc.steps``.

    See ``docs/HUMANGO_ENRICHMENT_SPEC.md``. HumanGo's shared-calendar push
    arrives as plain-text only — UI shows no steps, compliance can't compute.
    This actor parses the description, converts absolute units to %X corridors
    against the athlete's thresholds, and writes both ``workout_doc.steps``
    (for our compliance + Garmin/Wahoo FIT export) plus a native-format
    top-level ``description`` (so Intervals UI renders the step chart).

    Idempotent: re-fetches the event fresh, re-checks ``is_humango_event``
    before pushing. If the athlete (or another integration) has populated
    ``workout_doc.steps`` between dispatch and run, we skip.

    HumanGo's original description text is moved into ``workout_doc.description``
    where Garmin Connect renders it as the workout note — athlete still sees
    HumanGo's goal-of-session + View-link on their phone.
    """
    try:
        with IntervalsSyncClient.for_user(user) as client:
            event = client.get_event(intervals_event_id)
            if event is None:
                logger.info("HumanGo enrichment: event %s not found for user %d", intervals_event_id, user.id)
                return

            # Defensive re-check — event state may have changed between
            # dispatch (scheduler iteration) and this actor invocation.
            if not is_humango_event(event.description, event.workout_doc):
                logger.info(
                    "HumanGo enrichment: event %s no longer eligible (idempotency / detection)",
                    intervals_event_id,
                )
                return

            if not event.type:
                logger.info("HumanGo enrichment: event %s has no sport type — skipping", intervals_event_id)
                return

            thresholds = AthleteSettings.get_thresholds(user.id)
            steps = humango_to_intervals_steps(event.description, event.type, thresholds)
            if not steps:
                # Converter logged the reason already (missing threshold /
                # unsupported sport / parser found nothing).
                return

            native_desc = render_native_description(steps, event.type)

            # Mirror `PlannedWorkoutDTO.to_intervals_event`: Run/Swim with pace
            # or distance steps need an explicit `target="PACE"` so Garmin's
            # FIT export renders pace cells instead of falling back to HR
            # (verified live 2026-05-07, ramp-test pre-flight).
            target = "PACE" if event.type in ("Swim", "Run") and _steps_have_pace_or_distance(steps) else None

            payload = EventExDTO(
                workout_doc={
                    "steps": [s.model_dump(exclude_none=True) for s in steps],
                    "description": event.description,  # preserve HumanGo text for Garmin Connect note
                },
                description=native_desc,
                target=target,
            )
            client.update_event(intervals_event_id, payload)
            logger.info(
                "HumanGo enrichment: pushed %d step(s) to event %s (user %d, sport %s)",
                len(steps),
                intervals_event_id,
                user.id,
                event.type,
            )
    except IntervalsAccessError as e:
        logger.info("HumanGo enrichment skipped for user %d: %s", user.id, e)
        return


def _steps_have_pace_or_distance(steps: list) -> bool:
    """Recursively check whether any step (or sub-step) targets pace or uses distance.

    Used to decide whether to set ``EventExDTO.target = "PACE"`` for Swim/Run
    workouts — mirrors the equivalent logic on ``PlannedWorkoutDTO.has_pace_steps``
    / ``has_distance_steps`` but accepts a free-standing list since we don't
    construct a PlannedWorkoutDTO for HumanGo enrichment (different DTO contract).
    """
    for s in steps:
        if s.pace is not None and not s.steps:
            return True
        if s.distance is not None and s.distance > 0:
            return True
        if s.steps and _steps_have_pace_or_distance(s.steps):
            return True
    return False
