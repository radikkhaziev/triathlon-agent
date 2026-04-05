"""Tests for ATP Phase 2: workout parsing and adaptation."""

from data.intervals.dto import RecoveryScoreDTO, ScheduledWorkoutDTO, WorkoutStepDTO
from data.workout_adapter import (
    adapt_workout,
    clamp_step,
    compute_constraints,
    estimate_step_zone,
    estimate_workout_max_zone,
    needs_adaptation,
    parse_humango_description,
)

# ---------------------------------------------------------------------------
# Real HumanGo descriptions (from production DB)
# ---------------------------------------------------------------------------

BIKE_DESCRIPTION = """sport: cycling

total distance: 22.32 km

total duration: 50 min

The Goal Of This Session: is to improve fat utilisation in your body

View on HumanGo: https://redirect.humango.ai/myday?date=2026-04-02

==============================

warmup

duration: 5 min

power:

low: 116 W

high: 151 W

==============================

======= repeat 8 times =====

==============================

interval

Build cadence from 90 to 115 gradually

duration: 4 min

power:

low: 119 W

high: 170 W

==============================

recovery

duration: 1 min

power:

low: 95 W

high: 119 W

==============================

cooldown

Reduce intensity, if needed, to lower heart rate.

duration: 5 min

power:

low: 95 W

high: 119 W
"""

RUN_DESCRIPTION = """sport: running

total duration: 22 min

==============================

warmup

Big arm swing, focus on deep and complete breaths

duration: 3 min

heart rate:

low: 102 bpm

high: 127 bpm

==============================

======= repeat 3 times =====

==============================

interval

duration: 4 min

heart rate:

low: 127 bpm

high: 148 bpm

==============================

recovery

duration: 2 min

heart rate:

low: 102 bpm

high: 127 bpm

==============================

cooldown

duration: 1 min

heart rate:

low: 102 bpm

high: 127 bpm
"""

SWIM_DESCRIPTION = """sport: swimming

total distance: 1100 meters

View on HumanGo: https://redirect.humango.ai/myday?date=2026-04-01

==============================

======= repeat 4 times =====

==============================

warmup

distance: 50 meters

pace:

low: 2:55 per 100 meters

high: 2:39 per 100 meters

==============================

rest

duration: 0 min 30 sec

==============================

interval

distance: 100 meters

pace:

low: 2:55 per 100 meters

high: 2:39 per 100 meters

==============================

rest

duration: 0 min 20 sec
"""


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParseHumangoDescription:
    def test_bike_workout_structure(self):
        steps = parse_humango_description(BIKE_DESCRIPTION)
        assert len(steps) >= 3  # warmup + repeat group + cooldown

        # Warmup
        assert steps[0].text == "Warm-up"
        assert steps[0].duration == 300  # 5 min
        assert steps[0].power is not None
        assert steps[0].power["low"] == 116

        # Cooldown (last step)
        cooldown = steps[-1]
        assert cooldown.text == "Cool-down"
        assert cooldown.duration == 300  # 5 min

    def test_bike_repeat_group(self):
        steps = parse_humango_description(BIKE_DESCRIPTION)
        repeat = next((s for s in steps if s.reps), None)
        assert repeat is not None
        assert repeat.reps == 8
        assert len(repeat.steps) == 2  # interval + recovery

    def test_bike_power_targets(self):
        steps = parse_humango_description(BIKE_DESCRIPTION)
        warmup = steps[0]
        assert warmup.power["units"] == "watts"
        assert warmup.power["low"] == 116
        assert warmup.power["high"] == 151

    def test_run_workout_structure(self):
        steps = parse_humango_description(RUN_DESCRIPTION)
        assert len(steps) >= 3

        warmup = steps[0]
        assert warmup.text == "Warm-up"
        assert warmup.duration == 180  # 3 min
        assert warmup.hr is not None
        assert warmup.hr["low"] == 102
        assert warmup.hr["high"] == 127

    def test_run_hr_targets(self):
        steps = parse_humango_description(RUN_DESCRIPTION)
        repeat = next((s for s in steps if s.reps), None)
        assert repeat is not None
        assert repeat.reps == 3
        interval = repeat.steps[0]
        assert interval.hr["low"] == 127
        assert interval.hr["high"] == 148

    def test_swim_workout_structure(self):
        steps = parse_humango_description(SWIM_DESCRIPTION)
        assert len(steps) >= 1

        # Should have a repeat group
        repeat = next((s for s in steps if s.reps), None)
        assert repeat is not None
        assert repeat.reps == 4

    def test_swim_pace_targets(self):
        steps = parse_humango_description(SWIM_DESCRIPTION)
        repeat = next((s for s in steps if s.reps), None)
        warmup = repeat.steps[0]
        assert warmup.pace is not None
        assert warmup.pace["units"] == "sec_per_100m"
        # 2:55 = 175s, 2:39 = 159s
        assert warmup.pace["low"] == 175
        assert warmup.pace["high"] == 159

    def test_empty_description(self):
        assert parse_humango_description("") == []
        assert parse_humango_description(None) == []

    def test_no_separator_description(self):
        """Plain text without ====== should return empty."""
        result = parse_humango_description("Just a plain text workout description")
        assert result == []


