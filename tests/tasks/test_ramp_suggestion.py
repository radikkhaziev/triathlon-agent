"""Tests for RampTrainingSuggestion in tasks/utils.py.

Covers:
- is_test_needed: returns False when tsb=0, ramp already planned, or fresh thresholds
- is_test_needed: returns True when threshold stale or no_data
- plan_ramp: returns message when ramp already exists
- plan_ramp: dispatches actor_push_workout.send when no existing ramp
"""

from datetime import date
from unittest.mock import MagicMock, patch

from data.db import ThresholdFreshnessDTO, UserDTO
from data.db.dto import WellnessPostDTO

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DT = date(2026, 4, 5)


def _user(*, id: int = 1) -> UserDTO:
    return UserDTO(
        id=id,
        chat_id="111",
        username="tester",
        athlete_id="i001",
    )


def _wellness(
    *,
    ctl: float | None = 60.0,
    atl: float | None = 55.0,
    recovery_score: float | None = 80.0,
) -> WellnessPostDTO:
    return WellnessPostDTO(
        id=1,
        user_id=1,
        date="2026-04-05",
        ctl=ctl,
        atl=atl,
        recovery_score=recovery_score,
    )


def _freshness(
    *,
    status: str = "fresh",
    sport: str = "Run",
    days_since: int | None = 10,
) -> ThresholdFreshnessDTO:
    return ThresholdFreshnessDTO(
        status=status,
        sport=sport,
        days_since=days_since,
    )


# ---------------------------------------------------------------------------
# is_test_needed
# ---------------------------------------------------------------------------


class TestIsTestNeeded:
    """RampTrainingSuggestion.is_test_needed logic."""

    def test_returns_false_when_no_wellness(self):
        """No wellness data → tsb=0 → False."""
        from tasks.utils import RampTrainingSuggestion

        ramp = RampTrainingSuggestion(user=_user(), wellness=None)
        assert ramp.is_test_needed is False

    def test_returns_false_when_ctl_atl_none(self):
        """Wellness with ctl/atl=None → tsb=0 → False."""
        from tasks.utils import RampTrainingSuggestion

        w = _wellness(ctl=None, atl=None)
        ramp = RampTrainingSuggestion(user=_user(), wellness=w)
        assert ramp.is_test_needed is False

    def test_returns_false_when_ramp_already_planned(self):
        """Upcoming workouts contain 'Ramp Test' → False."""
        from tasks.utils import RampTrainingSuggestion

        w = _wellness(ctl=60.0, atl=55.0)
        upcoming_mock = MagicMock()
        upcoming_mock.name = "Ramp Test (Run)"

        with patch(
            "tasks.utils.AiWorkout.get_upcoming",
            return_value=[upcoming_mock],
        ):
            ramp = RampTrainingSuggestion(user=_user(), wellness=w)
            assert ramp.is_test_needed is False

    def test_returns_true_when_stale(self):
        """Threshold days_since > 30 → True."""
        from tasks.utils import RampTrainingSuggestion

        w = _wellness(ctl=60.0, atl=55.0)
        stale = _freshness(status="stale", days_since=35)

        with (
            patch("tasks.utils.AiWorkout.get_upcoming", return_value=[]),
            patch("tasks.utils.User.get_threshold_freshness", return_value=stale),
        ):
            ramp = RampTrainingSuggestion(user=_user(), wellness=w)
            assert ramp.is_test_needed is True
            assert ramp.suggested_sport == "Run"
            assert ramp.days_since == 35

    def test_returns_true_when_no_data(self):
        """Threshold status='no_data' → True."""
        from tasks.utils import RampTrainingSuggestion

        w = _wellness(ctl=60.0, atl=55.0)
        no_data = _freshness(status="no_data", days_since=None)

        with (
            patch("tasks.utils.AiWorkout.get_upcoming", return_value=[]),
            patch("tasks.utils.User.get_threshold_freshness", return_value=no_data),
        ):
            ramp = RampTrainingSuggestion(user=_user(), wellness=w)
            assert ramp.is_test_needed is True
            assert ramp.suggested_sport == "Run"

    def test_returns_false_when_fresh(self):
        """Threshold status='fresh', days_since=10 → False."""
        from tasks.utils import RampTrainingSuggestion

        w = _wellness(ctl=60.0, atl=55.0)
        fresh = _freshness(status="fresh", days_since=10)

        with (
            patch("tasks.utils.AiWorkout.get_upcoming", return_value=[]),
            patch("tasks.utils.User.get_threshold_freshness", return_value=fresh),
        ):
            ramp = RampTrainingSuggestion(user=_user(), wellness=w)
            assert ramp.is_test_needed is False

    def test_returns_false_when_recovery_low_and_stale(self):
        """Recovery < 70 + stale threshold → False (low recovery noises DFA, fit collapses)."""
        from tasks.utils import RampTrainingSuggestion

        w = _wellness(ctl=60.0, atl=55.0, recovery_score=55.0)
        stale = _freshness(status="stale", days_since=35)

        with (
            patch("tasks.utils.AiWorkout.get_upcoming", return_value=[]),
            patch("tasks.utils.User.get_threshold_freshness", return_value=stale),
        ):
            ramp = RampTrainingSuggestion(user=_user(), wellness=w)
            assert ramp.is_test_needed is False

    def test_returns_false_when_recovery_missing_and_stale(self):
        """recovery_score=None + stale → False (don't push ramp without a clean baseline)."""
        from tasks.utils import RampTrainingSuggestion

        w = _wellness(ctl=60.0, atl=55.0, recovery_score=None)
        stale = _freshness(status="stale", days_since=35)

        with (
            patch("tasks.utils.AiWorkout.get_upcoming", return_value=[]),
            patch("tasks.utils.User.get_threshold_freshness", return_value=stale),
        ):
            ramp = RampTrainingSuggestion(user=_user(), wellness=w)
            assert ramp.is_test_needed is False

    def test_bootstrap_skips_recovery_gate_when_no_data(self):
        """First-ever test (no_data) → True even when recovery_score is None.

        Newcomers have no DFA baseline to "protect"; gate exists only to
        prevent polluting an existing baseline with a noisy re-fit.
        """
        from tasks.utils import RampTrainingSuggestion

        w = _wellness(ctl=60.0, atl=55.0, recovery_score=None)
        no_data = _freshness(status="no_data", days_since=None)

        with (
            patch("tasks.utils.AiWorkout.get_upcoming", return_value=[]),
            patch("tasks.utils.User.get_threshold_freshness", return_value=no_data),
        ):
            ramp = RampTrainingSuggestion(user=_user(), wellness=w)
            assert ramp.is_test_needed is True
            assert ramp.suggested_sport == "Run"

    def test_bootstrap_still_blocks_on_deep_fatigue(self):
        """Bootstrap path still respects TSB gate (DFA distortion is universal)."""
        from tasks.utils import RampTrainingSuggestion

        # tsb = 50 - 70 = -20
        w = _wellness(ctl=50.0, atl=70.0, recovery_score=None)
        ramp = RampTrainingSuggestion(user=_user(), wellness=w)
        assert ramp.is_test_needed is False

    def test_returns_false_when_tsb_deep_fatigue(self):
        """TSB <= -10 → False (deep fatigue distorts DFA a1)."""
        from tasks.utils import RampTrainingSuggestion

        # ctl=50, atl=70 → tsb=-20
        w = _wellness(ctl=50.0, atl=70.0, recovery_score=80.0)
        ramp = RampTrainingSuggestion(user=_user(), wellness=w)
        assert ramp.is_test_needed is False

    def test_suggests_ride_when_run_fresh(self):
        """sports=['Run','Ride']: if Run is fresh and Ride stale → suggest Ride."""
        from tasks.utils import RampTrainingSuggestion

        w = _wellness(ctl=60.0, atl=55.0)

        def _by_sport(*, user_id, sport):
            if sport == "Run":
                return _freshness(status="fresh", sport="Run", days_since=10)
            return _freshness(status="stale", sport="Ride", days_since=40)

        with (
            patch("tasks.utils.AiWorkout.get_upcoming", return_value=[]),
            patch("tasks.utils.User.get_threshold_freshness", side_effect=_by_sport),
        ):
            ramp = RampTrainingSuggestion(user=_user(), wellness=w)
            assert ramp.is_test_needed is True
            assert ramp.suggested_sport == "Ride"
            assert ramp.days_since == 40


