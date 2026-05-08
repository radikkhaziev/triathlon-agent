"""Tests for ATP Phase 4: Ramp tests and threshold analysis."""

from datetime import date

from data.ramp_tests import build_ramp_steps_ride, build_ramp_steps_run, create_ramp_test


class TestBuildRampStepsRide:
    """Bike protocol per docs/RAMP_TEST_BIKE_SPEC.md §5.

    Layout: WU 5min@50% + 5min@60% + 11×3min (60→110% by 5%) + 1×4min @120% + CD 10min@50%.
    Total: 57 min. Step 12 (final 120%) is a deliberate 10% jump from step 11
    — calibration trap, see SPEC §5.2.
    """

    def test_returns_15_steps(self):
        """2 WU + 11 regular work + 1 final + 1 CD = 15 total."""
        steps, _warnings = build_ramp_steps_ride(bike_ftp_watts=208.0)
        assert len(steps) == 15
        assert steps[0].text == "Warm-up easy"
        assert steps[1].text == "Warm-up build"
        assert steps[-1].text == "Cool-down"

    def test_uses_power_units_throughout(self):
        steps, _ = build_ramp_steps_ride(208.0)
        for step in steps:
            assert step.power is not None
            assert step.power["units"] == "%ftp"
            assert step.hr is None

    def test_warmup_phases(self):
        steps, _ = build_ramp_steps_ride(208.0)
        assert steps[0].duration == 300 and steps[0].power["value"] == 50
        assert steps[1].duration == 300 and steps[1].power["value"] == 60

    def test_regular_work_steps_60_to_110_pct(self):
        """11 regular work steps × 3 min, 60→110% in uniform 5% increments."""
        steps, _ = build_ramp_steps_ride(208.0)
        regular_work = steps[2:13]  # after 2 WU, before final + CD
        assert len(regular_work) == 11
        pcts = [s.power["value"] for s in regular_work]
        assert pcts == [60, 65, 70, 75, 80, 85, 90, 95, 100, 105, 110]
        for s in regular_work:
            assert s.duration == 180  # 3 min

    def test_final_step_120_pct_4_min(self):
        """Final work step: 10% jump to 120%, 4 min, push-to-failure."""
        steps, _ = build_ramp_steps_ride(208.0)
        final = steps[-2]  # before CD
        assert final.power["value"] == 120
        assert final.duration == 240  # 4 min
        assert "push to failure" in final.text.lower()

    def test_cooldown(self):
        steps, _ = build_ramp_steps_ride(208.0)
        cd = steps[-1]
        assert cd.duration == 600  # 10 min
        assert cd.power["value"] == 50

    def test_ftp_param_currently_unused(self):
        """Steps don't depend on FTP value — Intervals.icu does the conversion.
        Param affects warnings only."""
        a, _ = build_ramp_steps_ride(150.0)
        b, _ = build_ramp_steps_ride(400.0)
        assert [s.power for s in a] == [s.power for s in b]

    def test_ftp_missing_emits_warning(self):
        _, warnings = build_ramp_steps_ride(None)
        assert any("FTP not configured" in w for w in warnings)
        assert any("200W" in w for w in warnings)

    def test_ftp_zero_treated_as_missing(self):
        _, warnings = build_ramp_steps_ride(0)
        assert any("FTP not configured" in w for w in warnings)


