"""Unit tests for Garmin GDPR export DTOs (data/garmin/dto.py).

All tests are pure in-memory — no database, no file system.
"""

from __future__ import annotations

import pytest

from data.garmin.dto import (
    GarminAbnormalHrEventDTO,
    GarminBioMetricsDTO,
    GarminDailySummaryDTO,
    GarminFitnessMetricsDTO,
    GarminHealthStatusDTO,
    GarminRacePredictionsDTO,
    GarminSleepDTO,
    GarminTrainingLoadDTO,
    GarminTrainingReadinessDTO,
    _ms_to_date,
)

# ---------------------------------------------------------------------------
# _ms_to_date helper
# ---------------------------------------------------------------------------


class TestMsToDate:
    def test_integer_ms_epoch_converts_to_iso_date(self):
        # 1701043200000 ms = 2023-11-27 00:00:00 UTC
        result = _ms_to_date(1701043200000)
        assert result == "2023-11-27"

    def test_string_passthrough_full_iso(self):
        result = _ms_to_date("2025-04-10")
        assert result == "2025-04-10"

    def test_string_passthrough_truncates_to_date_part(self):
        # ISO datetime string — only the first 10 chars returned
        result = _ms_to_date("2025-04-10T13:45:00.0")
        assert result == "2025-04-10"

    def test_zero_ms_epoch(self):
        # epoch 0 = 1970-01-01 UTC
        result = _ms_to_date(0)
        assert result == "1970-01-01"

    def test_recent_ms_epoch(self):
        # 1735689600000 = 2025-01-01 00:00:00 UTC
        result = _ms_to_date(1735689600000)
        assert result == "2025-01-01"


# ---------------------------------------------------------------------------
# GarminSleepDTO
# ---------------------------------------------------------------------------


class TestGarminSleepDTO:
    def _full_raw(self) -> dict:
        return {
            "calendarDate": "2025-04-05",
            "sleepStartTimestampGMT": "2025-04-04T22:00:00.0",
            "sleepEndTimestampGMT": "2025-04-05T06:30:00.0",
            "deepSleepSeconds": 4800,
            "lightSleepSeconds": 12000,
            "remSleepSeconds": 5400,
            "awakeSleepSeconds": 300,
            "averageRespiration": 15.5,
            "lowestRespiration": 12.0,
            "highestRespiration": 20.0,
            "avgSleepStress": 18.3,
            "awakeCount": 2,
            "restlessMomentCount": 5,
            "sleepScores": {
                "overallScore": 87,
                "qualityScore": 82,
                "durationScore": 90,
                "recoveryScore": 85,
                "deepScore": 88,
                "remScore": 79,
                "restfulnessScore": 91,
                "feedback": "GOOD",
            },
        }

    def test_full_data_parses_all_fields(self):
        dto = GarminSleepDTO.from_garmin(self._full_raw())

        assert dto.calendar_date == "2025-04-05"
        assert dto.sleep_start_gmt == "2025-04-04T22:00:00.0"
        assert dto.sleep_end_gmt == "2025-04-05T06:30:00.0"
        assert dto.deep_sleep_secs == 4800
        assert dto.light_sleep_secs == 12000
        assert dto.rem_sleep_secs == 5400
        assert dto.awake_sleep_secs == 300
        assert dto.avg_respiration == 15.5
        assert dto.lowest_respiration == 12.0
        assert dto.highest_respiration == 20.0
        assert dto.avg_sleep_stress == pytest.approx(18.3)
        assert dto.awake_count == 2
        assert dto.restless_moments == 5

    def test_full_data_parses_sleep_scores(self):
        dto = GarminSleepDTO.from_garmin(self._full_raw())

        assert dto.overall_score == 87
        assert dto.quality_score == 82
        assert dto.duration_score == 90
        assert dto.recovery_score == 85
        assert dto.deep_score == 88
        assert dto.rem_score == 79
        assert dto.restfulness_score == 91
        assert dto.feedback == "GOOD"

    def test_missing_sleep_scores_yields_none(self):
        raw = {"calendarDate": "2025-04-05"}
        dto = GarminSleepDTO.from_garmin(raw)

        assert dto.overall_score is None
        assert dto.quality_score is None
        assert dto.feedback is None

    def test_explicit_null_sleep_scores_yields_none(self):
        raw = {"calendarDate": "2025-04-05", "sleepScores": None}
        dto = GarminSleepDTO.from_garmin(raw)

        assert dto.overall_score is None

    def test_optional_fields_default_to_none(self):
        dto = GarminSleepDTO.from_garmin({"calendarDate": "2025-01-01"})

        assert dto.sleep_start_gmt is None
        assert dto.sleep_end_gmt is None
        assert dto.deep_sleep_secs is None
        assert dto.avg_respiration is None
        assert dto.awake_count is None


