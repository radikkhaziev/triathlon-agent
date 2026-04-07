"""Tests for Dramatiq task queue — echo actor, broker setup, and actor business logic."""

import os
import statistics
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from data.db.user import UserDTO
from data.intervals.dto import RhrStatusDTO, RmssdStatusDTO, ScheduledWorkoutDTO, TrendResultDTO, WellnessDTO
from tasks.dto import ORMDTO


class TestEchoActor:
    """Echo actor unit tests using StubBroker (no Redis required)."""

    def test_echo_sends_message(self):
        """Actor can be enqueued without error."""
        from tasks.actors import actor_echo

        result = actor_echo("hello")
        assert result == "hello"

    def test_echo_multiple_messages(self):
        """Multiple calls all return correct values."""
        from tasks.actors import actor_echo

        results = [actor_echo(f"msg-{i}") for i in range(5)]
        assert results == [f"msg-{i}" for i in range(5)]

    def test_echo_empty_string(self):
        """Empty string is a valid message."""
        from tasks.actors import actor_echo

        assert actor_echo("") == ""


class TestBrokerConfig:
    """Verify broker configuration from tasks/broker.py."""

    def test_broker_module_loads(self):
        """tasks.broker module imports without error."""
        from tasks import broker as broker_mod

        assert hasattr(broker_mod, "broker")
        assert hasattr(broker_mod, "setup_broker")

    def test_actors_module_loads(self):
        """tasks.actors module imports and has echo actor."""
        from tasks import actors as actors_mod

        assert hasattr(actors_mod, "actor_echo")
        assert callable(actors_mod.actor_echo.send)


@pytest.mark.real_db
@pytest.mark.skip(reason="Integration: requires real Redis + Intervals.icu API")
class TestSyncUserWellness:
    """actor_user_wellness — tests the real function from tasks/actors.py."""

    def test_calls_real_function(self):
        """Real actor_user_wellness accepts UserDTO and executes."""
        from data.db.user import UserDTO
        from tasks.actors import actor_user_wellness

        user = UserDTO(
            id=1,
            chat_id=os.environ["TELEGRAM_CHAT_ID"],
            username=os.environ.get("TELEGRAM_USERNAME", "test"),
            athlete_id=os.environ["INTERVALS_ATHLETE_ID"],
            api_key=os.environ["INTERVALS_API_KEY"],
        )
        result = actor_user_wellness(user)
        assert result is None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DT = date(2026, 4, 6)
_DT_STR = "2026-04-06"


def _user(*, id: int = 1) -> UserDTO:
    return UserDTO(id=id, chat_id="111", username="tester", athlete_id="i001", api_key="key1")


def _trend(direction: str = "stable") -> TrendResultDTO:
    return TrendResultDTO(direction=direction, slope=0.1, r_squared=0.5, emoji="→")


def _rhr_status(*, status: str = "yellow", days: int = 30) -> RhrStatusDTO:
    return RhrStatusDTO(
        status=status,
        days_available=days,
        days_needed=0,
        rhr_today=52.0,
        rhr_7d=51.0,
        rhr_sd_7d=1.5,
        rhr_30d=51.5,
        rhr_sd_30d=2.0,
        lower_bound=50.5,
        upper_bound=52.5,
        cv_7d=2.9,
        trend=_trend(),
    )


def _rmssd_status(*, status: str = "green") -> RmssdStatusDTO:
    return RmssdStatusDTO(
        status=status,
        days_available=20,
        days_needed=0,
        rmssd_7d=65.0,
        rmssd_sd_7d=5.0,
        rmssd_60d=60.0,
        rmssd_sd_60d=7.0,
        lower_bound=57.5,
        upper_bound=72.5,
        cv_7d=7.7,
        swc=3.0,
        trend=_trend("rising"),
    )


# ---------------------------------------------------------------------------
# _actor_calculate_rhr
# ---------------------------------------------------------------------------


