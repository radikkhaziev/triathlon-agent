"""Tests for athlete actors: actor_update_zones, actor_sync_athlete_settings, actor_sync_athlete_goals."""

from datetime import date
from unittest.mock import MagicMock, patch

from data.db.dto import DriftAlertDTO, ThresholdDriftDTO
from data.db.user import UserDTO
from data.intervals.dto import ScheduledWorkoutDTO, SportSettingsDTO


def _user(id: int = 1, chat_id: str = "test_user") -> UserDTO:
    return UserDTO(id=id, chat_id=chat_id, athlete_id="i123")


# ---------------------------------------------------------------------------
# actor_update_zones
# ---------------------------------------------------------------------------


class TestActorUpdateZones:
    def test_no_drift_does_nothing(self):
        from tasks.actors.athlets import actor_update_zones

        with patch("tasks.actors.athlets.User") as mock_user:
            mock_user.detect_threshold_drift.return_value = None
            actor_update_zones(_user())

        mock_user.detect_threshold_drift.assert_called_once_with(user_id=1)

    def test_drift_updates_settings_and_intervals(self):
        from tasks.actors.athlets import actor_update_zones

        drift = ThresholdDriftDTO(
            alerts=[
                DriftAlertDTO(
                    sport="Ride",
                    metric="LTHR",
                    measured=155,
                    config_value=148,
                    diff_pct=4.7,
                    message="HRVT1 stable at 155 bpm",
                ),
            ]
        )

        mock_client = MagicMock()
        with (
            patch("tasks.actors.athlets.User") as mock_user,
            patch("tasks.actors.athlets.AthleteSettings") as mock_settings,
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets._actor_send_zones_notification") as mock_notify,
        ):
            mock_user.detect_threshold_drift.return_value = drift
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)

            actor_update_zones(_user())

        mock_settings.upsert.assert_called_once_with(user_id=1, sport="Ride", lthr=155)
        mock_client.update_sport_settings.assert_called_once_with("Ride", {"lthr": 155})
        mock_notify.send.assert_called_once()
        args = mock_notify.send.call_args
        updated_list = args[0][1]
        assert "LTHR Ride: 148 → 155 bpm" in updated_list

    def test_multiple_alerts_updates_all(self):
        from tasks.actors.athlets import actor_update_zones

        drift = ThresholdDriftDTO(
            alerts=[
                DriftAlertDTO(
                    sport="Ride",
                    metric="LTHR",
                    measured=155,
                    config_value=148,
                    diff_pct=4.7,
                    message="",
                ),
                DriftAlertDTO(
                    sport="Run",
                    metric="LTHR",
                    measured=170,
                    config_value=162,
                    diff_pct=4.9,
                    message="",
                ),
            ]
        )

        mock_client = MagicMock()
        with (
            patch("tasks.actors.athlets.User") as mock_user,
            patch("tasks.actors.athlets.AthleteSettings") as mock_settings,
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets._actor_send_zones_notification"),
        ):
            mock_user.detect_threshold_drift.return_value = drift
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)

            actor_update_zones(_user())

        assert mock_settings.upsert.call_count == 2
        mock_settings.upsert.assert_any_call(user_id=1, sport="Ride", lthr=155)
        mock_settings.upsert.assert_any_call(user_id=1, sport="Run", lthr=170)
        assert mock_client.update_sport_settings.call_count == 2

    def test_threshold_pace_alert_pushes_m_per_s(self):
        """THRESHOLD_PACE alert (sec/km in DB) → m/s for the Intervals.icu API."""
        from tasks.actors.athlets import actor_update_zones

        drift = ThresholdDriftDTO(
            alerts=[
                DriftAlertDTO(
                    sport="Run",
                    metric="THRESHOLD_PACE",
                    measured=260,  # 4:20/km
                    config_value=295,  # 4:55/km
                    diff_pct=-11.9,
                    message="",
                ),
            ]
        )

        mock_client = MagicMock()
        with (
            patch("tasks.actors.athlets.User") as mock_user,
            patch("tasks.actors.athlets.AthleteSettings") as mock_settings,
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets._actor_send_zones_notification") as mock_notify,
        ):
            mock_user.detect_threshold_drift.return_value = drift
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)

            actor_update_zones(_user())

        mock_settings.upsert.assert_called_once_with(user_id=1, sport="Run", threshold_pace=260.0)
        # 1000m / 260s = 3.846 m/s, rounded to 3 decimals
        mock_client.update_sport_settings.assert_called_once_with("Run", {"threshold_pace": 3.846})
        notify_payload = mock_notify.send.call_args[0][1]
        assert "Threshold pace Run: 4:55/km → 4:20/km" in notify_payload

    def test_ftp_alert_pushes_watts(self):
        """FTP alert (Ride) → push raw watts to Intervals + persist locally."""
        from tasks.actors.athlets import actor_update_zones

        drift = ThresholdDriftDTO(
            alerts=[
                DriftAlertDTO(
                    sport="Ride",
                    metric="FTP",
                    measured=240,
                    config_value=208,
                    diff_pct=15.4,
                    message="",
                ),
            ]
        )

        mock_client = MagicMock()
        with (
            patch("tasks.actors.athlets.User") as mock_user,
            patch("tasks.actors.athlets.AthleteSettings") as mock_settings,
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets._actor_send_zones_notification") as mock_notify,
        ):
            mock_user.detect_threshold_drift.return_value = drift
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)

            actor_update_zones(_user())

        mock_settings.upsert.assert_called_once_with(user_id=1, sport="Ride", ftp=240)
        mock_client.update_sport_settings.assert_called_once_with("Ride", {"ftp": 240})
        notify_payload = mock_notify.send.call_args[0][1]
        assert "FTP Ride: 208 → 240 W" in notify_payload

    def test_unknown_metric_skipped_with_warning(self):
        """Unrecognized metric does not break the loop."""
        from tasks.actors.athlets import actor_update_zones

        drift = ThresholdDriftDTO(
            alerts=[
                DriftAlertDTO(
                    sport="Run",
                    metric="MYSTERY",
                    measured=1,
                    config_value=2,
                    diff_pct=0,
                    message="",
                ),
            ]
        )
        mock_client = MagicMock()
        with (
            patch("tasks.actors.athlets.User") as mock_user,
            patch("tasks.actors.athlets.AthleteSettings") as mock_settings,
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets._actor_send_zones_notification"),
        ):
            mock_user.detect_threshold_drift.return_value = drift
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)
            actor_update_zones(_user())

        mock_settings.upsert.assert_not_called()
        mock_client.update_sport_settings.assert_not_called()


