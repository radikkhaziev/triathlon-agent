"""Tests for data/ml/noise_classifier.py.

Three test classes:

* :class:`TestIsZ1Dominated` — zone-composition primitive (relocated from
  test_race_features.py, content unchanged — module path updated).
* :class:`TestIsRunRecoveryJog` — combined Z1 + TSS check (relocated).
* :class:`TestIsRunWalk` — new in Phase 1.6 (mistagged-walk detection
  via personalized LTHR + threshold_pace baseline × global multipliers).
* :class:`TestClassifyNoise` — priority order (run_walk > run_recovery_jog),
  non-Run pass-through, missing-field tolerance.
* :class:`TestClassifyActivityRow` — convenience wrapper exercising ORM-row
  attribute access + pace derivation from moving_time/distance.

Calibration history (2026-05-12): zone-only filter regressed pro athlete
R² 0.44 → 0.04. TSS gate restored signal while still dropping fluff jogs.
The Z1 + TSS tests pin both gates so anyone tuning the rule must update
the test deliberately.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from data.ml import noise_classifier as nc

# ---------------------------------------------------------------------------
# _is_z1_dominated — zone-composition primitive
# ---------------------------------------------------------------------------


class TestIsZ1Dominated:
    def test_z1_dominated_returns_true(self):
        zones = [4800, 900, 300, 0, 0]  # 80% Z1
        assert nc._is_z1_dominated(zones) is True

    def test_z2_base_run_returns_false(self):
        zones = [600, 4500, 720, 180, 0]  # 10% Z1 + 75% Z2 → base, not Z1
        assert nc._is_z1_dominated(zones) is False

    def test_threshold_workout_returns_false(self):
        zones = [600, 1200, 800, 1800, 1200]  # mixed Z4-Z5 main set
        assert nc._is_z1_dominated(zones) is False

    def test_missing_zones_returns_false(self):
        assert nc._is_z1_dominated(None) is False
        assert nc._is_z1_dominated([]) is False
        assert nc._is_z1_dominated([0, 0, 0, 0, 0]) is False

    def test_threshold_boundary(self):
        # Exactly 70% Z1 → drop (inclusive — `>=`).
        assert nc._is_z1_dominated([7000, 3000, 0, 0, 0]) is True
        # 69% Z1 → keep.
        assert nc._is_z1_dominated([6900, 3100, 0, 0, 0]) is False

    def test_threshold_constant(self):
        assert nc.Z1_RECOVERY_THRESHOLD == 0.70

    def test_tuple_input_works(self):
        # SQLAlchemy/pandas can return tuple instead of list.
        assert nc._is_z1_dominated((4800, 900, 300, 0, 0)) is True

    def test_ndarray_input_works(self):
        import numpy as np

        assert nc._is_z1_dominated(np.array([4800, 900, 300, 0, 0])) is True

    def test_string_input_rejected(self):
        assert nc._is_z1_dominated("invalid") is False
        assert nc._is_z1_dominated(b"invalid") is False

    def test_nan_values_neutralized(self):
        # NaN poison guard — `bool(float('nan')) is True`.
        zones = [4800, 900, float("nan"), None, 0]
        assert nc._is_z1_dominated(zones) is True

    def test_all_nan_returns_false(self):
        assert nc._is_z1_dominated([float("nan")] * 5) is False

    def test_negative_seconds_clamped(self):
        zones = [4800, -100, 200, 0, 0]
        assert nc._is_z1_dominated(zones) is True


# ---------------------------------------------------------------------------
# is_run_recovery_jog — combined Z1 + TSS check
# ---------------------------------------------------------------------------


class TestIsRunRecoveryJog:
    """Empirical calibration 2026-05-12: TSS gate restored pro-athlete R²
    that zone-only filter destroyed (0.44 → 0.04 → 0.44). Combined check
    is the contract — both gates required.
    """

    Z1_DOMINATED_ZONES = [4800, 900, 300, 0, 0]  # 80% Z1
    BALANCED_ZONES = [600, 4500, 720, 180, 0]  # 10% Z1 + 75% Z2

    def test_short_z1_jog_is_filtered(self):
        assert nc.is_run_recovery_jog(self.Z1_DOMINATED_ZONES, tss=25.0) is True

    def test_long_z1_base_session_is_kept(self):
        # TSS 70 — structured Z1-base 80/20 → keep.
        assert nc.is_run_recovery_jog(self.Z1_DOMINATED_ZONES, tss=70.0) is False

    def test_z2_base_run_is_kept(self):
        assert nc.is_run_recovery_jog(self.BALANCED_ZONES, tss=25.0) is False
        assert nc.is_run_recovery_jog(self.BALANCED_ZONES, tss=70.0) is False

    def test_missing_tss_keeps_activity(self):
        assert nc.is_run_recovery_jog(self.Z1_DOMINATED_ZONES, tss=None) is False

    def test_missing_zones_keeps_activity(self):
        assert nc.is_run_recovery_jog(None, tss=25.0) is False
        assert nc.is_run_recovery_jog([], tss=25.0) is False

    def test_tss_boundary(self):
        ceiling = nc.RECOVERY_TSS_CEILING
        # At ceiling → keep (strictly `<`).
        assert nc.is_run_recovery_jog(self.Z1_DOMINATED_ZONES, tss=ceiling) is False
        assert nc.is_run_recovery_jog(self.Z1_DOMINATED_ZONES, tss=ceiling - 0.01) is True

    def test_nan_tss_keeps_activity(self):
        assert nc.is_run_recovery_jog(self.Z1_DOMINATED_ZONES, tss=float("nan")) is False

    def test_non_numeric_tss_keeps_activity(self):
        assert nc.is_run_recovery_jog(self.Z1_DOMINATED_ZONES, tss="bad") is False

    def test_constants(self):
        assert nc.Z1_RECOVERY_THRESHOLD == 0.70
        assert nc.RECOVERY_TSS_CEILING == 40.0


# ---------------------------------------------------------------------------
# is_run_walk — personalized walk-as-Run detection (Phase 1.6)
# ---------------------------------------------------------------------------


class TestIsRunWalk:
    """Walk-paced low-HR Run = mistagged sport (athlete's pet walk pushed to
    Intervals as Run). Personalized via LTHR + threshold_pace × global
    multipliers (WALK_PACE_MULT=1.6, WALK_HR_MULT=0.65).

    Three athlete cohorts probe that personalization actually fires —
    fixed thresholds would mis-classify the same activity across athletes.
    """

    # ---- Sub-3 marathoner: threshold_pace 3:30/km = 210, LTHR 178 -----
    # pace_floor = 210 × 1.6 = 336 (5:36/km), hr_ceil = 178 × 0.65 = 115.7

    def test_sub3_walk_with_dog_is_walk(self):
        # 7:30/km @ HR 100 — slower than 5:36 AND below 115.7 → walk.
        assert nc.is_run_walk(avg_pace_sec_per_km=450, avg_hr=100, lthr=178, threshold_pace_sec_per_km=210) is True

    def test_sub3_recovery_jog_at_120bpm_is_not_walk(self):
        # 6:00/km @ HR 130 — pace > pace_floor but HR > hr_ceil → not walk.
        assert nc.is_run_walk(avg_pace_sec_per_km=360, avg_hr=130, lthr=178, threshold_pace_sec_per_km=210) is False

    def test_sub3_threshold_workout_is_not_walk(self):
        # 3:30/km @ HR 170 — fast and high HR. Not walk on either axis.
        assert nc.is_run_walk(avg_pace_sec_per_km=210, avg_hr=170, lthr=178, threshold_pace_sec_per_km=210) is False

    # ---- Mid-pack athlete: threshold_pace 4:30 = 270, LTHR 170 --------
    # pace_floor = 270 × 1.6 = 432 (7:12/km), hr_ceil = 170 × 0.65 = 110.5

    def test_midpack_slow_walk_is_walk(self):
        assert nc.is_run_walk(avg_pace_sec_per_km=480, avg_hr=95, lthr=170, threshold_pace_sec_per_km=270) is True

    def test_midpack_recovery_jog_just_above_floor_is_not_walk(self):
        # 7:15/km @ HR 115 — pace barely above floor BUT HR above ceil.
        assert nc.is_run_walk(avg_pace_sec_per_km=435, avg_hr=115, lthr=170, threshold_pace_sec_per_km=270) is False

    # ---- 60yo athlete: threshold_pace 5:00 = 300, LTHR 158 -----------
    # pace_floor = 300 × 1.6 = 480 (8:00/km), hr_ceil = 158 × 0.65 = 102.7

    def test_60yo_jog_at_120bpm_is_not_walk(self):
        # 7:30/km @ HR 120 — pace below floor (480) AND HR above ceil (102.7).
        assert nc.is_run_walk(avg_pace_sec_per_km=450, avg_hr=120, lthr=158, threshold_pace_sec_per_km=300) is False

    def test_60yo_slow_walk_below_personalized_thresholds_is_walk(self):
        # 8:30/km @ HR 95 — both axes crossed.
        assert nc.is_run_walk(avg_pace_sec_per_km=510, avg_hr=95, lthr=158, threshold_pace_sec_per_km=300) is True

    # ---- Missing thresholds → fallback constants ---------------------

    def test_missing_thresholds_uses_fallback(self):
        # New athlete, no synced settings yet. Fallback: 6:30/km AND 120bpm.
        # 7:00/km @ HR 105 → both fallback conditions met.
        assert nc.is_run_walk(avg_pace_sec_per_km=420, avg_hr=105, lthr=None, threshold_pace_sec_per_km=None) is True

    def test_missing_only_lthr_uses_fallback(self):
        # Asymmetric None → fallback engages on BOTH axes (defensive).
        assert nc.is_run_walk(avg_pace_sec_per_km=420, avg_hr=105, lthr=None, threshold_pace_sec_per_km=270) is True
        # 5:00/km @ HR 105 — pace below fallback (6:30), so not walk.
        assert nc.is_run_walk(avg_pace_sec_per_km=300, avg_hr=105, lthr=None, threshold_pace_sec_per_km=270) is False

    # ---- Defensive: missing fields ----------------------------------

    def test_missing_pace_or_hr_returns_false(self):
        assert nc.is_run_walk(None, 100, 170, 270) is False
        assert nc.is_run_walk(450, None, 170, 270) is False

    def test_nan_or_zero_inputs_return_false(self):
        assert nc.is_run_walk(float("nan"), 100, 170, 270) is False
        assert nc.is_run_walk(450, float("nan"), 170, 270) is False
        assert nc.is_run_walk(0, 100, 170, 270) is False
        assert nc.is_run_walk(450, 0, 170, 270) is False


# ---------------------------------------------------------------------------
# classify_noise — top-level priority dispatch
# ---------------------------------------------------------------------------


def _thresholds(lthr=170, threshold_pace_run=270):
    return SimpleNamespace(lthr_run=lthr, threshold_pace_run=threshold_pace_run)


class TestClassifyNoise:
    def test_walk_wins_over_recovery_jog(self):
        # Slow + low HR + Z1≥70% + TSS<40 → both rules trigger. walk wins.
        reason = nc.classify_noise(
            sport="Run",
            avg_hr=100,
            avg_pace_sec_per_km=480,
            hr_zone_times=[4800, 900, 300, 0, 0],
            tss=25.0,
            lthr=170,
            threshold_pace_sec_per_km=270,
        )
        assert reason == "run_walk"

    def test_recovery_jog_alone(self):
        # Pace above walk floor, normal HR for recovery, but Z1≥70 AND TSS<40.
        reason = nc.classify_noise(
            sport="Run",
            avg_hr=130,
            avg_pace_sec_per_km=360,
            hr_zone_times=[4800, 900, 300, 0, 0],
            tss=25.0,
            lthr=170,
            threshold_pace_sec_per_km=270,
        )
        assert reason == "run_recovery_jog"

    def test_clean_run_returns_none(self):
        # 4:30/km Z2 base session — neither rule fires.
        reason = nc.classify_noise(
            sport="Run",
            avg_hr=145,
            avg_pace_sec_per_km=270,
            hr_zone_times=[600, 4500, 720, 180, 0],
            tss=60.0,
            lthr=170,
            threshold_pace_sec_per_km=270,
        )
        assert reason is None

    def test_ride_passes_through_as_none(self):
        # Phase 1.6 scope is Run only. Even if Ride matches Run rules → None.
        reason = nc.classify_noise(
            sport="Ride",
            avg_hr=100,
            avg_pace_sec_per_km=480,
            hr_zone_times=[4800, 900, 300, 0, 0],
            tss=25.0,
            lthr=170,
            threshold_pace_sec_per_km=270,
        )
        assert reason is None

    def test_swim_passes_through_as_none(self):
        # Same — Swim is deferred (small n).
        reason = nc.classify_noise(
            sport="Swim",
            avg_hr=120,
            avg_pace_sec_per_km=None,
            hr_zone_times=None,
            tss=30.0,
            lthr=None,
            threshold_pace_sec_per_km=None,
        )
        assert reason is None

    def test_none_sport_returns_none(self):
        assert (
            nc.classify_noise(
                sport=None,
                avg_hr=100,
                avg_pace_sec_per_km=480,
                hr_zone_times=[4800, 900, 0, 0, 0],
                tss=25.0,
            )
            is None
        )


# ---------------------------------------------------------------------------
# classify_activity_row — ORM-row convenience wrapper
# ---------------------------------------------------------------------------


class TestClassifyActivityRow:
    def _activity(self, **overrides):
        defaults = dict(type="Run", average_hr=140.0, icu_training_load=60.0, moving_time=3600)
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _detail(self, **overrides):
        defaults = dict(distance=10000.0, hr_zone_times=[600, 4500, 720, 180, 0])
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_clean_run_returns_none(self):
        # 6:00/km, HR 140 — clean base run.
        result = nc.classify_activity_row(self._activity(), self._detail(), _thresholds())
        assert result is None

    def test_walk_row_returns_run_walk(self):
        # 60 min over 6km = 10:00/km @ HR 95 — walk.
        result = nc.classify_activity_row(
            self._activity(average_hr=95.0, moving_time=3600),
            self._detail(distance=6000.0),
            _thresholds(),
        )
        assert result == "run_walk"

    def test_jog_row_returns_run_recovery_jog(self):
        # 4:00/km pace, HR 130, Z1≥70%, TSS<40 → recovery jog.
        result = nc.classify_activity_row(
            self._activity(average_hr=130.0, icu_training_load=25.0, moving_time=1800),
            self._detail(distance=7500.0, hr_zone_times=[4800, 900, 300, 0, 0]),
            _thresholds(),
        )
        assert result == "run_recovery_jog"

    def test_missing_distance_returns_none(self):
        # No distance → can't derive pace → walk rule disabled. Recovery-jog
        # rule needs Z1≥70% AND TSS<40 — default fixture has balanced zones
        # AND TSS=60, so neither rule fires → None.
        result = nc.classify_activity_row(
            self._activity(average_hr=95.0, icu_training_load=60.0),
            self._detail(distance=None),
            _thresholds(),
        )
        assert result is None

    def test_non_run_passes_through(self):
        result = nc.classify_activity_row(
            self._activity(type="Ride"),
            self._detail(),
            _thresholds(),
        )
        assert result is None

    def test_thresholds_attribute_access_with_none_values(self):
        # AthleteThresholdsDTO has lthr_run=None / threshold_pace_run=None
        # for athletes without synced settings → fallback path engaged.
        result = nc.classify_activity_row(
            self._activity(average_hr=105.0, moving_time=3600),
            self._detail(distance=8500.0, hr_zone_times=[600, 4500, 720, 180, 0]),
            _thresholds(lthr=None, threshold_pace_run=None),
        )
        # 7:04/km @ HR 105 → fallback (6:30 + 120) → walk.
        assert result == "run_walk"


# Smoke check — module exports the public Literal type for downstream typing.
def test_noise_reason_type_exported():
    assert hasattr(nc, "NoiseReason")
    # The Literal can't be runtime-asserted easily, just confirm the constants
    # match the canonical values used in spec §6.4.2.
    assert nc.classify_noise(
        sport="Run",
        avg_hr=100,
        avg_pace_sec_per_km=480,
        hr_zone_times=[4800, 900, 300, 0, 0],
        tss=25.0,
        lthr=170,
        threshold_pace_sec_per_km=270,
    ) in ("run_walk", "run_recovery_jog", None)


# Pytest discovery sanity — fail noisily if the module renames a constant.
def test_module_public_constants():
    expected = {
        "Z1_RECOVERY_THRESHOLD": 0.70,
        "RECOVERY_TSS_CEILING": 40.0,
        "WALK_PACE_MULT": 1.6,
        "WALK_HR_MULT": 0.65,
        "WALK_FALLBACK_PACE_SEC_PER_KM": 390,  # 6:30/km in sec
        "WALK_FALLBACK_HR_BPM": 120,
    }
    for name, value in expected.items():
        actual = getattr(nc, name)
        assert actual == pytest.approx(value), f"{name} drifted: {actual} != {value}"
