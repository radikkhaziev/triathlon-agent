"""Integration tests for data/garmin_client.py.

These tests hit the real Garmin Connect API using credentials from .env.
Requires GARMIN_EMAIL and GARMIN_PASSWORD to be set.
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from dotenv import load_dotenv

from data.garmin_client import GarminClient, _map_sport, _minutes_to_hours
from data.models import (
    Activity,
    BodyBatteryData,
    BodyCompositionData,
    CyclingFTPData,
    EnduranceScoreData,
    HeartRateData,
    HRVData,
    LactateThresholdData,
    MaxMetricsData,
    RacePrediction,
    RespirationData,
    ScheduledWorkout,
    SleepData,
    SpO2Data,
    SportType,
    StressData,
    DailyStats,
    TrainingReadinessData,
    TrainingStatusData,
)

load_dotenv()

TEST_DATE = "2026-03-15"
TEST_DATE_OBJ = date(2026, 3, 15)


# ---------------------------------------------------------------------------
# Pure function tests (no API needed)
# ---------------------------------------------------------------------------


class TestMapSport:
    def test_running(self):
        assert _map_sport("running") == SportType.RUN

    def test_trail_running(self):
        assert _map_sport("trail_running") == SportType.RUN

    def test_cycling(self):
        assert _map_sport("cycling") == SportType.BIKE

    def test_indoor_cycling(self):
        assert _map_sport("indoor_cycling") == SportType.BIKE

    def test_lap_swimming(self):
        assert _map_sport("lap_swimming") == SportType.SWIM

    def test_open_water(self):
        assert _map_sport("open_water_swimming") == SportType.SWIM

    def test_strength(self):
        assert _map_sport("strength_training") == SportType.STRENGTH

    def test_unknown(self):
        assert _map_sport("yoga") == SportType.OTHER

    def test_empty_string(self):
        assert _map_sport("") == SportType.OTHER

    def test_case_insensitive(self):
        assert _map_sport("Running") == SportType.RUN

    def test_spaces_to_underscores(self):
        assert _map_sport("trail running") == SportType.RUN


class TestMinutesToHours:
    def test_none_returns_none(self):
        assert _minutes_to_hours(None) is None

    def test_zero_returns_zero(self):
        assert _minutes_to_hours(0) == 0

    def test_exact_hour(self):
        assert _minutes_to_hours(60) == 1

    def test_rounds_up_partial_hour(self):
        assert _minutes_to_hours(61) == 2

    def test_rounds_up_one_minute(self):
        assert _minutes_to_hours(1) == 1

    def test_two_exact_hours(self):
        assert _minutes_to_hours(120) == 2

    def test_two_hours_plus_one_minute(self):
        assert _minutes_to_hours(121) == 3

    def test_large_value(self):
        assert _minutes_to_hours(1440) == 24


# ---------------------------------------------------------------------------
# Integration tests — real Garmin API
# ---------------------------------------------------------------------------

_email = os.getenv("GARMIN_EMAIL")
_password = os.getenv("GARMIN_PASSWORD")

pytestmark = pytest.mark.skipif(
    not _email or not _password,
    reason="GARMIN_EMAIL / GARMIN_PASSWORD not set in .env",
)


@pytest.fixture(scope="module")
def gc() -> GarminClient:
    """Single GarminClient instance shared across all integration tests."""
    return GarminClient(_email, _password)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_client_is_connected(self, gc):
        """After construction the internal client should be set."""
        assert gc.client is not None

    def test_ensure_connected_does_not_raise(self, gc):
        gc._ensure_connected()
        assert gc.client is not None


# ---------------------------------------------------------------------------
# get_sleep
# ---------------------------------------------------------------------------


class TestGetSleep:
    def test_returns_sleep_data(self, gc):
        result = gc.get_sleep(TEST_DATE)

        assert isinstance(result, SleepData)
        assert result.date == TEST_DATE_OBJ

    def test_sleep_score_in_valid_range(self, gc):
        result = gc.get_sleep(TEST_DATE)

        assert 0 <= result.score <= 100

    def test_duration_is_non_negative(self, gc):
        result = gc.get_sleep(TEST_DATE)

        assert result.duration >= 0


# ---------------------------------------------------------------------------
# get_hrv
# ---------------------------------------------------------------------------


class TestGetHrv:
    def test_returns_hrv_data(self, gc):
        result = gc.get_hrv(TEST_DATE)

        assert isinstance(result, HRVData)
        assert result.date == TEST_DATE_OBJ

    def test_hrv_values_are_non_negative(self, gc):
        result = gc.get_hrv(TEST_DATE)

        assert result.hrv_weekly_avg >= 0
        assert result.hrv_last_night >= 0

    def test_status_is_non_empty_string(self, gc):
        result = gc.get_hrv(TEST_DATE)

        assert isinstance(result.status, str)
        assert len(result.status) > 0


# ---------------------------------------------------------------------------
# get_body_battery
# ---------------------------------------------------------------------------


class TestGetBodyBattery:
    def test_returns_list(self, gc):
        results = gc.get_body_battery(TEST_DATE, TEST_DATE)

        assert isinstance(results, list)

    def test_entries_are_body_battery_data(self, gc):
        results = gc.get_body_battery(TEST_DATE, TEST_DATE)

        for entry in results:
            assert isinstance(entry, BodyBatteryData)

    def test_values_in_valid_range(self, gc):
        results = gc.get_body_battery(TEST_DATE, TEST_DATE)

        for entry in results:
            assert 0 <= entry.start_value <= 100
            assert 0 <= entry.end_value <= 100


# ---------------------------------------------------------------------------
# get_stress
# ---------------------------------------------------------------------------


class TestGetStress:
    def test_returns_stress_data(self, gc):
        result = gc.get_stress(TEST_DATE)

        assert isinstance(result, StressData)
        assert result.date == TEST_DATE_OBJ

    def test_stress_values_non_negative(self, gc):
        result = gc.get_stress(TEST_DATE)

        assert result.avg_stress >= 0
        assert result.max_stress >= 0
        assert result.stress_duration_seconds >= 0
        assert result.rest_duration_seconds >= 0


# ---------------------------------------------------------------------------
# get_resting_hr
# ---------------------------------------------------------------------------


class TestGetRestingHr:
    def test_returns_float(self, gc):
        result = gc.get_resting_hr(TEST_DATE)

        assert isinstance(result, float)

    def test_resting_hr_in_plausible_range(self, gc):
        result = gc.get_resting_hr(TEST_DATE)

        # 0 means no data; otherwise should be plausible
        assert result == 0.0 or 25.0 <= result <= 120.0


# ---------------------------------------------------------------------------
# get_scheduled_workouts
# ---------------------------------------------------------------------------


class TestGetScheduledWorkouts:
    def test_returns_list(self, gc):
        results = gc.get_scheduled_workouts(TEST_DATE, TEST_DATE)

        assert isinstance(results, list)

    def test_entries_are_scheduled_workout(self, gc):
        results = gc.get_scheduled_workouts(TEST_DATE, TEST_DATE)

        for w in results:
            assert isinstance(w, ScheduledWorkout)
            assert isinstance(w.sport, SportType)


# ---------------------------------------------------------------------------
# get_activities
# ---------------------------------------------------------------------------


class TestGetActivities:
    def test_returns_list(self, gc):
        results = gc.get_activities(start=0, limit=5)

        assert isinstance(results, list)

    def test_entries_are_activity(self, gc):
        results = gc.get_activities(start=0, limit=5)

        for a in results:
            assert isinstance(a, Activity)
            assert isinstance(a.sport, SportType)
            assert a.activity_id > 0
            assert a.duration_seconds >= 0

    def test_limit_is_respected(self, gc):
        results = gc.get_activities(start=0, limit=3)

        assert len(results) <= 3


# ---------------------------------------------------------------------------
# get_training_readiness
# ---------------------------------------------------------------------------


class TestGetTrainingReadiness:
    def test_returns_training_readiness_data(self, gc):
        result = gc.get_training_readiness(TEST_DATE)

        assert isinstance(result, TrainingReadinessData)
        assert result.date == TEST_DATE_OBJ

    def test_score_in_valid_range(self, gc):
        result = gc.get_training_readiness(TEST_DATE)

        assert 0 <= result.score <= 100

    def test_level_is_non_empty_string(self, gc):
        result = gc.get_training_readiness(TEST_DATE)

        assert isinstance(result.level, str)
        assert len(result.level) > 0


# ---------------------------------------------------------------------------
# get_training_status
# ---------------------------------------------------------------------------


class TestGetTrainingStatus:
    def test_returns_training_status_data(self, gc):
        result = gc.get_training_status(TEST_DATE)

        assert isinstance(result, TrainingStatusData)
        assert result.date == TEST_DATE_OBJ

    def test_training_status_is_non_empty_string(self, gc):
        result = gc.get_training_status(TEST_DATE)

        assert isinstance(result.training_status, str)
        assert len(result.training_status) > 0

    def test_vo2max_plausible_if_present(self, gc):
        result = gc.get_training_status(TEST_DATE)

        if result.vo2_max_run is not None:
            assert 20.0 <= result.vo2_max_run <= 90.0
        if result.vo2_max_bike is not None:
            assert 20.0 <= result.vo2_max_bike <= 90.0


# ---------------------------------------------------------------------------
# get_activities_by_date
# ---------------------------------------------------------------------------


class TestGetActivitiesByDate:
    def test_returns_list(self, gc):
        results = gc.get_activities_by_date(TEST_DATE, TEST_DATE)

        assert isinstance(results, list)

    def test_entries_are_activity(self, gc):
        results = gc.get_activities_by_date(TEST_DATE, TEST_DATE)

        for a in results:
            assert isinstance(a, Activity)
            assert isinstance(a.sport, SportType)


# ---------------------------------------------------------------------------
# get_heart_rates
# ---------------------------------------------------------------------------


class TestGetHeartRates:
    def test_returns_heart_rate_data(self, gc):
        result = gc.get_heart_rates(TEST_DATE)

        assert isinstance(result, HeartRateData)
        assert result.date == TEST_DATE_OBJ

    def test_values_non_negative(self, gc):
        result = gc.get_heart_rates(TEST_DATE)

        assert result.resting_hr >= 0
        assert result.max_hr >= 0
        assert result.min_hr >= 0


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    def test_returns_daily_stats(self, gc):
        result = gc.get_stats(TEST_DATE)

        assert isinstance(result, DailyStats)
        assert result.date == TEST_DATE_OBJ

    def test_values_non_negative(self, gc):
        result = gc.get_stats(TEST_DATE)

        assert result.total_steps >= 0
        assert result.total_distance_meters >= 0
        assert result.total_calories >= 0


# ---------------------------------------------------------------------------
# get_body_composition
# ---------------------------------------------------------------------------


class TestGetBodyComposition:
    def test_returns_list(self, gc):
        results = gc.get_body_composition(TEST_DATE, TEST_DATE)

        assert isinstance(results, list)

    def test_entries_are_body_composition(self, gc):
        results = gc.get_body_composition(TEST_DATE, TEST_DATE)

        for entry in results:
            assert isinstance(entry, BodyCompositionData)
            if entry.weight_kg is not None:
                assert 20.0 <= entry.weight_kg <= 300.0


# ---------------------------------------------------------------------------
# get_respiration
# ---------------------------------------------------------------------------


class TestGetRespiration:
    def test_returns_respiration_data(self, gc):
        result = gc.get_respiration(TEST_DATE)

        assert isinstance(result, RespirationData)
        assert result.date == TEST_DATE_OBJ

    def test_breathing_rate_non_negative(self, gc):
        result = gc.get_respiration(TEST_DATE)

        assert result.avg_breathing_rate >= 0


# ---------------------------------------------------------------------------
# get_spo2
# ---------------------------------------------------------------------------


class TestGetSpO2:
    def test_returns_spo2_data(self, gc):
        result = gc.get_spo2(TEST_DATE)

        assert isinstance(result, SpO2Data)
        assert result.date == TEST_DATE_OBJ

    def test_spo2_in_valid_range(self, gc):
        result = gc.get_spo2(TEST_DATE)

        # 0 means no data; otherwise 70-100%
        assert result.avg_spo2 == 0.0 or 70.0 <= result.avg_spo2 <= 100.0


# ---------------------------------------------------------------------------
# get_max_metrics
# ---------------------------------------------------------------------------


class TestGetMaxMetrics:
    def test_returns_max_metrics_data(self, gc):
        result = gc.get_max_metrics(TEST_DATE)

        assert isinstance(result, MaxMetricsData)
        assert result.date == TEST_DATE_OBJ

    def test_vo2max_plausible_if_present(self, gc):
        result = gc.get_max_metrics(TEST_DATE)

        if result.vo2_max_run is not None:
            assert 20.0 <= result.vo2_max_run <= 90.0
        if result.vo2_max_bike is not None:
            assert 20.0 <= result.vo2_max_bike <= 90.0


# ---------------------------------------------------------------------------
# get_race_predictions
# ---------------------------------------------------------------------------


class TestGetRacePredictions:
    def test_returns_list(self, gc):
        results = gc.get_race_predictions()

        assert isinstance(results, list)

    def test_entries_are_race_prediction(self, gc):
        results = gc.get_race_predictions()

        for p in results:
            assert isinstance(p, RacePrediction)
            assert p.predicted_time_seconds > 0


# ---------------------------------------------------------------------------
# get_endurance_score
# ---------------------------------------------------------------------------


class TestGetEnduranceScore:
    def test_returns_endurance_score_data(self, gc):
        result = gc.get_endurance_score(TEST_DATE)

        assert isinstance(result, EnduranceScoreData)
        assert result.date == TEST_DATE_OBJ

    def test_score_non_negative(self, gc):
        result = gc.get_endurance_score(TEST_DATE)

        assert result.overall_score >= 0


# ---------------------------------------------------------------------------
# get_lactate_threshold
# ---------------------------------------------------------------------------


class TestGetLactateThreshold:
    def test_returns_lactate_threshold_data(self, gc):
        result = gc.get_lactate_threshold()

        assert isinstance(result, LactateThresholdData)

    def test_heart_rate_plausible_if_present(self, gc):
        result = gc.get_lactate_threshold()

        if result.heart_rate is not None:
            assert 80.0 <= result.heart_rate <= 220.0


# ---------------------------------------------------------------------------
# get_cycling_ftp
# ---------------------------------------------------------------------------


class TestGetCyclingFTP:
    def test_returns_cycling_ftp_data(self, gc):
        result = gc.get_cycling_ftp()

        assert isinstance(result, CyclingFTPData)

    def test_ftp_plausible_if_present(self, gc):
        result = gc.get_cycling_ftp()

        if result.ftp is not None:
            assert 50.0 <= result.ftp <= 500.0
