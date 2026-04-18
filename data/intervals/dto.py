from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from data.utils import normalize_sport


class WellnessDTO(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,  # allows using both snake_case and camelCase on input
    )

    id: str | None = None  # date in ISO format, e.g. "2024-06-01"
    ctl: float | None = Field(None, json_schema_extra={"sync": True})
    atl: float | None = Field(None, json_schema_extra={"sync": True})
    ramp_rate: float | None = Field(None, json_schema_extra={"sync": True})
    ctl_load: float | None = Field(None, json_schema_extra={"sync": True})
    atl_load: float | None = Field(None, json_schema_extra={"sync": True})
    sport_info: list[dict] | None = Field(None, json_schema_extra={"sync": True})
    updated: datetime | None = Field(None, json_schema_extra={"sync": True})
    weight: float | None = Field(None, json_schema_extra={"sync": True})
    resting_hr: int | None = Field(None, alias="restingHR", json_schema_extra={"sync": True})
    hrv: float | None = Field(None, json_schema_extra={"sync": True})
    hrv_sdnn: float | None = Field(None, alias="hrvSDNN")
    kcal_consumed: int | None = None
    sleep_secs: int | None = Field(None, json_schema_extra={"sync": True})
    sleep_score: float | None = Field(None, json_schema_extra={"sync": True})
    sleep_quality: int | None = Field(None, json_schema_extra={"sync": True})
    avg_sleeping_hr: float | None = Field(None, alias="avgSleepingHR")
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
    body_fat: float | None = Field(None, json_schema_extra={"sync": True})
    abdomen: float | None = None
    vo2max: float | None = Field(None, json_schema_extra={"sync": True})
    comments: str | None = None
    steps: int | None = Field(None, json_schema_extra={"sync": True})
    respiration: float | None = None
    carbohydrates: float | None = None
    protein: float | None = None
    fat_total: float | None = None
    locked: bool | None = None

    def intervals_dict(self) -> dict:
        """Return only the fields marked with sync=True."""
        sync_fields = {
            name for name, info in WellnessDTO.model_fields.items() if (info.json_schema_extra or {}).get("sync")
        }
        data = self.model_dump(by_alias=False)
        return {k: v for k, v in data.items() if k in sync_fields}


class SportSettingsDTO(BaseModel):
    """Sport settings from Intervals.icu (GET /athlete/{id}/sport-settings/{type})."""

    id: int
    types: list[str] = []  # ["Ride", "VirtualRide"]
    lthr: int | None = None
    max_hr: int | None = None
    ftp: int | None = None
    threshold_pace: float | None = None
    pace_units: str | None = None  # SECS_100M, MINS_KM, SECS_100Y, etc.
    hr_zones: list[int] | None = None
    hr_zone_names: list[str] | None = None
    power_zones: list[int] | None = None
    power_zone_names: list[str] | None = None
    pace_zones: list[float] | None = None
    pace_zone_names: list[str] | None = None


class EventExDTO(BaseModel):
    """Input DTO for Intervals.icu POST/PUT /events (EventEx schema)."""

    category: str = "WORKOUT"
    type: str | None = None  # Run, Ride, Swim, etc.
    name: str | None = None
    start_date_local: str | None = None  # "2026-04-05T00:00:00"
    moving_time: int | None = None  # planned duration in seconds
    external_id: str | None = None
    workout_doc: dict | None = None  # {"steps": [...]}
    target: str | None = None  # AUTO | POWER | HR | PACE
    description: str | None = None
    indoor: bool | None = None
    distance: float | None = None


class ScheduledWorkoutDTO(BaseModel):
    """Planned workout from Intervals.icu calendar (events endpoint)."""

    id: int
    start_date_local: date
    end_date_local: date | None = None
    name: str | None = None
    category: str = "WORKOUT"  # WORKOUT | RACE_A | RACE_B | RACE_C | NOTE
    type: str | None = None  # Normalized: Ride | Run | Swim | Other
    description: str | None = None
    moving_time: int | None = None  # planned duration in seconds
    distance: float | None = None  # planned distance in km
    workout_doc: dict | None = None  # structured intervals
    updated: datetime | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, v: str | None) -> str | None:
        return normalize_sport(v)

    @field_validator("start_date_local", "end_date_local", mode="before")
    @classmethod
    def _parse_date(cls, v: str | date | None) -> date | None:
        if v is None:
            return None
        if isinstance(v, str):
            return datetime.fromisoformat(v).date()
        return v


