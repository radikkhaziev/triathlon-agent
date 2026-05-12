import re
from datetime import date, datetime
from typing import Any

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


class MmpModelDTO(BaseModel):
    """Mean-Max Power model — only delivered for Ride sport_settings (Run/Swim omit)."""

    model_config = ConfigDict(populate_by_name=True)

    type: str | None = None
    critical_power: float | None = Field(None, alias="criticalPower")
    w_prime: float | None = Field(None, alias="wPrime")
    p_max: float | None = Field(None, alias="pMax")
    ftp: int | None = None


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
    mmp_model: MmpModelDTO | None = None


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
    icu_rpe: int | None = None  # Borg CR-10 (1-10), from Intervals.icu / Garmin

    # WEBHOOK_DATA_CAPTURE Phase 1: rolling power model + fitness snapshot per
    # activity. Arrive on ACTIVITY_ACHIEVEMENTS (~60s after upload) for every
    # activity, regardless of whether a PR was hit. Persisted to activity_details
    # via _dispatch_achievements.
    trimp: float | None = None
    carbs_used: int | None = None
    icu_rolling_ftp: int | None = None
    icu_rolling_ftp_delta: int | None = None
    icu_rolling_w_prime: float | None = None
    icu_rolling_p_max: float | None = None
    icu_ctl: float | None = None
    icu_atl: float | None = None
    icu_achievements: list[dict] | None = None  # captured into activity_achievements (separate table)

    # Outdoor weather block (indoor / virtual rides have has_weather=False and
    # other fields None). Persisted to activity_weather via _dispatch_activity_uploaded.
    has_weather: bool | None = None
    average_weather_temp: float | None = None
    min_weather_temp: float | None = None
    max_weather_temp: float | None = None
    average_feels_like: float | None = None
    average_wind_speed: float | None = None
    average_wind_gust: float | None = None
    prevailing_wind_deg: int | None = None
    headwind_percent: float | None = None
    tailwind_percent: float | None = None
    average_clouds: float | None = None
    max_rain: float | None = None
    max_snow: float | None = None

    # WEBHOOK_DATA_CAPTURE Phase 2: warmup/cooldown durations + polarization
    # index. Arrive on ACTIVITY_UPLOADED inline; persisted to activity_details
    # via _dispatch_activity_uploaded.
    icu_warmup_time: int | None = None  # seconds
    icu_cooldown_time: int | None = None  # seconds
    polarization_index: float | None = None

    @field_validator("icu_rpe", mode="before")
    @classmethod
    def _validate_rpe(cls, v: Any) -> int | None:
        if v is None:
            return None
        try:
            val = int(v)
        except (TypeError, ValueError):
            return None
        return val if 1 <= val <= 10 else None

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
    # Schema: {"units": "%lthr"|"%ftp"|"%pace"|"rpm", "start": <int>, "end": <int>}.
    # `start` is the corridor lower bound (NOT `value` — Intervals' FIT export
    # routes `{value, end}` payloads to «Lap HR / zone-mapped» mode which Garmin
    # then clamps to its own zone boundaries, drifting from intended range).
    # Verified empirically 2026-05-12 via «AI: Absolute V2» test workout — see
    # `docs/WORKOUT_ABSOLUTE_TARGETS_SPEC.md` §12 «Attempt 3b».
    hr: dict | None = None  # {"units": "%lthr", "start": 75, "end": 82}
    power: dict | None = None  # {"units": "%ftp", "start": 65, "end": 78}
    pace: dict | None = None  # {"units": "%pace", "start": 95, "end": 105}
    cadence: dict | None = None  # {"units": "rpm", "start": 85, "end": 95}
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

# ---------------------------------------------------------------------------
# Native-format description renderer (Intervals.icu structured-workout text).
# Grammar + parser quirks: docs/INTERVALS_NATIVE_WORKOUT_FORMAT.md.
# Without a top-level `description`, Intervals.icu web/mobile UI shows only the
# workout's name and length — steps stay invisible. Rendering matching native
# text into `event.description` makes the structure visible; FIT export to
# watches still rides on `workout_doc.steps`.
# ---------------------------------------------------------------------------

