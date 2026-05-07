"""Tests for ATP Phase 4: Ramp tests and threshold analysis."""

from datetime import date

from data.ramp_tests import RAMP_STEPS_RIDE, build_ramp_steps_run, create_ramp_test


class TestRampProtocolRide:
    def test_ride_has_8_steps(self):
        assert len(RAMP_STEPS_RIDE) == 8  # warmup + 6 steps + cooldown
        assert RAMP_STEPS_RIDE[0].text == "Warm-up"
        assert RAMP_STEPS_RIDE[-1].text == "Cool-down"

    def test_ride_uses_power(self):
        for step in RAMP_STEPS_RIDE:
            assert step.power is not None
            assert step.power["units"] == "%ftp"
            assert step.hr is None

    def test_ride_work_steps_progressive(self):
        work_steps = RAMP_STEPS_RIDE[1:-1]
        values = [s.power["value"] for s in work_steps]
        assert values == sorted(values), "Ride steps should be progressive"

    def test_ride_step_durations(self):
        assert RAMP_STEPS_RIDE[0].duration == 600  # WU 10 min
        assert RAMP_STEPS_RIDE[-1].duration == 600  # CD 10 min
        for s in RAMP_STEPS_RIDE[1:-1]:
            assert s.duration == 300  # 5 min work steps


class TestBuildRampStepsRun:
    def test_returns_12_steps(self):
        """WU + 10 work + CD = 12 total."""
        steps = build_ramp_steps_run(threshold_pace_sec_per_km=295.0)
        assert len(steps) == 12
        assert steps[0].text == "Warm-up"
        assert steps[-1].text == "Cool-down"

    def test_warmup_and_cooldown_use_hr(self):
        steps = build_ramp_steps_run(295.0)
        for s in (steps[0], steps[-1]):
            assert s.hr == {"units": "%lthr", "value": 70}
            assert s.pace is None
            assert s.duration == 600

    def test_work_steps_use_pct_pace(self):
        steps = build_ramp_steps_run(295.0)
        for s in steps[1:-1]:
            assert s.pace is not None
            assert s.pace["units"] == "%pace"
            assert isinstance(s.pace["value"], int)
            assert s.duration == 180  # 3 min

    def test_work_steps_pct_progressive(self):
        """%pace ascends from 85% to 130% across 10 work steps."""
        steps = build_ramp_steps_run(295.0)
        pcts = [s.pace["value"] for s in steps[1:-1]]
        assert pcts == [85, 90, 95, 100, 105, 110, 115, 120, 125, 130]

    def test_step_labels_include_threshold_pct(self):
        steps = build_ramp_steps_run(295.0)
        labels = [s.text for s in steps[1:-1]]
        assert "85% threshold" in labels[0]
        assert "130% threshold" in labels[-1]

    def test_threshold_param_currently_unused(self):
        """Steps don't depend on threshold_pace — Intervals.icu does the conversion.

        Parameter is kept on the signature for future fallback to s/km if %pace
        ever proves unreliable, but right now any value (or None) yields the
        same 85→130% ladder.
        """
        a = build_ramp_steps_run(threshold_pace_sec_per_km=240.0)
        b = build_ramp_steps_run(threshold_pace_sec_per_km=360.0)
        c = build_ramp_steps_run(None)
        assert [s.pace for s in a] == [s.pace for s in b] == [s.pace for s in c]


