"""Tests for Polarization Index computation."""

from datetime import date, timedelta
from types import SimpleNamespace

from data.metrics import (
    compute_polarization,
    compute_polarization_trends,
    delta_vs_target,
    polarization_index,
    target_distribution,
)
from mcp_server.tools.polarization import _attach_targets, _phase_from_upcoming


def _goal(days_from_today: int, *, is_active: bool = True):
    """Minimal AthleteGoal stand-in for phase-resolution tests."""
    return SimpleNamespace(is_active=is_active, event_date=date(2026, 1, 1) + timedelta(days=days_from_today))


class TestComputePolarization:
    def test_polarized_distribution(self):
        """80/10/10 split → polarized."""
        # 5 zones: Z1=2400, Z2=1200, Z3=400, Z4=200, Z5=200 (total 4400s)
        zt = [[2400, 1200, 400, 200, 200]]
        result = compute_polarization(zt)
        assert result["pattern"] == "polarized"
        assert result["low_pct"] > 75
        assert result["mid_pct"] < 15
        assert result["high_pct"] >= 5

    def test_threshold_pattern(self):
        """Too much Z3 → threshold (gray zone)."""
        zt = [[1000, 500, 1500, 200, 100]]  # Z3 = 1500/3300 = 45%
        result = compute_polarization(zt)
        assert result["pattern"] == "threshold"
        assert result["mid_pct"] > 25

    def test_too_easy(self):
        """All Z1/Z2, no intensity → too_easy."""
        zt = [[3000, 600, 0, 0, 0]]
        result = compute_polarization(zt)
        assert result["pattern"] == "too_easy"

    def test_too_hard(self):
        """Too much Z4/Z5 → too_hard."""
        zt = [[500, 300, 200, 1000, 1000]]  # low=800/3000=27%, high=2000/3000=67%
        result = compute_polarization(zt)
        assert result["pattern"] == "too_hard"

    def test_pyramidal(self):
        """Moderate mid zone → pyramidal."""
        zt = [[2000, 800, 600, 300, 100]]  # low=74%, mid=16%, high=10%
        result = compute_polarization(zt)
        assert result["pattern"] == "pyramidal"

    def test_multiple_activities(self):
        """Aggregates across multiple activities."""
        zt = [
            [1800, 600, 200, 100, 50],  # easy session
            [600, 300, 100, 400, 200],  # hard session
        ]
        result = compute_polarization(zt)
        assert result["n_activities"] == 2
        assert result["total_hours"] > 0
        assert abs(result["low_pct"] + result["mid_pct"] + result["high_pct"] - 100) < 0.5

    def test_empty_input(self):
        result = compute_polarization([])
        assert result["pattern"] == "insufficient_data"
        assert result["total_hours"] == 0

    def test_seven_zones(self):
        """7-zone model (Run Intervals.icu) works correctly."""
        zt = [[1200, 800, 400, 200, 100, 50, 30]]
        result = compute_polarization(zt)
        # Z1+Z2=2000, Z3=400, Z4-Z7=380, total=2780
        assert result["low_pct"] > 70
        assert "Z7" in result["by_zone"]

    def test_by_zone_breakdown(self):
        zt = [[1000, 500, 300, 200, 0]]
        result = compute_polarization(zt)
        assert "Z1" in result["by_zone"]
        assert "Z5" in result["by_zone"]
        total_pct = sum(result["by_zone"].values())
        assert abs(total_pct - 100) < 0.5


