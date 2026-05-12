"""Tests for data/ml/race_features.py.

Strategy: stub out the `_fetch_*` SQL helpers with synthetic pandas frames so
the test is hermetic (no DB) and the feature-row arithmetic / discipline
dispatch is exercised end-to-end.
"""

from __future__ import annotations

import math
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from data.ml import race_features


def _activities_df(rows: list[dict]) -> pd.DataFrame:
    cols = [
        "activity_id",
        "date",
        "sport",
        "sub_type",
        "moving_time",
        "tss",
        "avg_hr",
        "is_race",
        "distance",
        "elevation_gain",
        "avg_power",
        "normalized_power",
        "pace_mps",
        "hr_zone_times",
    ]
    return pd.DataFrame(rows, columns=cols)


def _wellness_df(rows: list[dict]) -> pd.DataFrame:
    cols = ["date", "ctl", "atl", "hrv", "resting_hr", "sleep_score", "recovery_score"]
    return pd.DataFrame(rows, columns=cols)


def _garmin_df(rows: list[dict] | None = None) -> pd.DataFrame:
    cols = ["date", "avg_stress"]
    return pd.DataFrame(rows or [], columns=cols)


def _training_log_df(rows: list[dict] | None = None) -> pd.DataFrame:
    cols = ["date", "compliance", "compliance_num"]
    return pd.DataFrame(rows or [], columns=cols)


# ---------------------------------------------------------------------------
# Target construction (§6.3)
# ---------------------------------------------------------------------------


class TestTargetValue:
    def test_run_pace_sec_per_km(self):
        row = pd.Series({"moving_time": 1800, "distance": 5000, "normalized_power": None, "avg_power": None})
        assert race_features._target_value(row, "Run") == pytest.approx(360.0)  # 5km in 30min → 6:00/km

    def test_ride_prefers_normalized_power(self):
        row = pd.Series({"moving_time": 3600, "distance": 30000, "normalized_power": 220, "avg_power": 200})
        assert race_features._target_value(row, "Ride") == 220.0

    def test_ride_falls_back_to_avg_power(self):
        row = pd.Series({"moving_time": 3600, "distance": 30000, "normalized_power": None, "avg_power": 195})
        assert race_features._target_value(row, "Ride") == 195.0

    def test_swim_pace_sec_per_100m(self):
        row = pd.Series({"moving_time": 1500, "distance": 1000, "normalized_power": None, "avg_power": None})
        assert race_features._target_value(row, "Swim") == pytest.approx(150.0)  # 1km in 25min → 2:30/100m

    def test_short_activity_rejected(self):
        row = pd.Series({"moving_time": 600, "distance": 2000, "normalized_power": None, "avg_power": None})
        assert race_features._target_value(row, "Run") is None  # <25 min cutoff

    def test_zero_distance_run_rejected(self):
        row = pd.Series({"moving_time": 1800, "distance": 0, "normalized_power": None, "avg_power": None})
        assert race_features._target_value(row, "Run") is None


# ---------------------------------------------------------------------------
# Per-sport CTL series — EMA over daily TSS
# ---------------------------------------------------------------------------


class TestSportCtlSeries:
    def test_empty_returns_empty_series(self):
        df = _activities_df([])
        result = race_features._compute_sport_ctl_series(df, "Run")
        assert result.empty

    def test_single_day_decays_correctly(self):
        # One Run activity with TSS=100 on day 0 → CTL on day 0 = 100 * (1 - exp(-1/42))
        df = pd.DataFrame([{"date": "2026-01-01", "sport": "Run", "tss": 100.0}])
        series = race_features._compute_sport_ctl_series(df, "Run", tau=42)
        expected = 100.0 * (1 - math.exp(-1 / 42))
        assert series["2026-01-01"] == pytest.approx(round(expected, 2))

    def test_sport_filter_ignores_other_sports(self):
        df = pd.DataFrame(
            [
                {"date": "2026-01-01", "sport": "Run", "tss": 100.0},
                {"date": "2026-01-01", "sport": "Ride", "tss": 999.0},
            ]
        )
        run_series = race_features._compute_sport_ctl_series(df, "Run")
        ride_series = race_features._compute_sport_ctl_series(df, "Ride")
        assert run_series["2026-01-01"] < 5  # ~2.35
        assert ride_series["2026-01-01"] > 20  # ~23.5


# ---------------------------------------------------------------------------
# State row composition
# ---------------------------------------------------------------------------