# Sports whose step labels must have `Z\d+` substrings stripped: without a
# qualifier, Intervals' parser resolves `Z1`-`Z5` as POWER zones. For Ride
# that's the correct default; for Run/Swim it produces 0-0w targets.
_STRIP_Z_LABEL_SPORTS = frozenset({"Run", "Swim"})

# Any digit-led token (`50`, `100m`, `4x`, `2km`). Stripped wholesale from
# labels — Intervals' parser would otherwise grab the first numeric token as
# the step's duration. Real prod label that motivated this: `Drill: 50
# fingertip drag + 50 free` (event 109762368).
_DIGIT_TOKEN_RE = re.compile(r"\b\d+\w*\b")
_ZONE_LABEL_RE = re.compile(r"\bZ\d+\b")
_WHITESPACE_RE = re.compile(r"\s+")


def _sanitize_label(text: str | None, sport: str) -> str:
    """Strip parser-conflict patterns from a step's cue text."""
    if not text:
        return ""
    cleaned = _DIGIT_TOKEN_RE.sub("", text)
    if sport in _STRIP_Z_LABEL_SPORTS:
        cleaned = _ZONE_LABEL_RE.sub("", cleaned)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def _render_duration(seconds: int) -> str:
    """Render seconds as `NhNmNs` combo (omitting zero components)."""
    if seconds <= 0:
        return "0s"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s:
        parts.append(f"{s}s")
    return "".join(parts)


def _render_distance(meters: float) -> str:
    """Render meters as `Nmtr` (<1km) or `Nkm` / `N.Nkm` (>=1km)."""
    if meters >= 1000:
        km = meters / 1000
        if km == int(km):
            return f"{int(km)}km"
        return f"{km:g}km"
    return f"{int(round(meters))}mtr"


# Mapping target-kind → (units string accepted by validator, suffix for native render).
# Adding a new units string also requires extending `_check_target_shape` validator
# on PlannedWorkoutDTO so bad input fails at DTO construction rather than render time.
_TARGET_SUFFIX = {
    "hr": ("%lthr", "% LTHR"),
    "power": ("%ftp", "%"),  # bare % → FTP-implied per native grammar
    "pace": ("%pace", "% Pace"),
}


def _render_target(step: "WorkoutStepDTO", sport: str) -> str | None:
    """Render the step's intensity target in Intervals' native target syntax.

    Returns ``None`` only when the step has no target at all (acceptable for
    ``Other``; rejected by the validator everywhere else). Bad-shape targets
    (unknown units, non-numeric ``start``) cannot reach this function — they
    fail in ``PlannedWorkoutDTO._check_steps_have_targets`` at DTO construction.
    """

    def _fmt(start: float, end: float | None, suffix: str) -> str:
        v = int(round(start))
        if end is not None and end != start:
            return f"{v}-{int(round(end))}{suffix}"
        return f"{v}{suffix}"

    for attr, (_units, suffix) in _TARGET_SUFFIX.items():
        target = getattr(step, attr)
        if target:
            return _fmt(target["start"], target.get("end"), suffix)
    return None


def _render_cadence(step: "WorkoutStepDTO") -> str | None:
    """Render optional cadence trailer (e.g. `90rpm`) per native grammar §«Step»."""
    if not step.cadence:
        return None
    value = step.cadence.get("start")
    # `bool` subclasses `int` — `isinstance(False, int) is True`. Exclude bools
    # so `start: True` can't render as `1rpm`. Cadence `0` (coasting marker) is
    # numerically valid and must render.
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    units = (step.cadence.get("units") or "rpm").lower()
    return f"{int(round(value))}{units}"


def _render_step(step: "WorkoutStepDTO", sport: str) -> str:
    """Render a single (non-repeat) step as a `- label dur target [cadence]` bullet."""
    label = _sanitize_label(step.text, sport)
    measure = _render_distance(step.distance) if step.distance else _render_duration(step.duration)
    target = _render_target(step, sport)
    cadence = _render_cadence(step)
    parts = [p for p in (label, measure, target, cadence) if p]
    return "- " + " ".join(parts)


