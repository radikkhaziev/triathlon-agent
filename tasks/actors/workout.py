import logging

import dramatiq
from pydantic import validate_call

from bot.formatter import build_workout_pushed_message
from data.db import AiWorkout, UserDTO
from data.intervals.client import IntervalsSyncClient
from data.intervals.dto import EventExDTO, PlannedWorkoutDTO, ScheduledWorkoutDTO
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

    with IntervalsSyncClient.for_user(user) as client:
        _event: EventExDTO = workout.to_intervals_event()
        data_event: ScheduledWorkoutDTO = client.create_event(_event)

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
