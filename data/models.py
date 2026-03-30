from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


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


class WorkoutStep(BaseModel):
    """A single step in a structured workout for Intervals.icu workout_doc."""

    text: str = ""  # step label: "Warm-up", "Tempo", etc.
    duration: int = 0  # seconds (0 for repeat groups)
    distance: float | None = None  # meters (e.g. 100, 200, 1000). Mutually exclusive with duration
    reps: int | None = None  # repeat count (e.g. 3 for 3x intervals)
    hr: dict | None = None  # {"units": "%lthr", "value": 75}
    power: dict | None = None  # {"units": "%ftp", "value": 80}
    pace: dict | None = None  # {"units": "%pace", "value": 90}
    cadence: dict | None = None  # {"units": "rpm", "value": 90}
    steps: list["WorkoutStep"] | None = None  # sub-steps for repeat groups

    @model_validator(mode="after")
    def _check_duration_or_distance(self) -> "WorkoutStep":
        """Step must have either duration or distance, not both (repeat groups exempt)."""
        if self.reps and self.steps:
            return self  # repeat group — size defined by sub-steps
        if self.duration > 0 and self.distance is not None and self.distance > 0:
            raise ValueError("Step cannot have both duration and distance; use one or the other")
        return self

    @classmethod
    def from_raw_list(cls, raw_steps: list[dict]) -> list["WorkoutStep"]:
        """Parse a list of raw dicts (e.g. from JSON) into WorkoutStep objects."""
        result = []
        for s in raw_steps:
            subs = cls.from_raw_list(s["steps"]) if s.get("steps") else None
            result.append(
                cls(
                    text=s.get("text", ""),
                    duration=s.get("duration", 0),
                    distance=s.get("distance"),
                    reps=s.get("reps"),
                    hr=s.get("hr"),
                    power=s.get("power"),
                    pace=s.get("pace"),
                    cadence=s.get("cadence"),
                    steps=subs,
                )
            )
        return result


class PlannedWorkout(BaseModel):
    """AI-generated workout to push to Intervals.icu (Phase 1: Adaptive Training Plan)."""

    sport: str  # "Ride" | "Run" | "Swim" | "WeightTraining"
    name: str  # "Z2 Endurance + 3x5m Tempo"
    steps: list[WorkoutStep]  # structured workout steps
    duration_minutes: int  # 60
    target_tss: int | None = None  # estimated TSS
    rationale: str = ""  # why this workout
    target_date: date = Field(default_factory=date.today)
    slot: str = "morning"  # "morning" | "evening"
    suffix: str = "generated"  # "generated" | "adapted"

    @property
    def external_id(self) -> str:
        return f"tricoach:{self.target_date}:{self.sport.lower()}:{self.slot}"

    @property
    def has_distance_steps(self) -> bool:
        """Check if any step uses distance instead of duration."""

        def _has_dist(steps: list[WorkoutStep]) -> bool:
            for s in steps:
                if s.distance is not None and s.distance > 0:
                    return True
                if s.steps and _has_dist(s.steps):
                    return True
            return False

        return _has_dist(self.steps)

    def to_intervals_event(self) -> dict:
        """Convert to Intervals.icu POST /events JSON body.

        Always uses workout_doc — works for both time-based and distance-based steps.
        Verified: Intervals.icu parses workout_doc distance correctly (Этап 0 tests).
        Plain text description does NOT parse distance steps.
        """
        event: dict = {
            "category": "WORKOUT",
            "type": self.sport,
            "name": f"AI: {self.name} ({self.suffix})",
            "start_date_local": f"{self.target_date}T00:00:00",
            "moving_time": self.duration_minutes * 60,
            "external_id": self.external_id,
            "workout_doc": {
                "steps": [s.model_dump(exclude_none=True) for s in self.steps],
            },
        }

        if self.has_distance_steps and self.sport in ("Swim", "Run"):
            event["target"] = "PACE"

        return event


class GoalProgress(BaseModel):
    event_name: str
    event_date: date
    weeks_remaining: int
    overall_pct: float
    swim_pct: float
    bike_pct: float
    run_pct: float
    on_track: bool