class TestActorCalculateRhr:
    """Pure logic tests for _actor_calculate_rhr — no DB or network required."""

    def _call(self, rhr_rows: list[float], dt: date = _DT) -> RhrStatusDTO:
        """Patch Wellness.get_rhr_history and call the actor directly."""
        from tasks.actors.wellness import _actor_calculate_rhr

        user = _user()
        with patch("tasks.actors.wellness.Wellness.get_rhr_history", return_value=rhr_rows):
            return _actor_calculate_rhr(user.model_dump(), dt)

    # --- insufficient data ---

    def test_returns_insufficient_when_fewer_than_7_days(self):
        """Fewer than 7 data points → insufficient_data with correct counters."""
        result = self._call([55.0, 54.0, 53.0])
        assert result.status == "insufficient_data"
        assert result.days_available == 3
        assert result.days_needed == 4  # 7 - 3

    def test_returns_insufficient_with_zero_days(self):
        """Empty history → insufficient_data."""
        result = self._call([])
        assert result.status == "insufficient_data"
        assert result.days_available == 0
        assert result.days_needed == 7

    def test_returns_insufficient_with_exactly_6_days(self):
        """Six values is still insufficient (boundary condition)."""
        result = self._call([52.0] * 6)
        assert result.status == "insufficient_data"
        assert result.days_available == 6

    # --- status thresholds ---

    def test_status_red_when_rhr_above_upper_bound(self):
        """Today's RHR clearly above mean_30 + 0.5*sd_30 → red."""
        # 29 normal days at 50, today spiked to 60
        rows = [50.0] * 29 + [60.0]
        result = self._call(rows)
        assert result.status == "red"
        assert result.rhr_today == 60.0

    def test_status_green_when_rhr_below_lower_bound(self):
        """Today's RHR clearly below mean_30 - 0.5*sd_30 → green (unusually low = good)."""
        # 29 normal days at 60, today dropped to 50
        rows = [60.0] * 29 + [50.0]
        result = self._call(rows)
        assert result.status == "green"
        assert result.rhr_today == 50.0

    def test_status_yellow_when_rhr_within_bounds(self):
        """Today's RHR within ±0.5 SD → yellow."""
        # All values equal → sd=0, bounds collapse to mean; today == mean → yellow
        rows = [55.0] * 30
        result = self._call(rows)
        assert result.status == "yellow"

    def test_exactly_7_days_is_sufficient(self):
        """Exactly 7 data points is enough to produce a result."""
        rows = [54.0, 53.0, 55.0, 54.0, 53.0, 54.0, 54.0]
        result = self._call(rows)
        assert result.status != "insufficient_data"
        assert result.days_available == 7
        assert result.days_needed == 0

    # --- computed fields ---

    def test_7d_mean_matches_statistics(self):
        """rhr_7d must equal statistics.mean of the last 7 values."""
        rows = list(range(40, 70))  # 30 values: 40..69
        result = self._call(rows)
        expected_7d = round(statistics.mean(rows[-7:]), 1)
        assert result.rhr_7d == expected_7d

    def test_30d_mean_and_bounds(self):
        """lower_bound = mean_30 - 0.5 * sd_30, upper_bound = mean_30 + 0.5 * sd_30."""
        rows = list(range(30, 60))  # 30 values
        result = self._call(rows)
        mean_30 = statistics.mean(rows[-30:])
        sd_30 = statistics.stdev(rows[-30:])
        assert result.lower_bound == round(mean_30 - 0.5 * sd_30, 1)
        assert result.upper_bound == round(mean_30 + 0.5 * sd_30, 1)

    def test_60d_values_populated_when_enough_data(self):
        """With 60+ data points, rhr_60d and rhr_sd_60d are populated."""
        rows = [52.0 + (i % 5) for i in range(60)]
        result = self._call(rows)
        assert result.rhr_60d is not None
        assert result.rhr_sd_60d is not None

    def test_60d_values_none_when_fewer_than_60_days(self):
        """With fewer than 60 data points, rhr_60d is None."""
        rows = [52.0] * 30
        result = self._call(rows)
        assert result.rhr_60d is None
        assert result.rhr_sd_60d is None

    def test_cv_7d_is_populated(self):
        """CV 7d = (sd_7 / mean_7) * 100, rounded to 1 decimal."""
        rows = [50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 56.0]
        result = self._call(rows)
        mean_7 = statistics.mean(rows)
        sd_7 = statistics.stdev(rows)
        assert result.cv_7d == round(sd_7 / mean_7 * 100, 1)

    def test_trend_is_populated(self):
        """Trend must be a TrendResultDTO when enough data."""
        rows = [52.0 + (i * 0.1) for i in range(30)]
        result = self._call(rows)
        assert result.trend is not None
        assert result.trend.direction in ("rising_fast", "rising", "stable", "declining", "declining_fast")


# ---------------------------------------------------------------------------
# _actor_calculate_hrv
# ---------------------------------------------------------------------------


class TestActorCalculateHrv:
    """Pure logic tests for _actor_calculate_hrv."""

    def _call(self, hrv_rows: list[float], dt: date = _DT):
        from tasks.actors.wellness import _actor_calculate_hrv

        user = _user()
        with patch("tasks.actors.wellness.Wellness.get_hrv_history", return_value=hrv_rows):
            return _actor_calculate_hrv(user.model_dump(), dt)

    def test_insufficient_when_fewer_than_14_days(self):
        """Fewer than 14 data points → both algorithms return insufficient_data."""
        result = self._call([65.0] * 10)
        assert result["flatt_esco"].status == "insufficient_data"
        assert result["ai_endurance"].status == "insufficient_data"
        assert result["flatt_esco"].days_available == 10
        assert result["flatt_esco"].days_needed == 4  # 14 - 10

    def test_insufficient_with_empty_history(self):
        """Empty history → both algorithms are insufficient_data."""
        result = self._call([])
        assert result["flatt_esco"].status == "insufficient_data"
        assert result["ai_endurance"].status == "insufficient_data"

    def test_insufficient_with_exactly_13_days(self):
        """Exactly 13 days is still insufficient (boundary)."""
        result = self._call([65.0] * 13)
        assert result["flatt_esco"].status == "insufficient_data"
        assert result["flatt_esco"].days_needed == 1

    def test_delegates_to_algorithms_when_enough_data(self):
        """With 14+ rows, both rmssd_flatt_esco and rmssd_ai_endurance are called."""
        hrv_rows = [65.0 + (i % 5) for i in range(20)]

        fe_result = _rmssd_status(status="green")
        aie_result = _rmssd_status(status="yellow")

        with (
            patch("tasks.actors.wellness.Wellness.get_hrv_history", return_value=hrv_rows),
            patch("tasks.actors.wellness.rmssd_flatt_esco", return_value=fe_result) as mock_fe,
            patch("tasks.actors.wellness.rmssd_ai_endurance", return_value=aie_result) as mock_aie,
        ):
            from tasks.actors.wellness import _actor_calculate_hrv

            result = _actor_calculate_hrv(_user().model_dump(), _DT)

        mock_fe.assert_called_once_with(hrv_rows)
        mock_aie.assert_called_once_with(hrv_rows)
        assert result["flatt_esco"] is fe_result
        assert result["ai_endurance"] is aie_result

    def test_returns_dict_with_both_keys(self):
        """Return value always has both algorithm keys."""
        hrv_rows = [60.0 + (i % 8) for i in range(20)]
        result = self._call(hrv_rows)
        assert "flatt_esco" in result
        assert "ai_endurance" in result