# ---------------------------------------------------------------------------
# Zone estimation tests
# ---------------------------------------------------------------------------


class TestZoneEstimation:
    def test_power_z2(self):
        step = WorkoutStepDTO(power={"low": 116, "high": 151, "units": "watts"})
        assert estimate_step_zone(step, ftp=233) == 2  # ~57% FTP

    def test_power_z3(self):
        step = WorkoutStepDTO(power={"low": 180, "high": 210, "units": "watts"})
        assert estimate_step_zone(step, ftp=233) == 3  # ~84% FTP

    def test_power_z4(self):
        step = WorkoutStepDTO(power={"low": 210, "high": 245, "units": "watts"})
        assert estimate_step_zone(step, ftp=233) == 4  # ~98% FTP

    def test_hr_z1(self):
        step = WorkoutStepDTO(hr={"low": 90, "high": 110, "units": "bpm"})
        assert estimate_step_zone(step, lthr=153) == 1  # ~65% LTHR

    def test_hr_z4(self):
        step = WorkoutStepDTO(hr={"low": 127, "high": 148, "units": "bpm"})
        # mid = 137.5 bpm, 137.5/153 = 89.9% LTHR → Z4 (87-92%)
        assert estimate_step_zone(step, lthr=153) == 4

    def test_workout_max_zone(self):
        steps = parse_humango_description(BIKE_DESCRIPTION)
        max_z = estimate_workout_max_zone(steps, ftp=233)
        assert max_z >= 2  # warmup is Z2, intervals Z2-Z3


# ---------------------------------------------------------------------------
# Constraint computation tests
# ---------------------------------------------------------------------------


class TestComputeConstraints:
    def _recovery(self, score, category):
        return RecoveryScoreDTO(score=score, category=category, recommendation="")

    def test_excellent_green_no_constraints(self):
        z, f = compute_constraints(self._recovery(90, "excellent"), "green", tsb=5)
        assert z == 5
        assert f == 1.0

    def test_good_green_no_constraints(self):
        z, f = compute_constraints(self._recovery(75, "good"), "green", tsb=0)
        assert z == 5
        assert f == 1.0

    def test_good_yellow_capped(self):
        z, f = compute_constraints(self._recovery(75, "good"), "yellow", tsb=0)
        assert z <= 3
        assert f <= 0.90

    def test_moderate_capped_z2(self):
        z, f = compute_constraints(self._recovery(55, "moderate"), "green", tsb=-5)
        assert z <= 2
        assert f <= 0.85

    def test_low_recovery_capped(self):
        z, f = compute_constraints(self._recovery(30, "low"), "red", tsb=-10)
        assert z <= 2
        assert f <= 0.75

    def test_tsb_override(self):
        z, f = compute_constraints(self._recovery(85, "excellent"), "green", tsb=-30)
        assert z <= 2  # TSB < -25 caps at Z2

    def test_ra_decline_additional_reduction(self):
        z, f = compute_constraints(self._recovery(75, "good"), "green", tsb=0, ra=-8)
        assert z < 5 or f < 1.0


# ---------------------------------------------------------------------------
# Adaptation tests
# ---------------------------------------------------------------------------


class TestNeedsAdaptation:
    def test_z3_workout_needs_adaptation_at_z2_cap(self):
        steps = [
            WorkoutStepDTO(text="Interval", duration=600, power={"low": 200, "high": 220, "units": "watts"}),
        ]
        assert needs_adaptation(steps, max_zone=2, ftp=233)

    def test_z2_workout_ok_at_z2_cap(self):
        steps = [
            WorkoutStepDTO(text="Easy", duration=1800, power={"low": 116, "high": 151, "units": "watts"}),
        ]
        assert not needs_adaptation(steps, max_zone=2, ftp=233)