# ---------------------------------------------------------------------------
# GarminDailySummaryDTO
# ---------------------------------------------------------------------------


class TestGarminDailySummaryDTO:
    def _full_raw(self) -> dict:
        return {
            "calendarDate": "2023-11-27",
            "totalSteps": 5288,
            "totalDistanceMeters": 4162.0,
            "totalKilocalories": 2460.0,
            "activeKilocalories": 446.0,
            "floorsAscendedInMeters": 30.674,
            "highlyActiveSeconds": 120,
            "activeSeconds": 5233,
            "minHeartRate": 59,
            "maxHeartRate": 142,
            "restingHeartRate": 63,
            "allDayStress": {
                "aggregatorList": [
                    {
                        "type": "TOTAL",
                        "averageStressLevel": 48,
                        "maxStressLevel": 96,
                        "highDuration": 5160,
                        "mediumDuration": 5880,
                        "lowDuration": 2760,
                        "restDuration": 7800,
                    },
                    {
                        "type": "AWAKE",
                        "averageStressLevel": 50,
                        "maxStressLevel": 96,
                    },
                ]
            },
            "bodyBattery": {
                "chargedValue": 45,
                "drainedValue": 30,
                "bodyBatteryStatList": [
                    {"bodyBatteryStatType": "HIGHEST", "statsValue": 85},
                    {"bodyBatteryStatType": "LOWEST", "statsValue": 22},
                ],
            },
        }

    def test_full_data_activity_fields(self):
        dto = GarminDailySummaryDTO.from_garmin(self._full_raw())

        assert dto.calendar_date == "2023-11-27"
        assert dto.total_steps == 5288
        assert dto.total_distance_m == 4162.0
        assert dto.total_calories == 2460
        assert dto.active_calories == 446
        assert dto.floors_ascended_m == pytest.approx(30.674)
        assert dto.highly_active_secs == 120
        assert dto.active_secs == 5233

    def test_full_data_hr_fields(self):
        dto = GarminDailySummaryDTO.from_garmin(self._full_raw())

        assert dto.min_hr == 59
        assert dto.max_hr == 142
        assert dto.resting_hr == 63

    def test_stress_extracted_from_total_aggregator(self):
        dto = GarminDailySummaryDTO.from_garmin(self._full_raw())

        # Must pick the TOTAL aggregator, not AWAKE
        assert dto.avg_stress == 48
        assert dto.max_stress == 96
        assert dto.stress_high_secs == 5160
        assert dto.stress_medium_secs == 5880
        assert dto.stress_low_secs == 2760
        assert dto.stress_rest_secs == 7800

    def test_body_battery_parsed(self):
        dto = GarminDailySummaryDTO.from_garmin(self._full_raw())

        assert dto.body_battery_high == 85
        assert dto.body_battery_low == 22
        assert dto.body_battery_charged == 45
        assert dto.body_battery_drained == 30

    def test_missing_all_day_stress_yields_none(self):
        raw = {"calendarDate": "2025-01-01"}
        dto = GarminDailySummaryDTO.from_garmin(raw)

        assert dto.avg_stress is None
        assert dto.max_stress is None
        assert dto.stress_high_secs is None

    def test_null_all_day_stress_yields_none(self):
        raw = {"calendarDate": "2025-01-01", "allDayStress": None}
        dto = GarminDailySummaryDTO.from_garmin(raw)

        assert dto.avg_stress is None

    def test_missing_body_battery_yields_none(self):
        raw = {"calendarDate": "2025-01-01"}
        dto = GarminDailySummaryDTO.from_garmin(raw)

        assert dto.body_battery_high is None
        assert dto.body_battery_low is None
        assert dto.body_battery_charged is None
        assert dto.body_battery_drained is None

    def test_calories_as_float_coerced_to_int(self):
        raw = {"calendarDate": "2025-01-01", "totalKilocalories": 2460.9, "activeKilocalories": 446.1}
        dto = GarminDailySummaryDTO.from_garmin(raw)

        assert dto.total_calories == 2460
        assert dto.active_calories == 446
        assert isinstance(dto.total_calories, int)

    def test_aggregator_list_without_total_yields_none_stress(self):
        raw = {
            "calendarDate": "2025-01-01",
            "allDayStress": {
                "aggregatorList": [
                    {"type": "AWAKE", "averageStressLevel": 50},
                ]
            },
        }
        dto = GarminDailySummaryDTO.from_garmin(raw)

        assert dto.avg_stress is None