# ---------------------------------------------------------------------------
# _actor_update_rhr_analysis
# ---------------------------------------------------------------------------


class TestActorUpdateRhrAnalysis:
    """_actor_update_rhr_analysis persists RHR status to DB."""

    def test_skips_db_write_when_insufficient_data(self):
        """status=insufficient_data → returns prev immediately, no DB session opened."""
        from tasks.actors.wellness import _actor_update_rhr_analysis

        prev = RhrStatusDTO(status="insufficient_data", days_available=3, days_needed=4)

        with patch("tasks.actors.wellness.get_sync_session") as mock_session:
            result = _actor_update_rhr_analysis(prev.model_dump(), user=_user().model_dump(), dt=_DT)

        mock_session.assert_not_called()
        assert result.status == "insufficient_data"

    def test_creates_new_row_when_not_found(self):
        """No existing DB row → new RhrAnalysis created and fields set."""
        from tasks.actors.wellness import _actor_update_rhr_analysis

        prev = _rhr_status(status="red")
        mock_rhr_row = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = None  # row not found

        with (
            patch("tasks.actors.wellness.get_sync_session", return_value=mock_session),
            patch("tasks.actors.wellness.RhrAnalysis", return_value=mock_rhr_row) as mock_rhr_cls,
        ):
            result = _actor_update_rhr_analysis(prev.model_dump(), user=_user().model_dump(), dt=_DT)

        mock_rhr_cls.assert_called_once_with(user_id=1, date=_DT_STR)
        mock_session.add.assert_called_once_with(mock_rhr_row)
        mock_session.commit.assert_called_once()
        assert result.status == "red"

    def test_updates_existing_row(self):
        """Existing DB row → fields updated, no session.add called."""
        from tasks.actors.wellness import _actor_update_rhr_analysis

        prev = _rhr_status(status="green")
        existing_row = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = existing_row

        with patch("tasks.actors.wellness.get_sync_session", return_value=mock_session):
            result = _actor_update_rhr_analysis(prev.model_dump(), user=_user().model_dump(), dt=_DT)

        mock_session.add.assert_not_called()
        assert existing_row.status == "green"
        assert existing_row.rhr_today == prev.rhr_today
        mock_session.commit.assert_called_once()
        assert result.status == "green"

    def test_persists_trend_fields(self):
        """Trend direction/slope/r_squared are written to the row."""
        from tasks.actors.wellness import _actor_update_rhr_analysis

        prev = _rhr_status(status="yellow")
        mock_row = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = mock_row

        with patch("tasks.actors.wellness.get_sync_session", return_value=mock_session):
            _actor_update_rhr_analysis(prev.model_dump(), user=_user().model_dump(), dt=_DT)

        assert mock_row.trend_direction == prev.trend.direction
        assert mock_row.trend_slope == prev.trend.slope
        assert mock_row.trend_r_squared == prev.trend.r_squared

    def test_returns_prev_unchanged(self):
        """Return value is always the incoming RhrStatusDTO."""
        from tasks.actors.wellness import _actor_update_rhr_analysis

        prev = _rhr_status(status="yellow")
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = MagicMock()

        with patch("tasks.actors.wellness.get_sync_session", return_value=mock_session):
            result = _actor_update_rhr_analysis(prev.model_dump(), user=_user().model_dump(), dt=_DT)

        assert result.status == prev.status
        assert result.rhr_today == prev.rhr_today


# ---------------------------------------------------------------------------
# _actor_update_hrv_analysis
# ---------------------------------------------------------------------------


