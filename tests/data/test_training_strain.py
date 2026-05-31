"""Tests for `data/training_strain.py` — pure Foster monotony/strain + ACWR."""

from datetime import date, timedelta

from data.training_strain import (
    ACUTE_WINDOW_DAYS,
    MONOTONY_CAP,
    STRAIN_HISTORY_MIN_DAYS,
    acwr,
    acwr_status,
    classify_strain,
    compute_training_strain,
    monotony,
    percentile,
    strain,
    strain_bands,
    strain_series,
    weekly_load,
)

REF_DATE = date(2026, 5, 30)

# Radik's real daily TSS, 24–30 May 2026 (the «hard build» week from the
# diagnosis). Rest day 25 May = 0. Source: activities.icu_training_load.
RADIK_WEEK = [117.0, 0.0, 60.0, 59.0, 112.0, 57.0, 115.0]


# ─── monotony ──────────────────────────────────────────────────────────────


class TestMonotony:
    def test_radik_week(self):
        # mean 74.29 / pstdev 39.9 ≈ 1.86 — verified by hand in the diagnosis.
        assert round(monotony(RADIK_WEEK), 2) == 1.86

    def test_no_training_is_zero(self):
        # All rest → mean 0 → not "monotonous", returns 0 (not a div-by-zero).
        assert monotony([0.0] * 7) == 0.0

    def test_perfectly_flat_load_caps(self):
        # Identical non-zero load every day → stdev 0 → maximal monotony, capped.
        assert monotony([80.0] * 7) == MONOTONY_CAP

    def test_high_variation_lowers_monotony(self):
        # Big valleys (rest days) pull monotony down vs a flat week.
        varied = monotony([200.0, 0.0, 0.0, 200.0, 0.0, 0.0, 200.0])
        flat = monotony([85.7] * 7)
        assert varied < flat

    def test_empty(self):
        assert monotony([]) == 0.0


# ─── weekly load + strain ────────────────────────────────────────────────────


class TestStrain:
    def test_radik_week_weekly_load(self):
        assert weekly_load(RADIK_WEEK) == 520.0

    def test_radik_week_strain(self):
        # 520 weekly load × 1.86 monotony ≈ 967.
        assert round(strain(RADIK_WEEK)) == 967

    def test_strain_zero_when_no_load(self):
        assert strain([0.0] * 7) == 0.0


# ─── ACWR ────────────────────────────────────────────────────────────────────


class TestAcwr:
    def test_radik_today(self):
        # ATL 75.2 / CTL 46.9 ≈ 1.60 — aggressive build, above the 1.5 danger.
        assert round(acwr(75.2, 46.9), 2) == 1.60

    def test_undefined_without_chronic(self):
        assert acwr(50.0, 0.0) is None
        assert acwr(50.0, None) is None
        assert acwr(None, 40.0) is None


class TestAcwrStatus:
    def test_bands(self):
        assert acwr_status(1.60) == "danger"  # Radik today
        assert acwr_status(1.5) == "danger"
        assert acwr_status(1.4) == "caution"
        assert acwr_status(1.0) == "sweet"
        assert acwr_status(0.8) == "sweet"
        assert acwr_status(0.5) == "low"

    def test_boundaries(self):
        # Exact sweet/caution boundary: 1.3 is the inclusive top of sweet,
        # anything strictly above flips to caution; 1.5 is the danger floor.
        assert acwr_status(1.3) == "sweet"
        assert acwr_status(1.31) == "caution"
        assert acwr_status(1.49) == "caution"
        assert acwr_status(0.79) == "low"

    def test_none(self):
        assert acwr_status(None) is None


# ─── percentile + bands + classification ─────────────────────────────────────


class TestPercentile:
    def test_basic(self):
        assert percentile([10, 20, 30, 40, 50], 50) == 30
        assert percentile([10, 20, 30, 40, 50], 0) == 10
        assert percentile([10, 20, 30, 40, 50], 100) == 50

    def test_interpolation_between_elements(self):
        # k = (4-1)*0.5 = 1.5 → lerp between s[1]=20 and s[2]=30 → 25.
        assert percentile([10, 20, 30, 40], 50) == 25
        # 85th of the same: k = 3*0.85 = 2.55 → 30 + (40-30)*0.55 = 35.5.
        assert percentile([10, 20, 30, 40], 85) == 35.5

    def test_empty(self):
        assert percentile([], 90) == 0.0


class TestStrainBands:
    def test_fallback_when_history_too_short(self):
        bands = strain_bands([500.0] * (STRAIN_HISTORY_MIN_DAYS - 1))
        assert bands.source == "monotony_fallback"

    def test_percentile_when_enough_history(self):
        hist = [float(i) for i in range(1, STRAIN_HISTORY_MIN_DAYS + 50)]
        bands = strain_bands(hist)
        assert bands.source == "percentile"
        assert bands.calm_max < bands.hard_min

    def test_zeros_excluded_from_history(self):
        # A long run of rest days shouldn't count toward the percentile sample.
        hist = [0.0] * 200 + [500.0] * (STRAIN_HISTORY_MIN_DAYS - 1)
        assert strain_bands(hist).source == "monotony_fallback"

    def test_degenerate_bands_collapse(self):
        # Enough history but all identical → p60 == p85: the building band
        # collapses to zero width. Classification must stay consistent — a
        # value at/above the (shared) threshold is overload, below is calm,
        # and building is unreachable. Mirrors the FE strainZoneAt branch.
        bands = strain_bands([700.0] * (STRAIN_HISTORY_MIN_DAYS + 5))
        assert bands.source == "percentile"
        assert bands.calm_max == bands.hard_min == 700.0
        assert classify_strain(700.0, 1.0, bands) == "overload"
        assert classify_strain(699.9, 1.0, bands) == "calm"


