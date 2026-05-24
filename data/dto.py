"""Domain DTOs for metrics, recovery, and goal progress.

These are pure data classes with no DB or API dependencies.
"""

from datetime import date
from enum import Enum

from pydantic import BaseModel


class ReadinessLevel(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class TrendResultDTO(BaseModel):
    direction: str  # "rising_fast" | "rising" | "stable" | "declining" | "declining_fast"
    slope: float  # units per day (e.g. ms/day for HRV, TSS/day for CTL)
    r_squared: float  # goodness of fit: <0.3 noisy, >0.7 clear trend
    emoji: str  # "up-up" | "up" | "right" | "down" | "down-down"


class RmssdStatusDTO(BaseModel):
    status: str  # "green" | "yellow" | "red" | "insufficient_data"
    days_available: int = 0
    days_needed: int = 0  # 0 if ready, else days remaining

    # 3-day rolling mean of RMSSD — the value actually compared against bounds
    # for classification. Exposed so dashboards / LLM prompts can explain why a
    # status was set (raw `today` may sit in-band while the smoothed value drifts).
    rmssd_today_smoothed: float | None = None
    rmssd_7d: float | None = None  # mean of last 7 days (recency)
    rmssd_sd_7d: float | None = None
    rmssd_60d: float | None = None
    rmssd_sd_60d: float | None = None
    # NOTE: lower/upper bounds are derived from a *shifted* baseline (last 7 days
    # BEFORE the 3-day smoothing window, to avoid leakage) — they intentionally
    # do NOT equal rmssd_7d ± rmssd_sd_7d. The canonical formula lives on
    # `data.metrics.rmssd_flatt_esco` — don't duplicate it here.
    lower_bound: float | None = None
    upper_bound: float | None = None
    cv_7d: float | None = None
    swc: float | None = None
    trend: TrendResultDTO | None = None


class RhrStatusDTO(BaseModel):
    status: str  # "green" | "yellow" | "red" | "insufficient_data"
    days_available: int = 0
    days_needed: int = 0
    rhr_today: float | None = None
    # 3-day rolling mean of RHR — the value actually compared against bounds.
    # Same rationale as `rmssd_today_smoothed`: raw daily RHR is noisy enough
    # that a sub-band raw value can map to an out-of-band smoothed status.
    rhr_today_smoothed: float | None = None
    rhr_7d: float | None = None
    rhr_sd_7d: float | None = None
    rhr_30d: float | None = None  # mean of last 30 days (recency)
    rhr_sd_30d: float | None = None
    rhr_60d: float | None = None
    rhr_sd_60d: float | None = None
    # Bounds use a *shifted* 30-day baseline (the 30 days BEFORE the smoothing
    # window) — they do NOT equal `rhr_30d ± rhr_sd_30d`. Canonical formula
    # in `data.metrics.rhr_baseline`.
    lower_bound: float | None = None
    upper_bound: float | None = None
    cv_7d: float | None = None
    trend: TrendResultDTO | None = None


class RecoveryStateDTO(BaseModel):
    date: date
    recovery_pct: float  # 0-100%, 100 = fully recovered
    ess: float  # External Stress Score for the day


class RecoveryScoreDTO(BaseModel):
    score: float  # 0-100 composite recovery score
    category: str  # "excellent" | "good" | "moderate" | "low"
    recommendation: str  # "zone2_ok" | "zone1_long" | "zone1_short" | "skip"
    flags: list[str] = []  # ["late_sleep", "hrv_unstable", ...]
    components: dict = {}  # {"rmssd": ..., "banister": ..., "rhr": ..., ...}


class DailyMetricsDTO(BaseModel):
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
    ctl_ride: float
    ctl_run: float


class GoalProgressDTO(BaseModel):
    event_name: str
    event_date: date
    weeks_remaining: int
    overall_pct: float
    swim_pct: float
    bike_pct: float
    run_pct: float
    on_track: bool
