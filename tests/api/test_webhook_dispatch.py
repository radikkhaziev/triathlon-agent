"""Tests for Intervals.icu webhook dispatch — one test per event type with real JSON fixtures."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.dto import IntervalsWebhookEvent
from api.routers.intervals.webhook import (
    _dispatch_achievements,
    _dispatch_activity,
    _dispatch_calendar,
    _dispatch_scope_changed,
    _dispatch_sport_settings,
    _dispatch_wellness,
    _handle_webhook_event,
)
from data.db.dto import UserDTO

# ---------------------------------------------------------------------------
# Fixtures — real JSON payloads from production (PII redacted)
# ---------------------------------------------------------------------------

WELLNESS_UPDATED_EVENT = {
    "athlete_id": "i317960",
    "type": "WELLNESS_UPDATED",
    "timestamp": "2026-04-15T16:29:56.819+00:00",
    "records": [
        {
            "id": "2026-04-15",
            "ctl": 18.41,
            "atl": 37.39,
            "rampRate": 4.69,
            "ctlLoad": 14.0,
            "atlLoad": 14.0,
            "sportInfo": [{"type": "Ride", "eftp": 207.82, "wPrime": 17460.5, "pMax": 642.5}],
            "updated": "2026-04-15T16:29:54.818+00:00",
            "weight": 77.36,
            "restingHR": 59,
            "hrv": 46.0,
            "sleepSecs": 22242,
            "sleepScore": 76.0,
            "sleepQuality": 3,
            "bodyFat": 24.6,
            "steps": 11208,
        }
    ],
}

CALENDAR_UPDATED_EVENT = {
    "athlete_id": "i317960",
    "type": "CALENDAR_UPDATED",
    "timestamp": "2026-04-16T14:22:33.000+00:00",
    "records": [],
}

SPORT_SETTINGS_UPDATED_EVENT = {
    "athlete_id": "i317960",
    "type": "SPORT_SETTINGS_UPDATED",
    "timestamp": "2026-04-16T12:15:44.000+00:00",
    "sportSettings": [
        {
            "id": 1,
            "types": ["Ride", "VirtualRide"],
            "lthr": 163,
            "max_hr": 179,
            "ftp": 210,
            "hr_zones": [110, 135, 153, 171, 195],
            "power_zones": [115, 156, 189, 220, 252],
        },
        {
            "id": 2,
            "types": ["Run", "VirtualRun", "TrailRun"],
            "lthr": 153,
            "max_hr": 179,
            "threshold_pace": 4.13,
            "pace_units": "MINS_KM",
            "hr_zones": [129, 136, 144, 152, 157, 161],
        },
    ],
    "records": [],
}

APP_SCOPE_CHANGED_EVENT = {
    "athlete_id": "i317960",
    "type": "APP_SCOPE_CHANGED",
    "scope": "ACTIVITY:WRITE,WELLNESS:READ,CALENDAR:WRITE,SETTINGS:WRITE",
    "deauthorized": False,
    "client_id": "endurai",
    "client_name": "EndurAI",
    "records": [],
}

APP_DEAUTHORIZED_EVENT = {
    "athlete_id": "i317960",
    "type": "APP_SCOPE_CHANGED",
    "scope": "",
    "deauthorized": True,
    "client_id": "endurai",
    "client_name": "EndurAI",
    "records": [],
}

ACTIVITY_UPLOADED_EVENT = {
    "athlete_id": "i317960",
    "type": "ACTIVITY_UPLOADED",
    "timestamp": "2026-04-16T15:42:26.000+00:00",
    "activity": {
        "id": "i317960-2026-04-16-abc123",
        "start_date_local": "2026-04-16T15:00:00",
        "type": "VirtualRide",
        "moving_time": 3312,
        "icu_training_load": 41.2,
        "average_heartrate": 141,
        "average_watts": 168,
        "distance": 25400,
        "name": "Zwift - Zone 2 Endurance",
        "source": "GARMIN_CONNECT",
    },
    "records": [],
}

ACTIVITY_DELETED_EVENT = {
    "athlete_id": "i317960",
    "type": "ACTIVITY_DELETED",
    "timestamp": "2026-04-16T16:00:00.000+00:00",
    "activity": {"id": "i317960-2026-04-16-abc123"},
    "records": [],
}

FITNESS_UPDATED_EVENT = {
    "athlete_id": "i317960",
    "type": "FITNESS_UPDATED",
    "timestamp": "2026-04-16T15:42:28.000+00:00",
    "records": [
        {"id": "2026-04-16", "ctl": 19.5, "atl": 38.0, "sportInfo": [{"type": "Ride", "eftp": 208.0}]},
        {"id": "2026-04-17", "ctl": 18.8, "atl": 35.2, "sportInfo": [{"type": "Ride", "eftp": 207.5}]},
    ],
}

ACTIVITY_ACHIEVEMENTS_EVENT = {
    "athlete_id": "i317960",
    "type": "ACTIVITY_ACHIEVEMENTS",
    "timestamp": "2026-04-16T15:43:26.000+00:00",
    "activity": {
        "id": "i317960-2026-04-16-abc123",
        "start_date_local": "2026-04-16T15:00:00",
        "type": "VirtualRide",
        "name": "Zwift - Zone 2 Endurance",
        "moving_time": 3312,
        "icu_training_load": 41.2,
        "average_heartrate": 141,
        "average_watts": 168,
        "icu_rolling_ftp": 210,
        "icu_rolling_ftp_delta": 2,
        "icu_ctl": 19.49,
        "icu_atl": 38.01,
        "icu_achievements": [
            {"id": "ps0_5", "type": "BEST_POWER", "watts": 500, "secs": 5},
        ],
    },
    "records": [],
}


def _make_user_dto(**kwargs) -> UserDTO:
    defaults = {"id": 1, "chat_id": "12345", "athlete_id": "i317960", "language": "en"}
    defaults.update(kwargs)
    return UserDTO(**defaults)


def _make_event(data: dict) -> IntervalsWebhookEvent:
    return IntervalsWebhookEvent.model_validate(data)


# ---------------------------------------------------------------------------
# DTO parsing tests
# ---------------------------------------------------------------------------


class TestEventParsing:
    """IntervalsWebhookEvent correctly parses all delivery patterns."""

    def test_wellness_records(self):
        event = _make_event(WELLNESS_UPDATED_EVENT)
        assert len(event.records) == 1
        assert event.records[0]["id"] == "2026-04-15"

    def test_calendar_empty_records(self):
        event = _make_event(CALENDAR_UPDATED_EVENT)
        assert event.records == []

    def test_sport_settings_alias(self):
        event = _make_event(SPORT_SETTINGS_UPDATED_EVENT)
        assert len(event.sport_settings) == 2
        assert event.sport_settings[0]["types"] == ["Ride", "VirtualRide"]

    def test_scope_changed_fields(self):
        event = _make_event(APP_SCOPE_CHANGED_EVENT)
        assert event.scope == "ACTIVITY:WRITE,WELLNESS:READ,CALENDAR:WRITE,SETTINGS:WRITE"
        assert event.deauthorized is False

    def test_deauthorized_event(self):
        event = _make_event(APP_DEAUTHORIZED_EVENT)
        assert event.deauthorized is True

    def test_activity_field(self):
        event = _make_event(ACTIVITY_UPLOADED_EVENT)
        assert event.activity is not None
        assert event.activity["type"] == "VirtualRide"

    def test_activity_deleted_minimal(self):
        event = _make_event(ACTIVITY_DELETED_EVENT)
        assert event.activity is not None
        assert "id" in event.activity

    def test_fitness_updated_records(self):
        event = _make_event(FITNESS_UPDATED_EVENT)
        assert len(event.records) == 2

    def test_achievements_activity(self):
        event = _make_event(ACTIVITY_ACHIEVEMENTS_EVENT)
        assert event.activity is not None
        assert event.activity["icu_rolling_ftp"] == 210
        assert event.activity["icu_achievements"][0]["watts"] == 500


# ---------------------------------------------------------------------------
# Dispatcher unit tests
# ---------------------------------------------------------------------------


class TestDispatchWellness:
    def test_dispatches_for_each_record(self):
        extra_record = {
            **WELLNESS_UPDATED_EVENT["records"][0],
            "id": "2026-04-14",
            "updated": "2026-04-14T16:29:54.818+00:00",
        }
        payload = {
            **WELLNESS_UPDATED_EVENT,
            "records": [WELLNESS_UPDATED_EVENT["records"][0], extra_record],
        }
        event = _make_event(payload)
        user = _make_user_dto()
        with patch("api.routers.intervals.webhook.actor_user_wellness") as mock:
            _dispatch_wellness(user, event)
            assert mock.send.call_count == 2
            # Records are sorted by `updated` ascending — oldest first.
            dispatched_dates = [call.kwargs["dt"] for call in mock.send.call_args_list]
            assert dispatched_dates == ["2026-04-14", "2026-04-15"]
            assert all(call.kwargs["user"] == user for call in mock.send.call_args_list)

    def test_skips_empty_records(self):
        event = _make_event({**WELLNESS_UPDATED_EVENT, "records": []})
        user = _make_user_dto()
        with patch("api.routers.intervals.webhook.actor_user_wellness") as mock:
            _dispatch_wellness(user, event)
            mock.send.assert_not_called()


class TestDispatchCalendar:
    def test_dispatches_both_actors(self):
        user = _make_user_dto()
        with (
            patch("api.routers.intervals.webhook.actor_user_scheduled_workouts") as mock_workouts,
            patch("api.routers.intervals.webhook.actor_sync_athlete_goals") as mock_goals,
        ):
            _dispatch_calendar(user)
            mock_workouts.send.assert_called_once_with(user=user)
            mock_goals.send.assert_called_once_with(user=user)


class TestDispatchSportSettings:
    def test_dispatches_with_parsed_settings(self):
        event = _make_event(SPORT_SETTINGS_UPDATED_EVENT)
        user = _make_user_dto()
        with patch("api.routers.intervals.webhook.actor_sync_athlete_settings") as mock:
            _dispatch_sport_settings(user, event)
            mock.send.assert_called_once()
            call_kwargs = mock.send.call_args.kwargs
            assert len(call_kwargs["sport_settings"]) == 2
            assert call_kwargs["sport_settings"][0].lthr == 163


class TestDispatchScopeChanged:
    @pytest.mark.asyncio
    async def test_updates_scope(self):
        event = _make_event(APP_SCOPE_CHANGED_EVENT)
        mock_user = MagicMock()
        mock_user.id = 1
        mock_db_user = MagicMock()
        mock_db_user.intervals_oauth_scope = None

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_db_user)
        mock_session.commit = AsyncMock()

        with patch("api.routers.intervals.webhook.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)
            await _dispatch_scope_changed(mock_user, event)

        assert mock_db_user.intervals_oauth_scope == "ACTIVITY:WRITE,WELLNESS:READ,CALENDAR:WRITE,SETTINGS:WRITE"

    @pytest.mark.asyncio
    async def test_clears_tokens_on_deauthorize(self):
        event = _make_event(APP_DEAUTHORIZED_EVENT)
        mock_user = MagicMock()
        mock_user.id = 1
        mock_db_user = MagicMock()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_db_user)
        mock_session.commit = AsyncMock()

        with patch("api.routers.intervals.webhook.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)
            await _dispatch_scope_changed(mock_user, event)

        mock_db_user.clear_oauth_tokens.assert_called_once()


class TestDispatchAchievements:
    def test_dispatches_notification_with_activity(self):
        event = _make_event(ACTIVITY_ACHIEVEMENTS_EVENT)
        user = _make_user_dto()
        with patch("api.routers.intervals.webhook.actor_send_achievement_notification") as mock:
            _dispatch_achievements(user, event)
            mock.send.assert_called_once()
            call_kwargs = mock.send.call_args.kwargs
            assert call_kwargs["user"] == user
            assert call_kwargs["activity"]["icu_rolling_ftp"] == 210

    def test_skips_without_activity(self):
        event = _make_event({**ACTIVITY_ACHIEVEMENTS_EVENT, "activity": None})
        user = _make_user_dto()
        with patch("api.routers.intervals.webhook.actor_send_achievement_notification") as mock:
            _dispatch_achievements(user, event)
            mock.send.assert_not_called()


class TestDispatchFitness:
    @pytest.mark.asyncio
    async def test_dispatches_save_bulk(self):
        from api.routers.intervals.webhook import _dispatch_fitness

        event = _make_event(FITNESS_UPDATED_EVENT)
        with patch("api.routers.intervals.webhook.FitnessProjection") as mock_cls:
            mock_cls.save_bulk = AsyncMock(return_value=2)
            await _dispatch_fitness(user_id=1, event=event)
            mock_cls.save_bulk.assert_called_once()
            call_kwargs = mock_cls.save_bulk.call_args.kwargs
            assert call_kwargs["user_id"] == 1
            assert len(call_kwargs["records"]) == 2

    @pytest.mark.asyncio
    async def test_skips_empty_records(self):
        from api.routers.intervals.webhook import _dispatch_fitness

        event = _make_event({**FITNESS_UPDATED_EVENT, "records": []})
        with patch("api.routers.intervals.webhook.FitnessProjection") as mock_cls:
            await _dispatch_fitness(user_id=1, event=event)
            mock_cls.save_bulk.assert_not_called()


class TestDispatchActivity:
    """Unified dispatcher for ACTIVITY_UPLOADED and ACTIVITY_UPDATED."""

    @pytest.mark.asyncio
    async def test_saves_and_dispatches_details(self):
        event = _make_event(ACTIVITY_UPLOADED_EVENT)
        user = _make_user_dto()
        with (
            patch("api.routers.intervals.webhook.Activity") as mock_activity,
            patch("api.routers.intervals.webhook.actor_update_activity_details") as mock_actor,
        ):
            mock_activity.save_bulk = AsyncMock()
            await _dispatch_activity(user, event)
            mock_activity.save_bulk.assert_called_once()
            mock_actor.send.assert_called_once()
            assert mock_actor.send.call_args.kwargs["activity_id"] == "i317960-2026-04-16-abc123"

    @pytest.mark.asyncio
    async def test_skips_without_activity(self):
        event = _make_event({**ACTIVITY_UPLOADED_EVENT, "activity": None})
        user = _make_user_dto()
        with patch("api.routers.intervals.webhook.Activity") as mock_activity:
            mock_activity.save_bulk = AsyncMock()
            await _dispatch_activity(user, event)
            mock_activity.save_bulk.assert_not_called()


# ---------------------------------------------------------------------------
# Integration: _handle_webhook_event routes to correct dispatcher
# ---------------------------------------------------------------------------


def _make_orm_user_mock(**kwargs):
    """Create a MagicMock that passes UserDTO.model_validate."""
    defaults = {
        "id": 1,
        "chat_id": "12345",
        "athlete_id": "i317960",
        "username": "test",
        "language": "en",
        "is_silent": False,
    }
    defaults.update(kwargs)
    mock = MagicMock(spec=[])
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


class TestHandleWebhookEvent:
    """Verify the dispatch table routes each event type correctly."""

    @pytest.mark.asyncio
    async def test_wellness_dispatched(self):
        event = _make_event(WELLNESS_UPDATED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_wellness") as mock_dispatch,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.INTERVALS_WEBHOOK_MONITORING = False
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_calendar_dispatched(self):
        event = _make_event(CALENDAR_UPDATED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_calendar") as mock_dispatch,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.INTERVALS_WEBHOOK_MONITORING = False
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_sport_settings_dispatched(self):
        event = _make_event(SPORT_SETTINGS_UPDATED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_sport_settings") as mock_dispatch,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.INTERVALS_WEBHOOK_MONITORING = False
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_scope_changed_dispatched(self):
        event = _make_event(APP_SCOPE_CHANGED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_scope_changed", new_callable=AsyncMock) as mock_dispatch,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.INTERVALS_WEBHOOK_MONITORING = False
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_achievements_dispatched(self):
        event = _make_event(ACTIVITY_ACHIEVEMENTS_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_achievements") as mock_dispatch,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.INTERVALS_WEBHOOK_MONITORING = False
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_fitness_dispatched(self):
        event = _make_event(FITNESS_UPDATED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_fitness", new_callable=AsyncMock) as mock_dispatch,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.INTERVALS_WEBHOOK_MONITORING = False
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_activity_uploaded_dispatched(self):
        event = _make_event(ACTIVITY_UPLOADED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_activity", new_callable=AsyncMock) as mock_dispatch,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.INTERVALS_WEBHOOK_MONITORING = False
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_activity_updated_dispatched(self):
        event = _make_event({**ACTIVITY_UPLOADED_EVENT, "type": "ACTIVITY_UPDATED"})
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_activity", new_callable=AsyncMock) as mock_dispatch,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.INTERVALS_WEBHOOK_MONITORING = False
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_athlete_skipped(self):
        event = _make_event(WELLNESS_UPDATED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_wellness") as mock_dispatch,
        ):
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=None)
            await _handle_webhook_event(event)
            mock_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_event_type_skipped(self):
        event = _make_event(
            {
                "athlete_id": "i317960",
                "type": "SOME_FUTURE_EVENT",
                "records": [],
            }
        )
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.INTERVALS_WEBHOOK_MONITORING = False
            mock_user = MagicMock()
            mock_user.id = 1
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=mock_user)
            # Should not raise
            await _handle_webhook_event(event)
