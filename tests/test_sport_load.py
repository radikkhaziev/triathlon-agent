"""Per-sport CTL/ATL EMA tests — data/metrics.py."""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from data.metrics import calculate_sport_atl, calculate_sport_ctl


def _act(sport: str, dt: date, load: float):
    a = MagicMock()
    a.type = sport
    a.icu_training_load = load
    a.start_date_local = dt.isoformat()
    return a


class TestCalculateSportCtl:
    def test_empty_returns_zeros(self):
        assert calculate_sport_ctl([]) == {"swim": 0.0, "ride": 0.0, "run": 0.0}

    def test_unknown_sport_ignored(self):
        result = calculate_sport_ctl([_act("Yoga", date(2026, 1, 1), 50.0)])
        assert result == {"swim": 0.0, "ride": 0.0, "run": 0.0}

    def test_none_load_skipped(self):
        result = calculate_sport_ctl([_act("Run", date(2026, 1, 1), None)])
        assert result == {"swim": 0.0, "ride": 0.0, "run": 0.0}

    def test_constant_daily_load_approaches_load(self):
        """EMA with constant input X converges to X. 5τ ≈ 99.3% of steady state."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 50.0) for i in range(210)]
        result = calculate_sport_ctl(acts)
        # 5τ → steady-state error <1%. CTL should be very close to daily load.
        assert 49.0 <= result["run"] <= 50.0
        assert result["ride"] == 0.0
        assert result["swim"] == 0.0

    def test_lowercase_sport_normalized(self):
        """Activity.type stored as canonical Title case; matcher lowers it."""
        acts = [_act("Run", date(2026, 1, 1), 50.0)]
        result = calculate_sport_ctl(acts)
        assert result["run"] > 0


class TestCalculateSportAtl:
    def test_empty_returns_zeros(self):
        assert calculate_sport_atl([]) == {"swim": 0.0, "ride": 0.0, "run": 0.0}

    def test_atl_reacts_faster_than_ctl(self):
        """τ_ATL=7 vs τ_CTL=42: after a short load burst ATL should be ≫ CTL."""
        # 7 consecutive days of TSS=100, evaluated on day 7.
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 100.0) for i in range(7)]

        ctl = calculate_sport_ctl(acts)["run"]
        atl = calculate_sport_atl(acts)["run"]

        # After 7 days at constant 100 TSS: ATL ≈ 1τ → ~63 of steady-state (100).
        # CTL ≈ 7/42 τ → far from steady-state.
        assert atl > ctl * 3, f"expected atl ≫ ctl (atl={atl}, ctl={ctl})"

    def test_constant_load_approaches_value(self):
        """5τ_ATL = 35 days. After 35 days at constant load, ATL ≈ steady-state."""
        start = date(2026, 1, 1)
        acts = [_act("Ride", start + timedelta(days=i), 80.0) for i in range(35)]
        atl = calculate_sport_atl(acts)["ride"]
        assert 78.0 <= atl <= 80.0

    def test_per_sport_independent(self):
        """ATL for one sport doesn't bleed into another's value."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 60.0) for i in range(30)]
        result = calculate_sport_atl(acts)
        assert result["run"] > 0
        assert result["ride"] == 0.0
        assert result["swim"] == 0.0


class TestAsOfDecay:
    """Regression: without `as_of`, EMA stops at last activity date — rest gaps
    leave the value frozen instead of decaying. See code-review M1 (2026-05-24)."""

    def test_rest_gap_after_training_decays_ctl(self):
        """100 days at TSS=50, then 30 days rest → CTL should halve under τ=42."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 50.0) for i in range(100)]
        last_train_day = start + timedelta(days=99)

        ctl_at_last_train = calculate_sport_ctl(acts, as_of=last_train_day)["run"]
        ctl_30d_later = calculate_sport_ctl(acts, as_of=last_train_day + timedelta(days=30))["run"]

        # e^(-30/42) ≈ 0.490 → CTL should drop ~51%
        import math

        expected = ctl_at_last_train * math.exp(-30 / 42)
        assert ctl_30d_later == pytest.approx(expected, abs=0.5)

    def test_as_of_before_first_activity_clamps_to_zero(self):
        """Defensive: as_of before any activity → loop should still run min..min."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start, 50.0)]
        # as_of way before first activity — clamps to min_date (one EMA step).
        result = calculate_sport_ctl(acts, as_of=date(2025, 1, 1))["run"]
        assert result > 0

    def test_as_of_none_preserves_legacy_behavior(self):
        """No as_of → iterate to last activity date (legacy)."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 50.0) for i in range(50)]
        with_explicit = calculate_sport_ctl(acts, as_of=start + timedelta(days=49))["run"]
        legacy = calculate_sport_ctl(acts)["run"]
        assert with_explicit == legacy

    def test_atl_decays_faster_than_ctl_through_gap(self):
        """ATL (τ=7) collapses much faster than CTL (τ=42) over a rest gap."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 80.0) for i in range(60)]
        target = start + timedelta(days=89)  # 30-day rest after day 59

        ctl = calculate_sport_ctl(acts, as_of=target)["run"]
        atl = calculate_sport_atl(acts, as_of=target)["run"]
        assert atl < ctl, f"ATL ({atl}) should decay below CTL ({ctl}) after long rest"
        assert atl < 5.0, f"ATL after 30d rest should be near zero, got {atl}"


class TestSharedCore:
    """Both wrappers share `_calculate_sport_load_ema` — these tests verify the
    contract holds identically for both."""

    @pytest.mark.parametrize("fn", [calculate_sport_ctl, calculate_sport_atl])
    def test_multi_sport_independent_accumulation(self, fn):
        start = date(2026, 1, 1)
        acts = (
            [_act("Run", start + timedelta(days=i), 40.0) for i in range(60)]
            + [_act("Ride", start + timedelta(days=i), 80.0) for i in range(60)]
            + [_act("Swim", start + timedelta(days=i), 20.0) for i in range(60)]
        )
        result = fn(acts)
        # Ride should be highest, Swim lowest — order preserved.
        assert result["ride"] > result["run"] > result["swim"] > 0

    @pytest.mark.parametrize("fn", [calculate_sport_ctl, calculate_sport_atl])
    def test_multiple_activities_same_day_summed(self, fn):
        """Two activities on the same day → loads summed before EMA step."""
        dt = date(2026, 1, 1)
        single = fn([_act("Run", dt, 100.0)])
        double = fn([_act("Run", dt, 50.0), _act("Run", dt, 50.0)])
        assert single["run"] == double["run"]