class TestCreateRampTest:
    def test_creates_ride_workout(self):
        workout = create_ramp_test("Ride", date(2026, 4, 1), days_since=25)
        assert workout.sport == "Ride"
        assert "Ramp Test" in workout.name
        assert workout.suffix is None
        assert len(workout.steps) == 8
        assert workout.duration_minutes == 50
        assert "25 days old" in workout.rationale
        assert "Chest strap" in workout.rationale
        assert "Treadmill" not in workout.rationale  # ride doesn't need it

    def test_creates_run_workout_with_threshold(self):
        """10+10*3+10 = 50 min total when threshold provided."""
        workout = create_ramp_test("Run", date(2026, 4, 1), threshold_pace=295.0)
        assert workout.sport == "Run"
        assert len(workout.steps) == 12
        assert workout.duration_minutes == 50
        assert "Treadmill" in workout.rationale
        assert "Threshold pace not set" not in workout.rationale

    def test_run_workout_warns_when_threshold_missing(self):
        workout = create_ramp_test("Run", date(2026, 4, 1), threshold_pace=None)
        assert len(workout.steps) == 12
        assert "Threshold pace not set" in workout.rationale
        assert "calibrate" in workout.rationale.lower()

    def test_rejects_swim(self):
        import pytest

        with pytest.raises(ValueError, match="not supported"):
            create_ramp_test("Swim", date(2026, 4, 1))

    def test_to_intervals_event_ride(self):
        workout = create_ramp_test("Ride", date(2026, 4, 1))
        event = workout.to_intervals_event()
        assert event.category == "WORKOUT"
        assert event.type == "Ride"
        assert "AI: Ramp Test" in event.name
        assert "(generated)" not in event.name  # suffix=None → no label
        assert event.workout_doc is not None
        assert len(event.workout_doc["steps"]) == 8

    def test_to_intervals_event_run(self):
        workout = create_ramp_test("Run", date(2026, 4, 1), threshold_pace=295.0)
        event = workout.to_intervals_event()
        assert event.type == "Run"
        assert len(event.workout_doc["steps"]) == 12
        # Work steps carry %pace targets
        work = event.workout_doc["steps"][1:-1]
        for s in work:
            assert s["pace"]["units"] == "%pace"

    def test_to_intervals_event_run_sets_target_pace(self):
        """Run with pace-targeted steps must set top-level target=PACE.

        Without it Intervals.icu defaults to AUTO → HR for Run, and Garmin
        silently drops pace cells from the workout step view. Verified live
        2026-05-07.
        """
        workout = create_ramp_test("Run", date(2026, 4, 1), threshold_pace=295.0)
        event = workout.to_intervals_event()
        assert event.target == "PACE"

    def test_to_intervals_event_ride_no_explicit_target(self):
        """Ride with power steps doesn't need explicit target — AUTO handles it."""
        workout = create_ramp_test("Ride", date(2026, 4, 1))
        event = workout.to_intervals_event()
        assert event.target is None


class TestMorningMessage:
    def test_compact_format(self):
        from types import SimpleNamespace

        from bot.formatter import build_morning_message

        row = SimpleNamespace(
            recovery_score=72.0,
            recovery_category="good",
            readiness_level="green",
            ctl=45.0,
            atl=38.0,
            sleep_score=80,
            sleep_secs=27000,
        )
        msg = build_morning_message(row)
        assert "Recovery 72" in msg
        assert "HRV" in msg
        # No AI recommendation in Telegram
        assert "рекоменда" not in msg.lower()

    def test_tsb_warning(self):
        from types import SimpleNamespace

        from bot.formatter import build_morning_message

        row = SimpleNamespace(
            recovery_score=60.0,
            recovery_category="moderate",
            readiness_level="yellow",
            ctl=50.0,
            atl=80.0,  # TSB = -30
            sleep_score=70,
            sleep_secs=25200,
        )
        msg = build_morning_message(row)
        assert "overtraining" in msg.lower()

    def test_threshold_drift_alert(self):
        from types import SimpleNamespace

        from bot.formatter import build_morning_message

        row = SimpleNamespace(
            recovery_score=80.0,
            recovery_category="good",
            readiness_level="green",
            ctl=50.0,
            atl=45.0,
            sleep_score=85,
            sleep_secs=28800,
        )
        drift = {
            "alerts": [
                {
                    "sport": "Run",
                    "measured_avg": 158,
                    "config_value": 153,
                    "diff_pct": 3.3,
                    "tests_count": 3,
                }
            ]
        }
        msg = build_morning_message(row, threshold_drift=drift)
        assert "ПОРОГИ" in msg
        assert "158" in msg
        assert "153" in msg

    def test_no_drift_no_block(self):
        from types import SimpleNamespace

        from bot.formatter import build_morning_message

        row = SimpleNamespace(
            recovery_score=80.0,
            recovery_category="good",
            readiness_level="green",
            ctl=50.0,
            atl=45.0,
            sleep_score=85,
            sleep_secs=28800,
        )
        msg = build_morning_message(row, threshold_drift=None)
        assert "ПОРОГИ" not in msg
