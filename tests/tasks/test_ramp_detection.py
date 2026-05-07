"""Tests for _is_ramp_test_activity — ScheduledWorkout primary + AiWorkout fallback."""

from datetime import date
from types import SimpleNamespace

import pytest

from tasks.actors.activities import _is_ramp_test_activity


@pytest.fixture
def _activity():
    return SimpleNamespace(
        id="i100",
        type="Run",
        start_date_local=str(date.today()),
    )


class TestIsRampTestActivity:
    def test_returns_false_when_no_sport(self, _activity, monkeypatch):
        _activity.type = None
        assert _is_ramp_test_activity(user_id=1, activity=_activity) is False

    def test_scheduled_workout_match_returns_true(self, _activity, monkeypatch):
        """Primary path: ScheduledWorkout has 'ramp test' in name → True."""
        scheduled = [SimpleNamespace(type="Run", name="Ramp Test (Run)")]
        ai_workouts = []  # would not be consulted

        monkeypatch.setattr(
            "tasks.actors.activities.ScheduledWorkout.get_for_date",
            lambda user_id, dt: scheduled,
        )
        monkeypatch.setattr(
            "tasks.actors.activities.AiWorkout.get_for_date",
            lambda user_id, dt: ai_workouts,
        )

        assert _is_ramp_test_activity(user_id=1, activity=_activity) is True

    def test_ai_workout_fallback_when_scheduled_misses(self, _activity, monkeypatch):
        """Fallback: ScheduledWorkout absent (e.g. CALENDAR_UPDATED webhook lost),
        AiWorkout has the ramp record from our own push → still True."""
        scheduled = []  # nothing synced from Intervals.icu yet
        ai_workouts = [SimpleNamespace(sport="Run", name="Ramp Test (Run)")]

        monkeypatch.setattr(
            "tasks.actors.activities.ScheduledWorkout.get_for_date",
            lambda user_id, dt: scheduled,
        )
        monkeypatch.setattr(
            "tasks.actors.activities.AiWorkout.get_for_date",
            lambda user_id, dt: ai_workouts,
        )

        assert _is_ramp_test_activity(user_id=1, activity=_activity) is True

    def test_neither_source_has_ramp_returns_false(self, _activity, monkeypatch):
        """No ramp test in either source — generic post-activity path applies."""
        scheduled = [SimpleNamespace(type="Run", name="Easy Z2 Run")]
        ai_workouts = [SimpleNamespace(sport="Run", name="AI: Z2 Endurance")]

        monkeypatch.setattr(
            "tasks.actors.activities.ScheduledWorkout.get_for_date",
            lambda user_id, dt: scheduled,
        )
        monkeypatch.setattr(
            "tasks.actors.activities.AiWorkout.get_for_date",
            lambda user_id, dt: ai_workouts,
        )

        assert _is_ramp_test_activity(user_id=1, activity=_activity) is False

    def test_ai_workout_wrong_sport_does_not_match(self, _activity, monkeypatch):
        """AiWorkout for Ride doesn't trigger Run ramp detection."""
        scheduled = []
        ai_workouts = [SimpleNamespace(sport="Ride", name="Ramp Test (Ride)")]

        monkeypatch.setattr(
            "tasks.actors.activities.ScheduledWorkout.get_for_date",
            lambda user_id, dt: scheduled,
        )
        monkeypatch.setattr(
            "tasks.actors.activities.AiWorkout.get_for_date",
            lambda user_id, dt: ai_workouts,
        )

        assert _is_ramp_test_activity(user_id=1, activity=_activity) is False

    def test_scheduled_workout_case_insensitive_match(self, _activity, monkeypatch):
        scheduled = [SimpleNamespace(type="Run", name="RAMP TEST today")]
        monkeypatch.setattr(
            "tasks.actors.activities.ScheduledWorkout.get_for_date",
            lambda user_id, dt: scheduled,
        )
        monkeypatch.setattr(
            "tasks.actors.activities.AiWorkout.get_for_date",
            lambda user_id, dt: [],
        )
        assert _is_ramp_test_activity(user_id=1, activity=_activity) is True
