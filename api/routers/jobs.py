"""Job trigger endpoints — dispatch dramatiq actors for the authenticated user."""

import logging

from fastapi import APIRouter, Depends

from api.deps import require_athlete
from data.db import User, UserDTO
from tasks.actors import actor_fetch_user_activities, actor_user_scheduled_workouts, actor_user_wellness

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_dto(user: User) -> dict:
    """Convert User ORM to dict for dramatiq serialization."""
    return UserDTO.model_validate(user).model_dump()


@router.post("/api/jobs/sync-workouts", status_code=202)
async def job_sync_workouts(user: User = Depends(require_athlete)) -> dict:
    actor_user_scheduled_workouts.send(user=_to_dto(user))
    logger.info("Dispatched sync-workouts for user %d", user.id)
    return {"status": "accepted", "job": "sync-workouts"}


@router.post("/api/jobs/sync-activities", status_code=202)
async def job_sync_activities(user: User = Depends(require_athlete)) -> dict:
    actor_fetch_user_activities.send(user=_to_dto(user))
    logger.info("Dispatched sync-activities for user %d", user.id)
    return {"status": "accepted", "job": "sync-activities"}


@router.post("/api/jobs/sync-wellness", status_code=202)
async def job_sync_wellness(user: User = Depends(require_athlete)) -> dict:
    actor_user_wellness.send(user=_to_dto(user))
    logger.info("Dispatched sync-wellness for user %d", user.id)
    return {"status": "accepted", "job": "sync-wellness"}
