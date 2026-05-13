"""Tests for Phase 1: Adaptive Training Plan — AI workout generation."""

from datetime import date

import pytest
from pydantic import ValidationError

from data.intervals.dto import (
    PlannedWorkoutDTO,
    WorkoutStepDTO,
    _render_distance,
    _render_duration,
    _render_step,
    _render_target,
    _sanitize_label,
    render_native_description,
)

# ---------------------------------------------------------------------------
# WorkoutStep
# ---------------------------------------------------------------------------


class TestWorkoutStep:
    def test_simple_step(self):
        step = WorkoutStepDTO(text="Warm-up", duration=600, hr={"units": "%lthr", "start": 65})
        assert step.text == "Warm-up"
        assert step.duration == 600
        assert step.hr == {"units": "%lthr", "start": 65}
        assert step.power is None

    def test_power_step(self):
        step = WorkoutStepDTO(text="Z2", duration=1800, power={"units": "%ftp", "start": 75})
        assert step.power["start"] == 75

    def test_repeat_group(self):
        step = WorkoutStepDTO(
            text="Tempo",
            reps=3,
            steps=[
                WorkoutStepDTO(duration=300, hr={"units": "%lthr", "start": 88}),
                WorkoutStepDTO(duration=120, hr={"units": "%lthr", "start": 65}),
            ],
        )
        assert step.reps == 3
        assert len(step.steps) == 2
        assert step.duration == 0

    def test_model_dump_excludes_none(self):
        step = WorkoutStepDTO(text="Warm-up", duration=600, hr={"units": "%lthr", "start": 65})
        d = step.model_dump(exclude_none=True)
        assert "power" not in d
        assert "pace" not in d
        assert "cadence" not in d
        assert "reps" not in d
        assert "steps" not in d
        assert d["text"] == "Warm-up"
        assert d["duration"] == 600


# ---------------------------------------------------------------------------
# PlannedWorkout
# ---------------------------------------------------------------------------


