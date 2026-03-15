from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel


class SportType(str, Enum):
    SWIM = "swimming"
    BIKE = "cycling"
    RUN = "running"
    STRENGTH = "strength_training"
    OTHER = "other"


class ReadinessLevel(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class SleepData(BaseModel):
    date: date
    score: int | None = None
    duration: int | None = None
    start: int | None = None
    end: int | None = None
    stress_avg: int | None = None
    hrv_avg: int | None = None
    heart_rate_avg: int | None = None


class HRVData(BaseModel):
    date: date
    hrv_weekly_avg: float
    hrv_last_night: float
    hrv_5min_high: float | None = None
    status: str


class BodyBatteryData(BaseModel):
    date: date
    start_value: int
    end_value: int
    charged: int
    drained: int


class StressData(BaseModel):
    date: date
    avg_stress: float
    max_stress: float
    stress_duration_seconds: int
    rest_duration_seconds: int


class TrainingReadinessData(BaseModel):
    date: date
    score: int
    level: str
    hrv_status: str | None = None
    sleep_score: int | None = None
    recovery_time_hours: int | None = None


class TrainingStatusData(BaseModel):
    date: date
    training_status: str
    vo2_max_run: float | None = None
    vo2_max_bike: float | None = None
    load_focus: str | None = None


class Activity(BaseModel):
    activity_id: int
    sport: SportType
    start_time: datetime
    duration_seconds: int
    distance_meters: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_power: float | None = None
    normalized_power: float | None = None
    tss: float | None = None


class ScheduledWorkout(BaseModel):
    scheduled_date: date
    workout_name: str
    sport: SportType
    description: str | None = None
    planned_duration_seconds: int | None = None
    planned_tss: float | None = None


class HeartRateData(BaseModel):
    date: date
    resting_hr: float
    max_hr: float
    min_hr: float
    avg_hr: float | None = None


class DailyStats(BaseModel):
    date: date
    total_steps: int
    total_distance_meters: float
    active_calories: int
    total_calories: int
    intensity_minutes: int
    floors_climbed: int


class BodyCompositionData(BaseModel):
    date: date
    weight_kg: float | None = None
    bmi: float | None = None
    body_fat_pct: float | None = None
    muscle_mass_kg: float | None = None
    bone_mass_kg: float | None = None
    body_water_pct: float | None = None


class RespirationData(BaseModel):
    date: date
    avg_breathing_rate: float
    lowest_breathing_rate: float | None = None
    highest_breathing_rate: float | None = None


class SpO2Data(BaseModel):
    date: date
    avg_spo2: float
    lowest_spo2: float | None = None


class MaxMetricsData(BaseModel):
    date: date
    vo2_max_run: float | None = None
    vo2_max_bike: float | None = None


class RacePrediction(BaseModel):
    distance_name: str
    predicted_time_seconds: float


class EnduranceScoreData(BaseModel):
    date: date
    overall_score: int
    rating: str | None = None


class LactateThresholdData(BaseModel):
    heart_rate: float | None = None
    speed: float | None = None


class CyclingFTPData(BaseModel):
    ftp: float | None = None
    ftp_date: date | None = None


class DailyMetrics(BaseModel):
    date: date
    readiness_score: int
    readiness_level: ReadinessLevel
    hrv_delta_pct: float
    sleep_score: int
    body_battery_morning: int
    resting_hr: float
    ctl: float
    atl: float
    tsb: float
    ctl_swim: float
    ctl_bike: float
    ctl_run: float


class GoalProgress(BaseModel):
    event_name: str
    event_date: date
    weeks_remaining: int
    overall_pct: float
    swim_pct: float
    bike_pct: float
    run_pct: float
    on_track: bool
