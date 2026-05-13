"""Tests for ATP Phase 2: workout parsing and adaptation."""

from data.db import AthleteThresholdsDTO
from data.intervals.dto import RecoveryScoreDTO, WorkoutStepDTO
from data.workout_adapter import (
    compute_constraints,
    estimate_step_zone,
    estimate_workout_max_zone,
    humango_to_intervals_steps,
    is_humango_event,
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


# ---------------------------------------------------------------------------
# HumanGo enrichment (docs/HUMANGO_ENRICHMENT_SPEC.md)
# ---------------------------------------------------------------------------


class TestIsHumangoEvent:
    """Detection guard — must accept only HumanGo events with parseable
    structure and no existing steps."""

    def test_accepts_humango_description_with_structure(self):
        assert is_humango_event(BIKE_DESCRIPTION, None) is True

    def test_accepts_humango_with_empty_workout_doc(self):
        assert is_humango_event(BIKE_DESCRIPTION, {}) is True
        assert is_humango_event(BIKE_DESCRIPTION, {"steps": []}) is True

    def test_rejects_non_humango_description(self):
        # Generic description without HumanGo signature.
        assert is_humango_event("Easy 30 min Z2 run", None) is False

    def test_rejects_humango_rest_day_no_structure(self):
        # HumanGo «rest day» — has View link but no separator.
        assert is_humango_event("View on HumanGo: https://app.humango.ai/...\n\nRest day", None) is False

    def test_rejects_event_already_enriched(self):
        # Idempotency: we (or anyone) already wrote steps.
        wd = {"steps": [{"duration": 300, "hr": {"units": "%lthr", "start": 70, "end": 80}}]}
        assert is_humango_event(BIKE_DESCRIPTION, wd) is False

    def test_rejects_empty_description(self):
        assert is_humango_event(None, None) is False
        assert is_humango_event("", None) is False


def _thresholds(**kwargs) -> AthleteThresholdsDTO:
    """Build an AthleteThresholdsDTO with override kwargs."""
    return AthleteThresholdsDTO(**kwargs)


class TestHumangoToIntervalsStepsRoundTrip:
    """Round-trip math: ``%X × threshold ≈ HumanGo's original absolute`` (±1
    unit rounding). Watches see the original HumanGo corridor verbatim."""

    def test_run_hr_round_trip(self):
        run_desc = (
            "View on HumanGo: https://app.humango.ai/myday?date=2026-05-14\n\n"
            "==============================\n\n"
            "warmup\n\nduration: 10 min\n\nheart rate:\n\nlow: 130 bpm\n\nhigh: 150 bpm\n\n"
            "==============================\n"
        )
        thresholds = _thresholds(lthr_run=160)
        steps = humango_to_intervals_steps(run_desc, "Run", thresholds)
        assert steps and len(steps) == 1

        hr = steps[0].hr
        assert hr["units"] == "%lthr"
        # Round-trip: pct × LTHR ≈ original bpm
        assert abs(round(hr["start"] * 160 / 100) - 130) <= 1
        assert abs(round(hr["end"] * 160 / 100) - 150) <= 1

    def test_ride_power_round_trip(self):
        # First step from the production BIKE_DESCRIPTION fixture.
        thresholds = _thresholds(ftp=200)
        steps = humango_to_intervals_steps(BIKE_DESCRIPTION, "Ride", thresholds)
        assert steps

        warmup = steps[0]
        assert warmup.power and warmup.power["units"] == "%ftp"
        # HumanGo warmup: 116-151 W against FTP=200
        assert abs(round(warmup.power["start"] * 200 / 100) - 116) <= 1
        assert abs(round(warmup.power["end"] * 200 / 100) - 151) <= 1

    def test_run_pace_round_trip(self):
        """HumanGo Run with pace-per-km targets (verified empirically 2026-05-16
        on event 109976490 — caused target-less push before the fix)."""
        run_desc = (
            "View on HumanGo: https://app.humango.ai/myday?date=2026-05-16\n\n"
            "==============================\n\n"
            "warmup\n\nduration: 5 min\n\npace:\n\nlow: 8:11 per km\n\nhigh: 6:33 per km\n\n"
            "==============================\n\n"
            "interval\n\nduration: 15 min\n\npace:\n\nlow: 6:33 per km\n\nhigh: 5:46 per km\n\n"
            "==============================\n\n"
            "cooldown\n\nduration: 5 min\n\npace:\n\nlow: 8:11 per km\n\nhigh: 6:33 per km\n\n"
            "==============================\n"
        )
        # threshold_pace_run = 5:30/km = 330 sec/km
        thresholds = _thresholds(threshold_pace_run=330.0)
        steps = humango_to_intervals_steps(run_desc, "Run", thresholds)
        assert steps and len(steps) == 3

        interval = steps[1]
        assert interval.pace and interval.pace["units"] == "%pace"
        # HumanGo: low=6:33 (393s/km), high=5:46 (346s/km). Threshold=330s/km.
        # %pace = threshold/target × 100 (velocity ratio).
        assert abs(round(330 / interval.pace["start"] * 100) - 393) <= 1
        assert abs(round(330 / interval.pace["end"] * 100) - 346) <= 1
        assert interval.pace["start"] < interval.pace["end"]  # slower < faster

    def test_run_pace_only_description_with_hr_only_threshold_returns_none(self):
        """Fail-closed: athlete has LTHR but no threshold_pace_run, HumanGo
        emits pace-only Run blocks → cold-start guard passes (LTHR > 0) but
        each step's pace converter fails (no threshold) → step emitted with
        all-null targets. Without the post-build guard, those would be pushed
        and lock out future re-enrichment via `is_humango_event` idempotency.

        Symmetric case (pace threshold only, HR-only description) is the
        same failure mode — covered by the second assertion below.
        """
        run_pace_desc = (
            "==============================\n\n"
            "interval\n\nduration: 10 min\n\npace:\n\nlow: 6:33 per km\n\nhigh: 5:46 per km\n\n"
            "==============================\n"
        )
        # LTHR set, pace threshold absent → must return None, not target-less steps.
        assert humango_to_intervals_steps(run_pace_desc, "Run", _thresholds(lthr_run=160)) is None

        # Symmetric: pace threshold set, HR-only description → also None.
        run_hr_desc = (
            "==============================\n\n"
            "interval\n\nduration: 10 min\n\nheart rate:\n\nlow: 130 bpm\n\nhigh: 150 bpm\n\n"
            "==============================\n"
        )
        assert humango_to_intervals_steps(run_hr_desc, "Run", _thresholds(threshold_pace_run=330.0)) is None

    def test_run_pace_cold_start_returns_none(self):
        """Run with pace targets but missing threshold_pace_run → skip entirely.

        Previously cold-start only checked lthr_run, so a pace-only run with
        threshold_pace=None silently produced target-less steps. After the
        fix, the guard accepts either threshold; absence of BOTH skips.
        """
        run_desc = (
            "==============================\n\n"
            "interval\n\nduration: 10 min\n\npace:\n\nlow: 6:33 per km\n\nhigh: 5:46 per km\n\n"
            "==============================\n"
        )
        # Neither LTHR nor threshold_pace_run set → cold-start skip.
        assert humango_to_intervals_steps(run_desc, "Run", _thresholds()) is None
        # With ONLY threshold_pace_run → must work.
        steps = humango_to_intervals_steps(run_desc, "Run", _thresholds(threshold_pace_run=330.0))
        assert steps and steps[0].pace and steps[0].pace["units"] == "%pace"

    def test_swim_pace_round_trip(self):
        # CSS = 110 sec/100m (slower than HumanGo's «low»).
        thresholds = _thresholds(css=110.0)
        steps = humango_to_intervals_steps(SWIM_DESCRIPTION, "Swim", thresholds)
        assert steps

        # Drill down to the inner warmup (HumanGo wraps in a repeat group).
        first = steps[0]
        warmup = first.steps[0] if first.reps else first
        assert warmup.pace and warmup.pace["units"] == "%pace"

        # HumanGo: low=2:55 (175s), high=2:39 (159s). CSS=110s.
        # %pace is velocity ratio: start = css/low_sec, end = css/high_sec.
        # Round-trip: css/pct × 100 ≈ original sec.
        assert abs(round(110 / warmup.pace["start"] * 100) - 175) <= 1
        assert abs(round(110 / warmup.pace["end"] * 100) - 159) <= 1
        # Velocity corridor: start < end (slower < faster).
        assert warmup.pace["start"] < warmup.pace["end"]


class TestHumangoToIntervalsStepsColdStart:
    """Cold-start fallback — return ``None`` so caller skips the enrichment push."""

    def test_run_missing_lthr_returns_none(self):
        run_desc = (
            "View on HumanGo: https://app.humango.ai/...\n\n"
            "==============================\n\nwarmup\n\nduration: 10 min\n\n"
            "heart rate:\n\nlow: 130 bpm\n\nhigh: 150 bpm\n\n=========================\n"
        )
        assert humango_to_intervals_steps(run_desc, "Run", _thresholds()) is None

    def test_ride_missing_ftp_and_lthr_returns_none(self):
        # Ride needs at least one of {ftp, lthr_bike}.
        assert humango_to_intervals_steps(BIKE_DESCRIPTION, "Ride", _thresholds()) is None

    def test_ride_with_only_lthr_works_for_hr_steps(self):
        # Some HumanGo rides emit HR not power — LTHR is enough.
        ride_hr_desc = (
            "View on HumanGo: https://app.humango.ai/...\n\n"
            "==============================\n\nwarmup\n\nduration: 5 min\n\n"
            "heart rate:\n\nlow: 110 bpm\n\nhigh: 135 bpm\n\n=========================\n"
        )
        steps = humango_to_intervals_steps(ride_hr_desc, "Ride", _thresholds(lthr_bike=150))
        assert steps and steps[0].hr and steps[0].hr["units"] == "%lthr"

    def test_swim_missing_css_returns_none(self):
        assert humango_to_intervals_steps(SWIM_DESCRIPTION, "Swim", _thresholds()) is None

    def test_unsupported_sport_returns_none(self):
        assert humango_to_intervals_steps(BIKE_DESCRIPTION, "WeightTraining", _thresholds(ftp=200)) is None
        assert humango_to_intervals_steps(BIKE_DESCRIPTION, "Other", _thresholds(ftp=200)) is None

    def test_zero_threshold_treated_as_missing(self):
        """A corrupted DB row with ``ftp=0`` must not divide-by-zero — same skip path."""
        assert humango_to_intervals_steps(BIKE_DESCRIPTION, "Ride", _thresholds(ftp=0)) is None


class TestHumangoToIntervalsStepsEdgeCases:
    """Edge cases the actor-side `is_humango_event` gate normally filters out,
    but the converter must remain defensive in case it's invoked directly."""

    def test_description_without_separators_returns_none(self):
        """Defense-in-depth: if ``is_humango_event`` is bypassed and a flat-text
        description reaches the converter, it must return ``None`` (no steps
        to push) — not an empty list that the caller might still try to push."""
        desc = "View on HumanGo: https://app.humango.ai/...\n\nRest day — no workout planned."
        result = humango_to_intervals_steps(desc, "Run", _thresholds(lthr_run=160))
        assert result is None

    def test_leading_trailing_whitespace_tolerated(self):
        """Padded description should parse the same as the unpadded version —
        ``_split_into_blocks`` already ``.strip()``s each block."""
        desc = (
            "   \n\n"
            "View on HumanGo: https://app.humango.ai/...\n\n"
            "==============================\n\n"
            "warmup\n\nduration: 10 min\n\nheart rate:\n\nlow: 130 bpm\n\nhigh: 150 bpm\n\n"
            "==============================\n   \n\n"
        )
        steps = humango_to_intervals_steps(desc, "Run", _thresholds(lthr_run=160))
        assert steps and len(steps) == 1
        assert steps[0].text == "Warm-up"

    def test_sport_mismatch_returns_none(self):
        """A Ride-power-only block fed in as 'Run' produces zero targets per
        step (Run branch only looks at HR/pace). The post-build fail-closed
        guard then drops the whole workout to None instead of emitting
        target-less steps — pushing target-less would lock out future
        re-enrichment via `is_humango_event` idempotency.
        """
        ride_power_desc = (
            "View on HumanGo: https://app.humango.ai/...\n\n"
            "==============================\n\n"
            "warmup\n\nduration: 5 min\n\npower:\n\nlow: 100 W\n\nhigh: 130 W\n\n"
            "==============================\n"
        )
        # Run looks at HR + pace only — finds neither in a power-only block.
        # All steps target-less → fail-closed guard returns None.
        assert humango_to_intervals_steps(ride_power_desc, "Run", _thresholds(lthr_run=160)) is None


class TestHumangoToIntervalsStepsRepeatGroup:
    """HumanGo's ``repeat N times`` blocks must lift to ``WorkoutStepDTO(reps=N, steps=[…])``."""

    def test_bike_repeat_group_preserved(self):
        # BIKE_DESCRIPTION has «repeat 8 times» around interval+recovery.
        steps = humango_to_intervals_steps(BIKE_DESCRIPTION, "Ride", _thresholds(ftp=200))
        assert steps
        repeat_step = next((s for s in steps if s.reps), None)
        assert repeat_step is not None
        assert repeat_step.reps == 8
        assert repeat_step.steps and len(repeat_step.steps) >= 2  # interval + recovery
        # Inner steps carry corridor schema, not legacy `value/low/high`.
        for sub in repeat_step.steps:
            target = sub.hr or sub.power or sub.pace
            if target:
                assert "start" in target and "end" in target
                assert "value" not in target  # explicit guard against schema regression

    def test_single_rep_group_flattens(self):
        """HumanGo occasionally wraps a single interval+recovery pair in
        ``repeat 1 times`` — that adds no value in the UI / watch view, so
        the converter must inline the sub-steps as plain sequential entries
        rather than emitting a ``1x`` repeat group."""
        desc = (
            "View on HumanGo: https://app.humango.ai/...\n\n"
            "==============================\n\n"
            "warmup\n\nduration: 5 min\n\npower:\n\nlow: 100 W\n\nhigh: 130 W\n\n"
            "==============================\n\n"
            "======= repeat 1 times =====\n\n"
            "==============================\n\n"
            "interval\n\nduration: 8 min\n\npower:\n\nlow: 120 W\n\nhigh: 150 W\n\n"
            "==============================\n\n"
            "recovery\n\nduration: 2 min\n\npower:\n\nlow: 80 W\n\nhigh: 100 W\n\n"
            "==============================\n\n"
            "cooldown\n\nduration: 5 min\n\npower:\n\nlow: 80 W\n\nhigh: 100 W\n\n"
        )
        steps = humango_to_intervals_steps(desc, "Ride", _thresholds(ftp=200))
        assert steps

        # No step in the top-level list should be a repeat group: the
        # single-rep one must have been flattened, and the rest are plain.
        assert all(
            s.reps is None for s in steps
        ), f"expected no repeat groups (single-rep should flatten), got reps={[s.reps for s in steps]}"
        # Sequence: warmup, interval (flattened), recovery (flattened), cooldown
        assert [s.text for s in steps] == ["Warm-up", "Interval", "Recovery", "Cool-down"]