class TestStateRow:
    def test_picks_latest_wellness_le_target(self):
        wellness = _wellness_df(
            [
                {
                    "date": "2026-04-01",
                    "ctl": 50,
                    "atl": 60,
                    "hrv": 45,
                    "resting_hr": 50,
                    "sleep_score": 80,
                    "recovery_score": 70,
                },
                {
                    "date": "2026-04-15",
                    "ctl": 55,
                    "atl": 50,
                    "hrv": 48,
                    "resting_hr": 48,
                    "sleep_score": 85,
                    "recovery_score": 75,
                },
            ]
        )
        state = race_features._state_row("2026-04-20", wellness, {}, _garmin_df(), _training_log_df())
        assert state["ctl"] == 55.0
        assert state["atl"] == 50.0
        assert state["tsb"] == pytest.approx(5.0)
        assert state["hrv"] == 48.0

    def test_empty_wellness_gives_nan(self):
        state = race_features._state_row("2026-04-20", _wellness_df([]), {}, _garmin_df(), _training_log_df())
        assert math.isnan(state["ctl"])
        assert math.isnan(state["tsb"])

    def test_per_sport_ctl_picked_at_latest_date_le_target(self):
        ctl_per_sport = {
            "Run": pd.Series({"2026-04-01": 30.0, "2026-04-10": 35.0, "2026-04-30": 40.0}),
            "Ride": pd.Series(dtype="float64"),
            "Swim": pd.Series(dtype="float64"),
        }
        state = race_features._state_row(
            "2026-04-15", _wellness_df([]), ctl_per_sport, _garmin_df(), _training_log_df()
        )
        assert state["ctl_run"] == 35.0  # latest <= 2026-04-15
        assert state["ctl_ride"] == 0.0
        assert state["ctl_swim"] == 0.0


# ---------------------------------------------------------------------------
# Discipline dispatcher
# ---------------------------------------------------------------------------