# ---------------------------------------------------------------------------
# GarminTrainingReadinessDTO
# ---------------------------------------------------------------------------


class TestGarminTrainingReadinessDTO:
    def test_all_fields_parsed(self):
        raw = {
            "calendarDate": "2025-04-05",
            "timestamp": "2025-04-05T07:30:00.0",
            "score": 72,
            "level": "GOOD",
            "feedbackShort": "GOOD_RECOVERY",
            "feedbackLong": "Your recovery looks good.",
            "sleepScoreFactorPercent": 28.5,
            "recoveryTime": 24,
            "recoveryTimeFactorPercent": 15.0,
            "acwrFactorPercent": 10.0,
            "stressHistoryFactorPercent": 8.0,
            "hrvFactorPercent": 22.0,
            "sleepHistoryFactorPercent": 16.5,
            "hrvWeeklyAverage": 58.3,
            "acuteLoad": 420.0,
            "inputContext": "AFTER_WAKEUP_RESET",
        }
        dto = GarminTrainingReadinessDTO.from_garmin(raw)

        assert dto.calendar_date == "2025-04-05"
        assert dto.timestamp_gmt == "2025-04-05T07:30:00.0"
        assert dto.score == 72
        assert dto.level == "GOOD"
        assert dto.feedback_short == "GOOD_RECOVERY"
        assert dto.feedback_long == "Your recovery looks good."
        assert dto.sleep_score_factor_pct == pytest.approx(28.5)
        assert dto.recovery_time == 24
        assert dto.recovery_factor_pct == pytest.approx(15.0)
        assert dto.acwr_factor_pct == pytest.approx(10.0)
        assert dto.stress_history_factor_pct == pytest.approx(8.0)
        assert dto.hrv_factor_pct == pytest.approx(22.0)
        assert dto.sleep_history_factor_pct == pytest.approx(16.5)
        assert dto.hrv_weekly_avg == pytest.approx(58.3)
        assert dto.acute_load == pytest.approx(420.0)
        assert dto.input_context == "AFTER_WAKEUP_RESET"

    def test_minimal_raw_yields_none_optionals(self):
        dto = GarminTrainingReadinessDTO.from_garmin({"calendarDate": "2023-11-27"})

        assert dto.score is None
        assert dto.level is None
        assert dto.hrv_weekly_avg is None
        assert dto.input_context is None


# ---------------------------------------------------------------------------
# GarminHealthStatusDTO
# ---------------------------------------------------------------------------