class TestActorUpdateHrvAnalysis:
    """_actor_update_hrv_analysis persists HRV analysis for both algorithms."""

    def _make_prev(self, fe_status: str = "green", aie_status: str = "green") -> dict:
        return {
            "flatt_esco": _rmssd_status(status=fe_status),
            "ai_endurance": _rmssd_status(status=aie_status),
        }

    def test_skips_both_when_insufficient(self):
        """Both algorithms insufficient → no DB sessions opened."""
        from tasks.actors.wellness import _actor_update_hrv_analysis

        prev = {
            "flatt_esco": RmssdStatusDTO(status="insufficient_data", days_available=5, days_needed=9),
            "ai_endurance": RmssdStatusDTO(status="insufficient_data", days_available=5, days_needed=9),
        }
        serialised = {"flatt_esco": prev["flatt_esco"].model_dump(), "ai_endurance": prev["ai_endurance"].model_dump()}

        with patch("tasks.actors.wellness.get_sync_session") as mock_session:
            _actor_update_hrv_analysis(serialised, user=_user().model_dump(), dt=_DT)

        mock_session.assert_not_called()

    def test_writes_flatt_esco_row(self):
        """Valid flatt_esco → DB row created/updated for that algorithm."""
        from tasks.actors.wellness import _actor_update_hrv_analysis

        fe = _rmssd_status(status="yellow")
        aie = RmssdStatusDTO(status="insufficient_data", days_available=5, days_needed=9)
        prev = {"flatt_esco": fe.model_dump(), "ai_endurance": aie.model_dump()}

        mock_row = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = mock_row  # existing row

        with patch("tasks.actors.wellness.get_sync_session", return_value=mock_session):
            _actor_update_hrv_analysis(prev, user=_user().model_dump(), dt=_DT)

        # session.get called with composite key (user_id, date, algorithm)
        mock_session.get.assert_called_once()
        get_args = mock_session.get.call_args[0]
        assert get_args[1] == (1, _DT_STR, "flatt_esco")
        assert mock_row.status == "yellow"

    def test_writes_both_algorithms_when_valid(self):
        """Both algorithms valid → two DB sessions opened."""
        from tasks.actors.wellness import _actor_update_hrv_analysis

        prev = {
            "flatt_esco": _rmssd_status(status="green").model_dump(),
            "ai_endurance": _rmssd_status(status="yellow").model_dump(),
        }

        mock_row = MagicMock()
        sessions_created = []

        def session_factory():
            s = MagicMock()
            s.__enter__ = MagicMock(return_value=s)
            s.__exit__ = MagicMock(return_value=False)
            s.get.return_value = mock_row
            sessions_created.append(s)
            return s

        with patch("tasks.actors.wellness.get_sync_session", side_effect=session_factory):
            _actor_update_hrv_analysis(prev, user=_user().model_dump(), dt=_DT)

        # One session per algorithm (2 algorithms with valid data)
        assert len(sessions_created) == 2

    def test_returns_flatt_esco_result(self):
        """Return value is the flatt_esco RmssdStatusDTO."""
        from tasks.actors.wellness import _actor_update_hrv_analysis

        fe = _rmssd_status(status="green")
        prev = {
            "flatt_esco": fe.model_dump(),
            "ai_endurance": RmssdStatusDTO(status="insufficient_data", days_available=5, days_needed=9).model_dump(),
        }

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = MagicMock()

        with patch("tasks.actors.wellness.get_sync_session", return_value=mock_session):
            result = _actor_update_hrv_analysis(prev, user=_user().model_dump(), dt=_DT)

        assert result.status == "green"

    def test_creates_new_row_when_not_found(self):
        """No existing HrvAnalysis row → new row created and added to session."""
        from tasks.actors.wellness import _actor_update_hrv_analysis

        fe = _rmssd_status(status="red")
        prev = {
            "flatt_esco": fe.model_dump(),
            "ai_endurance": RmssdStatusDTO(status="insufficient_data", days_available=0, days_needed=14).model_dump(),
        }

        mock_new_row = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = None  # row not found

        with (
            patch("tasks.actors.wellness.get_sync_session", return_value=mock_session),
            patch("tasks.actors.wellness.HrvAnalysis", return_value=mock_new_row) as mock_hrv_cls,
        ):
            _actor_update_hrv_analysis(prev, user=_user().model_dump(), dt=_DT)

        mock_hrv_cls.assert_called_once_with(user_id=1, date=_DT_STR, algorithm="flatt_esco")
        mock_session.add.assert_called_once_with(mock_new_row)


# ---------------------------------------------------------------------------
# _actor_enrich_wellness_sport_info
# ---------------------------------------------------------------------------


class TestActorEnrichWellnessSportInfo:
    """_actor_enrich_wellness_sport_info calculates per-sport CTL and writes to wellness."""

    def _make_activity(self, activity_type: str, load: float):
        act = MagicMock()
        act.type = activity_type
        act.icu_training_load = load
        act.start_date_local = _DT_STR
        return act

    def test_no_activities_calls_update_with_empty_ctl(self):
        """No activities → calculate_sport_ctl returns empty/zero dict → update_sport_ctl called."""
        from tasks.actors.common import _actor_enrich_wellness_sport_info

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("tasks.actors.common.get_sync_session", return_value=mock_session),
            patch("tasks.actors.common.Activity.get_for_ctl", return_value=[]) as mock_get,
            patch("tasks.actors.common.calculate_sport_ctl", return_value={"swim": 0.0, "bike": 0.0, "run": 0.0}),
            patch("tasks.actors.common.Wellness.update_sport_ctl") as mock_update,
        ):
            _actor_enrich_wellness_sport_info(_user().model_dump(), _DT)

        mock_get.assert_called_once()
        assert mock_get.call_args[1]["user_id"] == 1
        assert mock_get.call_args[1]["as_of"] == _DT
        mock_update.assert_called_once()
        assert mock_update.call_args[1]["user_id"] == 1
        assert mock_update.call_args[1]["dt"] == _DT
        assert mock_update.call_args[1]["sport_ctl"] == {"swim": 0.0, "bike": 0.0, "run": 0.0}

    def test_activities_present_calculates_sport_ctl(self):
        """Activities passed to calculate_sport_ctl and result forwarded to Wellness."""
        from tasks.actors.common import _actor_enrich_wellness_sport_info

        activities = [self._make_activity("Run", 80.0), self._make_activity("Ride", 120.0)]
        expected_ctl = {"swim": 0.0, "bike": 10.5, "run": 6.3}

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("tasks.actors.common.get_sync_session", return_value=mock_session),
            patch("tasks.actors.common.Activity.get_for_ctl", return_value=activities),
            patch("tasks.actors.common.calculate_sport_ctl", return_value=expected_ctl) as mock_ctl,
            patch("tasks.actors.common.Wellness.update_sport_ctl") as mock_update,
        ):
            _actor_enrich_wellness_sport_info(_user().model_dump(), _DT)

        mock_ctl.assert_called_once_with(activities)
        mock_update.assert_called_once()
        assert mock_update.call_args[1]["sport_ctl"] == expected_ctl

    def test_uses_user_id_from_dto(self):
        """user_id passed to both Activity.get_for_ctl and Wellness.update_sport_ctl."""
        from tasks.actors.common import _actor_enrich_wellness_sport_info

        user = _user(id=7)

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("tasks.actors.common.get_sync_session", return_value=mock_session),
            patch("tasks.actors.common.Activity.get_for_ctl", return_value=[]) as mock_get,
            patch("tasks.actors.common.calculate_sport_ctl", return_value={}),
            patch("tasks.actors.common.Wellness.update_sport_ctl") as mock_update,
        ):
            _actor_enrich_wellness_sport_info(user.model_dump(), _DT)

        mock_get.assert_called_once()
        assert mock_get.call_args[1]["user_id"] == 7
        mock_update.assert_called_once()
        assert mock_update.call_args[1]["user_id"] == 7


