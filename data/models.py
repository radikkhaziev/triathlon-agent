from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, field_validator


class ReadinessLevel(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class HRVData(BaseModel):
    date: date
    hrv_weekly_avg: float
    hrv_last_night: float
    hrv_5min_high: float | None = None
    status: str


class Activity(BaseModel):
    """Completed activity from Intervals.icu activities endpoint."""

    id: str  # Intervals.icu activity ID (e.g. "i12345")
    start_date_local: date
    type: str | None = None  # Ride, Run, Swim, VirtualRide, etc.
    icu_training_load: float | None = None
    moving_time: int | None = None  # seconds
    average_hr: float | None = None  # average heart rate (from average_heartrate API field)

    @field_validator("start_date_local", mode="before")
    @classmethod
    def _parse_date(cls, v: str | date | None) -> date | None:
        if v is None:
            return None
        if isinstance(v, str):
            return datetime.fromisoformat(v).date()
        return v


class ScheduledWorkout(BaseModel):
    """Planned workout from Intervals.icu calendar (events endpoint)."""

    id: int
    start_date_local: date
    end_date_local: date | None = None
    name: str | None = None
    category: str = "WORKOUT"  # WORKOUT | RACE_A | RACE_B | RACE_C | NOTE
    type: str | None = None  # Run, Ride, Swim, etc.
    description: str | None = None
    moving_time: int | None = None  # planned duration in seconds
    distance: float | None = None  # planned distance in km
    workout_doc: dict | None = None  # structured intervals
    updated: datetime | None = None

    @field_validator("start_date_local", "end_date_local", mode="before")
    @classmethod
    def _parse_date(cls, v: str | date | None) -> date | None:
        if v is None:
            return None
        if isinstance(v, str):
            return datetime.fromisoformat(v).date()
        return v


class TrendResult(BaseModel):
    direction: str  # "rising_fast" | "rising" | "stable" | "declining" | "declining_fast"
    slope: float  # units per day (e.g. ms/day for HRV, TSS/day for CTL)
    r_squared: float  # goodness of fit: <0.3 noisy, >0.7 clear trend
    emoji: str  # "↑↑" | "↑" | "→" | "↓" | "↓↓"


class RmssdStatus(BaseModel):
    status: str  # "green" | "yellow" | "red" | "insufficient_data"
    days_available: int = 0
    days_needed: int = 0  # 0 if ready, else days remaining

    rmssd_7d: float | None = None
    rmssd_sd_7d: float | None = None
    rmssd_60d: float | None = None
    rmssd_sd_60d: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    cv_7d: float | None = None
    swc: float | None = None
    trend: TrendResult | None = None


class RhrStatus(BaseModel):
    status: str  # "green" | "yellow" | "red" | "insufficient_data"
    days_available: int = 0
    days_needed: int = 0
    rhr_today: float | None = None
    rhr_7d: float | None = None
    rhr_sd_7d: float | None = None
    rhr_30d: float | None = None
    rhr_sd_30d: float | None = None
    rhr_60d: float | None = None
    rhr_sd_60d: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    cv_7d: float | None = None
    trend: TrendResult | None = None


class RecoveryState(BaseModel):
    date: date
    recovery_pct: float  # 0-100%, 100 = fully recovered
    ess: float  # External Stress Score for the day


class RecoveryScore(BaseModel):
    score: float  # 0-100 composite recovery score
    category: str  # "excellent" | "good" | "moderate" | "low"
    recommendation: str  # "zone2_ok" | "zone1_long" | "zone1_short" | "skip"
    flags: list[str] = []  # ["late_sleep", "hrv_unstable", ...]
    components: dict = {}  # {"rmssd": ..., "banister": ..., "rhr": ..., ...}


class DailyMetrics(BaseModel):
    date: date
    readiness_score: int
    readiness_level: ReadinessLevel
    hrv_delta_pct: float
    sleep_score: int
    resting_hr: float
    ctl: float
    atl: float
    tsb: float
    ctl_swim: float
    ctl_bike: float
    ctl_run: float


# ---------------------------------------------------------------------------
# Intervals.icu
# ---------------------------------------------------------------------------


class Wellness(BaseModel):
    id: str | None = None
    ctl: float | None = None
    atl: float | None = None
    ramp_rate: float | None = None
    ctl_load: float | None = None
    atl_load: float | None = None
    sport_info: list[dict] | None = None
    updated: datetime | None = None
    weight: float | None = None
    resting_hr: int | None = None
    hrv: float | None = None
    hrv_sdnn: float | None = None
    kcal_consumed: int | None = None
    sleep_secs: int | None = None
    sleep_score: float | None = None
    sleep_quality: int | None = None
    avg_sleeping_hr: float | None = None
    soreness: int | None = None
    fatigue: int | None = None
    stress: int | None = None
    mood: int | None = None
    motivation: int | None = None
    injury: int | None = None
    sp_o2: float | None = None
    systolic: int | None = None
    diastolic: int | None = None
    hydration: int | None = None
    hydration_volume: float | None = None
    readiness: float | None = None
    baevsky_si: float | None = None
    blood_glucose: float | None = None
    lactate: float | None = None
    body_fat: float | None = None
    abdomen: float | None = None
    vo2max: float | None = None
    comments: str | None = None
    steps: int | None = None
    respiration: float | None = None
    carbohydrates: float | None = None
    protein: float | None = None
    fat_total: float | None = None
    locked: bool | None = None


class GoalProgress(BaseModel):
    event_name: str
    event_date: date
    weeks_remaining: int
    overall_pct: float
    swim_pct: float
    bike_pct: float
    run_pct: float
    on_track: bool