class TestGarminHealthStatusDTO:
    def _raw_with_all_metrics(self) -> dict:
        return {
            "calendarDate": "2025-09-18",
            "metrics": [
                {
                    "type": "HRV",
                    "value": 75.0,
                    "baselineLowerLimit": 50.0,
                    "baselineUpperLimit": 90.0,
                    "status": "BALANCED",
                },
                {
                    "type": "HR",
                    "value": 59.0,
                    "baselineLowerLimit": 52.0,
                    "baselineUpperLimit": 68.0,
                    "status": "BALANCED",
                },
                {
                    "type": "SPO2",
                    "value": 97.0,
                    "baselineLowerLimit": 95.0,
                    "baselineUpperLimit": 99.0,
                    "status": "BALANCED",
                },
                {
                    "type": "SKIN_TEMP_C",
                    "value": 36.2,
                    "baselineLowerLimit": 35.8,
                    "baselineUpperLimit": 36.6,
                    "status": "BALANCED",
                },
                {
                    "type": "RESPIRATION",
                    "value": 15.4,
                    "baselineLowerLimit": 13.0,
                    "baselineUpperLimit": 18.0,
                    "status": "BALANCED",
                },
            ],
        }

    def test_all_metric_types_parsed(self):
        dto = GarminHealthStatusDTO.from_garmin(self._raw_with_all_metrics())

        assert dto.calendar_date == "2025-09-18"

        assert dto.hrv_value == 75.0
        assert dto.hrv_baseline_lower == 50.0
        assert dto.hrv_baseline_upper == 90.0
        assert dto.hrv_status == "BALANCED"

        assert dto.hr_value == 59.0
        assert dto.hr_baseline_lower == 52.0
        assert dto.hr_baseline_upper == 68.0
        assert dto.hr_status == "BALANCED"

        assert dto.spo2_value == 97.0
        assert dto.spo2_status == "BALANCED"

        assert dto.skin_temp_value == pytest.approx(36.2)
        assert dto.skin_temp_status == "BALANCED"

        assert dto.respiration_value == pytest.approx(15.4)
        assert dto.respiration_status == "BALANCED"

    def test_missing_metric_type_yields_none_for_that_metric(self):
        # Only HRV and HR present — SPO2/SKIN_TEMP/RESPIRATION must be None
        raw = {
            "calendarDate": "2025-01-01",
            "metrics": [
                {"type": "HRV", "value": 60.0, "baselineLowerLimit": 45.0, "baselineUpperLimit": 80.0, "status": "OK"},
                {"type": "HR", "value": 55.0, "baselineLowerLimit": 50.0, "baselineUpperLimit": 70.0, "status": "OK"},
            ],
        }
        dto = GarminHealthStatusDTO.from_garmin(raw)

        assert dto.hrv_value == 60.0
        assert dto.hr_value == 55.0
        assert dto.spo2_value is None
        assert dto.spo2_status is None
        assert dto.skin_temp_value is None
        assert dto.respiration_value is None

    def test_empty_metrics_list_yields_all_none(self):
        dto = GarminHealthStatusDTO.from_garmin({"calendarDate": "2025-01-01", "metrics": []})

        assert dto.hrv_value is None
        assert dto.hr_value is None
        assert dto.spo2_value is None
        assert dto.skin_temp_value is None
        assert dto.respiration_value is None

    def test_missing_metrics_key_yields_all_none(self):
        dto = GarminHealthStatusDTO.from_garmin({"calendarDate": "2025-01-01"})

        assert dto.hrv_value is None


# ---------------------------------------------------------------------------
# GarminTrainingLoadDTO
# ---------------------------------------------------------------------------


class TestGarminTrainingLoadDTO:
    def test_ms_epoch_calendar_date_converted(self):
        # 1701043200000 ms = 2023-11-27 UTC
        raw = {
            "calendarDate": 1701043200000,
            "dailyTrainingLoadAcute": 1,
            "dailyTrainingLoadChronic": 519,
            "dailyAcuteChronicWorkloadRatio": 0.002,
            "acwrStatus": "NONE",
        }
        dto = GarminTrainingLoadDTO.from_garmin(raw)

        assert dto.calendar_date == "2023-11-27"
        assert dto.acute_load == 1
        assert dto.chronic_load == 519
        assert dto.acwr == pytest.approx(0.002)
        assert dto.acwr_status == "NONE"

    def test_optional_fields_default_to_none(self):
        raw = {"calendarDate": 1701043200000}
        dto = GarminTrainingLoadDTO.from_garmin(raw)

        assert dto.acute_load is None
        assert dto.chronic_load is None
        assert dto.acwr is None
        assert dto.acwr_status is None