class TestPlannedWorkout:
    def _make_workout(self, **kwargs):
        defaults = dict(
            sport="Ride",
            name="Z2 Endurance",
            steps=[WorkoutStepDTO(text="Main", duration=3600, power={"units": "%ftp", "start": 75})],
            duration_minutes=60,
            target_date=date(2026, 3, 29),
        )
        defaults.update(kwargs)
        return PlannedWorkoutDTO(**defaults)

    def test_external_id_format(self):
        w = self._make_workout(sport="Ride", target_date=date(2026, 3, 29), slot="morning")
        assert w.external_id == "tricoach:2026-03-29:ride:morning"

    def test_external_id_evening_slot(self):
        w = self._make_workout(slot="evening")
        assert w.external_id.endswith(":evening")

    def test_to_intervals_event_keys(self):
        w = self._make_workout()
        event = w.to_intervals_event()
        assert event.category == "WORKOUT"
        assert event.type == "Ride"
        assert event.start_date_local == "2026-03-29T00:00:00"
        assert event.moving_time == 3600
        assert event.external_id == "tricoach:2026-03-29:ride:morning"

    def test_no_suffix_in_event_name(self):
        """Default suffix=None produces clean name without parentheses."""
        w = self._make_workout()
        event = w.to_intervals_event()
        assert event.name == "AI: Z2 Endurance"

    def test_adapted_suffix(self):
        w = self._make_workout(suffix="adapted")
        event = w.to_intervals_event()
        assert event.name == "AI: Z2 Endurance (adapted)"

    def test_workout_doc_in_event(self):
        steps = [
            WorkoutStepDTO(text="Warm-up", duration=600, power={"units": "%ftp", "start": 60}),
            WorkoutStepDTO(text="Main", duration=1800, power={"units": "%ftp", "start": 75}),
        ]
        w = self._make_workout(steps=steps)
        event = w.to_intervals_event()
        doc = event.workout_doc
        assert "steps" in doc
        assert len(doc["steps"]) == 2
        assert doc["steps"][0]["text"] == "Warm-up"
        assert doc["steps"][1]["power"]["start"] == 75

    def test_workout_doc_repeat_group(self):
        steps = [
            WorkoutStepDTO(
                text="Intervals",
                reps=4,
                steps=[
                    WorkoutStepDTO(duration=300, hr={"units": "%lthr", "start": 90}),
                    WorkoutStepDTO(duration=120, hr={"units": "%lthr", "start": 60}),
                ],
            ),
        ]
        w = self._make_workout(steps=steps)
        event = w.to_intervals_event()
        interval = event.workout_doc["steps"][0]
        assert interval["reps"] == 4
        assert len(interval["steps"]) == 2

    def test_top_level_description_renders_native_format(self):
        """For sports with intensity targets, top-level description carries the
        native-format step list so Intervals.icu UI can render the structure.
        See docs/INTERVALS_NATIVE_WORKOUT_FORMAT.md for the grammar."""
        w = self._make_workout()  # Ride, 1×60min Z2 @ 75% FTP
        event = w.to_intervals_event()
        assert event.description == "- Main 1h 75%\n"

    def test_rationale_lands_in_workout_doc(self):
        """Rationale stays in `workout_doc.description` (rendered by Garmin
        Connect as the workout note); top-level description is the native
        step render, not the rationale."""
        w = self._make_workout(rationale="recovery day, easy aerobic")
        event = w.to_intervals_event()
        assert event.workout_doc["description"] == "recovery day, easy aerobic"
        assert event.description is not None
        assert "recovery day" not in event.description  # rationale must not leak into native render

    def test_empty_rationale_omits_workout_doc_description(self):
        """Empty rationale → key absent from workout_doc (Garmin Connect note empty)."""
        w = self._make_workout(rationale="")
        event = w.to_intervals_event()
        assert "description" not in event.workout_doc

    def test_other_sport_skips_native_description(self):
        """`Other` (yoga/mobility/strength) steps don't have intensity targets;
        native grammar can't represent them. Top-level description stays None
        so workout_cards.py can set its own URL prefix."""
        w = self._make_workout(
            sport="Other",
            steps=[WorkoutStepDTO(text="Stretch", duration=30)],
            duration_minutes=1,
        )
        event = w.to_intervals_event()
        assert event.description is None

    def test_payload_has_native_description_for_intensity_sports(self):
        """Wire payload (model_dump exclude_none) carries top-level description
        for every sport except Other — regression guard against silently
        dropping it back to None."""
        cases = [
            ("Swim", WorkoutStepDTO(text="Main", distance=200.0, pace={"units": "%pace", "start": 70, "end": 80})),
            ("Run", WorkoutStepDTO(text="Main", duration=600, hr={"units": "%lthr", "start": 70})),
            ("Ride", WorkoutStepDTO(text="Main", duration=600, power={"units": "%ftp", "start": 70})),
        ]
        for sport, step in cases:
            w = self._make_workout(sport=sport, steps=[step], duration_minutes=10, rationale="x")
            payload = w.to_intervals_event().model_dump(exclude_none=True)
            assert "description" in payload, f"{sport} dropped top-level description"
            assert payload["workout_doc"]["description"] == "x"

    def test_rejects_step_without_target(self):
        """Targetless terminal steps leave watches unable to alert — must raise."""
        with pytest.raises(ValidationError, match="no intensity target"):
            PlannedWorkoutDTO(
                sport="Run",
                name="Test",
                steps=[WorkoutStepDTO(text="Z2", duration=1200)],
                duration_minutes=20,
            )

    def test_rejects_unknown_units(self):
        """Native description renderer expects `%lthr`/`%ftp`/`%pace`. Unknown
        units (e.g. raw bpm, %hrr) → renderer would emit a target-less line →
        Intervals' parser drops `workout_doc.steps`. Fail at DTO construction
        instead."""
        with pytest.raises(ValidationError, match="expected '%lthr'"):
            PlannedWorkoutDTO(
                sport="Run",
                name="Test",
                steps=[WorkoutStepDTO(text="Main", duration=600, hr={"units": "bpm", "start": 150})],
                duration_minutes=10,
            )

    def test_rejects_missing_start(self):
        """Target dict must carry a numeric `start` — otherwise renderer would
        emit a malformed line."""
        with pytest.raises(ValidationError, match="missing numeric 'start'"):
            PlannedWorkoutDTO(
                sport="Run",
                name="Test",
                steps=[WorkoutStepDTO(text="Main", duration=600, hr={"units": "%lthr"})],
                duration_minutes=10,
            )

    def test_rejects_repeat_substep_without_target(self):
        """Sub-steps of a repeat group must also carry targets."""
        with pytest.raises(ValidationError, match="no intensity target"):
            PlannedWorkoutDTO(
                sport="Run",
                name="Test",
                steps=[
                    WorkoutStepDTO(
                        text="Intervals",
                        reps=3,
                        steps=[
                            WorkoutStepDTO(duration=300),  # missing target
                            WorkoutStepDTO(duration=120, hr={"units": "%lthr", "start": 60}),
                        ],
                    ),
                ],
                duration_minutes=20,
            )

    def test_rejects_nested_repeat_groups(self):
        """Nested repeats have no native-grammar syntax and would silently lose inner steps
        in the description renderer — reject at DTO construction instead."""
        with pytest.raises(ValidationError, match="nested inside another repeat"):
            PlannedWorkoutDTO(
                sport="Run",
                name="Test",
                steps=[
                    WorkoutStepDTO(
                        text="Outer",
                        reps=2,
                        steps=[
                            WorkoutStepDTO(
                                text="Inner",
                                reps=3,
                                steps=[
                                    WorkoutStepDTO(duration=60, hr={"units": "%lthr", "start": 80}),
                                ],
                            ),
                        ],
                    ),
                ],
                duration_minutes=20,
            )

    def test_accepts_rest_step_without_target(self):
        """Terminal step labelled `Rest` is allowed without hr/power/pace.

        Intervals.icu renders target-less Rest as a real pool-side / between-set
        pause; a fake low-Z target would render as «slow swimming» instead.
        See `docs/WORKOUT_ABSOLUTE_TARGETS_SPEC.md` §14.
        """
        PlannedWorkoutDTO(
            sport="Swim",
            name="Test",
            steps=[
                WorkoutStepDTO(text="Stage 1", distance=400, pace={"units": "%pace", "start": 90, "end": 95}),
                WorkoutStepDTO(text="Rest", duration=45),
                WorkoutStepDTO(text="Stage 2", distance=400, pace={"units": "%pace", "start": 95, "end": 100}),
            ],
            duration_minutes=15,
        )

    def test_accepts_recovery_step_without_target(self):
        """Terminal `Recovery` step is also allowed without target (same rationale as Rest)."""
        PlannedWorkoutDTO(
            sport="Swim",
            name="Test",
            steps=[
                WorkoutStepDTO(text="Tempo", distance=200, pace={"units": "%pace", "start": 90, "end": 95}),
                WorkoutStepDTO(text="Recovery", duration=30),
                WorkoutStepDTO(text="Tempo", distance=200, pace={"units": "%pace", "start": 90, "end": 95}),
            ],
            duration_minutes=10,
        )

    def test_rest_label_is_case_and_whitespace_insensitive(self):
        """Validator strips + lowercases `text` before checking `_NO_TARGET_STEP_LABELS`.

        Guards against regression if someone refactors and drops the `.strip().lower()`
        normalisation in `_check_steps_have_targets`.
        """
        for label in ("Rest", "rest", "REST", "  Rest  "):
            PlannedWorkoutDTO(
                sport="Swim",
                name="Test",
                steps=[
                    WorkoutStepDTO(text="Stage", distance=400, pace={"units": "%pace", "start": 90, "end": 95}),
                    WorkoutStepDTO(text=label, duration=30),
                ],
                duration_minutes=12,
            )

    def test_rejects_non_rest_label_without_target(self):
        """Only `Rest` / `Recovery` (exact, after strip+lower) bypass the target check.

        A made-up label like `RestX` or `Off` must still fail — otherwise Claude could
        smuggle text-only steps past the validator under any name.
        """
        for label in ("RestX", "Off", "Stop", ""):
            with pytest.raises(ValidationError, match="no intensity target"):
                PlannedWorkoutDTO(
                    sport="Swim",
                    name="Test",
                    steps=[
                        WorkoutStepDTO(text="Stage", distance=400, pace={"units": "%pace", "start": 90, "end": 95}),
                        WorkoutStepDTO(text=label, duration=30),
                    ],
                    duration_minutes=12,
                )

    def test_accepts_pace_target(self):
        """Swim steps with pace target are valid."""
        PlannedWorkoutDTO(
            sport="Swim",
            name="Test",
            steps=[WorkoutStepDTO(text="Main", distance=400, pace={"units": "%pace", "start": 95})],
            duration_minutes=15,
        )

    def test_default_date_is_today(self):
        w = PlannedWorkoutDTO(
            sport="Run",
            name="Test",
            steps=[WorkoutStepDTO(text="Run", duration=1200, hr={"units": "%lthr", "start": 72, "end": 82})],
            duration_minutes=20,
        )
        assert w.target_date == date.today()


