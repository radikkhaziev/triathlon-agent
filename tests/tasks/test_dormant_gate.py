"""Tests for the shared `is_user_dormant` gate and the three webhook-driven
actors that now use it.

`actor_user_wellness` is the original site (covered separately in
`test_actors.py::TestActorUserWellnessMocked`). This file pins down the
fan-out to `actor_user_scheduled_workouts`, `actor_sync_athlete_goals`,
`actor_fetch_user_activities` — all of which were burning Intervals.icu API
quota on dormant accounts before the chokepoint refactor (M1 from the
01324e8c review).

The `force_inactive=True` admin escape hatch is exercised on each — CLI
backfills must keep working against stale-deactivated users.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from data.db import UserDTO


def _user() -> UserDTO:
    return UserDTO(id=1, chat_id="111", username="tester", athlete_id="i001")


class TestIsUserDormantHelper:
    def test_missing_user_returns_true(self):
        """``User.get`` returns None → caller must skip. Tags the log with
        the supplied actor name for grep'ability in prod."""
        from tasks.actors.common import is_user_dormant

        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value=mock_session_ctx)
        mock_session_ctx.__exit__ = MagicMock(return_value=False)
        mock_session_ctx.get = MagicMock(return_value=None)

        with patch("tasks.actors.common.get_sync_session", return_value=mock_session_ctx):
            assert is_user_dormant(42, "actor_foo") is True

    def test_inactive_user_returns_true(self):
        from tasks.actors.common import is_user_dormant

        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value=mock_session_ctx)
        mock_session_ctx.__exit__ = MagicMock(return_value=False)
        mock_session_ctx.get = MagicMock(return_value=SimpleNamespace(is_active=False))

        with patch("tasks.actors.common.get_sync_session", return_value=mock_session_ctx):
            assert is_user_dormant(42, "actor_foo") is True

    def test_active_user_returns_false(self):
        """Active row → caller proceeds with the heavy work."""
        from tasks.actors.common import is_user_dormant

        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value=mock_session_ctx)
        mock_session_ctx.__exit__ = MagicMock(return_value=False)
        mock_session_ctx.get = MagicMock(return_value=SimpleNamespace(is_active=True))

        with patch("tasks.actors.common.get_sync_session", return_value=mock_session_ctx):
            assert is_user_dormant(42, "actor_foo") is False

    def test_is_active_read_inside_session(self):
        """Regression for the DetachedInstance smell flagged in code review:
        the attribute access must happen BEFORE the session context closes,
        otherwise SQLAlchemy can expire it and raise on a refresh attempt
        once `expire_on_commit=True` is configured (the project default)."""
        from tasks.actors.common import is_user_dormant

        access_order: list[str] = []

        # MagicMock with a side_effect that records WHEN .is_active is read
        # vs. when the session __exit__ runs.
        user_row = MagicMock()
        type(user_row).is_active = property(lambda self: access_order.append("read") or True)

        class _SessionCtx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                access_order.append("exit")
                return False

            def get(self, *a, **kw):
                return user_row

        with patch("tasks.actors.common.get_sync_session", _SessionCtx):
            is_user_dormant(1, "actor_foo")

        # is_active read must come BEFORE the session exits.
        assert access_order == ["read", "exit"]


class TestScheduledWorkoutsActorGate:
    def test_skips_when_dormant(self):
        from tasks.actors import actor_user_scheduled_workouts

        with (
            patch("tasks.actors.reports.is_user_dormant", return_value=True) as gate,
            patch("tasks.actors.reports.IntervalsSyncClient.for_user") as for_user,
            patch("tasks.actors.reports.ScheduledWorkout.save_bulk") as save,
        ):
            actor_user_scheduled_workouts(_user().model_dump())

        gate.assert_called_once_with(1, "actor_user_scheduled_workouts")
        for_user.assert_not_called()
        save.assert_not_called()

    def test_force_inactive_bypasses_gate(self):
        """CLI admin path passes ``force_inactive=True`` — gate not consulted,
        Intervals call IS attempted (then returns empty list in this test)."""
        from tasks.actors import actor_user_scheduled_workouts

        mock_client = MagicMock()
        mock_client.get_events.return_value = []
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("tasks.actors.reports.is_user_dormant", return_value=True) as gate,
            patch("tasks.actors.reports.IntervalsSyncClient.for_user", return_value=mock_client) as for_user,
        ):
            actor_user_scheduled_workouts(_user().model_dump(), force_inactive=True)

        gate.assert_not_called()
        for_user.assert_called_once()


class TestAthleteGoalsActorGate:
    def test_skips_when_dormant(self):
        from tasks.actors import actor_sync_athlete_goals

        with (
            patch("tasks.actors.athlets.is_user_dormant", return_value=True) as gate,
            patch("tasks.actors.athlets.IntervalsSyncClient.for_user") as for_user,
        ):
            actor_sync_athlete_goals(_user().model_dump())

        gate.assert_called_once_with(1, "actor_sync_athlete_goals")
        for_user.assert_not_called()

    def test_force_inactive_bypasses_gate(self):
        from tasks.actors import actor_sync_athlete_goals

        mock_client = MagicMock()
        mock_client.get_events.return_value = []
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("tasks.actors.athlets.is_user_dormant", return_value=True) as gate,
            patch("tasks.actors.athlets.AthleteGoal.get_all", return_value=[]),
            patch("tasks.actors.athlets.IntervalsSyncClient.for_user", return_value=mock_client) as for_user,
        ):
            actor_sync_athlete_goals(_user().model_dump(), force_inactive=True)

        gate.assert_not_called()
        for_user.assert_called_once()


class TestFetchActivitiesActorGate:
    def test_skips_when_dormant(self):
        from tasks.actors import actor_fetch_user_activities

        with (
            patch("tasks.actors.activities.is_user_dormant", return_value=True) as gate,
            patch("tasks.actors.activities.IntervalsSyncClient.for_user") as for_user,
        ):
            actor_fetch_user_activities(_user().model_dump())

        gate.assert_called_once_with(1, "actor_fetch_user_activities")
        for_user.assert_not_called()

    def test_force_inactive_bypasses_gate(self):
        from tasks.actors import actor_fetch_user_activities

        mock_client = MagicMock()
        mock_client.get_activities.return_value = []
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("tasks.actors.activities.is_user_dormant", return_value=True) as gate,
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client) as for_user,
        ):
            actor_fetch_user_activities(_user().model_dump(), force_inactive=True)

        gate.assert_not_called()
        for_user.assert_called_once()
