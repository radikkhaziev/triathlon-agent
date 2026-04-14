"""Tests for the scheduled workouts sync pipeline:

scheduler_scheduled_workouts (APScheduler, async)
  → dramatiq group → actor_user_scheduled_workouts (sync)
    → IntervalsSyncClient.get_events (sync HTTP)
    → ScheduledWorkout.save_bulk (sync DB)
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from data.db import ScheduledWorkout
from data.intervals.dto import ScheduledWorkoutDTO

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(*, id: int = 1, chat_id: str = "111", athlete_id: str = "i001"):
    """Create a mock User-like object that TypeAdapter(list[UserDTO]).validate_python can handle."""
    from unittest.mock import MagicMock

    user = MagicMock()
    user.id = id
    user.chat_id = chat_id
    user.username = "tester"
    user.athlete_id = athlete_id
    user.language = "ru"
    user.is_silent = False
    user.role = "athlete"
    user.is_active = True
    return user


def _make_dto(*, id: int = 9001, dt: date = date(2026, 4, 5), name: str = "Z2 Run") -> ScheduledWorkoutDTO:
    return ScheduledWorkoutDTO(
        id=id,
        start_date_local=dt,
        name=name,
        category="WORKOUT",
        type="Run",
        moving_time=3600,
    )


# ---------------------------------------------------------------------------
# 1. scheduler_scheduled_workouts dispatches dramatiq group
# ---------------------------------------------------------------------------


class TestSchedulerDispatch:
    """scheduler_scheduled_workouts fetches users and dispatches dramatiq messages."""

    @pytest.mark.asyncio
    async def test_dispatches_group_for_active_users(self):
        """Should create a dramatiq group with one message per active user."""
        users = [_make_user(id=1), _make_user(id=2, chat_id="222", athlete_id="i002")]

        mock_group_instance = MagicMock()

        with (
            patch("bot.decorator.User.get_active_athletes", new=AsyncMock(return_value=users)),
            patch("bot.scheduler.group", return_value=mock_group_instance) as mock_group_cls,
        ):
            from bot.scheduler import scheduler_scheduled_workouts

            await scheduler_scheduled_workouts()

        # group() was called with a list of 2 messages
        mock_group_cls.assert_called_once()
        messages = mock_group_cls.call_args[0][0]
        assert len(messages) == 2
        mock_group_instance.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatches_empty_group_when_no_users(self):
        """No active users → empty group dispatched."""
        mock_group_instance = MagicMock()

        with (
            patch("bot.decorator.User.get_active_athletes", new=AsyncMock(return_value=[])),
            patch("bot.scheduler.group", return_value=mock_group_instance) as mock_group_cls,
        ):
            from bot.scheduler import scheduler_scheduled_workouts

            await scheduler_scheduled_workouts()

        messages = mock_group_cls.call_args[0][0]
        assert len(messages) == 0
        mock_group_instance.run.assert_called_once()


# ---------------------------------------------------------------------------
# 2. actor_user_scheduled_workouts calls client + save_bulk
# ---------------------------------------------------------------------------


class TestActorUserScheduledWorkouts:
    """actor_user_scheduled_workouts fetches events and saves them."""

    def test_fetches_and_saves(self):
        """Actor calls IntervalsSyncClient.get_events and ScheduledWorkout.save_bulk."""
        from data.db.user import UserDTO

        user = UserDTO(id=1, chat_id="111", athlete_id="i001")

        workouts = [_make_dto(id=9001), _make_dto(id=9002, name="Swim")]

        mock_client = MagicMock()
        mock_client.get_events.return_value = workouts
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("tasks.actors.reports.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.reports.ScheduledWorkout.save_bulk", return_value=2) as mock_save,
        ):
            from tasks.actors import actor_user_scheduled_workouts

            # Call the underlying function, not .send()
            actor_user_scheduled_workouts(user.model_dump())

        mock_client.get_events.assert_called_once()
        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args
        assert call_kwargs[0][0] == 1  # first positional arg: user_id
        assert call_kwargs[0][1] == workouts  # second positional arg: workouts

    def test_passes_date_range(self):
        """Actor passes today → today+14 as oldest/newest."""
        from data.db.user import UserDTO

        user = UserDTO(id=1, chat_id="111", athlete_id="i001")

        workouts = [_make_dto(id=9001)]
        mock_client = MagicMock()
        mock_client.get_events.return_value = workouts
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("tasks.actors.reports.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.reports.ScheduledWorkout.save_bulk", return_value=1) as mock_save,
        ):
            from tasks.actors import actor_user_scheduled_workouts

            actor_user_scheduled_workouts(user.model_dump())

        call_kwargs = mock_save.call_args
        oldest = call_kwargs[1]["oldest"]
        newest = call_kwargs[1]["newest"]
        assert newest - oldest == timedelta(days=14)


# ---------------------------------------------------------------------------
# 3. Integration: save_bulk actually persists (requires DB)
# ---------------------------------------------------------------------------


class TestSaveBulkIntegration:
    """ScheduledWorkout.save_bulk persists to the database."""

    def test_saves_and_retrieves(self, _test_db):
        """save_bulk inserts rows that get_for_date can read back."""
        dt = date(2026, 4, 10)
        workouts = [
            _make_dto(id=8001, dt=dt, name="Morning Run"),
            _make_dto(id=8002, dt=dt, name="Evening Swim"),
        ]

        count = ScheduledWorkout.save_bulk(1, workouts, oldest=dt, newest=dt)
        assert count == 2

    def test_upsert_updates_existing(self, _test_db):
        """save_bulk updates existing rows on conflict."""
        dt = date(2026, 4, 11)
        ScheduledWorkout.save_bulk(1, [_make_dto(id=8010, dt=dt, name="Old Name")])
        ScheduledWorkout.save_bulk(1, [_make_dto(id=8010, dt=dt, name="New Name")])

    def test_deletes_stale(self, _test_db):
        """save_bulk removes rows not in the incoming list when date range given."""
        dt = date(2026, 4, 12)
        ScheduledWorkout.save_bulk(
            1,
            [_make_dto(id=8020, dt=dt), _make_dto(id=8021, dt=dt)],
            oldest=dt,
            newest=dt,
        )
        # Second call with only 8020 → 8021 should be deleted
        count = ScheduledWorkout.save_bulk(
            1,
            [_make_dto(id=8020, dt=dt)],
            oldest=dt,
            newest=dt,
        )
        assert count == 1