class TestBuildRampStepsRun:
    def test_returns_10_steps(self):
        """WU + 8 work + CD = 10 total."""
        steps, _ = build_ramp_steps_run(threshold_pace_sec_per_km=295.0)
        assert len(steps) == 10
        assert steps[0].text == "Warm-up"
        assert steps[-1].text == "Cool-down"

    def test_warmup_and_cooldown_use_hr(self):
        steps, _ = build_ramp_steps_run(295.0)
        for s in (steps[0], steps[-1]):
            assert s.hr == {"units": "%lthr", "value": 70}
            assert s.pace is None
        assert steps[0].duration == 600  # WU 10 min
        assert steps[-1].duration == 420  # CD 7 min

    def test_work_steps_use_pct_pace(self):
        steps, _ = build_ramp_steps_run(295.0)
        for s in steps[1:-1]:
            assert s.pace is not None
            assert s.pace["units"] == "%pace"
            assert isinstance(s.pace["value"], int)
            assert s.duration == 180  # 3 min

    def test_work_steps_pct_progressive(self):
        """%pace ascends from 80% to 115% across 8 work steps in 5% increments."""
        steps, _ = build_ramp_steps_run(295.0)
        pcts = [s.pace["value"] for s in steps[1:-1]]
        assert pcts == [80, 85, 90, 95, 100, 105, 110, 115]

    def test_step_labels_include_threshold_pct(self):
        steps, _ = build_ramp_steps_run(295.0)
        labels = [s.text for s in steps[1:-1]]
        assert "80% threshold" in labels[0]
        assert "115% threshold" in labels[-1]

    def test_pace_param_drives_warnings_only(self):
        """Step values are immutable %pace; param affects only treadmill-cap warning."""
        a, _ = build_ramp_steps_run(threshold_pace_sec_per_km=240.0)
        b, _ = build_ramp_steps_run(threshold_pace_sec_per_km=360.0)
        c, _ = build_ramp_steps_run(None)
        assert [s.pace for s in a] == [s.pace for s in b] == [s.pace for s in c]

    def test_threshold_missing_emits_default_warning(self):
        _, warnings = build_ramp_steps_run(None)
        assert any("threshold pace not configured" in w.lower() for w in warnings)
        assert any("295" in w for w in warnings)

    def test_threshold_zero_treated_as_missing(self):
        _, warnings = build_ramp_steps_run(0)
        assert any("threshold pace not configured" in w.lower() for w in warnings)

    def test_fast_athlete_triggers_treadmill_cap_warning(self):
        """Threshold 195 s/km (3:15/km, very fast) → top step 115% × 18.5 km/h ≈ 21.2 km/h.
        Most home treadmills cap at 18-20 km/h."""
        _, warnings = build_ramp_steps_run(195.0)
        assert any("treadmill" in w.lower() for w in warnings)


class TestCreateRampTest:
    def test_creates_ride_workout(self):
        """Bike: 10 WU + 11×3min + 1×4min + 10 CD = 57 min."""
        workout = create_ramp_test("Ride", date(2026, 4, 1), days_since=25, bike_ftp=208.0)
        assert workout.sport == "Ride"
        assert "Ramp Test" in workout.name
        assert workout.suffix is None
        assert len(workout.steps) == 15
        assert workout.duration_minutes == 57
        assert "25 days old" in workout.rationale
        assert "Chest strap" in workout.rationale
        assert "ERG" in workout.rationale  # bike-specific instructions

    def test_creates_run_workout_with_threshold(self):
        """Run: 10 + 8*3 + 7 = 41 min total when threshold provided."""
        workout = create_ramp_test("Run", date(2026, 4, 1), threshold_pace=295.0)
        assert workout.sport == "Run"
        assert len(workout.steps) == 10
        assert workout.duration_minutes == 41
        assert "Treadmill" in workout.rationale
        # Default-fallback warning absent when threshold provided
        assert "threshold pace not configured" not in workout.rationale.lower()

    def test_run_workout_warns_when_threshold_missing(self):
        workout = create_ramp_test("Run", date(2026, 4, 1), threshold_pace=None)
        assert len(workout.steps) == 10
        assert "threshold pace not configured" in workout.rationale.lower()
        assert "295" in workout.rationale  # default value mentioned

    def test_ride_workout_warns_when_ftp_missing(self):
        workout = create_ramp_test("Ride", date(2026, 4, 1), bike_ftp=None)
        assert "FTP not configured" in workout.rationale
        assert "200W" in workout.rationale

    def test_rejects_swim(self):
        import pytest

        with pytest.raises(ValueError, match="not supported"):
            create_ramp_test("Swim", date(2026, 4, 1))

    def test_to_intervals_event_ride(self):
        workout = create_ramp_test("Ride", date(2026, 4, 1), bike_ftp=208.0)
        event = workout.to_intervals_event()
        assert event.category == "WORKOUT"
        assert event.type == "Ride"
        assert "AI: Ramp Test" in event.name
        assert "(generated)" not in event.name  # suffix=None → no label
        assert event.workout_doc is not None
        assert len(event.workout_doc["steps"]) == 15

    def test_to_intervals_event_run(self):
        workout = create_ramp_test("Run", date(2026, 4, 1), threshold_pace=295.0)
        event = workout.to_intervals_event()
        assert event.type == "Run"
        assert len(event.workout_doc["steps"]) == 10
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

    def test_no_drift_no_block(self):
        """build_morning_message no longer renders drift inline (moved to actor)."""
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
        msg = build_morning_message(row)
        assert "ПОРОГИ" not in msg