# ---------------------------------------------------------------------------
# actor_sync_athlete_settings
# ---------------------------------------------------------------------------


class TestActorSyncAthleteSettings:
    def test_syncs_ride_run_swim(self):
        from tasks.actors.athlets import actor_sync_athlete_settings

        settings = [
            SportSettingsDTO(id=1, types=["Ride", "VirtualRide"], lthr=148, max_hr=179, ftp=250),
            SportSettingsDTO(
                id=2,
                types=["Run", "VirtualRun"],
                lthr=162,
                max_hr=185,
                threshold_pace=3.5,
                pace_units="min/km",
            ),
            SportSettingsDTO(id=3, types=["Swim"], lthr=140, threshold_pace=1.5, pace_units="min/100m"),
        ]

        mock_client = MagicMock()
        mock_client.list_sport_settings.return_value = settings

        with (
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets.AthleteSettings") as mock_as,
        ):
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)

            actor_sync_athlete_settings(_user())

        assert mock_as.upsert.call_count == 3
        # Ride — assert kwargs individually so the test stays resilient to new
        # optional fields being added to upsert (e.g. zones-related kwargs).
        ride_call = [c for c in mock_as.upsert.call_args_list if c.kwargs.get("sport") == "Ride"][0]
        assert ride_call.kwargs["user_id"] == 1
        assert ride_call.kwargs["lthr"] == 148
        assert ride_call.kwargs["max_hr"] == 179
        assert ride_call.kwargs["ftp"] == 250
        assert ride_call.kwargs["threshold_pace"] is None
        assert ride_call.kwargs["pace_units"] is None
        # Run: threshold_pace converted from m/s to sec/km (1000/3.5 ≈ 285.7)
        run_call = [c for c in mock_as.upsert.call_args_list if c.kwargs.get("sport") == "Run"][0]
        assert run_call.kwargs["lthr"] == 162
        assert run_call.kwargs["threshold_pace"] == round(1000 / 3.5, 1)
        # Swim: threshold_pace converted from m/s to sec/100m (100/1.5 ≈ 66.7)
        swim_call = [c for c in mock_as.upsert.call_args_list if c.kwargs.get("sport") == "Swim"][0]
        assert swim_call.kwargs["threshold_pace"] == round(100 / 1.5, 1)

    def test_skips_non_primary_sports(self):
        from tasks.actors.athlets import actor_sync_athlete_settings

        settings = [
            SportSettingsDTO(id=10, types=["WeightTraining"], lthr=None),
            SportSettingsDTO(id=11, types=["Yoga"], lthr=None),
        ]

        mock_client = MagicMock()
        mock_client.list_sport_settings.return_value = settings

        with (
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets.AthleteSettings") as mock_as,
        ):
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)

            actor_sync_athlete_settings(_user())

        mock_as.upsert.assert_not_called()

    def test_zero_pace_not_converted(self):
        """threshold_pace=0 should pass through without division (no div-by-zero)."""
        from tasks.actors.athlets import actor_sync_athlete_settings

        settings = [
            SportSettingsDTO(id=20, types=["Run"], lthr=160, threshold_pace=0),
        ]

        mock_client = MagicMock()
        mock_client.list_sport_settings.return_value = settings

        with (
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets.AthleteSettings") as mock_as,
        ):
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)

            actor_sync_athlete_settings(_user())

        run_call = mock_as.upsert.call_args_list[0]
        assert run_call.kwargs["threshold_pace"] == 0


# ---------------------------------------------------------------------------
# actor_sync_athlete_goals
# ---------------------------------------------------------------------------


