"""Athlete-scoped mutations: local-only overlay edits on `athlete_goals`.

Only fields that don't exist in Intervals.icu live here (``ctl_target``,
``per_sport_targets``). Fields that sync from Intervals (name/date/category)
are NOT writable — those require a chat-flow push through
``mcp_server/tools/races.py:suggest_race``, which mirrors the change to
Intervals.icu in addition to the local DB.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_data_user_id, require_athlete
from api.dto import AthleteGoalPatchRequest
from data.db import AthleteGoal, User

logger = logging.getLogger(__name__)
router = APIRouter()


@router.patch("/api/athlete/goal/{goal_id}")
async def patch_athlete_goal(
    goal_id: int,
    body: AthleteGoalPatchRequest,
    user: User = Depends(require_athlete),
) -> dict:
    """Update local-only overlay fields (``ctl_target``, ``per_sport_targets``).

    Fields omitted from the request body are left untouched — we use pydantic's
    ``model_fields_set`` to distinguish "not provided" from "explicit null" so
    PATCH never silently clears adjacent columns.

    Returns 404 (not 403) when ``goal_id`` belongs to a different user so we
    don't leak existence of other users' goals — see
    ``docs/MULTI_TENANT_SECURITY.md`` T1.
    """
    fields_set = body.model_fields_set
    if not fields_set:
        raise HTTPException(status_code=400, detail="Request body must contain at least one field.")

    kwargs: dict = {}
    if "ctl_target" in fields_set:
        kwargs["ctl_target"] = body.ctl_target
    if "per_sport_targets" in fields_set:
        payload = body.per_sport_targets
        if payload is None:
            kwargs["per_sport_targets"] = None
        else:
            # Only include keys the caller actually set, to avoid wiping
            # disciplines they didn't touch when writing the JSON blob.
            set_keys = payload.model_fields_set
            kwargs["per_sport_targets"] = {k: getattr(payload, k) for k in set_keys}

    data_uid = get_data_user_id(user)
    goal = await AthleteGoal.update_local_fields(goal_id, user_id=data_uid, **kwargs)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found.")

    return {
        "goal_id": goal.id,
        "ctl_target": goal.ctl_target,
        "per_sport_targets": goal.per_sport_targets,
    }