class TestBuildDataset:
    def test_unknown_discipline_raises(self):
        with pytest.raises(ValueError, match="Unknown discipline"):
            race_features.build_dataset(1, "yoga")

    def test_no_activities_returns_empty(self):
        with patch.object(race_features, "_fetch_activities", return_value=_activities_df([])):
            df = race_features.build_dataset(1, "run")
        assert df.empty

    def test_z1_filter_engages_in_pipeline(self):
        """Regression guard: confirm `_is_z1_dominated` actually fires
        inside the `build_dataset` loop, not just in isolation. Without this
        coverage, a refactor that drops the call site (or moves the check
        below where rows are counted) would slip through.
        """
        # 3 Run activities — 2 normal Z2-base + 1 recovery jog (85% Z1).
        # All have enough moving_time, distance, and HR to pass other gates;
        # only the z1-filter should remove the recovery row.
        run_rows = [
            {
                "activity_id": "i1",
                "date": "2026-04-01",
                "sport": "Run",
                "sub_type": None,
                "moving_time": 3600,
                "tss": 50.0,
                "avg_hr": 140.0,
                "is_race": False,
                "distance": 10000.0,
                "elevation_gain": 0.0,
                "avg_power": None,
                "normalized_power": None,
                "pace_mps": 2.78,
                "hr_zone_times": [600, 2400, 480, 120, 0],  # Z2 base — keep
            },
            {
                "activity_id": "i2",
                "date": "2026-04-08",
                "sport": "Run",
                "sub_type": None,
                "moving_time": 3600,
                # TSS below RECOVERY_TSS_CEILING — short jog, low load
                "tss": 25.0,
                "avg_hr": 138.0,
                "is_race": False,
                "distance": 10000.0,
                "elevation_gain": 0.0,
                "avg_power": None,
                "normalized_power": None,
                "pace_mps": 2.78,
                "hr_zone_times": [3060, 540, 0, 0, 0],  # 85% Z1 — recovery, drop
            },
            {
                "activity_id": "i3",
                "date": "2026-04-15",
                "sport": "Run",
                "sub_type": None,
                "moving_time": 3600,
                "tss": 55.0,
                "avg_hr": 145.0,
                "is_race": False,
                "distance": 10000.0,
                "elevation_gain": 0.0,
                "avg_power": None,
                "normalized_power": None,
                "pace_mps": 2.78,
                "hr_zone_times": [400, 2200, 700, 300, 0],  # Z2 base — keep
            },
        ]
        with (
            patch.object(race_features, "_fetch_activities", return_value=_activities_df(run_rows)),
            patch.object(
                race_features,
                "_fetch_all_sports_activities",
                return_value=pd.DataFrame(
                    columns=["date", "sport", "tss"],
                ),
            ),
            patch.object(race_features, "_fetch_wellness", return_value=_wellness_df([])),
            patch.object(race_features, "_fetch_garmin_daily", return_value=_garmin_df()),
            patch.object(race_features, "_fetch_training_log", return_value=_training_log_df()),
            patch.object(race_features, "_fetch_athlete_state", return_value={}),
        ):
            df = race_features.build_dataset(1, "run")
        # Recovery row (i2) dropped — only 2 of 3 survive
        assert len(df) == 2
        assert set(df["activity_id"]) == {"i1", "i3"}
        assert "i2" not in df["activity_id"].values

    def test_z1_filter_keeps_long_z1_base(self):
        """Regression guard for the user-62 case: a Z1-dominated session with
        TSS≥40 (structured 80/20 base) must NOT be filtered. Pre-TSS-gate this
        broke pro athletes (Ras R²=0.44→0.04). Catches anyone who would later
        re-tighten the filter back to zone-only.
        """
        run_rows = [
            {
                "activity_id": "i1",
                "date": "2026-04-01",
                "sport": "Run",
                "sub_type": None,
                "moving_time": 3600,
                "tss": 50.0,
                "avg_hr": 140.0,
                "is_race": False,
                "distance": 10000.0,
                "elevation_gain": 0.0,
                "avg_power": None,
                "normalized_power": None,
                "pace_mps": 2.78,
                "hr_zone_times": [600, 2400, 480, 120, 0],  # Z2 base — keep
            },
            {
                # Long Z1-base session: 90 min @ TSS 75, 85% Z1 — keep!
                "activity_id": "i2_long_base",
                "date": "2026-04-08",
                "sport": "Run",
                "sub_type": None,
                "moving_time": 5400,
                "tss": 75.0,
                "avg_hr": 135.0,
                "is_race": False,
                "distance": 15000.0,
                "elevation_gain": 0.0,
                "avg_power": None,
                "normalized_power": None,
                "pace_mps": 2.78,
                "hr_zone_times": [4590, 600, 210, 0, 0],  # 85% Z1, but TSS≥40
            },
        ]
        with (
            patch.object(race_features, "_fetch_activities", return_value=_activities_df(run_rows)),
            patch.object(
                race_features,
                "_fetch_all_sports_activities",
                return_value=pd.DataFrame(columns=["date", "sport", "tss"]),
            ),
            patch.object(race_features, "_fetch_wellness", return_value=_wellness_df([])),
            patch.object(race_features, "_fetch_garmin_daily", return_value=_garmin_df()),
            patch.object(race_features, "_fetch_training_log", return_value=_training_log_df()),
            patch.object(race_features, "_fetch_athlete_state", return_value={}),
        ):
            df = race_features.build_dataset(1, "run")
        # Both rows survive — Z1-base session kept despite Z1-dominated zones
        assert len(df) == 2
        assert "i2_long_base" in df["activity_id"].values

    def test_z1_filter_missing_tss_keeps_activity(self):
        """End-to-end coverage of the lenient missing-TSS path: a Z1-dominated
        row with `tss=None` must NOT be filtered. The helper-level test
        `test_missing_tss_keeps_activity` covers `_is_recovery_jog` directly;
        this integration test guards against a `build_dataset`-level regression
        where `act.get("tss")` might be coerced to NaN by pandas or the
        call-site stops passing it.
        """
        run_rows = [
            {
                "activity_id": "i1",
                "date": "2026-04-01",
                "sport": "Run",
                "sub_type": None,
                "moving_time": 3600,
                "tss": 50.0,
                "avg_hr": 140.0,
                "is_race": False,
                "distance": 10000.0,
                "elevation_gain": 0.0,
                "avg_power": None,
                "normalized_power": None,
                "pace_mps": 2.78,
                "hr_zone_times": [600, 2400, 480, 120, 0],  # Z2 base — keep
            },
            {
                # Z1-dominated but TSS missing → lenient, keep
                "activity_id": "i2_no_tss",
                "date": "2026-04-08",
                "sport": "Run",
                "sub_type": None,
                "moving_time": 3600,
                "tss": None,
                "avg_hr": 138.0,
                "is_race": False,
                "distance": 10000.0,
                "elevation_gain": 0.0,
                "avg_power": None,
                "normalized_power": None,
                "pace_mps": 2.78,
                "hr_zone_times": [3060, 540, 0, 0, 0],  # 85% Z1
            },
        ]
        with (
            patch.object(race_features, "_fetch_activities", return_value=_activities_df(run_rows)),
            patch.object(
                race_features,
                "_fetch_all_sports_activities",
                return_value=pd.DataFrame(columns=["date", "sport", "tss"]),
            ),
            patch.object(race_features, "_fetch_wellness", return_value=_wellness_df([])),
            patch.object(race_features, "_fetch_garmin_daily", return_value=_garmin_df()),
            patch.object(race_features, "_fetch_training_log", return_value=_training_log_df()),
            patch.object(race_features, "_fetch_athlete_state", return_value={}),
        ):
            df = race_features.build_dataset(1, "run")
        # Note: `_target_value` may reject the row if TSS being None propagates
        # to other paths; explicitly check that filter didn't fire (i.e. row
        # presence depends only on _target_value/HR gates, not on z1-filter).
        # Z1-dominated + no TSS → filter must NOT engage.
        assert "i2_no_tss" in df["activity_id"].values

    def test_z1_filter_tss_at_ceiling_keeps_activity(self):
        """Boundary integration test: TSS exactly at RECOVERY_TSS_CEILING
        (40.0) must survive — strict-less comparison (`<`, not `<=`), defensive
        lean toward keeping. Guards against a future flip of `<` to `<=`.
        """
        run_rows = [
            {
                "activity_id": "i1_boundary",
                "date": "2026-04-01",
                "sport": "Run",
                "sub_type": None,
                "moving_time": 3600,
                "tss": float(race_features.RECOVERY_TSS_CEILING),  # exactly 40.0
                "avg_hr": 138.0,
                "is_race": False,
                "distance": 10000.0,
                "elevation_gain": 0.0,
                "avg_power": None,
                "normalized_power": None,
                "pace_mps": 2.78,
                "hr_zone_times": [3060, 540, 0, 0, 0],  # 85% Z1
            },
        ]
        with (
            patch.object(race_features, "_fetch_activities", return_value=_activities_df(run_rows)),
            patch.object(
                race_features,
                "_fetch_all_sports_activities",
                return_value=pd.DataFrame(columns=["date", "sport", "tss"]),
            ),
            patch.object(race_features, "_fetch_wellness", return_value=_wellness_df([])),
            patch.object(race_features, "_fetch_garmin_daily", return_value=_garmin_df()),
            patch.object(race_features, "_fetch_training_log", return_value=_training_log_df()),
            patch.object(race_features, "_fetch_athlete_state", return_value={}),
        ):
            df = race_features.build_dataset(1, "run")
        # Survived — boundary err-to-keep
        assert "i1_boundary" in df["activity_id"].values