# ---------------------------------------------------------------------------
# GarminFitnessMetricsDTO
# ---------------------------------------------------------------------------


class TestGarminFitnessMetricsDTO:
    def test_from_vo2max_running(self):
        raw = {
            "calendarDate": "2025-03-15",
            "sport": "RUNNING",
            "vo2MaxValue": 42.5,
            "activityId": 123456789,
        }
        dto = GarminFitnessMetricsDTO.from_vo2max(raw)

        assert dto.calendar_date == "2025-03-15"
        assert dto.vo2max_running == pytest.approx(42.5)
        assert dto.vo2max_cycling is None
        assert dto.source_activity_id == "123456789"

    def test_from_vo2max_cycling(self):
        raw = {
            "calendarDate": "2025-08-06",
            "sport": "CYCLING",
            "vo2MaxValue": 54.0,
            "activityId": 19966844211,
        }
        dto = GarminFitnessMetricsDTO.from_vo2max(raw)

        assert dto.vo2max_cycling == pytest.approx(54.0)
        assert dto.vo2max_running is None
        assert dto.source_activity_id == "19966844211"

    def test_from_vo2max_unknown_sport_yields_both_none(self):
        raw = {
            "calendarDate": "2025-01-01",
            "sport": "SWIMMING",
            "vo2MaxValue": 40.0,
        }
        dto = GarminFitnessMetricsDTO.from_vo2max(raw)

        assert dto.vo2max_running is None
        assert dto.vo2max_cycling is None

    def test_from_vo2max_no_activity_id_yields_none_source(self):
        raw = {"calendarDate": "2025-01-01", "sport": "RUNNING", "vo2MaxValue": 38.0}
        dto = GarminFitnessMetricsDTO.from_vo2max(raw)

        assert dto.source_activity_id is None

    def test_from_endurance_with_ms_epoch(self):
        # 1701043200000 ms = 2023-11-27 UTC
        raw = {"calendarDate": 1701043200000, "overallScore": 62.5}
        dto = GarminFitnessMetricsDTO.from_endurance(raw)

        assert dto.calendar_date == "2023-11-27"
        assert dto.endurance_score == pytest.approx(62.5)
        assert dto.vo2max_running is None
        assert dto.vo2max_cycling is None

    def test_from_endurance_no_score_yields_none(self):
        raw = {"calendarDate": 1701043200000}
        dto = GarminFitnessMetricsDTO.from_endurance(raw)

        assert dto.endurance_score is None


# ---------------------------------------------------------------------------
# GarminRacePredictionsDTO
# ---------------------------------------------------------------------------


class TestGarminRacePredictionsDTO:
    def test_all_predictions_parsed(self):
        raw = {
            "calendarDate": "2023-12-03",
            "raceTime5K": 2023,
            "raceTime10K": 4375,
            "raceTimeHalf": 10160,
            "raceTimeMarathon": 22997,
        }
        dto = GarminRacePredictionsDTO.from_garmin(raw)

        assert dto.calendar_date == "2023-12-03"
        assert dto.prediction_5k_secs == 2023
        assert dto.prediction_10k_secs == 4375
        assert dto.prediction_half_secs == 10160
        assert dto.prediction_marathon_secs == 22997

    def test_missing_predictions_yield_none(self):
        dto = GarminRacePredictionsDTO.from_garmin({"calendarDate": "2025-01-01"})

        assert dto.prediction_5k_secs is None
        assert dto.prediction_10k_secs is None
        assert dto.prediction_half_secs is None
        assert dto.prediction_marathon_secs is None


# ---------------------------------------------------------------------------
# GarminBioMetricsDTO
# ---------------------------------------------------------------------------