# ---------------------------------------------------------------------------
# Native-format description renderer
# ---------------------------------------------------------------------------


class TestSanitizeLabel:
    def test_strips_leading_digits(self):
        """`Drill: 50 fingertip drag` → `fingertip drag` (parser would catch
        leading `50` as duration). Real label from prod (id 109762368)."""
        assert _sanitize_label("50 fingertip drag", "Swim") == "fingertip drag"
        assert _sanitize_label("4x cadence build", "Ride") == "cadence build"

    def test_strips_z_pattern_for_run_swim(self):
        assert _sanitize_label("Z2 freestyle DPS focus", "Swim") == "freestyle DPS focus"
        assert _sanitize_label("Z2 main aerobic", "Run") == "main aerobic"

    def test_keeps_z_pattern_for_ride(self):
        """For Ride, `Z\\d+` resolves to power zones — valid target. Keep it."""
        assert _sanitize_label("Z2 endurance", "Ride") == "Z2 endurance"

    def test_handles_none_and_empty(self):
        assert _sanitize_label(None, "Run") == ""
        assert _sanitize_label("", "Run") == ""

    def test_collapses_whitespace(self):
        assert _sanitize_label("Z2  drill   focus", "Run") == "drill focus"


class TestRenderDuration:
    def test_seconds_only(self):
        assert _render_duration(30) == "30s"

    def test_minutes_only(self):
        assert _render_duration(600) == "10m"

    def test_hours_only(self):
        assert _render_duration(7200) == "2h"

    def test_combined(self):
        assert _render_duration(5400) == "1h30m"
        assert _render_duration(330) == "5m30s"
        assert _render_duration(3725) == "1h2m5s"

    def test_zero(self):
        assert _render_duration(0) == "0s"


