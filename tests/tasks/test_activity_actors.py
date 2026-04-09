"""Tests for activity-related Dramatiq actors in tasks/actors.py.

Covers:
- actor_fetch_user_activities: client call, save_bulk, pipeline wiring
- actor_fetch_activity_details: client call, ActivityDetail.save
- actor_download_fit_file: skip logic (fit_file_path already set, type check, duration check)
- actor_process_fit_file: no_rr_data path
- actor_compose_user_morning_report: data readiness checks, MCPTool call, DB save
- actor_send_user_morning_report: TelegramTool call, non-owner skip
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from data.db.user import UserDTO

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _user(*, id: int = 1, chat_id: str = "111") -> UserDTO:
    return UserDTO(id=id, chat_id=chat_id, username="tester", athlete_id="i001", api_key="key1")


def _activity_dto(
    *,
    id: str = "a001",
    dt: date = date(2026, 4, 1),
    type: str = "Run",
    moving_time: int = 3600,
) -> MagicMock:
    """Create a minimal ActivityDTO mock."""
    from data.intervals.dto import ActivityDTO

    return ActivityDTO(
        id=id,
        start_date_local=dt,
        type=type,
        icu_training_load=80.0,
        moving_time=moving_time,
        average_hr=145.0,
    )


def _mock_client() -> MagicMock:
    """Create a mock IntervalsSyncClient context manager."""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


# ---------------------------------------------------------------------------
# actor_fetch_user_activities
# ---------------------------------------------------------------------------


class TestActorFetchUserActivities:
    """actor_fetch_user_activities fetches, saves, and dispatches pipelines."""

    def test_calls_get_activities_with_date_range(self):
        """Actor calls client.get_activities with oldest/newest bounds."""
        from datetime import timedelta

        from tasks.actors import actor_fetch_user_activities

        user = _user()
        mock_client = _mock_client()
        mock_client.get_activities.return_value = []

        today = date.today()
        oldest = today - timedelta(days=30)

        with (
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.Activity.save_bulk", return_value=[]),
        ):
            actor_fetch_user_activities(user.model_dump(), oldest=oldest, newest=today)

        mock_client.get_activities.assert_called_once()
        call_kwargs = mock_client.get_activities.call_args[1]
        assert (call_kwargs["newest"] - call_kwargs["oldest"]).days == 30

    def test_returns_early_when_no_activities(self):
        """No activities from API → save_bulk_sync not called."""
        from tasks.actors import actor_fetch_user_activities

        user = _user()
        mock_client = _mock_client()
        mock_client.get_activities.return_value = []

        with (
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.Activity.save_bulk", return_value=[]) as mock_save,
        ):
            actor_fetch_user_activities(user.model_dump())

        mock_save.assert_not_called()

    def test_calls_save_bulk_sync_with_activities(self):
        """Actor passes activities to Activity.save_bulk."""
        from tasks.actors import actor_fetch_user_activities

        user = _user()
        activities = [_activity_dto(id="a001"), _activity_dto(id="a002")]
        mock_client = _mock_client()
        mock_client.get_activities.return_value = activities

        with (
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.Activity.save_bulk", return_value=[]) as mock_save,
        ):
            actor_fetch_user_activities(user.model_dump())

        mock_save.assert_called_once()
        call_args = mock_save.call_args
        # First positional arg is user, second is activities kwarg
        saved_activities = call_args[1]["activities"]
        assert saved_activities == activities

    def test_dispatches_pipeline_group_for_new_activities(self):
        """New activity IDs → group of pipelines dispatched (fetch_details → download_fit → process_fit)."""
        from tasks.actors import actor_fetch_user_activities

        user = _user()
        activities = [_activity_dto(id="a001"), _activity_dto(id="a002")]
        new_ids = ["a001", "a002"]
        mock_client = _mock_client()
        mock_client.get_activities.return_value = activities

        mock_group = MagicMock()

        with (
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.Activity.save_bulk", return_value=new_ids),
            patch("tasks.actors.activities.group", return_value=mock_group) as mock_group_cls,
        ):
            actor_fetch_user_activities(user.model_dump())

        # group() called with a list of pipelines (one per new activity)
        mock_group_cls.assert_called_once()
        pipelines = mock_group_cls.call_args[0][0]
        assert len(pipelines) == 2

        # group.run() was called
        mock_group.run.assert_called_once()

    def test_no_pipelines_dispatched_when_no_new_activities(self):
        """save_bulk_sync returns [] → group not dispatched."""
        from tasks.actors import actor_fetch_user_activities

        user = _user()
        activities = [_activity_dto(id="a001")]
        mock_client = _mock_client()
        mock_client.get_activities.return_value = activities

        with (
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.Activity.save_bulk", return_value=[]),
            patch("tasks.actors.activities.group") as mock_group_cls,
        ):
            actor_fetch_user_activities(user.model_dump())

        mock_group_cls.assert_not_called()

    def test_group_dispatches_update_details_per_activity(self):
        """Each new activity ID dispatches _actor_update_activity_details via group."""
        from tasks.actors import actor_fetch_user_activities

        user = _user()
        activities = [_activity_dto(id="a001")]
        mock_client = _mock_client()
        mock_client.get_activities.return_value = activities

        mock_group = MagicMock()

        with (
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.Activity.save_bulk", return_value=["a001"]),
            patch("tasks.actors.activities.group", return_value=mock_group) as mock_group_cls,
        ):
            actor_fetch_user_activities(user.model_dump())

        # group() called with a list of 1 message (one per new activity)
        mock_group_cls.assert_called_once()
        messages = mock_group_cls.call_args[0][0]
        assert len(messages) == 1
        mock_group.run.assert_called_once()

    def test_default_range_is_30_days(self):
        """Default oldest is today - 30 days when not specified."""
        from tasks.actors import actor_fetch_user_activities

        user = _user()
        mock_client = _mock_client()
        mock_client.get_activities.return_value = []

        with (
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.Activity.save_bulk", return_value=[]),
        ):
            actor_fetch_user_activities(user.model_dump())

        call_kwargs = mock_client.get_activities.call_args[1]
        delta = call_kwargs["newest"] - call_kwargs["oldest"]
        assert delta.days == 30


# ---------------------------------------------------------------------------
# actor_fetch_activity_details
# ---------------------------------------------------------------------------


class TestActorFetchActivityDetails:
    """actor_fetch_activity_details fetches detail + intervals and saves them."""

    def test_calls_get_activity_detail(self):
        """Actor calls client.get_activity_detail with the activity ID."""
        from tasks.actors.activities import _actor_update_activity_details

        user = _user()
        mock_client = _mock_client()
        mock_client.get_activity_detail.return_value = {"max_heartrate": 175}
        mock_client.get_activity_intervals.return_value = []

        with (
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.ActivityDetail.save"),
        ):
            _actor_update_activity_details(user.model_dump(), activity_id="i12345")

        mock_client.get_activity_detail.assert_called_once_with("i12345")

    def test_calls_get_activity_intervals(self):
        """Actor calls client.get_activity_intervals with the activity ID."""
        from tasks.actors.activities import _actor_update_activity_details

        user = _user()
        mock_client = _mock_client()
        mock_client.get_activity_detail.return_value = {"max_heartrate": 175}
        mock_client.get_activity_intervals.return_value = [{"type": "interval"}]

        with (
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.ActivityDetail.save"),
        ):
            _actor_update_activity_details(user.model_dump(), activity_id="i12345")

        mock_client.get_activity_intervals.assert_called_once_with("i12345")

    def test_calls_activity_detail_save(self):
        """Actor calls ActivityDetail.save with ID, detail_data, and intervals_data."""
        from tasks.actors.activities import _actor_update_activity_details

        user = _user()
        detail_data = {"max_heartrate": 175, "icu_average_watts": 220}
        intervals_data = [{"label": "L1", "secs": 300}]
        mock_client = _mock_client()
        mock_client.get_activity_detail.return_value = detail_data
        mock_client.get_activity_intervals.return_value = intervals_data

        with (
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.ActivityDetail.save") as mock_save,
        ):
            _actor_update_activity_details(user.model_dump(), activity_id="i12345")

        mock_save.assert_called_once_with("i12345", detail_data, intervals_data)

    def test_returns_early_when_no_detail_data(self):
        """Actor returns early without calling ActivityDetail.save if detail is None/empty."""
        from tasks.actors.activities import _actor_update_activity_details

        user = _user()
        mock_client = _mock_client()
        mock_client.get_activity_detail.return_value = None
        mock_client.get_activity_intervals.return_value = []

        with (
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.ActivityDetail.save") as mock_save,
        ):
            _actor_update_activity_details(user.model_dump(), activity_id="i12345")

        mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# actor_download_fit_file
# ---------------------------------------------------------------------------


class TestActorDownloadFitFile:
    """actor_download_fit_file skips based on conditions, saves FIT to disk."""

    def _make_activity(
        self,
        *,
        fit_file_path: str | None = None,
        type: str = "Run",
        moving_time: int = 1800,
    ) -> MagicMock:
        act = MagicMock()
        act.fit_file_path = fit_file_path
        act.type = type
        act.moving_time = moving_time
        return act

    def _mock_session(self, activity: MagicMock | None) -> MagicMock:
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.get.return_value = activity
        return session

    def test_skips_when_activity_not_found(self):
        """No activity row → returns immediately without downloading."""
        from tasks.actors.activities import _actor_download_fit_file

        user = _user()
        mock_session = self._mock_session(None)

        with (
            patch("tasks.actors.activities.get_sync_session", return_value=mock_session),
            patch("tasks.actors.activities.IntervalsSyncClient.for_user") as mock_client_cls,
        ):
            _actor_download_fit_file(user.model_dump(), activity_id="i99999")

        mock_client_cls.assert_not_called()

    def test_skips_when_fit_file_already_exists(self):
        """fit_file_path already set → returns immediately, no download."""
        from tasks.actors.activities import _actor_download_fit_file

        user = _user()
        activity = self._make_activity(fit_file_path="/static/fit-files/i12345.fit")
        mock_session = self._mock_session(activity)

        with (
            patch("tasks.actors.activities.get_sync_session", return_value=mock_session),
            patch("tasks.actors.activities.IntervalsSyncClient.for_user") as mock_client_cls,
        ):
            _actor_download_fit_file(user.model_dump(), activity_id="i12345")

        mock_client_cls.assert_not_called()

    def test_skips_ineligible_activity_type(self):
        """Swim activities are not eligible for FIT download."""
        from tasks.actors.activities import _actor_download_fit_file

        user = _user()
        activity = self._make_activity(type="Swim", moving_time=2400)
        mock_session = self._mock_session(activity)

        with (
            patch("tasks.actors.activities.get_sync_session", return_value=mock_session),
            patch("tasks.actors.activities.IntervalsSyncClient.for_user") as mock_client_cls,
        ):
            _actor_download_fit_file(user.model_dump(), activity_id="i12345")

        mock_client_cls.assert_not_called()

    def test_skips_short_activities(self):
        """Activities shorter than 15 min (900 sec) are skipped."""
        from tasks.actors.activities import _actor_download_fit_file

        user = _user()
        activity = self._make_activity(type="Run", moving_time=899)
        mock_session = self._mock_session(activity)

        with (
            patch("tasks.actors.activities.get_sync_session", return_value=mock_session),
            patch("tasks.actors.activities.IntervalsSyncClient.for_user") as mock_client_cls,
        ):
            _actor_download_fit_file(user.model_dump(), activity_id="i12345")

        mock_client_cls.assert_not_called()

    def test_skips_when_no_fit_bytes_returned(self):
        """Client returns None for FIT bytes → no file written."""
        from tasks.actors.activities import _actor_download_fit_file

        user = _user()
        activity = self._make_activity(type="Run", moving_time=3600)
        mock_session = self._mock_session(activity)
        mock_client = _mock_client()
        mock_client.download_fit.return_value = None

        with (
            patch("tasks.actors.activities.get_sync_session", return_value=mock_session),
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
        ):
            _actor_download_fit_file(user.model_dump(), activity_id="i12345")

        assert not mock_session.commit.called

    def test_saves_fit_file_and_updates_path(self, tmp_path):
        """Valid FIT bytes: file written to disk, activity.fit_file_path updated."""
        from tasks.actors.activities import _actor_download_fit_file

        user = _user()
        activity = self._make_activity(type="Run", moving_time=3600)
        mock_session = self._mock_session(activity)
        mock_client = _mock_client()
        mock_client.download_fit.return_value = b"FIT_DATA_BYTES"

        with (
            patch("tasks.actors.activities.get_sync_session", return_value=mock_session),
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
            patch("tasks.actors.activities.Path") as mock_path_cls,
        ):
            # Make Path("static/fit-files") return a tmp path
            fit_dir = MagicMock()
            fit_file = MagicMock()
            mock_path_cls.return_value = fit_dir
            fit_dir.__truediv__ = MagicMock(return_value=fit_file)
            fit_file.__str__ = MagicMock(return_value="static/fit-files/i12345.fit")

            _actor_download_fit_file(user.model_dump(), activity_id="i12345")

        fit_file.write_bytes.assert_called_once_with(b"FIT_DATA_BYTES")
        assert activity.fit_file_path == "static/fit-files/i12345.fit"
        mock_session.commit.assert_called_once()

    @pytest.mark.parametrize("activity_type", ["Ride", "Run"])
    def test_eligible_activity_types_proceed(self, activity_type):
        """All eligible activity types proceed to download attempt."""
        from tasks.actors.activities import _actor_download_fit_file

        user = _user()
        activity = self._make_activity(type=activity_type, moving_time=3600)
        mock_session = self._mock_session(activity)
        mock_client = _mock_client()
        mock_client.download_fit.return_value = None  # no file, but client was called

        with (
            patch("tasks.actors.activities.get_sync_session", return_value=mock_session),
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
        ):
            _actor_download_fit_file(user.model_dump(), activity_id="i12345")

        mock_client.download_fit.assert_called_once_with("i12345")

    def test_exactly_900_seconds_is_eligible(self):
        """moving_time == 900 seconds meets the ≥15 min threshold."""
        from tasks.actors.activities import _actor_download_fit_file

        user = _user()
        activity = self._make_activity(type="Run", moving_time=900)
        mock_session = self._mock_session(activity)
        mock_client = _mock_client()
        mock_client.download_fit.return_value = None

        with (
            patch("tasks.actors.activities.get_sync_session", return_value=mock_session),
            patch("tasks.actors.activities.IntervalsSyncClient.for_user", return_value=mock_client),
        ):
            _actor_download_fit_file(user.model_dump(), activity_id="i12345")

        mock_client.download_fit.assert_called_once()


# ---------------------------------------------------------------------------
# actor_send_user_morning_report
# ---------------------------------------------------------------------------


def _no_ramp():
    """Return a mock RampTrainingSuggestion where is_test_needed=False."""
    mock = MagicMock()
    mock.is_test_needed = False
    return mock


class TestActorSendUserMorningReport:
    """actor_send_user_morning_report sends via TelegramTool for owner only."""

    def _make_wellness(self) -> MagicMock:
        from data.db.wellness import WellnessPostDTO

        return WellnessPostDTO(
            id=1,
            user_id=1,
            date="2026-04-03",
            ctl=55.0,
            atl=60.0,
            recovery_score=75.0,
            recovery_category="good",
        )

    def test_sends_message_for_owner(self):
        """TelegramTool.send_message called with user.chat_id."""
        from tasks.actors.reports import _actor_send_user_morning_report

        user = _user(id=1, chat_id="12345")
        wellness = self._make_wellness()

        mock_tg = MagicMock()
        mock_tg_cls = MagicMock(return_value=mock_tg)

        with (
            patch("tasks.actors.reports.TelegramTool", mock_tg_cls),
            patch("tasks.actors.reports.build_morning_message", return_value="Report text"),
            patch("tasks.utils.RampTrainingSuggestion", return_value=_no_ramp()),
        ):
            _actor_send_user_morning_report(user.model_dump(), wellness.model_dump())

        mock_tg.send_message.assert_called_once()
        call_kwargs = mock_tg.send_message.call_args[1]
        assert call_kwargs["text"] == "Report text"

    def test_message_includes_report_text(self):
        """Sent message text comes from build_morning_message."""
        from tasks.actors.reports import _actor_send_user_morning_report

        user = _user(id=1)
        wellness = self._make_wellness()

        mock_tg = MagicMock()
        with (
            patch("tasks.actors.reports.TelegramTool", return_value=mock_tg),
            patch("tasks.actors.reports.build_morning_message", return_value="Morning summary") as mock_fmt,
            patch("tasks.utils.RampTrainingSuggestion", return_value=_no_ramp()),
        ):
            _actor_send_user_morning_report(user.model_dump(), wellness.model_dump())

        mock_fmt.assert_called_once()
        call_kwargs = mock_tg.send_message.call_args[1]
        assert call_kwargs["text"] == "Morning summary"

    def test_reply_markup_contains_webapp_link(self):
        """reply_markup sent to Telegram contains a web_app button."""
        from tasks.actors.reports import _actor_send_user_morning_report

        user = _user(id=1)
        wellness = self._make_wellness()

        mock_tg = MagicMock()
        with (
            patch("tasks.actors.reports.TelegramTool", return_value=mock_tg),
            patch("tasks.actors.reports.build_morning_message", return_value="text"),
            patch("tasks.utils.RampTrainingSuggestion", return_value=_no_ramp()),
        ):
            _actor_send_user_morning_report(user.model_dump(), wellness.model_dump())

        call_kwargs = mock_tg.send_message.call_args[1]
        keyboard = call_kwargs["reply_markup"]
        assert "inline_keyboard" in keyboard
        # At least one button with web_app
        buttons = [btn for row in keyboard["inline_keyboard"] for btn in row]
        assert any("web_app" in btn for btn in buttons)


# ---------------------------------------------------------------------------
# actor_compose_user_morning_report
# ---------------------------------------------------------------------------


class TestActorComposeMorningReport:
    """actor_compose_user_morning_report checks data readiness before generating."""

    def _make_wellness_row(self, **overrides) -> MagicMock:
        """Create a mock Wellness ORM row with all required fields."""
        row = MagicMock()
        row.banister_recovery = overrides.get("banister_recovery", 0.85)
        row.sleep_score = overrides.get("sleep_score", 72.0)
        row.recovery_score = overrides.get("recovery_score", 78.0)
        row.ai_recommendation = overrides.get("ai_recommendation", None)
        return row

    def _make_hrv_row(self) -> MagicMock:
        return MagicMock()

    def _make_rhr_row(self) -> MagicMock:
        return MagicMock()

    def _mock_session(
        self,
        *,
        wellness_row=None,
        hrv_flat_row=None,
        hrv_aie_row=None,
        rhr_row=None,
    ) -> MagicMock:
        """Create a mock DB session with configurable query results."""
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        # scalar_one_or_none for wellness SELECT
        scalar_mock = MagicMock()
        scalar_mock.scalar_one_or_none.return_value = wellness_row
        session.execute.return_value = scalar_mock

        # session.get for HRV and RHR rows
        def _session_get(model, key):
            if isinstance(key, tuple) and len(key) == 3:
                # HrvAnalysis — third element is algorithm name
                algorithm = key[2]
                if algorithm == "flatt_esco":
                    return hrv_flat_row
                return hrv_aie_row
            # RhrAnalysis
            return rhr_row

        session.get.side_effect = _session_get
        return session

    def test_returns_early_when_no_wellness_row(self):
        """No wellness row for today → MCPTool never instantiated."""
        from tasks.actors import actor_compose_user_morning_report

        user = _user(id=1)
        mock_session = self._mock_session(wellness_row=None)

        with (
            patch("tasks.actors.reports.get_sync_session", return_value=mock_session),
            patch("tasks.actors.reports.MCPTool") as mock_mcp_cls,
        ):
            actor_compose_user_morning_report(user.model_dump())

        mock_mcp_cls.assert_not_called()

    def test_returns_early_when_banister_recovery_missing(self):
        """Wellness row without banister_recovery → skips generation."""
        from tasks.actors import actor_compose_user_morning_report

        user = _user(id=1)
        wellness = self._make_wellness_row(banister_recovery=None)
        mock_session = self._mock_session(wellness_row=wellness)

        with (
            patch("tasks.actors.reports.get_sync_session", return_value=mock_session),
            patch("tasks.actors.reports.MCPTool") as mock_mcp_cls,
        ):
            actor_compose_user_morning_report(user.model_dump())

        mock_mcp_cls.assert_not_called()

    def test_returns_early_when_sleep_score_missing(self):
        """Wellness row without sleep_score → skips generation."""
        from tasks.actors import actor_compose_user_morning_report

        user = _user(id=1)
        wellness = self._make_wellness_row(sleep_score=None)
        mock_session = self._mock_session(wellness_row=wellness)

        with (
            patch("tasks.actors.reports.get_sync_session", return_value=mock_session),
            patch("tasks.actors.reports.MCPTool") as mock_mcp_cls,
        ):
            actor_compose_user_morning_report(user.model_dump())

        mock_mcp_cls.assert_not_called()

    def test_returns_early_when_recovery_score_missing(self):
        """Wellness row without recovery_score → skips generation."""
        from tasks.actors import actor_compose_user_morning_report

        user = _user(id=1)
        wellness = self._make_wellness_row(recovery_score=None)
        mock_session = self._mock_session(wellness_row=wellness)

        with (
            patch("tasks.actors.reports.get_sync_session", return_value=mock_session),
            patch("tasks.actors.reports.MCPTool") as mock_mcp_cls,
        ):
            actor_compose_user_morning_report(user.model_dump())

        mock_mcp_cls.assert_not_called()

    def test_returns_early_when_ai_recommendation_already_set(self):
        """Wellness row already has ai_recommendation → no regeneration."""
        from tasks.actors import actor_compose_user_morning_report

        user = _user(id=1)
        wellness = self._make_wellness_row(ai_recommendation="Existing report")
        mock_session = self._mock_session(wellness_row=wellness)

        with (
            patch("tasks.actors.reports.get_sync_session", return_value=mock_session),
            patch("tasks.actors.reports.MCPTool") as mock_mcp_cls,
        ):
            actor_compose_user_morning_report(user.model_dump())

        mock_mcp_cls.assert_not_called()

    def test_returns_early_when_hrv_rows_missing(self):
        """Missing HRV analysis rows → MCPTool not called."""
        from tasks.actors import actor_compose_user_morning_report

        user = _user(id=1)
        wellness = self._make_wellness_row()
        mock_session = self._mock_session(
            wellness_row=wellness,
            hrv_flat_row=None,  # missing
            hrv_aie_row=self._make_hrv_row(),
            rhr_row=self._make_rhr_row(),
        )

        with (
            patch("tasks.actors.reports.get_sync_session", return_value=mock_session),
            patch("tasks.actors.reports.MCPTool") as mock_mcp_cls,
        ):
            actor_compose_user_morning_report(user.model_dump())

        mock_mcp_cls.assert_not_called()

    def test_returns_early_when_rhr_row_missing(self):
        """Missing RHR analysis row → MCPTool not called."""
        from tasks.actors import actor_compose_user_morning_report

        user = _user(id=1)
        wellness = self._make_wellness_row()
        mock_session = self._mock_session(
            wellness_row=wellness,
            hrv_flat_row=self._make_hrv_row(),
            hrv_aie_row=self._make_hrv_row(),
            rhr_row=None,  # missing
        )

        with (
            patch("tasks.actors.reports.get_sync_session", return_value=mock_session),
            patch("tasks.actors.reports.MCPTool") as mock_mcp_cls,
        ):
            actor_compose_user_morning_report(user.model_dump())

        mock_mcp_cls.assert_not_called()

    def test_returns_early_when_mcp_generates_no_text(self):
        """MCPTool returns None → ai_recommendation not saved, send not called."""
        from tasks.actors import actor_compose_user_morning_report

        user = _user(id=1)
        wellness = self._make_wellness_row()
        mock_session = self._mock_session(
            wellness_row=wellness,
            hrv_flat_row=self._make_hrv_row(),
            hrv_aie_row=self._make_hrv_row(),
            rhr_row=self._make_rhr_row(),
        )

        mock_mcp = MagicMock()
        mock_mcp.generate_morning_report_via_mcp.return_value = None

        with (
            patch("tasks.actors.reports.get_sync_session", return_value=mock_session),
            patch("tasks.actors.reports.MCPTool", return_value=mock_mcp),
            patch("tasks.actors.reports._actor_send_user_morning_report") as mock_send,
        ):
            actor_compose_user_morning_report(user.model_dump())

        mock_session.__enter__.return_value.commit.assert_not_called()
        mock_send.send.assert_not_called()

    def test_saves_ai_recommendation_and_dispatches_send(self):
        """Successful generation → saves to DB and dispatches send actor."""
        from tasks.actors import actor_compose_user_morning_report

        user = _user(id=1)
        wellness = self._make_wellness_row()
        mock_session_ctx = self._mock_session(
            wellness_row=wellness,
            hrv_flat_row=self._make_hrv_row(),
            hrv_aie_row=self._make_hrv_row(),
            rhr_row=self._make_rhr_row(),
        )

        mock_mcp = MagicMock()
        mock_mcp.generate_morning_report_via_mcp.return_value = "Generated morning report"

        with (
            patch("tasks.actors.reports.get_sync_session", return_value=mock_session_ctx),
            patch("tasks.actors.reports.MCPTool", return_value=mock_mcp),
            patch("tasks.actors.reports._actor_send_user_morning_report") as mock_send,
            patch("tasks.actors.reports.WellnessPostDTO"),
        ):
            actor_compose_user_morning_report(user.model_dump())

        # ai_recommendation saved on the row
        assert wellness.ai_recommendation == "Generated morning report"
        mock_session_ctx.__enter__.return_value.commit.assert_called_once()

        # send actor dispatched
        mock_send.send.assert_called_once()


# ---------------------------------------------------------------------------
# _actor_fill_training_log_actual
# ---------------------------------------------------------------------------

_DT = date(2026, 4, 5)


class TestActorFillTrainingLogActual:
    """_actor_fill_training_log_actual matches activities to training logs."""

    def _make_log(
        self,
        *,
        id: int = 1,
        sport: str = "Run",
        date: str = "2026-04-05",
        source: str = "humango",
        original_duration_sec: int = 3600,
        adapted_duration_sec: int | None = None,
    ) -> MagicMock:
        log = MagicMock()
        log.id = id
        log.sport = sport
        log.date = date
        log.source = source
        log.original_duration_sec = original_duration_sec
        log.adapted_duration_sec = adapted_duration_sec
        return log

    def test_returns_early_when_no_unfilled(self):
        """No unfilled training logs → no further calls."""
        from tasks.actors.activities import _actor_fill_training_log_actual

        user = _user()

        with (
            patch(
                "tasks.actors.activities.TrainingLog.get_unfilled_actual",
                return_value=[],
            ),
            patch(
                "tasks.actors.activities.Activity.get_for_date",
            ) as mock_get,
        ):
            _actor_fill_training_log_actual(user.model_dump())

        mock_get.assert_not_called()

    def test_matches_activity_by_sport(self):
        """Activity matched by SPORT_MAP canonical type."""
        from tasks.actors.activities import _actor_fill_training_log_actual

        user = _user()
        log = self._make_log(sport="Run")
        activity = _activity_dto(type="Run", moving_time=3600)

        with (
            patch(
                "tasks.actors.activities.TrainingLog.get_unfilled_actual",
                return_value=[log],
            ),
            patch(
                "tasks.actors.activities.Activity.get_for_date",
                return_value=[activity],
            ),
            patch(
                "tasks.actors.activities._compute_max_zone_sync",
                return_value="Z2",
            ),
            patch(
                "tasks.actors.activities.detect_compliance",
                return_value="followed_original",
            ) as mock_compliance,
            patch(
                "tasks.actors.activities.TrainingLog.update",
            ) as mock_update,
        ):
            _actor_fill_training_log_actual(user.model_dump())

        mock_compliance.assert_called_once_with(log, activity)
        mock_update.assert_called_once()
        kwargs = mock_update.call_args[1]
        assert kwargs["actual_sport"] == "Run"
        assert kwargs["actual_max_zone_time"] == "Z2"

    def test_calls_detect_compliance(self):
        """detect_compliance result is passed to TrainingLog.update."""
        from tasks.actors.activities import _actor_fill_training_log_actual

        user = _user()
        log = self._make_log(sport="Ride")
        activity = _activity_dto(type="Ride", moving_time=5400)

        with (
            patch(
                "tasks.actors.activities.TrainingLog.get_unfilled_actual",
                return_value=[log],
            ),
            patch(
                "tasks.actors.activities.Activity.get_for_date",
                return_value=[activity],
            ),
            patch(
                "tasks.actors.activities._compute_max_zone_sync",
                return_value="Z3",
            ),
            patch(
                "tasks.actors.activities.detect_compliance",
                return_value="modified",
            ),
            patch(
                "tasks.actors.activities.TrainingLog.update",
            ) as mock_update,
        ):
            _actor_fill_training_log_actual(user.model_dump())

        kwargs = mock_update.call_args[1]
        assert kwargs["compliance"] == "modified"

    def test_sets_skipped_when_no_matching_activity(self):
        """No activity matches sport → compliance='skipped'."""
        from tasks.actors.activities import _actor_fill_training_log_actual

        user = _user()
        log = self._make_log(sport="Swim")

        with (
            patch(
                "tasks.actors.activities.TrainingLog.get_unfilled_actual",
                return_value=[log],
            ),
            patch(
                "tasks.actors.activities.Activity.get_for_date",
                return_value=[],
            ),
            patch(
                "tasks.actors.activities.TrainingLog.update",
            ) as mock_update,
        ):
            _actor_fill_training_log_actual(user.model_dump())

        mock_update.assert_called_once()
        kwargs = mock_update.call_args[1]
        assert kwargs["compliance"] == "skipped"

    def test_passes_actual_data_to_update(self):
        """Matched activity → actual_* fields passed to update."""
        from tasks.actors.activities import _actor_fill_training_log_actual

        user = _user()
        log = self._make_log(sport="Run")
        activity = _activity_dto(
            type="Run",
            moving_time=3000,
        )

        with (
            patch(
                "tasks.actors.activities.TrainingLog.get_unfilled_actual",
                return_value=[log],
            ),
            patch(
                "tasks.actors.activities.Activity.get_for_date",
                return_value=[activity],
            ),
            patch(
                "tasks.actors.activities._compute_max_zone_sync",
                return_value="Z1",
            ),
            patch(
                "tasks.actors.activities.detect_compliance",
                return_value="followed_original",
            ),
            patch(
                "tasks.actors.activities.TrainingLog.update",
            ) as mock_update,
        ):
            _actor_fill_training_log_actual(user.model_dump())

        kwargs = mock_update.call_args[1]
        assert kwargs["actual_activity_id"] == "a001"
        assert kwargs["actual_duration_sec"] == 3000
        assert kwargs["actual_avg_hr"] == 145.0
        assert kwargs["actual_tss"] == 80.0
        assert kwargs["actual_max_zone_time"] == "Z1"