# ---------------------------------------------------------------------------
# _actor_update_banister_ess
# ---------------------------------------------------------------------------


class TestActorUpdateBanisterEss:
    """_actor_update_banister_ess calculates Banister model and updates wellness."""

    def _mock_session(self, wellness_row=None):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        result = MagicMock()
        result.scalar_one_or_none.return_value = wellness_row
        session.execute.return_value = result
        return session

    def test_no_activities_returns_early(self):
        """No activities → no DB session opened."""
        from tasks.actors.common import _actor_update_banister_ess

        with (
            patch("tasks.actors.common.Activity.get_for_banister", return_value=[]),
            patch("tasks.actors.common.get_sync_session") as mock_gs,
        ):
            _actor_update_banister_ess(_user().model_dump(), _DT)

        mock_gs.assert_not_called()

    def test_no_wellness_row_returns_early(self):
        """Wellness row missing → no calculation."""
        from tasks.actors.common import _actor_update_banister_ess

        act = MagicMock()
        act.start_date_local = _DT_STR
        mock_session = self._mock_session(wellness_row=None)

        with (
            patch("tasks.actors.common.Activity.get_for_banister", return_value=[act]),
            patch("tasks.actors.common.get_sync_session", return_value=mock_session),
        ):
            _actor_update_banister_ess(_user().model_dump(), _DT)

        mock_session.commit.assert_not_called()

    def test_no_resting_hr_returns_early(self):
        """Wellness row exists but resting_hr is None → no calculation."""
        from tasks.actors.common import _actor_update_banister_ess

        act = MagicMock()
        act.start_date_local = _DT_STR
        wellness = MagicMock()
        wellness.resting_hr = None
        mock_session = self._mock_session(wellness_row=wellness)

        with (
            patch("tasks.actors.common.Activity.get_for_banister", return_value=[act]),
            patch("tasks.actors.common.get_sync_session", return_value=mock_session),
        ):
            _actor_update_banister_ess(_user().model_dump(), _DT)

        mock_session.commit.assert_not_called()

    def test_calculates_and_saves(self):
        """Happy path: calculates Banister and saves to wellness row."""
        from tasks.actors.common import _actor_update_banister_ess

        act = MagicMock()
        act.start_date_local = _DT_STR
        wellness = MagicMock()
        wellness.resting_hr = 52

        mock_session = self._mock_session(wellness_row=wellness)
        thresholds = MagicMock()
        thresholds.max_hr = 179
        thresholds.lthr_run = 153

        with (
            patch("tasks.actors.common.Activity.get_for_banister", return_value=[act]),
            patch("tasks.actors.common.get_sync_session", return_value=mock_session),
            patch("tasks.actors.common.AthleteSettings.get_thresholds", return_value=thresholds),
            patch("tasks.actors.common.calculate_banister_for_date", return_value=(0.85, 42.0)) as mock_calc,
        ):
            _actor_update_banister_ess(_user().model_dump(), _DT)

        mock_calc.assert_called_once()
        assert wellness.banister_recovery == 0.85
        assert wellness.ess_today == 42.0
        mock_session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# actor_user_scheduled_workouts
# ---------------------------------------------------------------------------


class TestActorUserScheduledWorkouts:
    """actor_user_scheduled_workouts fetches events and saves them."""

    def _make_workout_dto(self, id: int = 9001) -> ScheduledWorkoutDTO:
        return ScheduledWorkoutDTO(
            id=id,
            start_date_local=date(2026, 4, 5),
            name="Z2 Run",
            category="WORKOUT",
            type="Run",
            moving_time=3600,
        )

    def _make_client(self, workouts: list[ScheduledWorkoutDTO]) -> MagicMock:
        mock_client = MagicMock()
        mock_client.get_events.return_value = workouts
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        return mock_client

    def test_no_workouts_returns_early(self):
        """No events returned by client → save_bulk is never called."""
        from tasks.actors import actor_user_scheduled_workouts

        mock_client = self._make_client([])

        with (
            patch("tasks.actors.reports.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.reports.ScheduledWorkout.save_bulk") as mock_save,
        ):
            actor_user_scheduled_workouts(_user().model_dump())

        mock_save.assert_not_called()

    def test_workouts_saved_via_save_bulk(self):
        """Fetched workouts forwarded to ScheduledWorkout.save_bulk."""
        from tasks.actors import actor_user_scheduled_workouts

        workouts = [self._make_workout_dto(9001), self._make_workout_dto(9002)]
        mock_client = self._make_client(workouts)

        with (
            patch("tasks.actors.reports.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.reports.ScheduledWorkout.save_bulk", return_value=2) as mock_save,
        ):
            actor_user_scheduled_workouts(_user().model_dump())

        mock_save.assert_called_once()
        args, kwargs = mock_save.call_args
        assert args[0] == 1
        assert args[1] == workouts

    def test_date_range_is_today_plus_14_days(self):
        """oldest=today, newest=today+14 is passed to save_bulk."""
        from datetime import timedelta

        from tasks.actors import actor_user_scheduled_workouts

        workouts = [self._make_workout_dto()]
        mock_client = self._make_client(workouts)

        with (
            patch("tasks.actors.reports.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.reports.ScheduledWorkout.save_bulk", return_value=1) as mock_save,
        ):
            actor_user_scheduled_workouts(_user().model_dump())

        _, kwargs = mock_save.call_args
        assert kwargs["newest"] - kwargs["oldest"] == timedelta(days=14)


# ---------------------------------------------------------------------------
# actor_user_wellness
# ---------------------------------------------------------------------------