class TestActorSyncAthleteGoals:
    def test_syncs_new_goal_and_notifies(self):
        from tasks.actors.athlets import actor_sync_athlete_goals

        event = ScheduledWorkoutDTO(
            id=999,
            name="Ironman 70.3",
            start_date_local=date(2026, 9, 15),
        )

        mock_client = MagicMock()
        mock_client.get_events.side_effect = lambda oldest, newest, category: ([event] if category == "RACE_A" else [])

        with (
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets.AthleteGoal") as mock_goal,
            patch("tasks.actors.athlets._actor_send_goal_notification") as mock_notify,
        ):
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)
            mock_goal.get_all.return_value = []  # no existing goals

            actor_sync_athlete_goals(_user())

        mock_goal.upsert_from_intervals.assert_called_once_with(
            user_id=1,
            category="RACE_A",
            event_name="Ironman 70.3",
            event_date=date(2026, 9, 15),
            intervals_event_id=999,
            sport_type="fitness",  # ScheduledWorkoutDTO.type defaults to None → "fitness"
        )
        mock_notify.send.assert_called_once()

    def test_existing_goal_no_notification(self):
        from tasks.actors.athlets import actor_sync_athlete_goals

        event = ScheduledWorkoutDTO(
            id=999,
            name="Ironman 70.3",
            start_date_local=date(2026, 9, 15),
        )
        existing_goal = MagicMock()
        existing_goal.intervals_event_id = 999

        mock_client = MagicMock()
        mock_client.get_events.side_effect = lambda oldest, newest, category: ([event] if category == "RACE_A" else [])

        with (
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets.AthleteGoal") as mock_goal,
            patch("tasks.actors.athlets._actor_send_goal_notification") as mock_notify,
        ):
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)
            mock_goal.get_all.return_value = [existing_goal]

            actor_sync_athlete_goals(_user())

        mock_goal.upsert_from_intervals.assert_called_once()
        mock_notify.send.assert_not_called()

    def test_syncs_multiple_events_per_category(self):
        """Two RACE_A events (e.g. IM 70.3 + Oceanlava) → both upserted, both notified."""
        from tasks.actors.athlets import actor_sync_athlete_goals

        ev1 = ScheduledWorkoutDTO(id=101, name="Ironman 70.3", start_date_local=date(2026, 9, 15))
        ev2 = ScheduledWorkoutDTO(id=102, name="Oceanlava", start_date_local=date(2026, 10, 10))

        mock_client = MagicMock()
        mock_client.get_events.side_effect = lambda oldest, newest, category: (
            [ev1, ev2] if category == "RACE_A" else []
        )

        with (
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets.AthleteGoal") as mock_goal,
            patch("tasks.actors.athlets._actor_send_goal_notification") as mock_notify,
        ):
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)
            mock_goal.get_all.return_value = []

            actor_sync_athlete_goals(_user())

        assert mock_goal.upsert_from_intervals.call_count == 2
        event_ids = {c.kwargs["intervals_event_id"] for c in mock_goal.upsert_from_intervals.call_args_list}
        assert event_ids == {101, 102}
        assert mock_notify.send.call_count == 2

    def test_no_events_found(self):
        from tasks.actors.athlets import actor_sync_athlete_goals

        mock_client = MagicMock()
        mock_client.get_events.return_value = []

        with (
            patch("tasks.actors.athlets.IntervalsSyncClient") as mock_isc,
            patch("tasks.actors.athlets.AthleteGoal") as mock_goal,
            patch("tasks.actors.athlets._actor_send_goal_notification") as mock_notify,
        ):
            mock_isc.for_user.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_isc.for_user.return_value.__exit__ = MagicMock(return_value=False)
            mock_goal.get_all.return_value = []

            actor_sync_athlete_goals(_user())

        mock_goal.upsert_from_intervals.assert_not_called()
        mock_notify.send.assert_not_called()


# ---------------------------------------------------------------------------
# _actor_send_zones_notification
# ---------------------------------------------------------------------------


class TestSendZonesNotification:
    def test_sends_updated_message(self):
        from tasks.actors.athlets import _actor_send_zones_notification

        with patch("tasks.actors.athlets.TelegramTool") as mock_tg_cls:
            mock_tg = MagicMock()
            mock_tg_cls.return_value = mock_tg

            _actor_send_zones_notification(_user(), ["LTHR Ride: 148 → 155 bpm"])

        mock_tg.send_message.assert_called_once()
        msg = mock_tg.send_message.call_args.kwargs["text"]
        assert "Зоны обновлены" in msg
        assert "LTHR Ride: 148 → 155 bpm" in msg

    def test_sends_no_drift_message(self):
        from tasks.actors.athlets import _actor_send_zones_notification

        with patch("tasks.actors.athlets.TelegramTool") as mock_tg_cls:
            mock_tg = MagicMock()
            mock_tg_cls.return_value = mock_tg

            _actor_send_zones_notification(_user(), [])

        msg = mock_tg.send_message.call_args.kwargs["text"]
        assert "зоны актуальны" in msg