class TestGarminBioMetricsDTO:
    def _raw_with_weight_g(self) -> dict:
        return {
            "metaData": {"calendarDate": "2023-11-27T13:12:55.727"},
            "weight": {"weight": 77000.0, "sourceType": "USER_SETTING"},
            "height": 173.0,
        }

    def test_weight_in_grams_converted_to_kg(self):
        dto = GarminBioMetricsDTO.from_garmin(self._raw_with_weight_g())

        assert dto is not None
        assert dto.weight_kg == pytest.approx(77.0, abs=0.05)

    def test_calendar_date_truncated_to_date_part(self):
        dto = GarminBioMetricsDTO.from_garmin(self._raw_with_weight_g())

        assert dto is not None
        assert dto.calendar_date == "2023-11-27"

    def test_height_as_float_meters_converted_to_cm(self):
        # height = 1.73 (meters) → should become 173.0 cm
        raw = {
            "metaData": {"calendarDate": "2023-11-27T13:12:55.727"},
            "height": 1.73,
            "weight": {"weight": 77000.0},
        }
        dto = GarminBioMetricsDTO.from_garmin(raw)

        assert dto is not None
        assert dto.height_cm == pytest.approx(173.0, abs=0.1)

    def test_height_as_float_cm_not_double_converted(self):
        # 173.0 >= 3.0 so it should stay as 173.0 cm (no conversion)
        dto = GarminBioMetricsDTO.from_garmin(self._raw_with_weight_g())

        assert dto is not None
        assert dto.height_cm == pytest.approx(173.0)

    def test_empty_entry_with_no_metrics_returns_none(self):
        # Entry with no weight, height, or LT data — from_garmin returns None
        raw = {
            "metaData": {"calendarDate": "2023-11-27T13:12:22.944"},
            "userSetNullForHeight": True,
            "userSetNullForWeight": True,
        }
        result = GarminBioMetricsDTO.from_garmin(raw)

        assert result is None

    def test_missing_calendar_date_returns_none(self):
        raw = {"weight": {"weight": 77000.0}}
        result = GarminBioMetricsDTO.from_garmin(raw)

        assert result is None

    def test_empty_meta_data_returns_none(self):
        raw = {"metaData": {}}
        result = GarminBioMetricsDTO.from_garmin(raw)

        assert result is None

    def test_lactate_threshold_fields_parsed(self):
        raw = {
            "metaData": {"calendarDate": "2024-06-01T00:00:00.0"},
            "lactateThreshold": {"heartRate": 162, "speed": 3.45},
        }
        dto = GarminBioMetricsDTO.from_garmin(raw)

        assert dto is not None
        assert dto.lactate_threshold_hr == 162
        assert dto.lactate_threshold_speed == pytest.approx(3.45)


# ---------------------------------------------------------------------------
# GarminAbnormalHrEventDTO
# ---------------------------------------------------------------------------


class TestGarminAbnormalHrEventDTO:
    def test_all_fields_parsed(self):
        raw = {
            "abnormalHrEventGMT": "2025-03-12T07:45:00.0",
            "calendarDate": "2025-03-12",
            "abnormalHrValue": 157,
            "abnormalHrThresholdValue": 150,
            "deviceId": 3454472246,
        }
        dto = GarminAbnormalHrEventDTO.from_garmin(raw)

        assert dto.timestamp_gmt == "2025-03-12T07:45:00.0"
        assert dto.calendar_date == "2025-03-12"
        assert dto.hr_value == 157
        assert dto.threshold_value == 150

    def test_calendar_date_falls_back_to_timestamp_prefix_when_missing(self):
        raw = {
            "abnormalHrEventGMT": "2025-06-15T08:30:00.0",
            "abnormalHrValue": 145,
            "abnormalHrThresholdValue": 140,
        }
        dto = GarminAbnormalHrEventDTO.from_garmin(raw)

        # calendarDate not provided — must derive first 10 chars from timestamp
        assert dto.calendar_date == "2025-06-15"

    def test_optional_hr_fields_yield_none_when_absent(self):
        raw = {"abnormalHrEventGMT": "2025-06-15T08:30:00.0"}
        dto = GarminAbnormalHrEventDTO.from_garmin(raw)

        assert dto.hr_value is None
        assert dto.threshold_value is None