# ---------------------------------------------------------------------------
# Domain DTOs (metrics, recovery, workouts)
# ---------------------------------------------------------------------------


class HRVDataDTO(BaseModel):
    date: date
    hrv_weekly_avg: float
    hrv_last_night: float
    hrv_5min_high: float | None = None
    status: str


class ActivityDTO(BaseModel):
    """Completed activity from Intervals.icu activities endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    id: str  # Intervals.icu activity ID (e.g. "i12345")
    start_date_local: date
    type: str | None = None  # Normalized: Ride | Run | Swim | Other
    icu_training_load: float | None = None
    moving_time: int | None = None  # seconds
    average_hr: float | None = Field(None, alias="average_heartrate")  # API field: average_heartrate
    is_race: bool = Field(False, alias="race")
    sub_type: str | None = None
    source: str | None = None  # e.g. "GARMIN_CONNECT", "OAUTH_CLIENT", "STRAVA"

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, v: str | None) -> str | None:
        return normalize_sport(v)

    @field_validator("start_date_local", mode="before")
    @classmethod
    def _parse_date(cls, v: str | date | None) -> date | None:
        if v is None:
            return None
        if isinstance(v, str):
            return datetime.fromisoformat(v).date()
        return v


# Backward-compatible re-exports (moved to data.dto)
from data.dto import (  # noqa: F401, E402
    DailyMetricsDTO,
    GoalProgressDTO,
    ReadinessLevel,
    RecoveryScoreDTO,
    RecoveryStateDTO,
    RhrStatusDTO,
    RmssdStatusDTO,
    TrendResultDTO,
)


class WorkoutStepDTO(BaseModel):
    """A single step in a structured workout for Intervals.icu workout_doc."""

    text: str = ""  # step label: "Warm-up", "Tempo", etc.
    duration: int = 0  # seconds (0 for repeat groups)
    distance: float | None = None  # meters (e.g. 100, 200, 1000). Mutually exclusive with duration
    reps: int | None = None  # repeat count (e.g. 3 for 3x intervals)
    hr: dict | None = None  # {"units": "%lthr", "value": 75}
    power: dict | None = None  # {"units": "%ftp", "value": 80}
    pace: dict | None = None  # {"units": "%pace", "value": 90}
    cadence: dict | None = None  # {"units": "rpm", "value": 90}
    steps: list["WorkoutStepDTO"] | None = None  # sub-steps for repeat groups

    @model_validator(mode="after")
    def _check_duration_or_distance(self) -> "WorkoutStepDTO":
        """Step must have either duration or distance, not both (repeat groups exempt)."""
        if self.reps and self.steps:
            return self  # repeat group — size defined by sub-steps
        if self.duration > 0 and self.distance is not None and self.distance > 0:
            raise ValueError("Step cannot have both duration and distance; use one or the other")
        return self

    @classmethod
    def from_raw_list(cls, raw_steps: list[dict]) -> list["WorkoutStepDTO"]:
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


# Sports where intensity targets are not applicable (yoga, stretching, mobility)
_NO_TARGET_SPORTS = frozenset({"Other"})


class PlannedWorkoutDTO(BaseModel):
    """AI-generated workout to push to Intervals.icu (Phase 1: Adaptive Training Plan)."""

    sport: str  # "Ride" | "Run" | "Swim" | "WeightTraining"
    name: str  # "Z2 Endurance + 3x5m Tempo"
    steps: list[WorkoutStepDTO]  # structured workout steps
    duration_minutes: int  # 60
    target_tss: int | None = None  # estimated TSS
    rationale: str = ""  # why this workout
    target_date: date = Field(default_factory=date.today)
    slot: str = "morning"  # "morning" | "evening"
    suffix: str | None = None  # "adapted"

    @model_validator(mode="after")
    def _check_steps_have_targets(self) -> "PlannedWorkoutDTO":
        """Every terminal step must carry at least one intensity target.

        Garmin/Wahoo watches alert on HR/power/pace corridors only when the step
        defines them. Text-only steps ('Z2' label + duration) leave the athlete
        running blind, so we reject them here rather than silently pushing a
        useless workout to Intervals.icu.

        Exception: ``Other`` sport (yoga, stretching, mobility) — no watch alerts needed.
        """
        if self.sport in _NO_TARGET_SPORTS:
            return self

        def _walk(steps: list[WorkoutStepDTO], trail: str) -> None:
            for i, s in enumerate(steps):
                label = f"{trail}[{i}]{' ' + s.text if s.text else ''}"
                if s.reps:
                    if not s.steps:
                        raise ValueError(
                            f"Step {label!r} sets reps={s.reps} but has no sub-steps. "
                            f"Repeat groups must contain a non-empty steps list."
                        )
                    _walk(s.steps, label)
                    continue
                if not (s.hr or s.power or s.pace):
                    raise ValueError(
                        f"Step {label!r} has no intensity target. Every non-repeat "
                        f"step must include hr/power/pace so watches can alert the "
                        f"athlete on the target corridor."
                    )

        _walk(self.steps, "steps")
        return self

    @model_validator(mode="after")
    def _check_steps_duration_consistency(self) -> "PlannedWorkoutDTO":
        """Guard against unit-mismatch where Claude passes step durations in minutes.

        If all steps are time-based, the sum of step seconds (accounting for repeat groups)
        must be at least 30% of duration_minutes * 60. A gross shortfall almost always means
        the caller confused minutes with seconds (a 60-min ride collapsing to ~60 seconds).
        """
        if self.has_distance_steps:
            return self

        def _sum_seconds(steps: list[WorkoutStepDTO]) -> int:
            total = 0
            for s in steps:
                if s.reps and s.steps:
                    total += s.reps * _sum_seconds(s.steps)
                else:
                    total += s.duration or 0
            return total

        expected = self.duration_minutes * 60
        actual = _sum_seconds(self.steps)
        if expected > 0 and actual > 0 and actual < expected * 0.3:
            raise ValueError(
                f"Workout steps total only {actual}s but duration_minutes={self.duration_minutes} "
                f"(expected ~{expected}s). Step `duration` must be in SECONDS, not minutes."
            )
        return self

    @property
    def external_id(self) -> str:
        return f"tricoach:{self.target_date}:{self.sport.lower()}:{self.slot}"

    @property
    def has_distance_steps(self) -> bool:
        """Check if any step uses distance instead of duration."""

        def _has_dist(steps: list[WorkoutStepDTO]) -> bool:
            for s in steps:
                if s.distance is not None and s.distance > 0:
                    return True
                if s.steps and _has_dist(s.steps):
                    return True
            return False

        return _has_dist(self.steps)

    def to_intervals_event(self) -> "EventExDTO":
        """Convert to Intervals.icu POST /events DTO.

        Always uses workout_doc — works for both time-based and distance-based steps.
        Verified: Intervals.icu parses workout_doc distance correctly (Этап 0 tests).
        Plain text description does NOT parse distance steps.
        """
        target = "PACE" if self.has_distance_steps and self.sport in ("Swim", "Run") else None

        # Strip duplicate "AI: " prefix if Claude already added it
        clean_name = self.name[4:] if self.name.startswith("AI: ") else self.name

        return EventExDTO(
            category="WORKOUT",
            type=self.sport,
            name=f"AI: {clean_name}" + (f" ({self.suffix})" if self.suffix else ""),
            description=self.rationale or "",
            start_date_local=f"{self.target_date}T00:00:00",
            moving_time=self.duration_minutes * 60,
            external_id=self.external_id,
            workout_doc={"steps": [s.model_dump(exclude_none=True) for s in self.steps]},
            target=target,
        )
