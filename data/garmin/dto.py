"""Pydantic DTOs for Garmin GDPR export data."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel


class GarminSleepDTO(BaseModel):
    calendar_date: str
    sleep_start_gmt: str | None = None
    sleep_end_gmt: str | None = None

    deep_sleep_secs: int | None = None
    light_sleep_secs: int | None = None
    rem_sleep_secs: int | None = None
    awake_sleep_secs: int | None = None

    overall_score: int | None = None
    quality_score: int | None = None
    duration_score: int | None = None
    recovery_score: int | None = None
    deep_score: int | None = None
    rem_score: int | None = None
    restfulness_score: int | None = None

    avg_respiration: float | None = None
    lowest_respiration: float | None = None
    highest_respiration: float | None = None

    avg_sleep_stress: float | None = None
    awake_count: int | None = None
    restless_moments: int | None = None
    feedback: str | None = None

    @classmethod
    def from_garmin(cls, raw: dict) -> GarminSleepDTO:
        scores = raw.get("sleepScores") or {}
        return cls(
            calendar_date=raw["calendarDate"],
            sleep_start_gmt=raw.get("sleepStartTimestampGMT"),
            sleep_end_gmt=raw.get("sleepEndTimestampGMT"),
            deep_sleep_secs=raw.get("deepSleepSeconds"),
            light_sleep_secs=raw.get("lightSleepSeconds"),
            rem_sleep_secs=raw.get("remSleepSeconds"),
            awake_sleep_secs=raw.get("awakeSleepSeconds"),
            overall_score=scores.get("overallScore"),
            quality_score=scores.get("qualityScore"),
            duration_score=scores.get("durationScore"),
            recovery_score=scores.get("recoveryScore"),
            deep_score=scores.get("deepScore"),
            rem_score=scores.get("remScore"),
            restfulness_score=scores.get("restfulnessScore"),
            avg_respiration=raw.get("averageRespiration"),
            lowest_respiration=raw.get("lowestRespiration"),
            highest_respiration=raw.get("highestRespiration"),
            avg_sleep_stress=raw.get("avgSleepStress"),
            awake_count=raw.get("awakeCount"),
            restless_moments=raw.get("restlessMomentCount"),
            feedback=scores.get("feedback"),
        )


class GarminDailySummaryDTO(BaseModel):
    calendar_date: str

    total_steps: int | None = None
    total_distance_m: float | None = None
    total_calories: int | None = None
    active_calories: int | None = None
    floors_ascended_m: float | None = None
    highly_active_secs: int | None = None
    active_secs: int | None = None

    min_hr: int | None = None
    max_hr: int | None = None
    resting_hr: int | None = None

    avg_stress: int | None = None
    max_stress: int | None = None
    stress_high_secs: int | None = None
    stress_medium_secs: int | None = None
    stress_low_secs: int | None = None
    stress_rest_secs: int | None = None

    body_battery_high: int | None = None
    body_battery_low: int | None = None
    body_battery_charged: int | None = None
    body_battery_drained: int | None = None

    @classmethod
    def from_garmin(cls, raw: dict) -> GarminDailySummaryDTO:
        # Stress from allDayStress → TOTAL aggregator
        stress = raw.get("allDayStress") or {}
        agg_list = stress.get("aggregatorList") or []
        total_stress = next((a for a in agg_list if a.get("type") == "TOTAL"), {})

        # Body Battery
        bb = raw.get("bodyBattery") or {}
        bb_stats = {}
        for s in bb.get("bodyBatteryStatList") or []:
            stat_type = s.get("bodyBatteryStatType")
            if stat_type:
                bb_stats[stat_type] = s.get("statsValue")

        return cls(
            calendar_date=raw["calendarDate"],
            total_steps=raw.get("totalSteps"),
            total_distance_m=raw.get("totalDistanceMeters"),
            total_calories=_int_or_none(raw.get("totalKilocalories")),
            active_calories=_int_or_none(raw.get("activeKilocalories")),
            floors_ascended_m=raw.get("floorsAscendedInMeters"),
            highly_active_secs=raw.get("highlyActiveSeconds"),
            active_secs=raw.get("activeSeconds"),
            min_hr=raw.get("minHeartRate"),
            max_hr=raw.get("maxHeartRate"),
            resting_hr=raw.get("restingHeartRate"),
            avg_stress=total_stress.get("averageStressLevel"),
            max_stress=total_stress.get("maxStressLevel"),
            stress_high_secs=total_stress.get("highDuration"),
            stress_medium_secs=total_stress.get("mediumDuration"),
            stress_low_secs=total_stress.get("lowDuration"),
            stress_rest_secs=total_stress.get("restDuration"),
            body_battery_high=bb_stats.get("HIGHEST"),
            body_battery_low=bb_stats.get("LOWEST"),
            body_battery_charged=bb.get("chargedValue"),
            body_battery_drained=bb.get("drainedValue"),
        )


class GarminTrainingReadinessDTO(BaseModel):
    calendar_date: str
    timestamp_gmt: str | None = None

    score: int | None = None
    level: str | None = None
    feedback_short: str | None = None
    feedback_long: str | None = None

    sleep_score_factor_pct: float | None = None
    recovery_time: int | None = None
    recovery_factor_pct: float | None = None
    acwr_factor_pct: float | None = None
    stress_history_factor_pct: float | None = None
    hrv_factor_pct: float | None = None
    sleep_history_factor_pct: float | None = None

    hrv_weekly_avg: float | None = None
    acute_load: float | None = None
    input_context: str | None = None

    @classmethod
    def from_garmin(cls, raw: dict) -> GarminTrainingReadinessDTO:
        return cls(
            calendar_date=raw["calendarDate"],
            timestamp_gmt=raw.get("timestamp"),
            score=raw.get("score"),
            level=raw.get("level"),
            feedback_short=raw.get("feedbackShort"),
            feedback_long=raw.get("feedbackLong"),
            sleep_score_factor_pct=raw.get("sleepScoreFactorPercent"),
            recovery_time=raw.get("recoveryTime"),
            recovery_factor_pct=raw.get("recoveryTimeFactorPercent"),
            acwr_factor_pct=raw.get("acwrFactorPercent"),
            stress_history_factor_pct=raw.get("stressHistoryFactorPercent"),
            hrv_factor_pct=raw.get("hrvFactorPercent"),
            sleep_history_factor_pct=raw.get("sleepHistoryFactorPercent"),
            hrv_weekly_avg=raw.get("hrvWeeklyAverage"),
            acute_load=raw.get("acuteLoad"),
            input_context=raw.get("inputContext"),
        )


class GarminHealthStatusDTO(BaseModel):
    calendar_date: str

    hrv_value: float | None = None
    hrv_baseline_lower: float | None = None
    hrv_baseline_upper: float | None = None
    hrv_status: str | None = None

    hr_value: float | None = None
    hr_baseline_lower: float | None = None
    hr_baseline_upper: float | None = None
    hr_status: str | None = None

    spo2_value: float | None = None
    spo2_baseline_lower: float | None = None
    spo2_baseline_upper: float | None = None
    spo2_status: str | None = None

    skin_temp_value: float | None = None
    skin_temp_baseline_lower: float | None = None
    skin_temp_baseline_upper: float | None = None
    skin_temp_status: str | None = None

    respiration_value: float | None = None
    respiration_baseline_lower: float | None = None
    respiration_baseline_upper: float | None = None
    respiration_status: str | None = None

    @classmethod
    def from_garmin(cls, raw: dict) -> GarminHealthStatusDTO:
        metrics_by_type: dict[str, dict] = {}
        for m in raw.get("metrics") or []:
            metrics_by_type[m["type"]] = m

        def _get(metric_type: str, field: str) -> float | None:
            m = metrics_by_type.get(metric_type)
            if not m:
                return None
            return m.get(field)

        def _get_str(metric_type: str, field: str) -> str | None:
            m = metrics_by_type.get(metric_type)
            if not m:
                return None
            return m.get(field)

        return cls(
            calendar_date=raw["calendarDate"],
            hrv_value=_get("HRV", "value"),
            hrv_baseline_lower=_get("HRV", "baselineLowerLimit"),
            hrv_baseline_upper=_get("HRV", "baselineUpperLimit"),
            hrv_status=_get_str("HRV", "status"),
            hr_value=_get("HR", "value"),
            hr_baseline_lower=_get("HR", "baselineLowerLimit"),
            hr_baseline_upper=_get("HR", "baselineUpperLimit"),
            hr_status=_get_str("HR", "status"),
            spo2_value=_get("SPO2", "value"),
            spo2_baseline_lower=_get("SPO2", "baselineLowerLimit"),
            spo2_baseline_upper=_get("SPO2", "baselineUpperLimit"),
            spo2_status=_get_str("SPO2", "status"),
            skin_temp_value=_get("SKIN_TEMP_C", "value"),
            skin_temp_baseline_lower=_get("SKIN_TEMP_C", "baselineLowerLimit"),
            skin_temp_baseline_upper=_get("SKIN_TEMP_C", "baselineUpperLimit"),
            skin_temp_status=_get_str("SKIN_TEMP_C", "status"),
            respiration_value=_get("RESPIRATION", "value"),
            respiration_baseline_lower=_get("RESPIRATION", "baselineLowerLimit"),
            respiration_baseline_upper=_get("RESPIRATION", "baselineUpperLimit"),
            respiration_status=_get_str("RESPIRATION", "status"),
        )


class GarminTrainingLoadDTO(BaseModel):
    calendar_date: str

    acute_load: float | None = None
    chronic_load: float | None = None
    acwr: float | None = None
    acwr_status: str | None = None

    @classmethod
    def from_garmin(cls, raw: dict) -> GarminTrainingLoadDTO:
        return cls(
            calendar_date=_ms_to_date(raw["calendarDate"]),
            acute_load=raw.get("dailyTrainingLoadAcute"),
            chronic_load=raw.get("dailyTrainingLoadChronic"),
            acwr=raw.get("dailyAcuteChronicWorkloadRatio"),
            acwr_status=raw.get("acwrStatus"),
        )


class GarminFitnessMetricsDTO(BaseModel):
    calendar_date: str

    vo2max_running: float | None = None
    vo2max_cycling: float | None = None
    endurance_score: float | None = None
    max_met: float | None = None
    fitness_age: int | None = None
    source_activity_id: str | None = None

    @classmethod
    def from_vo2max(cls, raw: dict) -> GarminFitnessMetricsDTO:
        sport = raw.get("sport", "")
        return cls(
            calendar_date=raw["calendarDate"],
            vo2max_running=raw.get("vo2MaxValue") if "RUNNING" in sport else None,
            vo2max_cycling=raw.get("vo2MaxValue") if "CYCLING" in sport else None,
            source_activity_id=str(raw["activityId"]) if raw.get("activityId") else None,
        )

    @classmethod
    def from_endurance(cls, raw: dict) -> GarminFitnessMetricsDTO:
        return cls(
            calendar_date=_ms_to_date(raw["calendarDate"]),
            endurance_score=raw.get("overallScore"),
        )

    @classmethod
    def from_max_met(cls, raw: dict) -> GarminFitnessMetricsDTO:
        return cls(
            calendar_date=raw["calendarDate"],
            max_met=raw.get("maxMet"),
            fitness_age=raw.get("fitnessAge"),
        )


class GarminRacePredictionsDTO(BaseModel):
    calendar_date: str

    prediction_5k_secs: int | None = None
    prediction_10k_secs: int | None = None
    prediction_half_secs: int | None = None
    prediction_marathon_secs: int | None = None

    @classmethod
    def from_garmin(cls, raw: dict) -> GarminRacePredictionsDTO:
        return cls(
            calendar_date=raw["calendarDate"],
            prediction_5k_secs=raw.get("raceTime5K"),
            prediction_10k_secs=raw.get("raceTime10K"),
            prediction_half_secs=raw.get("raceTimeHalf"),
            prediction_marathon_secs=raw.get("raceTimeMarathon"),
        )


class GarminBioMetricsDTO(BaseModel):
    calendar_date: str

    weight_kg: float | None = None
    height_cm: float | None = None
    lactate_threshold_hr: int | None = None
    lactate_threshold_speed: float | None = None

    @classmethod
    def from_garmin(cls, raw: dict) -> GarminBioMetricsDTO | None:
        meta = raw.get("metaData") or {}
        cal_date = meta.get("calendarDate", "")[:10]
        if not cal_date:
            return None

        weight_raw = raw.get("weight") or {}
        weight_g = weight_raw.get("weight")
        weight_kg = round(weight_g / 1000, 1) if weight_g else None

        height_raw = raw.get("height")
        height_cm = height_raw.get("height") if isinstance(height_raw, dict) else height_raw
        if height_cm and height_cm < 3.0:
            height_cm = round(height_cm * 100, 1)

        lt = raw.get("lactateThreshold") or {}
        lt_hr = lt.get("heartRate")
        lt_speed = lt.get("speed")

        if not any([weight_kg, height_cm, lt_hr, lt_speed]):
            return None

        return cls(
            calendar_date=cal_date,
            weight_kg=weight_kg,
            height_cm=height_cm,
            lactate_threshold_hr=lt_hr,
            lactate_threshold_speed=lt_speed,
        )


class GarminAbnormalHrEventDTO(BaseModel):
    timestamp_gmt: str
    calendar_date: str
    hr_value: int | None = None
    threshold_value: int | None = None

    @classmethod
    def from_garmin(cls, raw: dict) -> GarminAbnormalHrEventDTO:
        return cls(
            timestamp_gmt=raw["abnormalHrEventGMT"],
            calendar_date=raw.get("calendarDate", raw["abnormalHrEventGMT"][:10]),
            hr_value=raw.get("abnormalHrValue"),
            threshold_value=raw.get("abnormalHrThresholdValue"),
        )


def _int_or_none(v) -> int | None:
    if v is None:
        return None
    return int(v)


def _ms_to_date(v: int | str) -> str:
    """Convert milliseconds epoch or ISO string to YYYY-MM-DD."""
    if isinstance(v, int):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return str(v)[:10]