# ---------------------------------------------------------------------------
# Inference feature builder + Mode 2 overrides
# ---------------------------------------------------------------------------


class TestBuildInferenceFeatures:
    def _patch_all(self, **kwargs):
        """Helper — mock every _fetch_* to a deterministic non-empty default."""
        defaults = {
            "_fetch_all_sports_activities": _activities_df([]),
            "_fetch_wellness": _wellness_df(
                [
                    {
                        "date": "2026-04-15",
                        "ctl": 55,
                        "atl": 50,
                        "hrv": 48,
                        "resting_hr": 48,
                        "sleep_score": 85,
                        "recovery_score": 75,
                    },
                ]
            ),
            "_fetch_garmin_daily": _garmin_df(),
            "_fetch_training_log": _training_log_df(),
            "_fetch_athlete_state": {
                "Ride": {"ftp": 200, "critical_power": 210, "w_prime": 15000, "p_max": 600, "lthr": 160, "max_hr": 180},
                "Run": {"lthr": 165, "max_hr": 185, "threshold_pace": 290},
            },
        }
        defaults.update(kwargs)
        return defaults

    def test_unknown_discipline_raises(self):
        with pytest.raises(ValueError):
            race_features.build_inference_features(1, "yoga", date(2026, 9, 15), 150, 21000)

    def test_run_features_include_is_race(self):
        mocks = self._patch_all()
        with (
            patch.object(
                race_features, "_fetch_all_sports_activities", return_value=mocks["_fetch_all_sports_activities"]
            ),
            patch.object(race_features, "_fetch_wellness", return_value=mocks["_fetch_wellness"]),
            patch.object(race_features, "_fetch_garmin_daily", return_value=mocks["_fetch_garmin_daily"]),
            patch.object(race_features, "_fetch_training_log", return_value=mocks["_fetch_training_log"]),
            patch.object(race_features, "_fetch_athlete_state", return_value=mocks["_fetch_athlete_state"]),
        ):
            f = race_features.build_inference_features(1, "run", date(2026, 9, 15), 150, 21000)
        assert f["is_race"] == 1
        assert f["distance_m"] == 21000.0
        assert f["target_hr"] == 150.0

    def test_overrides_apply_after_state(self):
        mocks = self._patch_all()
        with (
            patch.object(
                race_features, "_fetch_all_sports_activities", return_value=mocks["_fetch_all_sports_activities"]
            ),
            patch.object(race_features, "_fetch_wellness", return_value=mocks["_fetch_wellness"]),
            patch.object(race_features, "_fetch_garmin_daily", return_value=mocks["_fetch_garmin_daily"]),
            patch.object(race_features, "_fetch_training_log", return_value=mocks["_fetch_training_log"]),
            patch.object(race_features, "_fetch_athlete_state", return_value=mocks["_fetch_athlete_state"]),
        ):
            f = race_features.build_inference_features(
                1,
                "ride",
                date(2026, 9, 15),
                150,
                90000,
                overrides={"ctl": 80.0, "atl": 75.0, "current_eftp": 230.0},
            )
        assert f["ctl"] == 80.0
        assert f["atl"] == 75.0
        assert f["tsb"] == pytest.approx(5.0)  # recomputed from overrides
        assert f["current_eftp"] == 230.0


