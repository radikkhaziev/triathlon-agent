"""Tests for data/ml/race_predict.py.

We don't load real joblib models — we patch ``_load_model`` to return a fake
bundle. That keeps tests fast and hermetic while exercising the CI / inflation
math and Mode 2 override application.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from data.ml import race_predict


def _fake_bundle(predict_value: float, residuals: list[float]):
    """Build a bundle mimicking joblib.dump shape from race_train."""
    model = MagicMock()
    model.predict.return_value = np.array([predict_value])
    return {
        "model": model,
        "residuals": np.array(residuals),
        "feature_names": ["ctl", "atl", "tsb", "hrv", "target_hr", "distance_m"],
    }


def _fake_features(**overrides):
    base = {
        "ctl": 55.0,
        "atl": 50.0,
        "tsb": 5.0,
        "hrv": 48.0,
        "target_hr": 150.0,
        "distance_m": 21000.0,
    }
    base.update(overrides)
    return base


class TestPredictOne:
    def test_returns_envelope_with_ci(self):
        bundle = _fake_bundle(predict_value=300.0, residuals=[-20, -10, 0, 10, 20, 30, -5, 5, 15, -15])
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
        ):
            out = race_predict._predict_one(
                user_id=1,
                discipline="run",
                target_date=date(2026, 9, 15),
                target_hr=150,
                distance_m=21000.0,
                overrides=None,
                inflation=1.0,
            )
        assert out["pred"] == 300.0
        # 5th percentile of [-20..30] ≈ -17.75, 95th ≈ 27.75
        assert out["ci_low"] < out["pred"] < out["ci_high"]
        # Run: total_sec = pred * distance/1000
        assert out["total_sec"] == int(300.0 * 21)

    def test_run_total_sec_scales_with_distance(self):
        bundle = _fake_bundle(predict_value=360.0, residuals=[0.0])
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
        ):
            out = race_predict._predict_one(1, "run", date(2026, 9, 15), 150, 10_000.0, None, 1.0)
        assert out["total_sec"] == 3600  # 6:00/km × 10 km

    def test_swim_total_sec_uses_100m(self):
        bundle = _fake_bundle(predict_value=120.0, residuals=[0.0])
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
        ):
            out = race_predict._predict_one(1, "swim", date(2026, 9, 15), None, 1900.0, None, 1.0)
        # 2:00/100m × 19 segments = 2280
        assert out["total_sec"] == 2280

    def test_ride_returns_no_total_sec(self):
        bundle = _fake_bundle(predict_value=210.0, residuals=[0.0])
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
        ):
            out = race_predict._predict_one(1, "ride", date(2026, 9, 15), 150, 90_000.0, None, 1.0)
        # Power-only — duration not derivable, omit total_sec
        assert "total_sec" not in out
        assert out["pred"] == 210.0

    def test_inflation_widens_ci(self):
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
        ):
            narrow = race_predict._predict_one(1, "run", date(2026, 9, 15), 150, 21000.0, None, 1.0)
            wide = race_predict._predict_one(1, "run", date(2026, 9, 15), 150, 21000.0, None, 2.0)
        narrow_span = narrow["ci_high"] - narrow["ci_low"]
        wide_span = wide["ci_high"] - wide["ci_low"]
        assert wide_span == pytest.approx(narrow_span * 2.0)


class TestPredictSplitsWithCi:
    def test_no_distance_returns_empty_splits(self):
        envelope = race_predict.predict_splits_with_ci(
            user_id=1, mode="today", race_date=(date.today() + timedelta(days=30)).isoformat()
        )
        assert envelope["splits"] == {}
        assert envelope["not_available"] == []

    def test_model_not_trained_lands_in_not_available(self):
        with patch.object(race_predict, "_load_model", side_effect=race_predict.ModelNotTrained):
            envelope = race_predict.predict_splits_with_ci(
                user_id=1,
                mode="today",
                race_date=(date.today() + timedelta(days=30)).isoformat(),
                race_distance_run_m=21000,
            )
        assert "run" in envelope["not_available"]
        assert envelope["splits"] == {}
        assert any("not trained" in w for w in envelope["warnings"])

    def test_mode_today_inflation_is_one(self):
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
        ):
            envelope = race_predict.predict_splits_with_ci(
                user_id=1,
                mode="today",
                race_date=(date.today() + timedelta(days=30)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert "run" in envelope["splits"]
        # inflation field is only emitted in race_day mode with overrides
        assert "inflation" not in envelope

    def test_mode_race_day_falls_back_when_no_projection(self):
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
            patch.object(race_predict, "_mode2_overrides", return_value=None),
        ):
            envelope = race_predict.predict_splits_with_ci(
                user_id=1,
                mode="race_day",
                race_date=(date.today() + timedelta(days=120)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert any("no_fitness_projection" in w for w in envelope["warnings"])
        # Mode-1 fallback still produces splits
        assert "run" in envelope["splits"]

    def test_mode_race_day_with_overrides_emits_projection_fields(self):
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        overrides = {"ctl": 75.0, "atl": 70.0, "current_eftp": 230.0}
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
            patch.object(race_predict, "_mode2_overrides", return_value=overrides),
        ):
            envelope = race_predict.predict_splits_with_ci(
                user_id=1,
                mode="race_day",
                race_date=(date.today() + timedelta(days=120)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert envelope["projected_ctl"] == 75.0
        assert envelope["projected_atl"] == 70.0
        assert envelope["inflation"] > 1.0  # 120 days → sqrt(4)=2
        assert envelope["inflation"] == pytest.approx(math.sqrt(120 / 30), abs=1e-3)
