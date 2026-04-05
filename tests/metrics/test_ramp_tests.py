"""Tests for ATP Phase 4: Ramp tests and threshold analysis."""

from datetime import date

from data.ramp_tests import RAMP_PROTOCOLS, create_ramp_test


class TestRampProtocols:
    def test_ride_protocol_has_steps(self):
        steps = RAMP_PROTOCOLS["Ride"]
        assert len(steps) == 8  # warmup + 6 steps + cooldown
        assert steps[0].text == "Warm-up"
        assert steps[-1].text == "Cool-down"

    def test_run_protocol_has_steps(self):
        steps = RAMP_PROTOCOLS["Run"]
        assert len(steps) == 7  # warmup + 5 steps + cooldown
        assert steps[0].text == "Warm-up"
        assert steps[-1].text == "Cool-down"

    def test_ride_uses_power(self):
        for step in RAMP_PROTOCOLS["Ride"]:
            assert step.power is not None
            assert step.power["units"] == "%ftp"
            assert step.hr is None

    def test_run_uses_hr(self):
        for step in RAMP_PROTOCOLS["Run"]:
            assert step.hr is not None
            assert step.hr["units"] == "%lthr"
            assert step.power is None

    def test_ride_progressive(self):
        """Steps should increase in intensity."""
        steps = RAMP_PROTOCOLS["Ride"]
        # Skip warmup and cooldown
        work_steps = steps[1:-1]
        values = [s.power["value"] for s in work_steps]
        assert values == sorted(values), "Ride steps should be progressive"

    def test_run_progressive(self):
        steps = RAMP_PROTOCOLS["Run"]
        work_steps = steps[1:-1]
        values = [s.hr["value"] for s in work_steps]
        assert values == sorted(values), "Run steps should be progressive"

    def test_each_step_5_min(self):
        """Work steps should be 5 min (300 sec), warmup/cooldown 10 min."""
        for sport in ("Ride", "Run"):
            steps = RAMP_PROTOCOLS[sport]
            assert steps[0].duration == 600  # warmup 10 min
            assert steps[-1].duration == 600  # cooldown 10 min
            for s in steps[1:-1]:
                assert s.duration == 300  # 5 min steps


class TestCreateRampTest:
    def test_creates_ride_workout(self):
        workout = create_ramp_test("Ride", date(2026, 4, 1), days_since=25)
        assert workout.sport == "Ride"
        assert "Ramp Test" in workout.name
        assert workout.suffix == "generated"
        assert len(workout.steps) == 8
        assert workout.duration_minutes == 50
        assert "25 days old" in workout.rationale
        assert "Chest strap" in workout.rationale

    def test_creates_run_workout(self):
        workout = create_ramp_test("Run", date(2026, 4, 1))
        assert workout.sport == "Run"
        assert len(workout.steps) == 7
        assert workout.duration_minutes == 45  # 10+5*5+10 = 45

    def test_rejects_swim(self):
        import pytest

        with pytest.raises(ValueError, match="not supported"):
            create_ramp_test("Swim", date(2026, 4, 1))

    def test_to_intervals_event(self):
        workout = create_ramp_test("Ride", date(2026, 4, 1))
        event = workout.to_intervals_event()
        assert event.category == "WORKOUT"
        assert event.type == "Ride"
        assert "AI: Ramp Test" in event.name
        assert "(generated)" in event.name
        assert event.workout_doc is not None
        assert len(event.workout_doc["steps"]) == 8


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
