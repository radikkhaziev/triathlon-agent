"""Athlete-scoped reads + mutations on `athlete_goals`.

PATCH targets local-only overlay fields (``ctl_target``, ``per_sport_targets``,
``sport_type``); fields that sync from Intervals (name/date/category) are NOT
writable — those require a chat-flow push through
``mcp_server/tools/races.py:suggest_race``, which mirrors the change to
Intervals.icu in addition to the local DB.

GET returns the list of active future goals for the Settings page list view
(#323 Strand C); ``auth_me.goal`` keeps a single-goal field for legacy callers
that only need a primary anchor.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_data_user_id, require_athlete, require_viewer
from api.dto import AthleteGoalPatchRequest, AthleteProfilePatchRequest
from data.db import ActivityHrv, AthleteGoal, User
from data.db.dto import MeasuredThresholdsDTO
from tasks.dto import local_today

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/athlete/goals")
async def list_athlete_goals(
    user: User = Depends(require_viewer),
) -> dict:
    """List active future goals for the Settings list view (#323 Strand C).

    Past races are filtered out — they're not editable. Sort: ``event_date
    ASC`` so the nearest race is first.

    Read-only endpoint — uses ``require_viewer`` so demo sessions can see the
    owner's goals (read-only tour). Write path (PATCH below) stays on
    ``require_athlete``; demo gets 403 on edits.
    """
    data_uid = get_data_user_id(user)
    goals = await AthleteGoal.get_goals_for_settings(data_uid, local_today())
    return {
        "goals": [
            {
                "id": g.id,
                "category": g.category,
                "event_name": g.event_name,
                "event_date": str(g.event_date),
                "sport_type": g.sport_type,
                "ctl_target": g.ctl_target,
                "per_sport_targets": g.per_sport_targets,
            }
            for g in goals
        ],
    }


@router.get("/api/athlete/measured-thresholds")
async def get_measured_thresholds(
    user: User = Depends(require_viewer),
) -> MeasuredThresholdsDTO:
    """Our DFA-α1 ramp-test thresholds (HRVT2 per sport).

    Distinct from the Intervals.icu-synced values in ``/auth/me``'s ``profile``
    block — this is what *we* measured (latest ramp-test HRVT2 with confidence +
    measurement date). Powers the Settings "measured vs auto-synced" threshold
    card. VO2max is not here: the card keeps the Intervals VO2max as sync-only,
    and our composite VO2max lives on the Endurance Score surface.

    Read-only — ``require_viewer`` so demo/viewer sessions see the owner's
    measured thresholds on the read-only tour.
    """
    data_uid = get_data_user_id(user)
    measured = await ActivityHrv.get_latest_measured(data_uid)
    return MeasuredThresholdsDTO(thresholds=measured)


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
    ``docs/MULTI_TENANT_SECURITY_SPEC.md`` T1.
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
            # other sports they didn't touch when writing the JSON blob.
            set_keys = payload.model_fields_set
            kwargs["per_sport_targets"] = {k: getattr(payload, k) for k in set_keys}
    if "sport_type" in fields_set:
        # Schema is NOT NULL — explicit ``null`` is not a valid edit (drop the
        # field instead to skip it). Pydantic Literal already restricts the
        # value-set to RACE_SPORT_TYPES.
        if body.sport_type is None:
            raise HTTPException(
                status_code=400, detail="sport_type cannot be null. Omit the field to leave it unchanged."
            )
        kwargs["sport_type"] = body.sport_type

    data_uid = get_data_user_id(user)
    goal = await AthleteGoal.update_local_fields(goal_id, user_id=data_uid, **kwargs)
    if goal is None:
        # Log the miss so we can spot probes / foreign-id leaks in prod.
        logger.info("PATCH /api/athlete/goal/%d denied for user_id=%d (not found or not owned)", goal_id, data_uid)
        raise HTTPException(status_code=404, detail="Goal not found.")

    logger.info(
        "PATCH /api/athlete/goal/%d by user_id=%d: fields=%s",
        goal_id,
        data_uid,
        sorted(fields_set),
    )
    return {
        "goal_id": goal.id,
        "ctl_target": goal.ctl_target,
        "per_sport_targets": goal.per_sport_targets,
        "sport_type": goal.sport_type,
    }


@router.patch("/api/athlete/profile")
async def patch_athlete_profile(
    body: AthleteProfilePatchRequest,
    user: User = Depends(require_athlete),
) -> dict:
    """Update overlay fields on the ``users`` row (age for now).

    Uses ``model_fields_set`` so a body omitting a field leaves it
    untouched. Multi-tenant safe — ``user_id`` always derived from the
    authenticated principal, never trusted from the request body.
    """
    fields_set = body.model_fields_set
    if not fields_set:
        raise HTTPException(status_code=400, detail="Request body must contain at least one field.")

    data_uid = get_data_user_id(user)
    if "age" in fields_set:
        await User.update_age(data_uid, body.age)

    logger.info(
        "PATCH /api/athlete/profile by user_id=%d: fields=%s",
        data_uid,
        sorted(fields_set),
    )
    return {"age": body.age if "age" in fields_set else user.age}