def _render_native_description(steps: list["WorkoutStepDTO"], sport: str) -> str:
    """Render a workout's steps as Intervals.icu native-format description.

    Top-level entities (single steps and repeat blocks) are separated by blank
    lines — the parser requires a blank line before/after every repeat block,
    and tolerates them between adjacent plain steps. Single-pass `\\n\\n`.join
    keeps the rule satisfied without branching.

    Sub-step recursion intentionally stops at one level: native grammar has no
    syntax for nested repeats, and `_check_steps_have_targets` already rejects
    a step that nests both `reps + steps` inside another repeat's `steps`. If
    such a payload ever reaches the renderer, the nested-group inner steps are
    dropped — fail closed at the validator, don't try to be clever here.
    """
    chunks: list[str] = []
    for step in steps:
        if step.reps and step.steps:
            sub_lines = [_render_step(sub, sport) for sub in step.steps]
            chunks.append(f"{step.reps}x\n" + "\n".join(sub_lines))
        else:
            chunks.append(_render_step(step, sport))
    return "\n\n".join(chunks) + "\n"


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
        """Every terminal step must carry at least one well-formed intensity target.

        Two failure modes both rejected here:
        1. Text-only steps (`Z2` label + duration, no hr/power/pace dict) leave
           Garmin/Wahoo unable to alert the athlete on a corridor.
        2. Targets with unknown `units` or missing/non-numeric `start` — these
           survive past basic presence checks but break the native-format
           description renderer, which would emit a target-less line and
           trigger Intervals' parse-failure-induced `workout_doc.steps` drop.

        Exception: ``Other`` sport (yoga, stretching, mobility) — no watch alerts needed.
        """
        if self.sport in _NO_TARGET_SPORTS:
            return self

        def _walk(steps: list[WorkoutStepDTO], trail: str, depth: int = 0) -> None:
            for i, s in enumerate(steps):
                label = f"{trail}[{i}]{' ' + s.text if s.text else ''}"
                if s.reps:
                    if not s.steps:
                        raise ValueError(
                            f"Step {label!r} sets reps={s.reps} but has no sub-steps. "
                            f"Repeat groups must contain a non-empty steps list."
                        )
                    if depth >= 1:
                        # Native-format renderer (`_render_native_description`)
                        # intentionally recurses only one level; a nested repeat
                        # would silently drop its inner steps from the workout
                        # description Intervals shows on web/mobile. Fail at
                        # DTO construction instead.
                        raise ValueError(
                            f"Step {label!r} is a repeat group nested inside another repeat. "
                            f"Native workout grammar has no syntax for nested repeats — "
                            f"flatten the structure (e.g. write 6×[A,B] instead of 2×[3×[A,B]])."
                        )
                    _walk(s.steps, label, depth + 1)
                    continue
                if not (s.hr or s.power or s.pace):
                    raise ValueError(
                        f"Step {label!r} has no intensity target. Every non-repeat "
                        f"step must include hr/power/pace so watches can alert the "
                        f"athlete on the target corridor."
                    )
                # Native-format description renderer needs a known units string
                # and a numeric value. Catching bad shape here fails the push
                # at DTO construction; a renderer-side fallback would silently
                # emit a target-less line, which Intervals' parser drops along
                # with the whole `workout_doc.steps` payload.
                for attr, (expected_units, _suffix) in _TARGET_SUFFIX.items():
                    target = getattr(s, attr)
                    if not target:
                        continue
                    units = (target.get("units") or "").lower()
                    if units != expected_units:
                        raise ValueError(
                            f"Step {label!r} has {attr} target with units "
                            f"{target.get('units')!r}; expected {expected_units!r}."
                        )
                    start = target.get("start")
                    end = target.get("end")
                    # `bool` subclasses `int` in Python — `isinstance(True, int)`
                    # is True. Explicit exclusion so a `start: True` payload
                    # can't sneak past the numeric guard.
                    if not isinstance(start, (int, float)) or isinstance(start, bool):
                        raise ValueError(f"Step {label!r} {attr} target missing numeric 'start' (got {start!r}).")
                    if end is not None and (not isinstance(end, (int, float)) or isinstance(end, bool)):
                        raise ValueError(f"Step {label!r} {attr} target has non-numeric 'end' (got {end!r}).")

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

    @property
    def has_pace_steps(self) -> bool:
        """Check if any terminal step targets pace.

        Used to set ``event.target = "PACE"`` so Garmin renders pace targets
        on Run/Swim workouts. Without an explicit top-level ``target`` Garmin
        defaults to ``AUTO`` → ``HR`` for Run, and silently drops pace-target
        cells from the workout step view (verified live, ramp-test pre-flight
        2026-05-07).
        """

        def _has_pace(steps: list[WorkoutStepDTO]) -> bool:
            for s in steps:
                if s.pace is not None and not s.steps:
                    return True
                if s.steps and _has_pace(s.steps):
                    return True
            return False

        return _has_pace(self.steps)

    def to_intervals_event(self) -> "EventExDTO":
        """Convert to Intervals.icu POST /events DTO.

        Two parallel representations are sent on every push:

        - ``workout_doc.steps`` (JSON) drives Garmin/Wahoo via FIT export and
          our local ``ScheduledWorkout`` mirror — both have worked since day 1.
        - Top-level ``description`` (native-format text) is what Intervals.icu
          web/mobile UI parses to render the structured workout view. Without
          it the UI shows only the workout's name and total duration — steps
          stay invisible. See ``docs/INTERVALS_NATIVE_WORKOUT_FORMAT.md`` for
          the grammar; the renderer lives in ``_render_native_description``.

        ``workout_doc.description`` carries the AI rationale (Garmin Connect
        shows it as the workout note on the phone). The top-level
        ``description`` is the structured form for Intervals' UI — different
        slots, different consumers.

        Sports listed in ``_NO_TARGET_SPORTS`` (currently just ``Other``) skip
        the native renderer: native grammar requires an intensity target on
        every step and yoga/mobility steps don't have one. Workout cards
        (``compose_workout``) set their own top-level description with the
        HTML link, which Intervals stores as plain text.

        Historical note: a 2026-04-30 regression caused Intervals to silently
        drop ``workout_doc.steps`` for Swim events when top-level description
        was present. Probes on 2026-05-12 showed the drop only happens when
        Intervals' parser fails to recognise the description as native
        format — successful parses preserve steps for every sport. Passing a
        validated native render through is safe.
        """
        # Top-level event target. Default `None` → Intervals.icu maps to `AUTO`
        # which Garmin then resolves to HR for Run / power for Ride. We must
        # set `PACE` explicitly when the workout uses pace targets (distance
        # steps or `pace` keys on terminal steps) — otherwise Garmin renders
        # only HR on Run pace-driven workouts (e.g. ramp tests).
        target = "PACE" if self.sport in ("Swim", "Run") and (self.has_distance_steps or self.has_pace_steps) else None

        # Strip duplicate "AI: " prefix if Claude already added it
        clean_name = self.name[4:] if self.name.startswith("AI: ") else self.name

        workout_doc: dict = {"steps": [s.model_dump(exclude_none=True) for s in self.steps]}
        if self.rationale:
            workout_doc["description"] = self.rationale

        description: str | None = None
        if self.sport not in _NO_TARGET_SPORTS:
            description = _render_native_description(self.steps, self.sport)

        return EventExDTO(
            category="WORKOUT",
            type=self.sport,
            name=f"AI: {clean_name}" + (f" ({self.suffix})" if self.suffix else ""),
            start_date_local=f"{self.target_date}T00:00:00",
            moving_time=self.duration_minutes * 60,
            external_id=self.external_id,
            workout_doc=workout_doc,
            target=target,
            description=description,
        )