class TestRenderDistance:
    def test_meters_under_1km(self):
        assert _render_distance(200.0) == "200mtr"

    def test_meters_rounded_to_nearest_int(self):
        # `int(round(...))` uses banker's rounding (half-to-even).
        # 150.4 → 150 (truncates down); 150.6 → 151 (rounds up).
        # 150.5 → 150 (half-to-even, rounds to nearest even integer).
        assert _render_distance(150.4) == "150mtr"
        assert _render_distance(150.6) == "151mtr"

    def test_exact_km(self):
        assert _render_distance(1000.0) == "1km"
        assert _render_distance(5000.0) == "5km"

    def test_fractional_km(self):
        assert _render_distance(1500.0) == "1.5km"


class TestRenderTarget:
    def test_hr_lthr_range(self):
        step = WorkoutStepDTO(text="Main", duration=600, hr={"units": "%lthr", "start": 75, "end": 82})
        assert _render_target(step, "Run") == "75-82% LTHR"

    def test_hr_lthr_single(self):
        step = WorkoutStepDTO(text="Main", duration=600, hr={"units": "%lthr", "start": 75})
        assert _render_target(step, "Run") == "75% LTHR"

    def test_power_ftp_range(self):
        """Bare `%` for Ride power — FTP implied by native grammar."""
        step = WorkoutStepDTO(text="Main", duration=600, power={"units": "%ftp", "start": 88, "end": 94})
        assert _render_target(step, "Ride") == "88-94%"

    def test_pace_range(self):
        step = WorkoutStepDTO(text="Main", distance=200.0, pace={"units": "%pace", "start": 80, "end": 90})
        assert _render_target(step, "Swim") == "80-90% Pace"

    def test_target_none_when_step_has_no_intensity(self):
        step = WorkoutStepDTO(text="Stretch", duration=30)  # Other-only shape
        assert _render_target(step, "Other") is None