class TestActorUserWellnessMocked:
    """actor_user_wellness — mocked unit tests for orchestration logic."""

    def _make_wellness_dto(self, dt: str = _DT_STR) -> WellnessDTO:
        return WellnessDTO(id=dt, resting_hr=52, hrv=65.0, sleep_score=80.0)

    def _make_client(self, wellness: WellnessDTO | None) -> MagicMock:
        mock_client = MagicMock()
        mock_client.get_wellness.return_value = wellness
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        return mock_client

    def test_returns_early_when_no_wellness_data(self):
        """Client returns falsy value → Wellness.save is never called."""
        from tasks.actors import actor_user_wellness

        mock_client = self._make_client(None)

        with (
            patch("tasks.actors.wellness.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.wellness.Wellness.save") as mock_save,
            patch("tasks.actors.wellness.actor_after_activity_update") as mock_enrich,
        ):
            actor_user_wellness(_user().model_dump(), _DT)

        mock_save.assert_not_called()
        mock_enrich.send.assert_not_called()

    def test_saves_wellness_when_data_present(self):
        """Wellness data returned → Wellness.save called with correct user_id."""
        from tasks.actors import actor_user_wellness

        wellness_dto = self._make_wellness_dto()
        mock_client = self._make_client(wellness_dto)
        mock_wellness_row = MagicMock()
        mock_pipeline = MagicMock()

        with (
            patch("tasks.actors.wellness.IntervalsSyncClient.for_user", return_value=mock_client),
            patch(
                "tasks.actors.wellness.Wellness.save",
                return_value=ORMDTO(is_new=False, is_changed=True, row=mock_wellness_row),
            ) as mock_save,
            patch("tasks.actors.wellness.actor_after_activity_update"),
            patch("tasks.actors.athlets.actor_sync_athlete_settings"),
            patch("tasks.actors.wellness.pipeline", return_value=mock_pipeline),
            patch("tasks.actors.wellness.group"),
        ):
            actor_user_wellness(_user().model_dump(), _DT)

        mock_save.assert_called_once_with(user_id=1, wellness=wellness_dto)

    def test_dispatches_after_activity_update(self):
        """After saving wellness, actor_after_activity_update.send is included in group."""
        from tasks.actors import actor_user_wellness

        wellness_dto = self._make_wellness_dto()
        mock_client = self._make_client(wellness_dto)
        mock_group = MagicMock()

        with (
            patch("tasks.actors.wellness.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.wellness.Wellness.save", return_value=ORMDTO(is_changed=True, row=MagicMock())),
            patch("tasks.actors.wellness.actor_after_activity_update") as mock_after,
            patch("tasks.actors.athlets.actor_sync_athlete_settings"),
            patch("tasks.actors.wellness.pipeline"),
            patch("tasks.actors.wellness.group", return_value=mock_group) as mock_group_cls,
        ):
            actor_user_wellness(_user().model_dump(), _DT)

        assert mock_group_cls.call_count == 2
        mock_after.send.assert_called_once()

    def test_runs_rhr_hrv_group(self):
        """actor_user_wellness builds and runs a dramatiq group with completion callback."""
        from tasks.actors import actor_user_wellness

        wellness_dto = self._make_wellness_dto()
        mock_client = self._make_client(wellness_dto)
        mock_group = MagicMock()

        with (
            patch("tasks.actors.wellness.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.wellness.Wellness.save", return_value=ORMDTO(is_changed=True, row=MagicMock())),
            patch("tasks.actors.wellness.actor_after_activity_update"),
            patch("tasks.actors.athlets.actor_sync_athlete_settings"),
            patch("tasks.actors.wellness.pipeline"),
            patch("tasks.actors.wellness.group", return_value=mock_group),
        ):
            actor_user_wellness(_user().model_dump(), _DT)

        mock_group.add_completion_callback.assert_called_once()
        assert mock_group.run.call_count == 2  # first group (settings+activity) + second group (RHR/HRV pipelines)

    def test_accepts_none_dt_and_uses_today(self):
        """When dt=None, actor uses today's date and calls the client."""
        from tasks.actors import actor_user_wellness

        wellness_dto = self._make_wellness_dto()
        mock_client = self._make_client(wellness_dto)
        mock_pipeline = MagicMock()

        with (
            patch("tasks.actors.wellness.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.wellness.Wellness.save", return_value=ORMDTO(is_changed=True, row=MagicMock())),
            patch("tasks.actors.wellness.actor_after_activity_update"),
            patch("tasks.actors.athlets.actor_sync_athlete_settings"),
            patch("tasks.actors.wellness.pipeline", return_value=mock_pipeline),
            patch("tasks.actors.wellness.group"),
        ):
            # dt=None means "today" — should not raise
            actor_user_wellness(_user().model_dump(), None)

        mock_client.get_wellness.assert_called_once()


# ---------------------------------------------------------------------------
# _actor_update_recovery_score
# ---------------------------------------------------------------------------


class TestActorUpdateRecoveryScore:
    """_actor_update_recovery_score — currently a pass stub; verify it exists and is safe."""

    def test_exists_and_is_callable(self):
        """_actor_update_recovery_score must be importable and callable."""
        from tasks.actors.wellness import _actor_update_recovery_score

        assert callable(_actor_update_recovery_score)

    def test_does_not_raise(self):
        """Calling with valid inputs and mocked DB must not raise."""
        from tasks.actors.wellness import _actor_update_recovery_score

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        # Return None for wellness row so it returns early
        scalar_mock = MagicMock()
        scalar_mock.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = scalar_mock

        with patch("tasks.actors.wellness.get_sync_session", return_value=mock_session):
            result = _actor_update_recovery_score(
                user=_user().model_dump(),
                dt=_DT,
            )
        # No wellness row → returns None
        assert result is None


# ---------------------------------------------------------------------------
# Integration: full wellness pipeline via StubBroker + real DB
# ---------------------------------------------------------------------------


