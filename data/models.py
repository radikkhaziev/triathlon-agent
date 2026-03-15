from pydantic import BaseModel
from datetime import date, datetime
from enum import Enum


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
    sleep_score: int
    duration_seconds: int
    deep_sleep_seconds: int
    rem_sleep_seconds: int
    awake_seconds: int
    avg_overnight_hrv: float | None = None
    avg_stress: float | None = None


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