class TestClampStep:
    def test_clamp_power_to_z2(self):
        step = WorkoutStepDTO(
            text="Interval",
            duration=600,
            power={"low": 200, "high": 240, "units": "watts", "value": 220},
        )
        clamped = clamp_step(step, max_zone=2, duration_factor=0.85, ftp=233)

        # Z2 upper = 82% of 233 = ~191W
        assert clamped.power["high"] <= 192
        assert clamped.duration < 600  # shortened

    def test_clamp_hr_to_z2(self):
        step = WorkoutStepDTO(
            text="Interval",
            duration=600,
            hr={"low": 140, "high": 155, "units": "bpm", "value": 147},
        )
        clamped = clamp_step(step, max_zone=2, duration_factor=1.0, lthr=153)

        # Z2 upper = 82% of 153 = ~125 bpm
        assert clamped.hr["high"] <= 126

    def test_clamp_repeat_group(self):
        step = WorkoutStepDTO(
            text="3x Intervals",
            reps=3,
            steps=[
                WorkoutStepDTO(text="Work", duration=300, power={"low": 200, "high": 240, "units": "watts"}),
                WorkoutStepDTO(text="Rest", duration=120, power={"low": 95, "high": 119, "units": "watts"}),
            ],
        )
        clamped = clamp_step(step, max_zone=2, duration_factor=0.85, ftp=233)
        assert clamped.reps == 3
        assert clamped.steps[0].power["high"] <= 192


# ---------------------------------------------------------------------------
# Full adaptation pipeline
# ---------------------------------------------------------------------------


class TestAdaptWorkout:
    def _make_workout(self, description=BIKE_DESCRIPTION):

        return ScheduledWorkoutDTO(
            id=1,
            start_date_local="2026-04-02",
            name="CYCLING:Cadence spin-ups-3",
            type="Ride",
            description=description,
            moving_time=3000,
        )

    def test_no_adaptation_when_excellent(self):
        recovery = RecoveryScoreDTO(score=90, category="excellent", recommendation="zone2_ok")
        result = adapt_workout(self._make_workout(), recovery, "green", tsb=5)
        assert result is None

    def test_adapts_when_moderate(self):
        recovery = RecoveryScoreDTO(score=55, category="moderate", recommendation="zone1_short")
        result = adapt_workout(self._make_workout(), recovery, "yellow", tsb=-10)
        assert result is not None
        assert result.suffix == "adapted"
        assert "Adapted:" in result.name
        assert result.duration_minutes > 0

    def test_adapted_has_correct_sport(self):
        recovery = RecoveryScoreDTO(score=45, category="moderate", recommendation="zone1_short")
        result = adapt_workout(self._make_workout(), recovery, "red", tsb=-20)
        assert result is not None
        assert result.sport == "Ride"

    def test_adapted_steps_are_clamped(self):
        recovery = RecoveryScoreDTO(score=35, category="low", recommendation="skip")
        result = adapt_workout(self._make_workout(), recovery, "red", tsb=-30, ftp=233)
        assert result is not None
        for step in result.steps:
            if step.power and not step.steps:
                zone = estimate_step_zone(step, ftp=233)
                assert zone <= 2, f"Step {step.text} zone {zone} exceeds cap"

    def test_run_adaptation(self):

        original = ScheduledWorkoutDTO(
            id=2,
            start_date_local="2026-03-31",
            name="RUNNING:Tempo Run",
            type="Run",
            description=RUN_DESCRIPTION,
            moving_time=1320,
        )
        recovery = RecoveryScoreDTO(score=50, category="moderate", recommendation="zone1_short")
        result = adapt_workout(original, recovery, "yellow", tsb=-15, lthr=153)
        assert result is not None
        assert result.sport == "Run"

    def test_empty_description_returns_none(self):

        original = ScheduledWorkoutDTO(
            id=3,
            start_date_local="2026-04-01",
            name="RUNNING:Easy run",
            type="Run",
            description="Just a simple easy run, no structured intervals.",
            moving_time=1800,
        )
        recovery = RecoveryScoreDTO(score=50, category="moderate", recommendation="zone1_short")
        result = adapt_workout(original, recovery, "yellow", tsb=-15)
        assert result is None  # no parseable steps