@pytest.mark.real_db
@pytest.mark.skip(reason="Integration: requires real Redis + Intervals.icu API")
class TestWellnessPipelineIntegration:
    """End-to-end: real API, real DB, real pipeline. No mocks.

    Uses real Intervals.icu credentials from .env (user_id=1).
    Run with: pytest tests/tasks/test_actors.py -m real_db
    """

    def _real_user(self) -> UserDTO:
        return UserDTO(
            id=1,
            chat_id=os.environ["TELEGRAM_CHAT_ID"],
            username=os.environ.get("TELEGRAM_USERNAME", "test"),
            athlete_id=os.environ["INTERVALS_ATHLETE_ID"],
            api_key=os.environ["INTERVALS_API_KEY"],
        )

    def test_actor_user_wellness(self):
        """Full actor_user_wellness — real API + real DB."""
        from tasks.actors import actor_user_wellness

        user = self._real_user()
        result = actor_user_wellness(user.model_dump())
        assert result is None

    def test_actor_user_wellness_with_worker(self, stub_broker, stub_worker):
        """Full pipeline with worker — verify completion callback fires."""
        from tasks.actors import actor_user_wellness

        user = self._real_user()
        actor_user_wellness(user.model_dump())

        # Wait for worker to process all queued messages (group pipelines + completion callback)
        stub_broker.join("default", timeout=30000)
        stub_worker.join()

    def test_actor_user_scheduled_workouts(self):
        """Full actor_user_scheduled_workouts — real API + real DB."""
        from tasks.actors import actor_user_scheduled_workouts

        user = self._real_user()
        actor_user_scheduled_workouts(user.model_dump())

    def test_rhr_pipeline(self):
        """_actor_calculate_rhr → _actor_update_rhr_analysis — real DB."""
        from data.db.common import get_sync_session
        from data.db.hrv import RhrAnalysis
        from tasks.actors.wellness import _actor_calculate_rhr, _actor_update_rhr_analysis

        user = self._real_user()
        rhr_status = _actor_calculate_rhr(user.model_dump(), date.today())

        if rhr_status.status == "insufficient_data":
            pytest.skip(f"Not enough RHR data: {rhr_status.days_available} days")

        assert rhr_status.status in ("green", "yellow", "red")
        assert rhr_status.rhr_7d is not None

        _actor_update_rhr_analysis(rhr_status.model_dump(), user=user.model_dump(), dt=date.today())

        with get_sync_session() as session:
            row = session.get(RhrAnalysis, (user.id, date.today().isoformat()))
            assert row is not None
            assert row.status == rhr_status.status

    def test_hrv_pipeline(self):
        """_actor_calculate_hrv → _actor_update_hrv_analysis — real DB."""
        from data.db.common import get_sync_session
        from data.db.hrv import HrvAnalysis
        from tasks.actors.wellness import _actor_calculate_hrv, _actor_update_hrv_analysis

        user = self._real_user()
        hrv_result = _actor_calculate_hrv(user.model_dump(), date.today())

        if hrv_result["flatt_esco"].status == "insufficient_data":
            pytest.skip(f"Not enough HRV data: {hrv_result['flatt_esco'].days_available} days")

        serialized = {k: v.model_dump() for k, v in hrv_result.items()}
        _actor_update_hrv_analysis(serialized, user=user.model_dump(), dt=date.today())

        dt_str = date.today().isoformat()
        with get_sync_session() as session:
            fe_row = session.get(HrvAnalysis, (user.id, dt_str, "flatt_esco"))
            assert fe_row is not None
            assert fe_row.status == hrv_result["flatt_esco"].status


# ---------------------------------------------------------------------------
# _actor_record_training_log
# ---------------------------------------------------------------------------

_DT_LOG = date(2026, 4, 5)
_DT_LOG_STR = "2026-04-05"


