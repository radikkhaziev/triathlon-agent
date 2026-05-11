"""Tests for data/ml/race_predict.py.

We don't load real joblib models — we patch ``_load_model`` to return a fake
bundle. That keeps tests fast and hermetic while exercising the CI / inflation
math and Mode 2 override application.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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
    @pytest.mark.asyncio
    async def test_no_distance_returns_empty_splits(self):
        envelope = await race_predict.predict_splits_with_ci(
            user_id=1, mode="today", race_date=(date.today() + timedelta(days=30)).isoformat()
        )
        assert envelope["splits"] == {}
        assert envelope["not_available"] == []

    @pytest.mark.asyncio
    async def test_model_not_trained_lands_in_not_available(self):
        with patch.object(race_predict, "_load_model", side_effect=race_predict.ModelNotTrained):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="today",
                race_date=(date.today() + timedelta(days=30)).isoformat(),
                race_distance_run_m=21000,
            )
        assert "run" in envelope["not_available"]
        assert envelope["splits"] == {}
        assert any("not trained" in w for w in envelope["warnings"])

    @pytest.mark.asyncio
    async def test_mode_today_inflation_is_one(self):
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="today",
                race_date=(date.today() + timedelta(days=30)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert "run" in envelope["splits"]
        # inflation field is only emitted in race_day mode with overrides
        assert "inflation" not in envelope

    @pytest.mark.asyncio
    async def test_mode_race_day_falls_back_when_no_projection(self):
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        # `_mode2_overrides` is async (awaits @dual ORM methods) — needs AsyncMock.
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
            patch.object(race_predict, "_mode2_overrides", AsyncMock(return_value=None)),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="race_day",
                race_date=(date.today() + timedelta(days=120)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert any("no_fitness_projection" in w for w in envelope["warnings"])
        # Mode-1 fallback still produces splits
        assert "run" in envelope["splits"]

    @pytest.mark.asyncio
    async def test_mode_race_day_with_overrides_emits_projection_fields(self):
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        overrides = {"ctl": 75.0, "atl": 70.0, "current_eftp": 230.0}
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
            patch.object(race_predict, "_mode2_overrides", AsyncMock(return_value=overrides)),
        ):
            envelope = await race_predict.predict_splits_with_ci(
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


class TestQualityGate:
    """Per-discipline acceptance floor blocks catastrophic models from output.

    `_enforce_quality_gate` reads `bundle["metrics"]` and raises
    :class:`ModelBelowAcceptance` when `r2` or `mae` fall below the
    discipline-specific floor.
    """

    def test_passes_when_metrics_above_floor(self):
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        bundle["metrics"] = {"r2": 0.35, "mae": 30.0}  # Run floor: r2≥0.20, mae≤40
        # No exception
        race_predict._enforce_quality_gate(bundle, "run", user_id=1)

    def test_rejects_negative_r2(self):
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        bundle["metrics"] = {"r2": -0.5, "mae": 30.0, "n_examples": 100}
        bundle["user_id"] = 1
        with pytest.raises(race_predict.ModelBelowAcceptance):
            race_predict._enforce_quality_gate(bundle, "run", user_id=1)

    def test_rejects_mae_above_cap(self):
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        bundle["metrics"] = {"r2": 0.40, "mae": 100.0, "n_examples": 100}  # mae > 40
        bundle["user_id"] = 1
        with pytest.raises(race_predict.ModelBelowAcceptance):
            race_predict._enforce_quality_gate(bundle, "run", user_id=1)

    def test_swim_has_lower_r2_floor(self):
        """Swim's floor is 0.05 (spec §12.3 acknowledges Swim is weakest)."""
        bundle = _fake_bundle(predict_value=120.0, residuals=[-5, 5])
        bundle["metrics"] = {"r2": 0.08, "mae": 7.0}  # would fail Run/Ride floor of 0.20
        race_predict._enforce_quality_gate(bundle, "swim", user_id=1)  # no raise

    def test_legacy_bundle_without_metrics_passes(self):
        """Backwards-compat: bundles trained before the gate landed don't have
        a ``metrics`` field. We trust them rather than refuse silently."""
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        # no metrics key
        race_predict._enforce_quality_gate(bundle, "run", user_id=1)  # no raise

    @pytest.mark.asyncio
    async def test_below_acceptance_lands_in_envelope(self):
        """Full integration: a low-quality model surfaces as `model_below_acceptance`."""
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        bundle["metrics"] = {"r2": -75.0, "mae": 164.0, "n_examples": 159}
        bundle["user_id"] = 14

        # End-to-end wiring: exercise the real _load_model → _enforce_quality_gate
        # → raise path by patching joblib.load and Path.exists at the lowest layer.
        # An earlier draft mocked `_load_model` directly with side_effect; that
        # short-circuited the wire-up under test and would still pass even if
        # _load_model stopped calling _enforce_quality_gate in a regression.
        with (
            patch("joblib.load", return_value=bundle),
            patch("pathlib.Path.exists", return_value=True),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=14,
                mode="today",
                race_date=(date.today() + timedelta(days=30)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert "run" in envelope["below_acceptance"]
        assert "run" not in envelope["not_available"]
        assert envelope["splits"] == {}
        assert any("below acceptance" in w for w in envelope["warnings"])