class TestZ1Dominated:
    """`_is_z1_dominated` — zone-composition primitive. Returns True when Z1 ≥
    70% of recorded HR time. Does NOT distinguish recovery jog from structured
    Z1-base — the filter decision uses :class:`TestRecoveryJog` (combined
    helper). These tests just exercise the zone-only check.
    """

    def test_z1_dominated_returns_true(self):
        # 80% Z1 + 15% Z2 + 5% Z3 → dominated by Z1
        zones = [4800, 900, 300, 0, 0]  # secs per zone
        assert race_features._is_z1_dominated(zones) is True

    def test_z2_base_run_returns_false(self):
        # 10% Z1 + 75% Z2 + 12% Z3 + 3% Z4 → standard Z2 base — not Z1-dominated
        zones = [600, 4500, 720, 180, 0]
        assert race_features._is_z1_dominated(zones) is False

    def test_threshold_workout_returns_false(self):
        # WU/CD Z1+Z2 + main set Z4-Z5 → intervals, definitely not Z1-dominated
        zones = [600, 1200, 800, 1800, 1200]
        assert race_features._is_z1_dominated(zones) is False

    def test_missing_zones_returns_false(self):
        """Older activities w/o synced details → don't filter what we can't measure."""
        assert race_features._is_z1_dominated(None) is False
        assert race_features._is_z1_dominated([]) is False
        assert race_features._is_z1_dominated([0, 0, 0, 0, 0]) is False

    def test_threshold_boundary(self):
        """Exactly 70% Z1 → drop (inclusive — `>=`)."""
        zones = [7000, 3000, 0, 0, 0]  # 70% / 30%
        assert race_features._is_z1_dominated(zones) is True
        # 69% Z1 → keep
        zones = [6900, 3100, 0, 0, 0]
        assert race_features._is_z1_dominated(zones) is False

    def test_threshold_constant(self):
        """Floor value tracked in constant — anyone tuning the filter must
        update both the constant and the conservative-defaults narrative."""
        assert race_features.Z1_RECOVERY_THRESHOLD == 0.70

    def test_tuple_input_works(self):
        """SQLAlchemy/pandas may return tuple instead of list — accept both."""
        zones = (4800, 900, 300, 0, 0)  # 80% Z1 — recovery
        assert race_features._is_z1_dominated(zones) is True

    def test_ndarray_input_works(self):
        """numpy arrays are returned by some pandas paths — should work too."""
        import numpy as np

        zones = np.array([4800, 900, 300, 0, 0])
        assert race_features._is_z1_dominated(zones) is True

    def test_string_input_rejected(self):
        """Strings are technically iterable; reject so we don't iterate chars."""
        # Wouldn't be a real use case (JSON wouldn't return string) but defensive.
        assert race_features._is_z1_dominated("invalid") is False
        assert race_features._is_z1_dominated(b"invalid") is False

    def test_nan_values_neutralized(self):
        """NaN poison check — `bool(float('nan')) is True` so `z or 0`
        wouldn't have neutralized it. Coercion must treat NaN as zero, else
        sum becomes NaN and `nan >= 0.70` returns False, silently disabling
        the filter on a real recovery activity.
        """
        # 80% Z1 + NaN garbage in higher zones — should still flag as recovery.
        zones = [4800, 900, float("nan"), None, 0]
        assert race_features._is_z1_dominated(zones) is True

    def test_all_nan_returns_false(self):
        """If every entry is NaN the sum coerces to 0 → can't classify."""
        zones = [float("nan")] * 5
        assert race_features._is_z1_dominated(zones) is False

    def test_negative_seconds_clamped(self):
        """Defensive — negative time has no physical meaning, treat as 0."""
        zones = [4800, -100, 200, 0, 0]
        # Z1=4800, total=4800+0+200=5000 → 96% Z1 → recovery
        assert race_features._is_z1_dominated(zones) is True


