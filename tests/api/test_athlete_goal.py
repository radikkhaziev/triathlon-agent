"""Tests for api/routers/athlete.py — PATCH /api/athlete/goal/{goal_id}.

PATCH semantics:
- empty body → 400
- partial update preserves untouched fields (no silent PUT)
- ctl_target: null explicitly clears to None
- per_sport_targets merges only the set keys
- 404 when goal belongs to another user (not 403 — T1 in MULTI_TENANT_SECURITY)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.dto import AthleteGoalPatchRequest, PerSportTargetsPayload
from api.routers.athlete import patch_athlete_goal


def _user(user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        role="athlete",
        is_active=True,
        athlete_id="i001",
        language="ru",
    )


def _goal(*, goal_id: int = 10, ctl_target: float | None = 55.0, per_sport_targets: dict | None = None):
    return SimpleNamespace(id=goal_id, ctl_target=ctl_target, per_sport_targets=per_sport_targets)


class TestPatchAthleteGoal:
    @pytest.mark.asyncio
    async def test_empty_body_returns_400(self):
        body = AthleteGoalPatchRequest()  # nothing set
        with pytest.raises(HTTPException) as exc:
            await patch_athlete_goal(goal_id=10, body=body, user=_user())
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_ctl_target_set_calls_update_with_value(self):
        body = AthleteGoalPatchRequest(ctl_target=65.0)
        updated = _goal(ctl_target=65.0)
        with patch(
            "api.routers.athlete.AthleteGoal.update_local_fields",
            AsyncMock(return_value=updated),
        ) as update_mock:
            out = await patch_athlete_goal(goal_id=10, body=body, user=_user())

        update_mock.assert_awaited_once()
        kwargs = update_mock.await_args.kwargs
        assert kwargs["user_id"] == 1
        assert kwargs["ctl_target"] == 65.0
        # per_sport_targets NOT in kwargs — field was not set in the body
        assert "per_sport_targets" not in kwargs
        assert out == {"goal_id": 10, "ctl_target": 65.0, "per_sport_targets": None}

    @pytest.mark.asyncio
    async def test_ctl_target_explicit_null_clears_field(self):
        body = AthleteGoalPatchRequest(ctl_target=None)
        updated = _goal(ctl_target=None)
        with patch(
            "api.routers.athlete.AthleteGoal.update_local_fields",
            AsyncMock(return_value=updated),
        ) as update_mock:
            await patch_athlete_goal(goal_id=10, body=body, user=_user())

        # ctl_target explicitly passed as None — distinguishable from "not set"
        assert update_mock.await_args.kwargs["ctl_target"] is None
        assert "per_sport_targets" not in update_mock.await_args.kwargs

    @pytest.mark.asyncio
    async def test_per_sport_targets_only_includes_set_keys(self):
        """Router-level filter: dict passed to ORM contains only keys the caller
        actually set. The ORM helper merges them into the existing JSON blob —
        the true preservation guarantee is tested via test_per_sport_targets_merge_preserves_existing.
        """
        body = AthleteGoalPatchRequest(
            per_sport_targets=PerSportTargetsPayload(swim=15.0, run=25.0),  # no ride
        )
        updated = _goal(per_sport_targets={"swim": 15.0, "ride": 35.0, "run": 25.0})
        with patch(
            "api.routers.athlete.AthleteGoal.update_local_fields",
            AsyncMock(return_value=updated),
        ) as update_mock:
            await patch_athlete_goal(goal_id=10, body=body, user=_user())

        per_sport = update_mock.await_args.kwargs["per_sport_targets"]
        assert per_sport == {"swim": 15.0, "run": 25.0}
        # ride key omitted by router — ORM will merge, not replace
        assert "ride" not in per_sport

    @pytest.mark.asyncio
    async def test_audit_log_emitted_on_success(self):
        """Audit log is emitted via module logger — capture by patching the
        logger directly rather than relying on caplog, which can miss records
        when pytest/sentry logger config disables propagation.
        """
        body = AthleteGoalPatchRequest(ctl_target=72.0)
        updated = _goal(ctl_target=72.0)
        with (
            patch(
                "api.routers.athlete.AthleteGoal.update_local_fields",
                AsyncMock(return_value=updated),
            ),
            patch("api.routers.athlete.logger.info") as mock_info,
        ):
            await patch_athlete_goal(goal_id=10, body=body, user=_user())

        # At least one .info(...) call contains the goal_id + user_id + field list.
        calls_rendered = [
            str(call.args[0]) % call.args[1:] if len(call.args) > 1 else str(call.args[0])
            for call in mock_info.call_args_list
        ]
        assert any("PATCH /api/athlete/goal/10" in r for r in calls_rendered)
        assert any("user_id=1" in r for r in calls_rendered)
        assert any("ctl_target" in r for r in calls_rendered)

    @pytest.mark.asyncio
    async def test_audit_log_emitted_on_404(self):
        body = AthleteGoalPatchRequest(ctl_target=72.0)
        with (
            patch(
                "api.routers.athlete.AthleteGoal.update_local_fields",
                AsyncMock(return_value=None),
            ),
            patch("api.routers.athlete.logger.info") as mock_info,
        ):
            try:
                await patch_athlete_goal(goal_id=99999, body=body, user=_user())
            except HTTPException:
                pass

        calls_rendered = [
            str(call.args[0]) % call.args[1:] if len(call.args) > 1 else str(call.args[0])
            for call in mock_info.call_args_list
        ]
        assert any("denied" in r for r in calls_rendered)

    @pytest.mark.asyncio
    async def test_ctl_target_rejected_out_of_bounds(self):
        """ge=0 / le=200 clamp on DTO."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AthleteGoalPatchRequest(ctl_target=-1)
        with pytest.raises(ValidationError):
            AthleteGoalPatchRequest(ctl_target=201)


