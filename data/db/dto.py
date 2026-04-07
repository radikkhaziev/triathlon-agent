"""DB-related DTOs for users, athletes, and wellness.

Pure Pydantic models with no SQLAlchemy dependencies.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class UserDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: str
    username: str | None = None
    athlete_id: str | None = None
    api_key: str | None = None
    mcp_token: str | None = None
    is_silent: bool = False


class ThresholdTestDTO(BaseModel):
    sport: str
    date: str
    hrvt1_hr: float | None = None
    hrvt2_hr: float | None = None


class ThresholdFreshnessDTO(BaseModel):
    status: str  # "no_data" | "stale" | "fresh"
    sport: str
    days_since: int | None = None
    last_date: str | None = None
    last_hrvt1: float | None = None
    last_hrvt2: float | None = None
    recent_tests: list[ThresholdTestDTO] = []


class DriftAlertDTO(BaseModel):
    sport: str
    metric: str
    measured_avg: int
    config_value: int
    diff_pct: float
    tests_count: int
    message: str


class ThresholdDriftDTO(BaseModel):
    alerts: list[DriftAlertDTO] = []


class AthleteThresholdsDTO(BaseModel):
    """Flat view of athlete thresholds across all sports."""

    age: int | None = None
    primary_sport: str | None = None
    lthr_run: int | None = None
    lthr_bike: int | None = None
    max_hr: int | None = None
    ftp: int | None = None
    css: float | None = None  # threshold_pace for Swim (sec/100m)
    threshold_pace_run: float | None = None  # sec/km


class AthleteGoalDTO(BaseModel):
    """Active goal summary."""

    event_name: str
    event_date: date
    sport_type: str
    disciplines: list[str] | None = None
    ctl_target: float | None = None
    per_sport_targets: dict | None = None  # {"swim": 15, "bike": 35, "run": 25}


class WellnessPostDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    date: str

    # Intervals.icu fields
    ctl: float | None = None
    atl: float | None = None
    ramp_rate: float | None = None
    ctl_load: float | None = None
    atl_load: float | None = None
    sport_info: list[dict] | None = None
    weight: float | None = None
    resting_hr: int | None = None
    hrv: float | None = None
    sleep_secs: int | None = None
    sleep_score: float | None = None
    sleep_quality: int | None = None
    body_fat: float | None = None
    vo2max: float | None = None
    steps: int | None = None
    updated: datetime | None = None

    # ESS and Banister
    ess_today: float | None = None
    banister_recovery: float | None = None

    # Combined recovery
    recovery_score: float | None = None
    recovery_category: str | None = None
    recovery_recommendation: str | None = None

    # Readiness
    readiness_score: int | None = None
    readiness_level: str | None = None

    # AI output
    ai_recommendation: str | None = None