class TestRecoveryJog:
    """`_is_recovery_jog(zones, tss)` — combined filter decision.

    Calibration empirical (2026-05-12 retrain): zone-only filter broke pro
    athletes (user 62 Ras: R²=0.44 → 0.04) because it dropped their long
    Z1-base sessions (TSS 60+) as «recovery». Adding TSS<40 gate keeps the
    base sessions in train-set while still dropping fluff recovery jogs.
    """

    Z1_DOMINATED_ZONES = [4800, 900, 300, 0, 0]  # 80% Z1 — recovery shape
    BALANCED_ZONES = [600, 4500, 720, 180, 0]  # 10% Z1 + 75% Z2 — base shape

    def test_short_z1_jog_is_filtered(self):
        """Classic recovery jog: Z1≥70% AND TSS<40 → drop."""
        assert race_features._is_recovery_jog(self.Z1_DOMINATED_ZONES, tss=25.0) is True

    def test_long_z1_base_session_is_kept(self):
        """Structured Z1-base 90 min @ TSS 70: Z1≥70% but TSS≥ceiling → keep."""
        assert race_features._is_recovery_jog(self.Z1_DOMINATED_ZONES, tss=70.0) is False

    def test_z2_base_run_is_kept(self):
        """Standard Z2-base run: Z1<70% → keep regardless of TSS."""
        assert race_features._is_recovery_jog(self.BALANCED_ZONES, tss=25.0) is False
        assert race_features._is_recovery_jog(self.BALANCED_ZONES, tss=70.0) is False

    def test_missing_tss_keeps_activity(self):
        """Can't classify safely without TSS → don't filter (lenient)."""
        assert race_features._is_recovery_jog(self.Z1_DOMINATED_ZONES, tss=None) is False

    def test_missing_zones_keeps_activity(self):
        """No HR zone data → primitive returns False, so combined → False."""
        assert race_features._is_recovery_jog(None, tss=25.0) is False
        assert race_features._is_recovery_jog([], tss=25.0) is False

    def test_tss_boundary(self):
        """Exactly TSS_CEILING → keep (strictly-less, not less-or-equal)."""
        ceiling = race_features.RECOVERY_TSS_CEILING
        # TSS exactly at ceiling → not filtered (defensive — boundary errs to keep)
        assert race_features._is_recovery_jog(self.Z1_DOMINATED_ZONES, tss=ceiling) is False
        # TSS just below → filtered
        assert race_features._is_recovery_jog(self.Z1_DOMINATED_ZONES, tss=ceiling - 0.01) is True

    def test_nan_tss_keeps_activity(self):
        """NaN TSS → unclassifiable → keep."""
        assert race_features._is_recovery_jog(self.Z1_DOMINATED_ZONES, tss=float("nan")) is False

    def test_non_numeric_tss_keeps_activity(self):
        """Non-numeric TSS (defensive) → keep."""
        assert race_features._is_recovery_jog(self.Z1_DOMINATED_ZONES, tss="bad") is False

    def test_constants(self):
        """Pin exact values — symmetric with `Z1_RECOVERY_THRESHOLD == 0.70`
        pin in `TestZ1Dominated`. Anyone tuning the gate must update the test
        deliberately, surfaced as a diff hunk in review.
        """
        assert race_features.Z1_RECOVERY_THRESHOLD == 0.70
        assert race_features.RECOVERY_TSS_CEILING == 40.0