class TestPolarizationTrends:
    def test_gray_zone_drift(self):
        """7d mid much higher than 28d → drift signal."""
        windows = {
            7: {"mid_pct": 30, "low_pct": 55, "high_pct": 15, "pattern": "threshold"},
            28: {"mid_pct": 12, "low_pct": 78, "high_pct": 10, "pattern": "polarized"},
        }
        signals = compute_polarization_trends(windows)
        assert any("Gray zone growing" in s for s in signals)

    def test_taper_detected(self):
        """14d high dropping vs 56d → taper."""
        windows = {
            14: {"mid_pct": 10, "low_pct": 85, "high_pct": 5, "pattern": "polarized"},
            56: {"mid_pct": 10, "low_pct": 75, "high_pct": 15, "pattern": "polarized"},
        }
        signals = compute_polarization_trends(windows)
        assert any("Taper mode" in s for s in signals)

    def test_deload_week(self):
        """7d much more easy than 28d → deload."""
        windows = {
            7: {"mid_pct": 5, "low_pct": 92, "high_pct": 3, "pattern": "too_easy"},
            28: {"mid_pct": 10, "low_pct": 78, "high_pct": 12, "pattern": "polarized"},
        }
        signals = compute_polarization_trends(windows)
        assert any("Deload week" in s for s in signals)

    def test_threshold_warning(self):
        """14d mid > 20% → warning."""
        windows = {
            14: {"mid_pct": 25, "low_pct": 60, "high_pct": 15, "pattern": "threshold"},
        }
        signals = compute_polarization_trends(windows)
        assert any("Too much Z3" in s for s in signals)

    def test_too_hard(self):
        """14d pattern too_hard → signal."""
        windows = {
            14: {"mid_pct": 10, "low_pct": 50, "high_pct": 40, "pattern": "too_hard"},
        }
        signals = compute_polarization_trends(windows)
        assert any("Overtraining risk" in s for s in signals)

    def test_no_signals_when_optimal(self):
        """All windows polarized, no drift → no signals."""
        base = {"mid_pct": 10, "low_pct": 80, "high_pct": 10, "pattern": "polarized"}
        windows = {7: base, 14: base, 28: base, 56: base}
        signals = compute_polarization_trends(windows)
        assert signals == []

    def test_partial_windows(self):
        """Only some windows present → no crash."""
        windows = {28: {"mid_pct": 10, "low_pct": 80, "high_pct": 10, "pattern": "polarized"}}
        signals = compute_polarization_trends(windows)
        assert isinstance(signals, list)


class TestPolarizationIndex:
    def test_true_polarized_above_threshold(self):
        """Z3 > Z2 (80/6/14) → PI > 2.0."""
        pi = polarization_index(80.0, 6.0, 14.0)
        assert pi == 2.27
        assert pi > 2.0

    def test_classic_8012_8_is_pyramidal_by_index(self):
        """Esteve-Lanao 80/12/8 has Z2 > Z3 → PI ≈ 1.73, below the 2.0 line."""
        pi = polarization_index(80.0, 12.0, 8.0)
        assert pi == 1.73
        assert pi < 2.0

    def test_zero_low_is_degenerate(self):
        """All-hard activity (zero Z1+Z2) → log10(0) guard, returns None not a crash."""
        assert polarization_index(0.0, 40.0, 60.0) is None
        # reachable via the public path: an activity with no easy time
        result = compute_polarization([[0, 0, 600, 300, 100]])
        assert result["polarization_index"] is None

    def test_zero_mid_is_degenerate(self):
        """No moderate time → undefined index, returns None (use %-pattern)."""
        assert polarization_index(95.0, 0.0, 5.0) is None

    def test_zero_high_is_degenerate(self):
        """No hard time → undefined index, returns None."""
        assert polarization_index(95.0, 5.0, 0.0) is None

    def test_compute_polarization_exposes_index(self):
        """compute_polarization output carries polarization_index."""
        result = compute_polarization([[2400, 1200, 400, 200, 200]])
        assert "polarization_index" in result
        assert isinstance(result["polarization_index"], float)

    def test_insufficient_data_index_none(self):
        result = compute_polarization([])
        assert result["polarization_index"] is None


class TestTargetDistribution:
    def test_phase_none_returns_both_bands(self):
        """No phase → phase-dependent dual band (base + race)."""
        t = target_distribution("run")
        assert t["model"] == "phase-dependent"
        assert t["base"]["model"] == "pyramidal"
        assert t["race"]["model"] == "polarized"
        assert t["race"]["pi_target_min"] == 2.0

    def test_phase_base_is_pyramidal(self):
        t = target_distribution("run", phase="base")
        assert t["model"] == "pyramidal"
        assert t["pi_target_min"] is None

    def test_phase_race_is_polarized(self):
        t = target_distribution("run", phase="peak")
        assert t["model"] == "polarized"
        assert t["pi_target_min"] == 2.0

    def test_bike_tolerates_more_z2_than_run(self):
        """Cycling band allows a higher gray-zone ceiling than running."""
        run = target_distribution("run", phase="base")
        ride = target_distribution("ride", phase="base")
        assert ride["mid_pct_max"] > run["mid_pct_max"]
        assert ride["low_pct_target"] < run["low_pct_target"]

    def test_ride_polarized_has_no_pi_gate(self):
        """PI>2 gate is run/swim-only; ride carries more Z2 and can't reach it."""
        assert target_distribution("ride", phase="peak")["pi_target_min"] is None
        assert target_distribution("run", phase="peak")["pi_target_min"] == 2.0
        assert target_distribution("swim", phase="peak")["pi_target_min"] == 2.0

    def test_unknown_sport_falls_back_to_run(self):
        assert target_distribution("kayak", phase="base") == target_distribution("run", phase="base")


