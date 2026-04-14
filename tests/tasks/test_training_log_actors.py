"""Tests for training log actors: actor_fill_training_log, actor_fill_training_log_post."""

from datetime import date
from unittest.mock import MagicMock, patch

from data.db.user import UserDTO

_DT = date(2026, 4, 5)
_DT_STR = "2026-04-05"
_MODULE = "tasks.actors.training_log"


def _user(*, id: int = 1) -> UserDTO:
    return UserDTO(id=id, chat_id="111", username="tester", athlete_id="i001")


def _wellness_row(**overrides):
    row = MagicMock()
    row.hrv = overrides.get("hrv", 65.0)
    row.ctl = overrides.get("ctl", 55.0)
    row.atl = overrides.get("atl", 50.0)
    row.recovery_score = overrides.get("recovery_score", 75.0)
    row.recovery_category = overrides.get("recovery_category", "good")
    row.sleep_score = overrides.get("sleep_score", 72.0)
    return row


def _activity(*, id: str = "a001", type: str = "Run", moving_time: int = 3600):
    a = MagicMock()
    a.id = id
    a.type = type
    a.moving_time = moving_time
    a.average_hr = 145.0
    a.icu_training_load = 80.0
    return a


def _scheduled_workout(*, type: str = "Run", name: str = "Easy Run", moving_time: int = 3600):
    w = MagicMock()
    w.type = type
    w.name = name
    w.description = "Zone 2"
    w.moving_time = moving_time
    return w


def _log_obj(*, id: int = 1):
    log = MagicMock()
    log.id = id
    return log


# ---------------------------------------------------------------------------
# actor_fill_training_log
# ---------------------------------------------------------------------------


class TestActorFillTrainingLog:
    """actor_fill_training_log creates PRE + fills ACTUAL per activity."""

    def test_returns_early_when_no_activities(self):
        from tasks.actors.training_log import actor_fill_training_log

        user = _user()
        with patch(f"{_MODULE}.Activity.get_for_date", return_value=[]):
            actor_fill_training_log(user.model_dump(), _DT)

    def test_returns_early_when_no_wellness(self):
        from tasks.actors.training_log import actor_fill_training_log

        user = _user()
        activity = _activity()

        with (
            patch(f"{_MODULE}.Activity.get_for_date", return_value=[activity]),
            patch(f"{_MODULE}.Wellness.get", return_value=None),
        ):
            actor_fill_training_log(user.model_dump(), _DT)

    def test_skips_already_linked_activities(self):
        from tasks.actors.training_log import actor_fill_training_log

        user = _user()
        activity = _activity(id="a001")
        existing_log = MagicMock()
        existing_log.actual_activity_id = "a001"

        with (
            patch(f"{_MODULE}.Activity.get_for_date", return_value=[activity]),
            patch(f"{_MODULE}.Wellness.get", return_value=_wellness_row()),
            patch(f"{_MODULE}.TrainingLog.get_for_date", return_value=[existing_log]),
            patch(f"{_MODULE}.TrainingLog.create") as mock_create,
        ):
            actor_fill_training_log(user.model_dump(), _DT)

        mock_create.assert_not_called()

    def test_creates_log_with_humango_source(self):
        from tasks.actors.training_log import actor_fill_training_log

        user = _user()
        activity = _activity(type="Run")
        sw = _scheduled_workout(type="Run")

        with (
            patch(f"{_MODULE}.Activity.get_for_date", return_value=[activity]),
            patch(f"{_MODULE}.Wellness.get", return_value=_wellness_row()),
            patch(f"{_MODULE}.TrainingLog.get_for_date", return_value=[]),
            patch(f"{_MODULE}.HrvAnalysis.get", return_value=None),
            patch(f"{_MODULE}.RhrAnalysis.get", return_value=None),
            patch(f"{_MODULE}.ActivityHrv.get_for_date", return_value=[]),
            patch(f"{_MODULE}.ScheduledWorkout.get_for_date", return_value=[sw]),
            patch(f"{_MODULE}.AiWorkout.get_for_date", return_value=[]),
            patch(f"{_MODULE}.TrainingLog.create", return_value=_log_obj()) as mock_create,
            patch(f"{_MODULE}.detect_compliance", return_value="followed_original"),
            patch(f"{_MODULE}.compute_max_zone_sync", return_value="Z2"),
            patch(f"{_MODULE}.TrainingLog.update"),
        ):
            actor_fill_training_log(user.model_dump(), _DT)

        mock_create.assert_called_once()
        kwargs = mock_create.call_args[1]
        assert kwargs["source"] == "humango"
        assert kwargs["sport"] == "Run"

    def test_creates_log_with_unplanned_compliance(self):
        from tasks.actors.training_log import actor_fill_training_log

        user = _user()
        activity = _activity(type="Run")

        with (
            patch(f"{_MODULE}.Activity.get_for_date", return_value=[activity]),
            patch(f"{_MODULE}.Wellness.get", return_value=_wellness_row()),
            patch(f"{_MODULE}.TrainingLog.get_for_date", return_value=[]),
            patch(f"{_MODULE}.HrvAnalysis.get", return_value=None),
            patch(f"{_MODULE}.RhrAnalysis.get", return_value=None),
            patch(f"{_MODULE}.ActivityHrv.get_for_date", return_value=[]),
            patch(f"{_MODULE}.ScheduledWorkout.get_for_date", return_value=[]),
            patch(f"{_MODULE}.AiWorkout.get_for_date", return_value=[]),
            patch(f"{_MODULE}.TrainingLog.create", return_value=_log_obj()) as mock_create,
            patch(f"{_MODULE}.compute_max_zone_sync", return_value="Z1"),
            patch(f"{_MODULE}.TrainingLog.update") as mock_update,
        ):
            actor_fill_training_log(user.model_dump(), _DT)

        kwargs = mock_create.call_args[1]
        assert kwargs["source"] == "none"
        update_kwargs = mock_update.call_args[1]
        assert update_kwargs["compliance"] == "unplanned"

    def test_fills_actual_data(self):
        from tasks.actors.training_log import actor_fill_training_log

        user = _user()
        activity = _activity(type="Ride", moving_time=5400)
        sw = _scheduled_workout(type="Ride", moving_time=5400)

        with (
            patch(f"{_MODULE}.Activity.get_for_date", return_value=[activity]),
            patch(f"{_MODULE}.Wellness.get", return_value=_wellness_row()),
            patch(f"{_MODULE}.TrainingLog.get_for_date", return_value=[]),
            patch(f"{_MODULE}.HrvAnalysis.get", return_value=None),
            patch(f"{_MODULE}.RhrAnalysis.get", return_value=None),
            patch(f"{_MODULE}.ActivityHrv.get_for_date", return_value=[]),
            patch(f"{_MODULE}.ScheduledWorkout.get_for_date", return_value=[sw]),
            patch(f"{_MODULE}.AiWorkout.get_for_date", return_value=[]),
            patch(f"{_MODULE}.TrainingLog.create", return_value=_log_obj()),
            patch(f"{_MODULE}.detect_compliance", return_value="followed_original"),
            patch(f"{_MODULE}.compute_max_zone_sync", return_value="Z2"),
            patch(f"{_MODULE}.TrainingLog.update") as mock_update,
        ):
            actor_fill_training_log(user.model_dump(), _DT)

        mock_update.assert_called_once()
        kwargs = mock_update.call_args[1]
        assert kwargs["actual_activity_id"] == "a001"
        assert kwargs["actual_duration_sec"] == 5400
        assert kwargs["actual_max_zone_time"] == "Z2"
        assert kwargs["compliance"] == "followed_original"


