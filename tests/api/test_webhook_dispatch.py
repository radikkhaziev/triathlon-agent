"""Tests for Intervals.icu webhook dispatch — one test per event type with real JSON fixtures."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.dto import IntervalsWebhookEvent
from api.routers.intervals.webhook import (
    _dispatch_achievements,
    _dispatch_activity_updated,
    _dispatch_activity_uploaded,
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

ACTIVITY_UPLOADED_WITH_WEATHER_EVENT = {
    "athlete_id": "i317960",
    "type": "ACTIVITY_UPLOADED",
    "timestamp": "2026-05-06T08:00:00.000+00:00",
    "activity": {
        "id": "i317960-2026-05-06-outdoor",
        "start_date_local": "2026-05-06T08:00:00",
        "type": "Run",
        "moving_time": 3600,
        "distance": 10000,
        "trimp": 87.4,
        "has_weather": True,
        "average_weather_temp": 18.2,
        "min_weather_temp": 14.0,
        "max_weather_temp": 22.5,
        "average_feels_like": 17.8,
        "average_wind_speed": 3.5,
        "average_wind_gust": 6.1,
        "prevailing_wind_deg": 200,
        "headwind_percent": 35.0,
        "tailwind_percent": 65.0,
        "average_clouds": 25.0,
        "max_rain": 0.0,
        "max_snow": 0.0,
    },
    "records": [],
}

ACTIVITY_UPLOADED_INDOOR_WITH_TRIMP_EVENT = {
    # Indoor / trainer rides have has_weather=False but still carry trimp.
    # Catches the regression where someone wraps both writes in the same
    # `if dto.has_weather` block.
    "athlete_id": "i317960",
    "type": "ACTIVITY_UPLOADED",
    "timestamp": "2026-05-06T19:00:00.000+00:00",
    "activity": {
        "id": "i317960-2026-05-06-trainer",
        "start_date_local": "2026-05-06T19:00:00",
        "type": "VirtualRide",
        "moving_time": 3000,
        "trimp": 65.2,
        "has_weather": False,
    },
    "records": [],
}

ACTIVITY_UPLOADED_PHASE2_EVENT = {
    # WEBHOOK_DATA_CAPTURE Phase 2: warmup/cooldown/polarization ride along on
    # ACTIVITY_UPLOADED. Trimp present too — verifies all four fields land in
    # the same patch call. polarization_index uses a negative real-world value
    # (sample A.7 in INTERVALS_WEBHOOKS_RESEARCH.md shows -0.12) to ensure the
    # `is not None` skip-check doesn't trip on negative or zero values.
    "athlete_id": "i317960",
    "type": "ACTIVITY_UPLOADED",
    "timestamp": "2026-05-07T08:00:00.000+00:00",
    "activity": {
        "id": "i317960-2026-05-07-phase2",
        "start_date_local": "2026-05-07T08:00:00",
        "type": "Run",
        "moving_time": 3600,
        "trimp": 92.1,
        "icu_warmup_time": 600,
        "icu_cooldown_time": 480,
        "polarization_index": -0.12,
        "has_weather": False,
    },
    "records": [],
}

SPORT_SETTINGS_WITH_MMP_EVENT = {
    "athlete_id": "i317960",
    "type": "SPORT_SETTINGS_UPDATED",
    "timestamp": "2026-05-06T12:00:00.000+00:00",
    "sportSettings": [
        {
            "id": 10,
            "types": ["Ride", "VirtualRide"],
            "lthr": 163,
            "max_hr": 179,
            "ftp": 250,
            "mmp_model": {
                "type": "CP",
                "criticalPower": 245.5,
                "wPrime": 25000.0,
                "pMax": 1100.0,
                "ftp": 250,
            },
        },
    ],
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

    def test_parses_mmp_model_for_ride(self):
        """SPORT_SETTINGS_UPDATED with mmp_model block (Ride only) → SportSettingsDTO
        carries critical_power / w_prime / p_max for downstream actor to persist.

        The actor wires these through to AthleteSettings.upsert; here we verify
        the DTO parsing is the source-of-truth (handler-side regression catches
        any future alias drift on criticalPower / wPrime / pMax)."""
        event = _make_event(SPORT_SETTINGS_WITH_MMP_EVENT)
        user = _make_user_dto()
        with patch("api.routers.intervals.webhook.actor_sync_athlete_settings") as mock:
            _dispatch_sport_settings(user, event)
            mock.send.assert_called_once()
            settings_arg = mock.send.call_args.kwargs["sport_settings"][0]
            assert settings_arg.mmp_model is not None
            assert settings_arg.mmp_model.critical_power == 245.5
            assert settings_arg.mmp_model.w_prime == 25000.0
            assert settings_arg.mmp_model.p_max == 1100.0
            assert settings_arg.mmp_model.ftp == 250

    def test_run_settings_have_no_mmp_model(self):
        """Run/Swim sport_settings payloads omit mmp_model — DTO must default to None."""
        event = _make_event(SPORT_SETTINGS_UPDATED_EVENT)
        user = _make_user_dto()
        with patch("api.routers.intervals.webhook.actor_sync_athlete_settings") as mock:
            _dispatch_sport_settings(user, event)
            for ss in mock.send.call_args.kwargs["sport_settings"]:
                assert ss.mmp_model is None


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
    @pytest.mark.asyncio
    async def test_dispatches_notification_with_activity(self):
        event = _make_event(ACTIVITY_ACHIEVEMENTS_EVENT)
        user = _make_user_dto()
        with (
            patch(
                "api.routers.intervals.webhook.Activity.exists_for_user",
                new=AsyncMock(return_value=True),
            ),
            patch("api.routers.intervals.webhook.ActivityAchievement.save_bulk", new=AsyncMock(return_value=1)),
            patch("api.routers.intervals.webhook.ActivityDetail") as mock_detail,
            patch("api.routers.intervals.webhook.actor_send_achievement_notification") as mock,
        ):
            mock_detail.patch = AsyncMock()  # @dual returns awaitable in async dispatcher context
            await _dispatch_achievements(user, event)
            mock.send.assert_called_once()
            call_kwargs = mock.send.call_args.kwargs
            assert call_kwargs["user"] == user
            assert call_kwargs["activity"]["icu_rolling_ftp"] == 210

            # WEBHOOK_DATA_CAPTURE Phase 1 contract: rolling power model + CTL/ATL
            # snapshot get layered onto activity_details from the achievements payload.
            mock_detail.patch.assert_called_once()
            patch_kwargs = mock_detail.patch.call_args.kwargs
            assert patch_kwargs["rolling_ftp"] == 210
            assert patch_kwargs["rolling_ftp_delta"] == 2
            assert patch_kwargs["ctl_snapshot"] == 19.49
            assert patch_kwargs["atl_snapshot"] == 38.01

    @pytest.mark.asyncio
    async def test_skips_persist_when_activity_unknown_to_user(self):
        """T19 (cross-tenant write guard): a webhook with an activity_id that
        doesn't belong to this user must NOT touch ActivityAchievement.
        Notification still fires — Telegram-side ping is harmless even if
        the activity is foreign (the actor itself is also tenant-scoped)."""
        event = _make_event(ACTIVITY_ACHIEVEMENTS_EVENT)
        user = _make_user_dto()
        with (
            patch(
                "api.routers.intervals.webhook.Activity.exists_for_user",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "api.routers.intervals.webhook.ActivityAchievement.save_bulk",
                new=AsyncMock(),
            ) as mock_save,
            patch("api.routers.intervals.webhook.ActivityDetail") as mock_detail,
            patch("api.routers.intervals.webhook.actor_send_achievement_notification") as mock_notify,
        ):
            mock_detail.patch = AsyncMock()
            await _dispatch_achievements(user, event)

        mock_save.assert_not_called()
        # Same guard must protect ActivityDetail.patch — both writes live inside the
        # `else: exists_for_user` branch. Locks in the multi-tenant invariant against
        # an accidental refactor that flattens the branches.
        mock_detail.patch.assert_not_called()
        mock_notify.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_without_activity(self):
        event = _make_event({**ACTIVITY_ACHIEVEMENTS_EVENT, "activity": None})
        user = _make_user_dto()
        with (
            patch("api.routers.intervals.webhook.ActivityAchievement.save_bulk", new=AsyncMock()) as mock_save,
            patch("api.routers.intervals.webhook.actor_send_achievement_notification") as mock_notify,
        ):
            await _dispatch_achievements(user, event)
            mock_save.assert_not_called()
            mock_notify.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_persists_before_notify(self):
        """Save BEFORE Telegram so a notification outage doesn't cost data.
        Uses a shared call_log to lock the order — assert_called alone would
        let a refactor flip the sequence undetected."""
        event = _make_event(ACTIVITY_ACHIEVEMENTS_EVENT)
        user = _make_user_dto()
        call_log: list[str] = []

        async def _save_spy(**kwargs):
            call_log.append("save")
            return 2

        def _notify_spy(**kwargs):
            call_log.append("notify")

        with (
            patch(
                "api.routers.intervals.webhook.Activity.exists_for_user",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "api.routers.intervals.webhook.ActivityAchievement.save_bulk",
                new=AsyncMock(side_effect=_save_spy),
            ) as mock_save,
            patch("api.routers.intervals.webhook.actor_send_achievement_notification") as mock_notify,
        ):
            mock_notify.send.side_effect = _notify_spy
            await _dispatch_achievements(user, event)

        assert call_log == ["save", "notify"]
        # Args: user_id, activity_id, activity (raw dict)
        call_kwargs = mock_save.call_args.kwargs
        assert call_kwargs["user_id"] == user.id
        assert call_kwargs["activity_id"] == str(event.activity["id"])
        assert call_kwargs["activity"]["icu_rolling_ftp"] == 210

    @pytest.mark.asyncio
    async def test_notify_still_fires_when_save_raises(self):
        """Persistence error must NOT block the realtime Telegram nudge —
        the user should still get the ping even if our archive missed it."""
        event = _make_event(ACTIVITY_ACHIEVEMENTS_EVENT)
        user = _make_user_dto()
        with (
            patch(
                "api.routers.intervals.webhook.Activity.exists_for_user",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "api.routers.intervals.webhook.ActivityAchievement.save_bulk",
                new=AsyncMock(side_effect=RuntimeError("DB down")),
            ),
            # Stub ActivityDetail.patch — without this it would FK-violate against
            # the empty test DB and add a second Sentry capture call, drowning out
            # the assertion about achievement-save isolation. AsyncMock because
            # patch is awaited from the async dispatcher (via @dual).
            patch("api.routers.intervals.webhook.ActivityDetail.patch", new=AsyncMock()),
            patch("api.routers.intervals.webhook.actor_send_achievement_notification") as mock_notify,
            patch("api.routers.intervals.webhook.sentry_sdk.capture_exception") as mock_capture,
        ):
            await _dispatch_achievements(user, event)

        mock_notify.send.assert_called_once()
        # Explicit count + isinstance: count guards against silent drift if a future
        # dispatcher change adds another exception path; isinstance pins which one we caught.
        assert mock_capture.call_count == 1, mock_capture.call_args_list
        assert isinstance(mock_capture.call_args.args[0], RuntimeError)


class TestDispatchFitness:
    def test_dispatches_actor(self):
        from api.routers.intervals.webhook import _dispatch_fitness

        event = _make_event(FITNESS_UPDATED_EVENT)
        user = _make_user_dto()
        with patch("api.routers.intervals.webhook.actor_save_fitness_projection") as mock_actor:
            _dispatch_fitness(user, event)
            mock_actor.send.assert_called_once()
            call_kwargs = mock_actor.send.call_args.kwargs
            assert call_kwargs["user"] == user
            assert len(call_kwargs["records"]) == 2

    def test_skips_empty_records(self):
        from api.routers.intervals.webhook import _dispatch_fitness

        event = _make_event({**FITNESS_UPDATED_EVENT, "records": []})
        user = _make_user_dto()
        with patch("api.routers.intervals.webhook.actor_save_fitness_projection") as mock_actor:
            _dispatch_fitness(user, event)
            mock_actor.send.assert_not_called()


class TestDispatchActivityUploaded:
    @pytest.mark.asyncio
    async def test_saves_and_dispatches_details_and_rename(self):
        """User in STRAVA_SIGNATURE_USER_IDS allowlist → rename actor dispatched."""
        event = _make_event(ACTIVITY_UPLOADED_EVENT)
        user = _make_user_dto()
        with (
            patch("api.routers.intervals.webhook.Activity") as mock_activity,
            patch("api.routers.intervals.webhook.actor_update_activity_details") as mock_details,
            patch("api.routers.intervals.webhook.actor_rename_activity") as mock_rename,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.STRAVA_SIGNATURE_USER_IDS = {1}
            mock_activity.save_bulk = AsyncMock()
            await _dispatch_activity_uploaded(user, event)
            mock_activity.save_bulk.assert_called_once()
            mock_details.send.assert_called_once()
            mock_rename.send_with_options.assert_called_once()
            rename_kwargs = mock_rename.send_with_options.call_args.kwargs
            assert rename_kwargs["kwargs"]["activity_id"] == "i317960-2026-04-16-abc123"
            assert rename_kwargs["delay"] == 300000  # 5 min

    @pytest.mark.asyncio
    async def test_skips_rename_when_user_not_in_allowlist(self):
        """User outside STRAVA_SIGNATURE_USER_IDS → details dispatched, rename skipped.

        Save + details run unconditionally; only the AI-rename actor is gated by
        the private-beta allowlist. Without this gate, every tenant would enqueue
        a no-op every upload.
        """
        event = _make_event(ACTIVITY_UPLOADED_EVENT)
        user = _make_user_dto(id=42)
        with (
            patch("api.routers.intervals.webhook.Activity") as mock_activity,
            patch("api.routers.intervals.webhook.actor_update_activity_details") as mock_details,
            patch("api.routers.intervals.webhook.actor_rename_activity") as mock_rename,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.STRAVA_SIGNATURE_USER_IDS = {1}
            mock_activity.save_bulk = AsyncMock()
            await _dispatch_activity_uploaded(user, event)
            mock_activity.save_bulk.assert_called_once()
            mock_details.send.assert_called_once()
            mock_rename.send_with_options.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_without_activity(self):
        event = _make_event({**ACTIVITY_UPLOADED_EVENT, "activity": None})
        user = _make_user_dto()
        with patch("api.routers.intervals.webhook.Activity") as mock_activity:
            mock_activity.save_bulk = AsyncMock()
            await _dispatch_activity_uploaded(user, event)
            mock_activity.save_bulk.assert_not_called()

    @pytest.mark.asyncio
    async def test_persists_weather_and_trimp_for_outdoor_activity(self):
        """ACTIVITY_UPLOADED with has_weather=True → ActivityWeather row + trimp patched.

        WEBHOOK_DATA_CAPTURE Phase 1: the webhook delivers weather + trimp inline
        on the activity payload — no extra API call needed. Verify both write
        paths fire from the same dispatch.
        """
        event = _make_event(ACTIVITY_UPLOADED_WITH_WEATHER_EVENT)
        user = _make_user_dto()
        with (
            patch("api.routers.intervals.webhook.Activity") as mock_activity,
            patch("api.routers.intervals.webhook.actor_update_activity_details"),
            patch("api.routers.intervals.webhook.actor_rename_activity"),
            patch("api.routers.intervals.webhook.ActivityWeather") as mock_weather,
            patch("api.routers.intervals.webhook.ActivityDetail") as mock_detail,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.STRAVA_SIGNATURE_USER_IDS = set()  # rename gated off — irrelevant here
            mock_activity.save_bulk = AsyncMock()
            mock_weather.upsert_from_dto = AsyncMock()  # @dual — awaited from async dispatcher
            mock_detail.patch = AsyncMock()
            await _dispatch_activity_uploaded(user, event)

            mock_weather.upsert_from_dto.assert_called_once()
            dto_arg = mock_weather.upsert_from_dto.call_args.args[0]
            assert dto_arg.id == "i317960-2026-05-06-outdoor"
            assert dto_arg.average_weather_temp == 18.2
            assert dto_arg.has_weather is True

            mock_detail.patch.assert_called_once()
            patch_kwargs = mock_detail.patch.call_args.kwargs
            assert patch_kwargs["trimp"] == 87.4

    @pytest.mark.asyncio
    async def test_persists_trimp_for_indoor_activity(self):
        """has_weather=False but trimp present → weather skipped, trimp patched.
        Regression guard: if both writes ever get wrapped in a single
        `if dto.has_weather` block, indoor sessions stop contributing trimp
        to the HRV-prediction feature builder."""
        event = _make_event(ACTIVITY_UPLOADED_INDOOR_WITH_TRIMP_EVENT)
        user = _make_user_dto()
        with (
            patch("api.routers.intervals.webhook.Activity") as mock_activity,
            patch("api.routers.intervals.webhook.actor_update_activity_details"),
            patch("api.routers.intervals.webhook.actor_rename_activity"),
            patch("api.routers.intervals.webhook.ActivityWeather") as mock_weather,
            patch("api.routers.intervals.webhook.ActivityDetail") as mock_detail,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.STRAVA_SIGNATURE_USER_IDS = set()
            mock_activity.save_bulk = AsyncMock()
            mock_weather.upsert_from_dto = AsyncMock()
            mock_detail.patch = AsyncMock()
            await _dispatch_activity_uploaded(user, event)

            mock_weather.upsert_from_dto.assert_not_called()
            mock_detail.patch.assert_called_once()
            assert mock_detail.patch.call_args.kwargs["trimp"] == 65.2

    @pytest.mark.asyncio
    async def test_skips_weather_when_indoor(self):
        """has_weather=False (indoor / virtual ride) → no weather row written."""
        event = _make_event(ACTIVITY_UPLOADED_EVENT)  # has no has_weather field
        user = _make_user_dto()
        with (
            patch("api.routers.intervals.webhook.Activity") as mock_activity,
            patch("api.routers.intervals.webhook.actor_update_activity_details"),
            patch("api.routers.intervals.webhook.actor_rename_activity"),
            patch("api.routers.intervals.webhook.ActivityWeather") as mock_weather,
            patch("api.routers.intervals.webhook.ActivityDetail") as mock_detail,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.STRAVA_SIGNATURE_USER_IDS = set()
            mock_activity.save_bulk = AsyncMock()
            mock_weather.upsert_from_dto = AsyncMock()
            mock_detail.patch = AsyncMock()
            await _dispatch_activity_uploaded(user, event)

            mock_weather.upsert_from_dto.assert_not_called()
            # No trimp / warmup / cooldown / polarization in this fixture either → no patch call.
            mock_detail.patch.assert_not_called()

    @pytest.mark.asyncio
    async def test_persists_phase2_fields_for_outdoor_activity(self):
        """ACTIVITY_UPLOADED with warmup/cooldown/polarization → all four
        fields (incl. trimp) land in a single ActivityDetail.patch call.

        WEBHOOK_DATA_CAPTURE Phase 2 — these come inline on the upload payload
        and are not in _DETAIL_FIELD_MAP, so the dispatcher patch is the only
        write path.
        """
        event = _make_event(ACTIVITY_UPLOADED_PHASE2_EVENT)
        user = _make_user_dto()
        with (
            patch("api.routers.intervals.webhook.Activity") as mock_activity,
            patch("api.routers.intervals.webhook.actor_update_activity_details"),
            patch("api.routers.intervals.webhook.actor_rename_activity"),
            patch("api.routers.intervals.webhook.ActivityWeather") as mock_weather,
            patch("api.routers.intervals.webhook.ActivityDetail") as mock_detail,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.STRAVA_SIGNATURE_USER_IDS = set()
            mock_activity.save_bulk = AsyncMock()
            mock_weather.upsert_from_dto = AsyncMock()
            mock_detail.patch = AsyncMock()
            await _dispatch_activity_uploaded(user, event)

            mock_detail.patch.assert_called_once()
            patch_kwargs = mock_detail.patch.call_args.kwargs
            assert patch_kwargs["trimp"] == 92.1
            assert patch_kwargs["warmup_time_sec"] == 600
            assert patch_kwargs["cooldown_time_sec"] == 480
            assert patch_kwargs["polarization_index"] == -0.12

    @pytest.mark.asyncio
    async def test_phase2_fields_skip_when_absent(self):
        """Old payload without Phase 2 fields → no warmup/cooldown/polarization
        kwargs in the patch call. Sentinel-default `_UNSET` semantics: skip None
        rather than overwrite with NULL."""
        event = _make_event(ACTIVITY_UPLOADED_INDOOR_WITH_TRIMP_EVENT)
        user = _make_user_dto()
        with (
            patch("api.routers.intervals.webhook.Activity") as mock_activity,
            patch("api.routers.intervals.webhook.actor_update_activity_details"),
            patch("api.routers.intervals.webhook.actor_rename_activity"),
            patch("api.routers.intervals.webhook.ActivityWeather") as mock_weather,
            patch("api.routers.intervals.webhook.ActivityDetail") as mock_detail,
            patch("api.routers.intervals.webhook.settings") as mock_settings,
        ):
            mock_settings.STRAVA_SIGNATURE_USER_IDS = set()
            mock_activity.save_bulk = AsyncMock()
            mock_weather.upsert_from_dto = AsyncMock()
            mock_detail.patch = AsyncMock()
            await _dispatch_activity_uploaded(user, event)

            mock_detail.patch.assert_called_once()
            patch_kwargs = mock_detail.patch.call_args.kwargs
            assert "warmup_time_sec" not in patch_kwargs
            assert "cooldown_time_sec" not in patch_kwargs
            assert "polarization_index" not in patch_kwargs
            assert patch_kwargs["trimp"] == 65.2


class TestDispatchActivityUpdated:
    @pytest.mark.asyncio
    async def test_saves_without_actors(self):
        """ACTIVITY_UPDATED only saves — no actors to avoid rename→update loop."""
        event = _make_event({**ACTIVITY_UPLOADED_EVENT, "type": "ACTIVITY_UPDATED"})
        user = _make_user_dto()
        with (
            patch("api.routers.intervals.webhook.Activity") as mock_activity,
            patch("api.routers.intervals.webhook.actor_update_activity_details") as mock_details,
        ):
            mock_activity.save_bulk = AsyncMock()
            await _dispatch_activity_updated(user, event)
            mock_activity.save_bulk.assert_called_once()
            mock_details.send.assert_not_called()


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
        ):
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_calendar_dispatched(self):
        event = _make_event(CALENDAR_UPDATED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_calendar") as mock_dispatch,
        ):
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_sport_settings_dispatched(self):
        event = _make_event(SPORT_SETTINGS_UPDATED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_sport_settings") as mock_dispatch,
        ):
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_scope_changed_dispatched(self):
        event = _make_event(APP_SCOPE_CHANGED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_scope_changed", new_callable=AsyncMock) as mock_dispatch,
        ):
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_achievements_dispatched(self):
        event = _make_event(ACTIVITY_ACHIEVEMENTS_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_achievements") as mock_dispatch,
        ):
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_fitness_dispatched(self):
        event = _make_event(FITNESS_UPDATED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_fitness") as mock_dispatch,
        ):
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_activity_uploaded_dispatched(self):
        event = _make_event(ACTIVITY_UPLOADED_EVENT)
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_activity_uploaded", new_callable=AsyncMock) as mock_dispatch,
        ):
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=_make_orm_user_mock())
            await _handle_webhook_event(event)
            mock_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_activity_updated_dispatched(self):
        event = _make_event({**ACTIVITY_UPLOADED_EVENT, "type": "ACTIVITY_UPDATED"})
        with (
            patch("api.routers.intervals.webhook.User") as mock_user_cls,
            patch("api.routers.intervals.webhook._dispatch_activity_updated", new_callable=AsyncMock) as mock_dispatch,
        ):
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
        with patch("api.routers.intervals.webhook.User") as mock_user_cls:
            mock_user = MagicMock()
            mock_user.id = 1
            mock_user_cls.get_by_athlete_id = AsyncMock(return_value=mock_user)
            # Should not raise
            await _handle_webhook_event(event)