class TestDeltaVsTarget:
    def test_on_target(self):
        band = target_distribution("run", phase="base")
        d = delta_vs_target(85.0, 10.0, 5.0, band)
        assert d["verdict"] == "on_target"
        assert d["issues"] == []

    def test_too_much_z2(self):
        band = target_distribution("run", phase="base")  # mid_max 14
        d = delta_vs_target(70.0, 22.0, 8.0, band)
        assert "too_much_z2" in d["issues"]
        assert d["mid_over"] == 8.0

    def test_too_little_easy(self):
        band = target_distribution("run", phase="base")  # low_target 84
        d = delta_vs_target(74.0, 16.0, 10.0, band)
        assert "too_little_easy" in d["issues"]

    def test_sport_calibration_changes_verdict(self):
        """Same 65/30/5 split: flagged too_much_z2 on run, accepted on bike."""
        split = (65.0, 30.0, 5.0)
        run = delta_vs_target(*split, target_distribution("run", phase="base"))
        ride = delta_vs_target(*split, target_distribution("ride", phase="base"))
        assert "too_much_z2" in run["issues"]
        assert "too_much_z2" not in ride["issues"]

    def test_too_much_hard(self):
        band = target_distribution("run", phase="base")  # high_max 10
        d = delta_vs_target(70.0, 12.0, 18.0, band)
        assert "too_much_hard" in d["issues"]
        assert d["high_over"] == 8.0

    def test_verdict_severity_ordering(self):
        """When both too_much_hard and too_much_z2 fire, headline = the more dangerous hard."""
        band = target_distribution("run", phase="base")  # mid_max 14, high_max 10
        d = delta_vs_target(60.0, 22.0, 18.0, band)
        assert {"too_much_hard", "too_much_z2", "too_little_easy"} <= set(d["issues"])
        assert d["verdict"] == "too_much_hard"
        assert d["issues"][0] == "too_much_hard"

    def test_dual_band_rejected(self):
        """Passing a phase=None dual-band dict is a programming error → clear ValueError."""
        import pytest

        dual = target_distribution("run")  # {base, race} — no flat keys
        with pytest.raises(ValueError, match="concrete-phase band"):
            delta_vs_target(80.0, 10.0, 10.0, dual)


class TestPhaseResolution:
    TODAY = date(2026, 1, 1)

    def test_no_goals_is_base(self):
        assert _phase_from_upcoming([], self.TODAY) == "base"

    def test_far_race_is_base(self):
        assert _phase_from_upcoming([_goal(60)], self.TODAY) == "base"

    def test_near_race_is_peak(self):
        assert _phase_from_upcoming([_goal(7)], self.TODAY) == "peak"

    def test_boundary_14d_is_peak(self):
        assert _phase_from_upcoming([_goal(14)], self.TODAY) == "peak"
        assert _phase_from_upcoming([_goal(15)], self.TODAY) == "base"

    def test_nearest_race_wins(self):
        """RACE_A in 200d + RACE_B in 7d → peak for the close B-race (not build for far A)."""
        assert _phase_from_upcoming([_goal(200), _goal(7)], self.TODAY) == "peak"

    def test_past_and_inactive_goals_ignored(self):
        goals = [_goal(-3), _goal(7, is_active=False), _goal(60)]
        assert _phase_from_upcoming(goals, self.TODAY) == "base"  # only the 60d active one counts


class TestAttachTargets:
    def test_populated_window_gets_target_and_delta(self):
        windows = {28: compute_polarization([[2400, 1200, 400, 200, 200]])}
        _attach_targets(windows, "run", "base")
        assert windows[28]["target"]["phase"] == "base"
        assert "verdict" in windows[28]["delta"]

    def test_insufficient_window_gets_target_but_no_delta(self):
        """Contract: empty windows still carry target (for UI), but never a delta."""
        windows = {7: compute_polarization([])}
        _attach_targets(windows, "run", "base")
        assert windows[7]["target"]["phase"] == "base"
        assert "delta" not in windows[7]