class TestRenderStep:
    def test_label_distance_target(self):
        step = WorkoutStepDTO(
            text="Drill freestyle",
            distance=100.0,
            pace={"units": "%pace", "start": 80, "end": 90},
        )
        assert _render_step(step, "Swim") == "- Drill freestyle 100mtr 80-90% Pace"

    def test_empty_label_omitted(self):
        step = WorkoutStepDTO(duration=300, hr={"units": "%lthr", "start": 70})
        assert _render_step(step, "Run") == "- 5m 70% LTHR"


class TestRenderNativeDescription:
    def test_blank_lines_between_top_level_entities(self):
        steps = [
            WorkoutStepDTO(text="WU", duration=600, hr={"units": "%lthr", "start": 70}),
            WorkoutStepDTO(text="Main", duration=1800, hr={"units": "%lthr", "start": 85}),
            WorkoutStepDTO(text="CD", duration=300, hr={"units": "%lthr", "start": 60}),
        ]
        rendered = render_native_description(steps, "Run")
        assert rendered == "- WU 10m 70% LTHR\n\n- Main 30m 85% LTHR\n\n- CD 5m 60% LTHR\n"

    def test_repeat_block_inline_substeps_and_surrounding_blanks(self):
        """Repeat blocks must have blank lines around them and sub-steps
        flush-left without indentation — see syntax guide §«Repeat Group»."""
        steps = [
            WorkoutStepDTO(text="WU", duration=600, hr={"units": "%lthr", "start": 70}),
            WorkoutStepDTO(
                text="Intervals",
                reps=4,
                steps=[
                    WorkoutStepDTO(text="On", duration=300, hr={"units": "%lthr", "start": 90}),
                    WorkoutStepDTO(text="Off", duration=120, hr={"units": "%lthr", "start": 60}),
                ],
            ),
            WorkoutStepDTO(text="CD", duration=300, hr={"units": "%lthr", "start": 60}),
        ]
        rendered = render_native_description(steps, "Run")
        assert rendered == (
            "- WU 10m 70% LTHR\n" "\n" "4x\n" "- On 5m 90% LTHR\n" "- Off 2m 60% LTHR\n" "\n" "- CD 5m 60% LTHR\n"
        )

    def test_swim_full_mirror(self):
        """Mirror of the working Swim probe 109771472 — distance steps,
        pace targets, sanitised labels, two adjacent repeat blocks."""
        steps = [
            WorkoutStepDTO(text="Warm-up easy mix", distance=300.0, pace={"units": "%pace", "start": 65, "end": 78}),
            WorkoutStepDTO(
                text="Drill set",
                reps=4,
                steps=[
                    WorkoutStepDTO(
                        text="50 fingertip drag",  # leading digit — must be stripped
                        distance=100.0,
                        pace={"units": "%pace", "start": 80, "end": 90},
                    ),
                    WorkoutStepDTO(text="Rest", duration=15, pace={"units": "%pace", "start": 40, "end": 50}),
                ],
            ),
            WorkoutStepDTO(text="Cool-down", distance=100.0, pace={"units": "%pace", "start": 60, "end": 75}),
        ]
        rendered = render_native_description(steps, "Swim")
        assert "- 50" not in rendered  # leading digit stripped
        assert "fingertip drag" in rendered
        assert "4x\n" in rendered
        assert "- Rest 15s 40-50% Pace" in rendered


