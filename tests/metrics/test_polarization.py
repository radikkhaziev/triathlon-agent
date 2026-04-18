"""Tests for Polarization Index computation."""

from data.metrics import compute_polarization, compute_polarization_trends


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