# ---------------------------------------------------------------------------
# actor_fill_training_log_post
# ---------------------------------------------------------------------------


class TestActorFillTrainingLogPost:
    """actor_fill_training_log_post fills POST outcome from next day's wellness."""

    def test_returns_early_when_no_wellness(self):
        from tasks.actors.training_log import actor_fill_training_log_post

        user = _user()
        with patch(f"{_MODULE}.Wellness.get", return_value=None):
            actor_fill_training_log_post(user.model_dump(), _DT)

    def test_returns_early_when_no_unfilled(self):
        from tasks.actors.training_log import actor_fill_training_log_post

        user = _user()
        with (
            patch(f"{_MODULE}.Wellness.get", return_value=_wellness_row()),
            patch(f"{_MODULE}.TrainingLog.get_unfilled_post", return_value=[]),
        ):
            actor_fill_training_log_post(user.model_dump(), _DT)

    def test_fills_post_recovery_delta(self):
        from tasks.actors.training_log import actor_fill_training_log_post

        user = _user()
        wellness = _wellness_row(recovery_score=80.0, hrv=70.0)

        yesterday_log = MagicMock()
        yesterday_log.id = 42
        yesterday_log.pre_recovery_score = 70.0

        hrv_mock = MagicMock(rmssd_7d=60.0)
        rhr_mock = MagicMock(rhr_today=65.0)

        with (
            patch(f"{_MODULE}.Wellness.get", return_value=wellness),
            patch(f"{_MODULE}.TrainingLog.get_unfilled_post", return_value=[yesterday_log]),
            patch(f"{_MODULE}.HrvAnalysis.get", return_value=hrv_mock),
            patch(f"{_MODULE}.RhrAnalysis.get", return_value=rhr_mock),
            patch(f"{_MODULE}.ActivityHrv.get_for_date", return_value=[]),
            patch(f"{_MODULE}.TrainingLog.update") as mock_update,
        ):
            actor_fill_training_log_post(user.model_dump(), _DT)

        mock_update.assert_called_once()
        kwargs = mock_update.call_args[1]
        assert kwargs["post_recovery_score"] == 80.0
        assert kwargs["recovery_delta"] == 10.0

    def test_fills_post_sleep_score(self):
        from tasks.actors.training_log import actor_fill_training_log_post

        user = _user()
        wellness = _wellness_row(sleep_score=85.0)

        log = MagicMock()
        log.id = 1
        log.pre_recovery_score = 60.0

        hrv_mock = MagicMock(rmssd_7d=50.0)
        rhr_mock = MagicMock(rhr_today=68.0)

        with (
            patch(f"{_MODULE}.Wellness.get", return_value=wellness),
            patch(f"{_MODULE}.TrainingLog.get_unfilled_post", return_value=[log]),
            patch(f"{_MODULE}.HrvAnalysis.get", return_value=hrv_mock),
            patch(f"{_MODULE}.RhrAnalysis.get", return_value=rhr_mock),
            patch(f"{_MODULE}.ActivityHrv.get_for_date", return_value=[]),
            patch(f"{_MODULE}.TrainingLog.update") as mock_update,
        ):
            actor_fill_training_log_post(user.model_dump(), _DT)

        kwargs = mock_update.call_args[1]
        assert kwargs["post_sleep_score"] == 85.0