class TestClassifyStrain:
    def _percentile_bands(self):
        # Real percentile source: calm_max < hard_min over a spread sample.
        hist = [float(x) for x in range(100, 1000, 5)]  # >= MIN history, spread
        bands = strain_bands(hist)
        assert bands.source == "percentile"
        return bands

    def test_percentile_overload(self):
        bands = self._percentile_bands()
        assert classify_strain(bands.hard_min + 1, 1.0, bands) == "overload"

    def test_percentile_building(self):
        bands = self._percentile_bands()
        mid = (bands.calm_max + bands.hard_min) / 2
        assert classify_strain(mid, 1.0, bands) == "building"

    def test_percentile_calm(self):
        bands = self._percentile_bands()
        assert classify_strain(max(0.0, bands.calm_max - 1), 1.0, bands) == "calm"

    def test_monotony_fallback_bands(self):
        bands = strain_bands([100.0] * 3)  # too short → fallback
        assert bands.source == "monotony_fallback"
        assert classify_strain(9999, 2.1, bands) == "overload"  # monotony ≥ 2.0
        assert classify_strain(9999, 1.7, bands) == "building"  # 1.5 ≤ m < 2.0
        assert classify_strain(9999, 1.0, bands) == "calm"  # m < 1.5


# ─── strain_series + compute ─────────────────────────────────────────────────


def _radik_map(end: date) -> dict[date, float]:
    """RADIK_WEEK indexed onto calendar days ending at `end`."""
    return {end - timedelta(days=ACUTE_WINDOW_DAYS - 1 - i): RADIK_WEEK[i] for i in range(ACUTE_WINDOW_DAYS)}


class TestStrainSeries:
    def test_last_point_matches_week_strain(self):
        m = _radik_map(REF_DATE)
        series = strain_series(m, start=REF_DATE, end=REF_DATE)
        assert len(series) == 1
        _, s, mono, wl = series[0]
        assert round(s) == 967
        assert round(mono, 2) == 1.86
        assert wl == 520.0

    def test_fills_rest_days_as_zero(self):
        # Empty map → every window all-zero → strain 0 throughout.
        series = strain_series({}, start=REF_DATE - timedelta(days=5), end=REF_DATE)
        assert all(s == 0.0 for (_, s, _, _) in series)
        assert len(series) == 6


class TestComputeTrainingStrain:
    def test_radik_hard_build(self):
        m = _radik_map(REF_DATE)
        res = compute_training_strain(
            ref_date=REF_DATE,
            daily_tss_by_date=m,
            atl=75.2,
            ctl=46.9,
            trend_start=REF_DATE - timedelta(days=6),
            history_start=REF_DATE - timedelta(days=6),
        )
        assert res.strain == 967.5  # 967.47 stored at 1-decimal precision
        assert res.monotony == 1.86
        assert res.weekly_load == 520.0
        assert res.acwr == 1.60
        assert not res.insufficient_data
        # Only 7 days of history → percentile bands fall back to monotony.
        assert res.bands.source == "monotony_fallback"
        # monotony 1.86 is in [1.5, 2.0) → building («жёсткий билд»).
        assert res.zone_id == "building"
        assert len(res.trend) == 7

    def test_percentile_zone_end_to_end(self):
        # ≥56 non-zero strain days → percentile bands kick in. Steady 50 TSS
        # baseline (strain 875/day) for ~11 weeks, then a 100 TSS spike week →
        # today's strain is the unique series max, so it lands ≥ p85 = overload.
        # Exercises the strain_series → strain_bands → classify_strain wiring
        # that the fallback-path tests never reach.
        m: dict = {}
        for i in range(7, 86):  # ref-85 .. ref-7 : steady baseline
            m[REF_DATE - timedelta(days=i)] = 50.0
        for i in range(0, 7):  # ref-6 .. ref : spike week
            m[REF_DATE - timedelta(days=i)] = 100.0
        res = compute_training_strain(
            ref_date=REF_DATE,
            daily_tss_by_date=m,
            atl=80.0,
            ctl=50.0,
            trend_start=REF_DATE - timedelta(days=13),
            history_start=REF_DATE - timedelta(days=79),
        )
        assert res.bands.source == "percentile"
        assert res.bands.calm_max <= res.bands.hard_min
        assert res.zone_id == "overload"
        assert res.strain >= res.bands.hard_min

    def test_weekly_load_prev_uses_separate_window(self):
        # Current 7d window (REF-6..REF) at 100/day = 700; previous window
        # (REF-13..REF-7) at 50/day = 350. No overlap between the two.
        m: dict = {}
        for i in range(0, 7):
            m[REF_DATE - timedelta(days=i)] = 100.0
        for i in range(7, 14):
            m[REF_DATE - timedelta(days=i)] = 50.0
        res = compute_training_strain(
            ref_date=REF_DATE,
            daily_tss_by_date=m,
            atl=None,
            ctl=None,
            trend_start=REF_DATE,
            history_start=REF_DATE - timedelta(days=13),
        )
        assert res.weekly_load == 700.0
        assert res.weekly_load_prev == 350.0

    def test_insufficient_when_no_recent_load(self):
        res = compute_training_strain(
            ref_date=REF_DATE,
            daily_tss_by_date={},
            atl=None,
            ctl=None,
            trend_start=REF_DATE - timedelta(days=6),
            history_start=REF_DATE - timedelta(days=6),
        )
        assert res.insufficient_data
        assert res.insufficient_reason == "no_recent_load"
        assert res.acwr is None