class TestActorRecordTrainingLog:
    """_actor_record_training_log records PRE + fills POST."""

    def _mock_session(self, *, wellness_row=None):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        scalar_mock = MagicMock()
        scalar_mock.scalar_one_or_none.return_value = wellness_row
        session.execute.return_value = scalar_mock
        return session

    def _wellness_row(self, **overrides):
        row = MagicMock()
        row.hrv = overrides.get("hrv", 65.0)
        row.ctl = overrides.get("ctl", 55.0)
        row.atl = overrides.get("atl", 50.0)
        row.recovery_score = overrides.get("recovery_score", 75.0)
        row.recovery_category = overrides.get("recovery_category", "good")
        row.sleep_score = overrides.get("sleep_score", 72.0)
        return row

    def test_returns_early_when_no_wellness_row(self):
        """No wellness row → no TrainingLog.create called."""
        from tasks.actors.wellness import _actor_record_training_log

        user = _user()
        mock_session = self._mock_session(wellness_row=None)

        with (
            patch(
                "tasks.actors.wellness.get_sync_session",
                return_value=mock_session,
            ),
            patch("tasks.actors.wellness.TrainingLog.get_for_date") as mock_get,
        ):
            _actor_record_training_log(user.model_dump(), _DT_LOG)

        mock_get.assert_not_called()

    def test_skips_pre_if_existing_log(self):
        """Existing training log for the date → skip PRE creation."""
        from tasks.actors.wellness import _actor_record_training_log

        user = _user()
        wellness = self._wellness_row()
        mock_session = self._mock_session(wellness_row=wellness)

        with (
            patch(
                "tasks.actors.wellness.get_sync_session",
                return_value=mock_session,
            ),
            patch(
                "tasks.actors.wellness.TrainingLog.get_for_date",
                return_value=[MagicMock()],
            ),
            patch(
                "tasks.actors.wellness.TrainingLog.get_unfilled_post",
                return_value=[],
            ),
            patch("tasks.actors.wellness.TrainingLog.create") as mock_create,
        ):
            _actor_record_training_log(user.model_dump(), _DT_LOG)

        mock_create.assert_not_called()

    def test_creates_log_with_humango_source(self):
        """Scheduled workouts exist → source='humango'."""
        from tasks.actors.wellness import _actor_record_training_log

        user = _user()
        wellness = self._wellness_row()
        mock_session = self._mock_session(wellness_row=wellness)

        sw = MagicMock()
        sw.type = "Run"
        sw.name = "Easy Run"
        sw.description = "Zone 2 run"
        sw.moving_time = 3600

        with (
            patch(
                "tasks.actors.wellness.get_sync_session",
                return_value=mock_session,
            ),
            patch(
                "tasks.actors.wellness.TrainingLog.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.wellness.ScheduledWorkout.get_for_date",
                return_value=[sw],
            ),
            patch(
                "tasks.actors.wellness.AiWorkout.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.wellness.HrvAnalysis.get",
                return_value=None,
            ),
            patch(
                "tasks.actors.wellness.RhrAnalysis.get",
                return_value=None,
            ),
            patch(
                "tasks.actors.wellness.ActivityHrv.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.wellness.TrainingLog.create",
            ) as mock_create,
            patch(
                "tasks.actors.wellness.TrainingLog.get_unfilled_post",
                return_value=[],
            ),
        ):
            _actor_record_training_log(user.model_dump(), _DT_LOG)

        mock_create.assert_called_once()
        kwargs = mock_create.call_args[1]
        assert kwargs["source"] == "humango"
        assert kwargs["sport"] == "Run"

    def test_creates_log_with_ai_source(self):
        """Only AI workouts → source='ai'."""
        from tasks.actors.wellness import _actor_record_training_log

        user = _user()
        wellness = self._wellness_row()
        mock_session = self._mock_session(wellness_row=wellness)

        ai_w = MagicMock()
        ai_w.sport = "Ride"
        ai_w.name = "AI Ride"
        ai_w.duration_minutes = 60

        with (
            patch(
                "tasks.actors.wellness.get_sync_session",
                return_value=mock_session,
            ),
            patch(
                "tasks.actors.wellness.TrainingLog.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.wellness.ScheduledWorkout.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.wellness.AiWorkout.get_for_date",
                return_value=[ai_w],
            ),
            patch(
                "tasks.actors.wellness.HrvAnalysis.get",
                return_value=None,
            ),
            patch(
                "tasks.actors.wellness.RhrAnalysis.get",
                return_value=None,
            ),
            patch(
                "tasks.actors.wellness.ActivityHrv.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.wellness.TrainingLog.create",
            ) as mock_create,
            patch(
                "tasks.actors.wellness.TrainingLog.get_unfilled_post",
                return_value=[],
            ),
        ):
            _actor_record_training_log(user.model_dump(), _DT_LOG)

        mock_create.assert_called_once()
        kwargs = mock_create.call_args[1]
        assert kwargs["source"] == "ai"
        assert kwargs["sport"] == "Ride"

    def test_creates_log_with_none_source(self):
        """No scheduled or AI workouts → source='none'."""
        from tasks.actors.wellness import _actor_record_training_log

        user = _user()
        wellness = self._wellness_row()
        mock_session = self._mock_session(wellness_row=wellness)

        with (
            patch(
                "tasks.actors.wellness.get_sync_session",
                return_value=mock_session,
            ),
            patch(
                "tasks.actors.wellness.TrainingLog.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.wellness.ScheduledWorkout.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.wellness.AiWorkout.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.wellness.HrvAnalysis.get",
                return_value=None,
            ),
            patch(
                "tasks.actors.wellness.RhrAnalysis.get",
                return_value=None,
            ),
            patch(
                "tasks.actors.wellness.ActivityHrv.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.wellness.TrainingLog.create",
            ) as mock_create,
            patch(
                "tasks.actors.wellness.TrainingLog.get_unfilled_post",
                return_value=[],
            ),
        ):
            _actor_record_training_log(user.model_dump(), _DT_LOG)

        mock_create.assert_called_once()
        kwargs = mock_create.call_args[1]
        assert kwargs["source"] == "none"

    def test_post_fills_yesterday_recovery_delta(self):
        """POST phase: fills recovery_delta for yesterday's logs."""
        from tasks.actors.wellness import _actor_record_training_log

        user = _user()
        wellness = self._wellness_row(recovery_score=80.0, hrv=70.0)
        mock_session = self._mock_session(wellness_row=wellness)

        yesterday_log = MagicMock()
        yesterday_log.id = 42
        yesterday_log.date = str(_DT_LOG - __import__("datetime").timedelta(days=1))
        yesterday_log.pre_recovery_score = 70.0

        with (
            patch(
                "tasks.actors.wellness.get_sync_session",
                return_value=mock_session,
            ),
            patch(
                "tasks.actors.wellness.TrainingLog.get_for_date",
                return_value=[MagicMock()],
            ),
            patch(
                "tasks.actors.wellness.TrainingLog.get_unfilled_post",
                return_value=[yesterday_log],
            ),
            patch(
                "tasks.actors.wellness.HrvAnalysis.get",
                return_value=None,
            ),
            patch(
                "tasks.actors.wellness.RhrAnalysis.get",
                return_value=None,
            ),
            patch(
                "tasks.actors.wellness.ActivityHrv.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.wellness.TrainingLog.update",
            ) as mock_update,
        ):
            _actor_record_training_log(user.model_dump(), _DT_LOG)

        mock_update.assert_called_once()
        kwargs = mock_update.call_args[1]
        assert kwargs["post_recovery_score"] == 80.0
        assert kwargs["recovery_delta"] == 10.0
