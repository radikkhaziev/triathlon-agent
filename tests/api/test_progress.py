"""Tests for progress tracking: EF trend, SWOLF calculation, Z2 filtering."""

from datetime import date
from types import SimpleNamespace

from mcp_server.tools.progress import _calc_swolf, _is_z2, _trend_pct, _week_key

# Mock thresholds matching the test expectations: LTHR=153 for both bike and run
_MOCK_THRESHOLDS = SimpleNamespace(lthr_run=153, lthr_bike=153)


def _patched_is_z2(avg_hr, sport):
    """Call _is_z2 with mock thresholds."""
    return _is_z2(avg_hr, sport, _MOCK_THRESHOLDS)


class TestCalcSwolf:
    """Tests for SWOLF calculation."""

    def test_basic(self):
        """Standard 25m pool, typical swim data."""
        # pace=0.74 m/s, stride=0.99 m/stroke, pool=25m
        swolf = _calc_swolf(0.74, 0.99, 25.0)
        # time = 25/0.74 = 33.78, strokes = 25/0.99 = 25.25 → ~59.0
        assert swolf is not None
        assert 58.0 <= swolf <= 60.0

    def test_50m_pool(self):
        """50m pool should give higher SWOLF (more time + more strokes per length)."""
        swolf_25 = _calc_swolf(0.74, 0.99, 25.0)
        swolf_50 = _calc_swolf(0.74, 0.99, 50.0)
        assert swolf_50 > swolf_25

    def test_faster_pace_lower_swolf(self):
        """Faster pace → lower SWOLF."""
        slow = _calc_swolf(0.60, 0.99, 25.0)
        fast = _calc_swolf(0.80, 0.99, 25.0)
        assert fast < slow

    def test_longer_stride_lower_swolf(self):
        """Longer stride → fewer strokes → lower SWOLF."""
        short = _calc_swolf(0.74, 0.80, 25.0)
        long = _calc_swolf(0.74, 1.10, 25.0)
        assert long < short

    def test_zero_pace_returns_none(self):
        assert _calc_swolf(0, 0.99, 25.0) is None

    def test_zero_stride_returns_none(self):
        assert _calc_swolf(0.74, 0, 25.0) is None

    def test_none_inputs(self):
        assert _calc_swolf(None, 0.99, 25.0) is None
        assert _calc_swolf(0.74, None, 25.0) is None
        assert _calc_swolf(0.74, 0.99, None) is None

    def test_negative_values(self):
        assert _calc_swolf(-0.5, 0.99, 25.0) is None
        assert _calc_swolf(0.74, -0.5, 25.0) is None


class TestIsZ2:
    """Tests for Z2 HR filtering."""

    def test_bike_z2_in_range(self):
        """Bike Z2: 68-83% of LTHR=153 → 104-127 bpm."""
        assert _patched_is_z2(115, "bike") is True

    def test_bike_below_z2(self):
        assert _patched_is_z2(95, "bike") is False

    def test_bike_above_z2(self):
        assert _patched_is_z2(140, "bike") is False

    def test_run_z2_in_range(self):
        """Run Z2: 72-82% of LTHR=153 → 110-125 bpm."""
        assert _patched_is_z2(118, "run") is True

    def test_run_below_z2(self):
        assert _patched_is_z2(100, "run") is False

    def test_run_above_z2(self):
        assert _patched_is_z2(135, "run") is False

    def test_swim_always_passes(self):
        """Swim has no HR filter."""
        assert _patched_is_z2(150, "swim") is True
        assert _patched_is_z2(80, "swim") is True

    def test_none_hr(self):
        assert _patched_is_z2(None, "bike") is False
        assert _patched_is_z2(None, "run") is False


class TestTrendPct:
    """Tests for trend percentage calculation."""

    def test_rising(self):
        t = _trend_pct([1.0, 1.1, 1.2])
        assert t["direction"] == "rising"
        assert t["pct"] == 20.0

    def test_falling(self):
        t = _trend_pct([100.0, 90.0, 80.0])
        assert t["direction"] == "falling"
        assert t["pct"] == -20.0

    def test_stable(self):
        t = _trend_pct([1.0, 1.005, 1.0])
        assert t["direction"] == "stable"

    def test_insufficient_data(self):
        t = _trend_pct([1.0])
        assert t["direction"] == "insufficient_data"

    def test_empty(self):
        t = _trend_pct([])
        assert t["direction"] == "insufficient_data"


class TestWeekKey:
    def test_format(self):
        assert _week_key(date(2026, 3, 29)) == "2026-W13"

    def test_single_digit_week(self):
        assert _week_key(date(2026, 1, 5)) == "2026-W02"
