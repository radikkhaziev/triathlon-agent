"""DB-related DTOs for users, athletes, and wellness.

Pure Pydantic models with no SQLAlchemy dependencies.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class UserDTO(BaseModel):
    """Lightweight user snapshot for cross-boundary transport (dramatiq, logs).

    Credentials (api_key, mcp_token) are deliberately excluded — they must NOT
    cross the dramatiq broker or Sentry boundary. Issue #147: leaked secrets
    in #146 came from dramatiq serializing `repr(UserDTO)` into an exception
    message. Callers that need credentials re-fetch the ORM `User` by `id`.
    """

    # `extra="forbid"` hard-rejects any attempt to reintroduce credentials
    # (api_key, mcp_token, etc.) via the constructor — regression guard for #147.
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: int
    chat_id: str
    username: str | None = None
    athlete_id: str | None = None
    language: str = "ru"
    is_silent: bool = False
    avatar_url: str | None = None
    # See User.bot_chat_initialized — false means TelegramTool must skip sends
    # to avoid the 400 chat-not-found Sentry storm (issue #266). Default True
    # because production rows flow through ``model_validate(user)`` which
    # picks up the real DB value, and the only ad-hoc constructions left
    # (tests, manual fan-outs) are over already-onboarded athletes.
    bot_chat_initialized: bool = True
    # Subset of {"swim","ride","run"}; None = athlete hasn't passed through
    # SportsPicker gate yet. Read by the morning-report actor to scope ramp
    # suggestions to disciplines the athlete actually trains.
    sports: list[str] | None = None


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


# Drift thresholds — absolute units per RAMP_TEST_BIKE_SPEC §8.
# Replaces a uniform 5% gate that ignored metric scale (e.g. 5% × LTHR=160
# = 8 bpm — much looser than the 3 bpm clinically-relevant delta).
DRIFT_LTHR_BPM = 3
DRIFT_PACE_SEC_PER_KM = 5
DRIFT_FTP_WATTS = 5

# R² tiers for confidence-based update behavior (§8):
#   ≥ 0.85 = high   → auto-update zones, no button
#   ≥ 0.70 = medium → suggest with button (current default path)
#   <  0.70 = low   → no update, recommend retest
DRIFT_R2_HIGH = 0.85
DRIFT_R2_MEDIUM = 0.70


class DriftAlertDTO(BaseModel):
    sport: str
    metric: str
    measured: int  # latest ramp-test reading (HRVT2 HR or pace at HRVT2 in s/km)
    config_value: int
    diff_pct: float
    message: str


class ThresholdDriftDTO(BaseModel):
    alerts: list[DriftAlertDTO] = []


class AthleteThresholdsDTO(BaseModel):
    """Flat view of athlete thresholds across all sports."""

    age: int | None = None
    sports: list[str] | None = None  # subset of {"swim","ride","run"}; None = not yet picked
    lthr_run: int | None = None
    lthr_bike: int | None = None
    max_hr: int | None = None
    ftp: int | None = None
    css: float | None = None  # threshold_pace for Swim (sec/100m)
    threshold_pace_run: float | None = None  # sec/km


class AthleteGoalDTO(BaseModel):
    """Active goal summary."""

    id: int | None = None
    event_name: str
    event_date: date
    sport_type: str
    # RACE_A / RACE_B / RACE_C — populated by get_goals_for_settings (#323
    # Strand C, list view). Older callers (get_goal_dto / get_goals_for_prompt)
    # don't need it for prompt rendering, so the field stays optional.
    category: str | None = None
    ctl_target: float | None = None
    per_sport_targets: dict | None = None  # {"swim": 15, "ride": 35, "run": 25}


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