# ---------------------------------------------------------------------------
# plan_ramp
# ---------------------------------------------------------------------------


class TestPlanRamp:
    """RampTrainingSuggestion.plan_ramp dispatches or skips."""

    def test_returns_already_planned_when_ramp_exists(self):
        """Upcoming workouts contain 'Ramp Test' → returns skip message."""
        from tasks.utils import RampTrainingSuggestion

        w = _wellness()
        upcoming_mock = MagicMock()
        upcoming_mock.name = "Ramp Test (Run)"

        with patch("tasks.utils.AiWorkout.get_upcoming", return_value=[upcoming_mock]):
            ramp = RampTrainingSuggestion(user=_user(), wellness=w)
            result = ramp.plan_ramp(sport="Run", dt=_DT)

        assert "уже запланирован" in result

    def test_dispatches_actor_push_workout(self):
        """No existing ramp → creates ramp test and sends actor."""
        from tasks.utils import RampTrainingSuggestion

        w = _wellness()
        fresh = _freshness(status="stale", sport="Run", days_since=30)
        mock_workout = MagicMock()
        run_settings = MagicMock()
        run_settings.threshold_pace = 295.0

        with (
            patch("tasks.utils.AiWorkout.get_upcoming", return_value=[]),
            patch(
                "tasks.utils.User.get_threshold_freshness",
                return_value=fresh,
            ),
            patch(
                "tasks.utils.AthleteSettings.get",
                return_value=run_settings,
            ),
            patch(
                "tasks.utils.create_ramp_test",
                return_value=mock_workout,
            ) as mock_create,
            patch("tasks.utils.actor_push_workout") as mock_actor,
        ):
            ramp = RampTrainingSuggestion(user=_user(), wellness=w)
            result = ramp.plan_ramp(sport="Run", dt=_DT)

        mock_create.assert_called_once_with("Run", _DT, 30, threshold_pace=295.0)
        mock_actor.send.assert_called_once()
        assert "поставлен в очередь" in result
        assert "05.04" in result