# ---------------------------------------------------------------------------
# Database CRUD (requires PostgreSQL via conftest)
# ---------------------------------------------------------------------------


class TestAiWorkoutsCRUD:
    async def test_save_and_get_by_external_id(self, _test_db):
        from data.db import AiWorkout

        row = await AiWorkout.save(
            user_id=1,
            date_str="2026-03-29",
            sport="Ride",
            slot="morning",
            external_id="tricoach:2026-03-29:ride:morning",
            intervals_id=12345,
            name="Z2 Endurance",
            description="Warm-up; Main; Cool-down",
            duration_minutes=60,
            target_tss=65,
            rationale="Bike CTL needs building",
        )
        assert row.external_id == "tricoach:2026-03-29:ride:morning"
        assert row.status == "active"

        fetched = await AiWorkout.get_by_external_id(1, "tricoach:2026-03-29:ride:morning")
        assert fetched is not None
        assert fetched.name == "Z2 Endurance"
        assert fetched.intervals_id == 12345

    async def test_upsert_updates_existing(self, _test_db):
        from data.db import AiWorkout

        await AiWorkout.save(
            user_id=1,
            date_str="2026-03-30",
            sport="Run",
            slot="morning",
            external_id="tricoach:2026-03-30:run:morning",
            intervals_id=111,
            name="Easy Run v1",
            description="Z2",
            duration_minutes=40,
            target_tss=30,
            rationale="v1",
        )

        await AiWorkout.save(
            user_id=1,
            date_str="2026-03-30",
            sport="Run",
            slot="morning",
            external_id="tricoach:2026-03-30:run:morning",
            intervals_id=222,
            name="Easy Run v2",
            description="Z2 updated",
            duration_minutes=45,
            target_tss=35,
            rationale="v2",
        )

        fetched = await AiWorkout.get_by_external_id(1, "tricoach:2026-03-30:run:morning")
        assert fetched.name == "Easy Run v2"
        assert fetched.intervals_id == 222

    async def test_cancel_ai_workout(self, _test_db):
        from data.db import AiWorkout

        await AiWorkout.save(
            user_id=1,
            date_str="2026-03-31",
            sport="Swim",
            slot="morning",
            external_id="tricoach:2026-03-31:swim:morning",
            intervals_id=333,
            name="CSS Intervals",
            description="Swim",
            duration_minutes=50,
            target_tss=45,
            rationale="test",
        )

        row = await AiWorkout.cancel(1, "tricoach:2026-03-31:swim:morning")
        assert row is not None
        assert row.status == "cancelled"

    async def test_get_upcoming(self, _test_db):
        from data.db import AiWorkout

        today = str(date.today())
        await AiWorkout.save(
            user_id=1,
            date_str=today,
            sport="Ride",
            slot="morning",
            external_id=f"tricoach:{today}:ride:upcoming-test",
            intervals_id=444,
            name="Today Ride",
            description="test",
            duration_minutes=60,
            target_tss=60,
            rationale="test",
        )

        rows = await AiWorkout.get_upcoming(user_id=1, days_ahead=1)
        names = [r.name for r in rows]
        assert "Today Ride" in names

    async def test_get_for_date(self, _test_db):
        from data.db import AiWorkout

        await AiWorkout.save(
            user_id=1,
            date_str="2026-04-01",
            sport="Run",
            slot="morning",
            external_id="tricoach:2026-04-01:run:date-test",
            intervals_id=555,
            name="April Run",
            description="test",
            duration_minutes=30,
            target_tss=25,
            rationale="test",
        )

        rows = await AiWorkout.get_for_date(1, date(2026, 4, 1))
        assert len(rows) >= 1
        assert any(r.name == "April Run" for r in rows)