class TestOrmPartialMerge:
    """Direct tests on AthleteGoal.update_local_fields — the real preservation
    guarantee lives here, not in the router filter. See review blocker #1.
    """

    @staticmethod
    def _fake_session(goal_obj):
        """Minimal sync-session mock matching what `@dual` invokes."""
        session = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=goal_obj)
        session.execute = MagicMock(return_value=result)
        session.commit = MagicMock()
        return session

    def test_merge_preserves_untouched_sport_keys(self):
        """Patching only {ride: 40} must NOT wipe swim/run."""
        from data.db import AthleteGoal

        goal = SimpleNamespace(
            id=10,
            user_id=1,
            ctl_target=55.0,
            per_sport_targets={"swim": 15.0, "ride": 35.0, "run": 25.0},
        )
        session = self._fake_session(goal)

        AthleteGoal.update_local_fields(
            10,
            user_id=1,
            per_sport_targets={"ride": 40.0},
            session=session,
        )

        assert goal.per_sport_targets == {"swim": 15.0, "ride": 40.0, "run": 25.0}
        assert goal.ctl_target == 55.0  # untouched

    def test_explicit_none_clears_per_sport_blob(self):
        from data.db import AthleteGoal

        goal = SimpleNamespace(
            id=10,
            user_id=1,
            ctl_target=55.0,
            per_sport_targets={"swim": 15.0, "ride": 35.0, "run": 25.0},
        )
        session = self._fake_session(goal)

        AthleteGoal.update_local_fields(
            10,
            user_id=1,
            per_sport_targets=None,  # explicit clear
            session=session,
        )

        assert goal.per_sport_targets is None

    def test_merge_when_existing_is_none(self):
        """Patching {swim: 15} when blob is NULL starts a fresh dict."""
        from data.db import AthleteGoal

        goal = SimpleNamespace(id=10, user_id=1, ctl_target=None, per_sport_targets=None)
        session = self._fake_session(goal)

        AthleteGoal.update_local_fields(
            10,
            user_id=1,
            per_sport_targets={"swim": 15.0},
            session=session,
        )

        assert goal.per_sport_targets == {"swim": 15.0}

    def test_foreign_user_returns_none_without_write(self):
        from data.db import AthleteGoal

        # scalar_one_or_none returns None when WHERE clause doesn't match.
        session = self._fake_session(None)

        out = AthleteGoal.update_local_fields(
            10,
            user_id=999,
            ctl_target=80.0,
            session=session,
        )
        assert out is None
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_per_sport_targets_null_clears_whole_blob(self):
        body = AthleteGoalPatchRequest(per_sport_targets=None)
        updated = _goal(per_sport_targets=None)
        with patch(
            "api.routers.athlete.AthleteGoal.update_local_fields",
            AsyncMock(return_value=updated),
        ) as update_mock:
            await patch_athlete_goal(goal_id=10, body=body, user=_user())

        assert update_mock.await_args.kwargs["per_sport_targets"] is None

    @pytest.mark.asyncio
    async def test_foreign_goal_returns_404(self):
        body = AthleteGoalPatchRequest(ctl_target=70.0)
        # update_local_fields returns None when ownership check fails
        with patch(
            "api.routers.athlete.AthleteGoal.update_local_fields",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc:
                await patch_athlete_goal(goal_id=99999, body=body, user=_user())

        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_both_fields_set(self):
        body = AthleteGoalPatchRequest(
            ctl_target=80.0,
            per_sport_targets=PerSportTargetsPayload(swim=18.0, ride=40.0, run=30.0),
        )
        updated = _goal(
            ctl_target=80.0,
            per_sport_targets={"swim": 18.0, "ride": 40.0, "run": 30.0},
        )
        with patch(
            "api.routers.athlete.AthleteGoal.update_local_fields",
            AsyncMock(return_value=updated),
        ) as update_mock:
            await patch_athlete_goal(goal_id=10, body=body, user=_user())

        kwargs = update_mock.await_args.kwargs
        assert kwargs["ctl_target"] == 80.0
        assert kwargs["per_sport_targets"] == {"swim": 18.0, "ride": 40.0, "run": 30.0}
