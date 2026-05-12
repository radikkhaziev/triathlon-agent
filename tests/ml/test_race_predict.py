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


def _fake_bundle(predict_value: float, residuals: list[float], ctl_feature_p90: float | None = None):
    """Build a bundle mimicking joblib.dump shape from race_train."""
    model = MagicMock()
    model.predict.return_value = np.array([predict_value])
    bundle = {
        "model": model,
        "residuals": np.array(residuals),
        "feature_names": ["ctl", "atl", "tsb", "hrv", "target_hr", "distance_m", "ctl_run"],
    }
    if ctl_feature_p90 is not None:
        bundle["metrics"] = {"mae": 8.0, "r2": 0.5, "n_examples": 200, "ctl_feature_p90": ctl_feature_p90}
    return bundle


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
        # Issue #361: inflation fields emit in both modes for schema parity.
        # In today mode inflation is trivially 1.0 (no horizon to extrapolate).
        assert envelope["inflation"] == pytest.approx(1.0, abs=1e-3)
        assert envelope["inflation_raw"] == pytest.approx(1.0, abs=1e-3)
        assert envelope["inflation_capped"] is False

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
        # Issue #350 cap engaged — raw sqrt(120/30)=2.0 > INFLATION_MAX=1.8.
        assert envelope["inflation"] == pytest.approx(race_predict.INFLATION_MAX, abs=1e-3)

    @pytest.mark.asyncio
    async def test_inflation_capped_at_long_horizon(self):
        """Issue #350: beyond ~97 days, raw sqrt(days/30) exceeds INFLATION_MAX=1.8.
        Cap engages so CI doesn't blow out to ±67 min on a half-marathon prediction
        (current_state observed at 126 days out). 200 days → raw 2.58 → capped 1.8.
        """
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
                race_date=(date.today() + timedelta(days=200)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert envelope["inflation"] == pytest.approx(race_predict.INFLATION_MAX, abs=1e-3)

    @pytest.mark.asyncio
    async def test_inflation_below_min_days_threshold(self):
        """Issue #350: within MIN_RACE_DAYS_FOR_FORECAST=14, Mode 2 fall back to
        inflation=1.0 (Mode 1 width). Within 2 weeks projected_ctl ≈ current_ctl
        (taper window), so wider band misleads. Race in 10 days → inflation 1.0.
        """
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        overrides = {"ctl": 32.0, "atl": 28.0}
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
            patch.object(race_predict, "_mode2_overrides", AsyncMock(return_value=overrides)),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="race_day",
                race_date=(date.today() + timedelta(days=10)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert envelope["inflation"] == pytest.approx(1.0, abs=1e-3)

    @pytest.mark.asyncio
    async def test_inflation_within_sqrt_window(self):
        """Mid-horizon (60 days): raw sqrt(60/30)=1.414 — below cap, above floor."""
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        overrides = {"ctl": 50.0, "atl": 45.0}
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
            patch.object(race_predict, "_mode2_overrides", AsyncMock(return_value=overrides)),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="race_day",
                race_date=(date.today() + timedelta(days=60)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert envelope["inflation"] == pytest.approx(math.sqrt(60 / 30), abs=1e-3)


class TestCiEnvelopeMetadata:
    """Issue #359 follow-up: envelope surfaces `ci_level` (always), plus
    `inflation_raw` + `inflation_capped` in race_day mode. Lets callers
    (Claude prompt) distinguish «capped 1.8× from raw 2.6×» vs «honest 1.4×».
    """

    @pytest.mark.asyncio
    async def test_envelope_metadata_emitted_in_both_modes_today(self):
        """Issue #361 acceptance: all four fields on BOTH today and race_day modes
        for schema consistency. In today mode inflation logic is no-op so values
        are trivial (1.0 / 1.0 / False) but the keys must be present so the
        caller doesn't need to branch on `mode`."""
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="today",
                race_date=(date.today() + timedelta(days=60)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        # 90% PI semantics regardless of mode — derived from CI_LOW_PCT=5/CI_HIGH_PCT=95
        assert envelope["ci_level"] == pytest.approx(0.90, abs=1e-6)
        # Mode 1: inflation fields still present, just trivial values
        assert envelope["inflation"] == pytest.approx(1.0, abs=1e-3)
        assert envelope["inflation_raw"] == pytest.approx(1.0, abs=1e-3)
        assert envelope["inflation_capped"] is False

    @pytest.mark.asyncio
    async def test_inflation_raw_and_capped_emitted_in_race_day_below_cap(self):
        """60d → raw 1.414, applied 1.414 → capped=False."""
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        overrides = {"ctl": 50.0, "atl": 45.0}
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
            patch.object(race_predict, "_mode2_overrides", AsyncMock(return_value=overrides)),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="race_day",
                race_date=(date.today() + timedelta(days=60)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        raw = math.sqrt(60 / 30)
        assert envelope["ci_level"] == pytest.approx(0.90, abs=1e-6)
        assert envelope["inflation"] == pytest.approx(raw, abs=1e-3)
        assert envelope["inflation_raw"] == pytest.approx(raw, abs=1e-3)
        assert envelope["inflation_capped"] is False

    @pytest.mark.asyncio
    async def test_inflation_raw_diverges_from_inflation_when_capped(self):
        """200d → raw sqrt(200/30)≈2.58, applied 1.8 → capped=True. Raw value
        preserved so caller (Claude prompt) can render «model wanted 2.58×, we
        capped at 1.8× — uncertainty stopped growing at horizon»."""
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        overrides = {"ctl": 70.0, "atl": 65.0}
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
            patch.object(race_predict, "_mode2_overrides", AsyncMock(return_value=overrides)),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="race_day",
                race_date=(date.today() + timedelta(days=200)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        raw_expected = math.sqrt(200 / 30)
        assert envelope["inflation"] == pytest.approx(race_predict.INFLATION_MAX, abs=1e-3)
        assert envelope["inflation_raw"] == pytest.approx(raw_expected, abs=1e-3)
        assert envelope["inflation_raw"] > envelope["inflation"]
        assert envelope["inflation_capped"] is True
        # ci_level UNCHANGED — cap is on multiplier, not on percentile choice
        assert envelope["ci_level"] == pytest.approx(0.90, abs=1e-6)

    @pytest.mark.asyncio
    async def test_inflation_within_taper_window_emits_raw_one(self):
        """Inside MIN_RACE_DAYS_FOR_FORECAST=14: inflation=1.0 (Mode-1 fallback).
        `inflation_raw` stays 1.0 too (no sqrt computation performed). Capped is
        False (no cap engaged, just no inflation needed)."""
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])
        overrides = {"ctl": 32.0, "atl": 28.0}
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(race_predict, "build_inference_features", return_value=_fake_features()),
            patch.object(race_predict, "_mode2_overrides", AsyncMock(return_value=overrides)),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="race_day",
                race_date=(date.today() + timedelta(days=10)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert envelope["inflation"] == pytest.approx(1.0, abs=1e-3)
        assert envelope["inflation_raw"] == pytest.approx(1.0, abs=1e-3)
        assert envelope["inflation_capped"] is False


class TestOutOfSampleCtl:
    """Issue #359 (b): warn when Mode 2 projects CTL above the discipline's
    training-set p90. XGBoost trees clip to nearest observed leaf → output is
    held conservative + we tell the caller honestly.
    """

    @pytest.mark.asyncio
    async def test_no_warning_when_within_train_distribution(self):
        # ctl_run feature = 30 (from _fake_features below), ratio = 35/30 = 1.17
        # → scaled ctl_run = 35. p90 = 45 → within sample, no warning.
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10], ctl_feature_p90=45.0)
        overrides = {"ctl": 35.0, "atl": 32.0, "_ctl_ratio": 35.0 / 30.0}
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(
                race_predict,
                "build_inference_features",
                return_value=_fake_features(ctl_run=30.0),
            ),
            patch.object(race_predict, "_mode2_overrides", AsyncMock(return_value=overrides)),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="race_day",
                race_date=(date.today() + timedelta(days=60)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        # No OOS warning emitted
        assert not any("out-of-sample" in w for w in envelope["warnings"])
        # Private key stripped from leg before envelope returned
        assert "_ctl_out_of_sample" not in envelope["splits"]["run"]

    @pytest.mark.asyncio
    async def test_warning_emitted_when_projected_above_train_p90(self):
        # User-1 reproduction: training distribution ctl_run p90 = 30
        # (n=300, distribution 15-30), but Mode 2 projects to 66 via ratio 2.2.
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10], ctl_feature_p90=30.0)
        overrides = {"ctl": 66.0, "atl": 60.0, "_ctl_ratio": 66.0 / 30.0}
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(
                race_predict,
                "build_inference_features",
                return_value=_fake_features(ctl_run=30.0),
            ),
            patch.object(race_predict, "_mode2_overrides", AsyncMock(return_value=overrides)),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="race_day",
                race_date=(date.today() + timedelta(days=120)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        # OOS warning surfaces with both values
        oos_warnings = [w for w in envelope["warnings"] if "out-of-sample" in w]
        assert len(oos_warnings) == 1
        assert "run" in oos_warnings[0]
        assert "66" in oos_warnings[0]  # projected
        assert "30" in oos_warnings[0]  # train_p90
        # Private key stripped from public envelope
        assert "_ctl_out_of_sample" not in envelope["splits"]["run"]

    @pytest.mark.asyncio
    async def test_no_warning_when_bundle_lacks_p90_metric(self):
        # Legacy bundle without `metrics.ctl_feature_p90` — backwards compat,
        # don't false-positive on missing data, just stay silent.
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10])  # no p90
        overrides = {"ctl": 66.0, "atl": 60.0, "_ctl_ratio": 2.2}
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(
                race_predict,
                "build_inference_features",
                return_value=_fake_features(ctl_run=30.0),
            ),
            patch.object(race_predict, "_mode2_overrides", AsyncMock(return_value=overrides)),
        ):
            envelope = await race_predict.predict_splits_with_ci(
                user_id=1,
                mode="race_day",
                race_date=(date.today() + timedelta(days=120)).isoformat(),
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert not any("out-of-sample" in w for w in envelope["warnings"])

    def test_predict_one_attaches_private_oos_key_for_aggregation(self):
        """Lower-level `_predict_one` should attach `_ctl_out_of_sample` for the
        envelope aggregator to strip & translate into a public warning. Without
        this contract, the envelope can't tell which leg triggered OOS.
        """
        bundle = _fake_bundle(predict_value=300.0, residuals=[-10, 10], ctl_feature_p90=30.0)
        overrides = {"ctl": 66.0, "atl": 60.0, "_ctl_ratio": 2.2}
        with (
            patch.object(race_predict, "_load_model", return_value=bundle),
            patch.object(
                race_predict,
                "build_inference_features",
                return_value=_fake_features(ctl_run=30.0),
            ),
        ):
            out = race_predict._predict_one(
                user_id=1,
                discipline="run",
                target_date=date(2026, 9, 15),
                target_hr=150,
                distance_m=21000.0,
                overrides=overrides,
                inflation=1.5,
            )
        assert "_ctl_out_of_sample" in out
        assert out["_ctl_out_of_sample"]["train_p90"] == 30.0
        # Projected = 30 × 2.2 = 66
        assert out["_ctl_out_of_sample"]["projected"] == pytest.approx(66.0, abs=0.1)


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


class TestMode2LinearInterpolation:
    """`_mode2_overrides` projects current_CTL → goal.ctl_target linearly.

    Replaces the old Intervals `fitness_projection.ctl` lookup that returned a
    decay curve («what happens if you stop training») — wrong semantics for
    athletes who don't write future workouts into Intervals calendar (issue
    #349). The linear extrapolation matches spec §8.3 Phase 2 fallback.
    """

    def _wellness(self, ctl: float | None = 30.0, atl: float | None = 28.0):
        w = MagicMock()
        w.ctl = ctl
        w.atl = atl
        return w

    def _goal(self, event_date: date, ctl_target: float | None = 68.0):
        g = MagicMock()
        g.event_date = event_date
        g.ctl_target = ctl_target
        return g

    def _patch_deps(self, *, wellness, goal, projection=None):
        """Mock all 3 ORM/data dependencies."""
        return [
            patch.object(race_predict.Wellness, "get", AsyncMock(return_value=wellness)),
            patch.object(race_predict.AthleteGoal, "get_by_category", AsyncMock(return_value=goal)),
            patch.object(race_predict.FitnessProjection, "get", AsyncMock(return_value=projection)),
        ]

    @pytest.mark.asyncio
    async def test_race_at_goal_date_hits_target(self):
        """Race on the same date as the goal → projected_ctl == ctl_target.

        Uses a realistic ramp (current=40, target=68, ratio=1.7) to avoid the
        CTL_PROJECTION_RATIO_CAP=2.0 ceiling. The cap-engagement case is
        covered separately by `test_aggressive_target_engages_cap`.
        """
        today = date(2026, 5, 11)
        goal_dt = date(2026, 9, 15)  # 127 days out
        with (
            patch.object(race_predict, "local_today", return_value=today),
            patch.object(race_predict.Wellness, "get", AsyncMock(return_value=self._wellness(ctl=40.0))),
            patch.object(
                race_predict.AthleteGoal, "get_by_category", AsyncMock(return_value=self._goal(goal_dt, 68.0))
            ),
            patch.object(race_predict.FitnessProjection, "get", AsyncMock(return_value=None)),
        ):
            overrides = await race_predict._mode2_overrides(user_id=1, race_date="2026-09-15")
        assert overrides is not None
        assert overrides["ctl"] == pytest.approx(68.0)
        # Per-sport CTL ratio = projected / current = 68/40 = 1.7 (below cap 2.0)
        assert overrides["_ctl_ratio"] == pytest.approx(68.0 / 40.0)
        assert "_ctl_target_unrealistic" not in overrides

    @pytest.mark.asyncio
    async def test_aggressive_target_engages_cap(self):
        """Target requires ratio > 2.0 → cap engages, projected = current × 2,
        `_ctl_target_unrealistic` flag set so caller can warn the athlete.

        This is the canonical user-1 case (current=30, target=68 → ratio=2.27)
        — without the cap, scaling per-sport CTL features by 2.27× pushes them
        out-of-distribution for the XGBoost model.
        """
        today = date(2026, 5, 11)
        goal_dt = date(2026, 9, 15)
        with (
            patch.object(race_predict, "local_today", return_value=today),
            patch.object(race_predict.Wellness, "get", AsyncMock(return_value=self._wellness(ctl=30.0))),
            patch.object(
                race_predict.AthleteGoal, "get_by_category", AsyncMock(return_value=self._goal(goal_dt, 68.0))
            ),
            patch.object(race_predict.FitnessProjection, "get", AsyncMock(return_value=None)),
        ):
            overrides = await race_predict._mode2_overrides(user_id=1, race_date="2026-09-15")
        # Cap engages: projected_ctl = 30 × 2.0 = 60 (not 68)
        assert overrides["ctl"] == pytest.approx(30.0 * race_predict.CTL_PROJECTION_RATIO_CAP)
        assert overrides["_ctl_ratio"] == pytest.approx(race_predict.CTL_PROJECTION_RATIO_CAP)
        assert overrides["_ctl_target_unrealistic"] is True

    @pytest.mark.asyncio
    async def test_race_at_half_horizon_gets_halfway(self):
        """Race 50% of the way to the goal date → projected CTL halfway."""
        today = date(2026, 5, 11)
        race_dt = date(2026, 7, 12)  # 62 days out
        goal_dt = date(2026, 9, 12)  # 124 days out — race at ~50%
        with (
            patch.object(race_predict, "local_today", return_value=today),
            patch.object(race_predict.Wellness, "get", AsyncMock(return_value=self._wellness(ctl=30.0))),
            patch.object(
                race_predict.AthleteGoal, "get_by_category", AsyncMock(return_value=self._goal(goal_dt, 68.0))
            ),
            patch.object(race_predict.FitnessProjection, "get", AsyncMock(return_value=None)),
        ):
            overrides = await race_predict._mode2_overrides(user_id=1, race_date=race_dt.isoformat())
        # 50% along linear path: 30 + 0.5×(68−30) = 49
        ratio = 62 / 124
        expected = 30.0 + (68.0 - 30.0) * ratio
        assert overrides["ctl"] == pytest.approx(expected, abs=0.5)

    @pytest.mark.asyncio
    async def test_race_beyond_goal_caps_at_target(self):
        """Race AFTER the planned goal date → ratio clamped at 1.0 → projected
        approaches target (subject to CTL_PROJECTION_RATIO_CAP if aggressive).

        Realistic ramp here (current=40, target=68) to isolate ratio-clamp
        behavior from the cap-engagement test above.
        """
        today = date(2026, 5, 11)
        race_dt = date(2026, 10, 31)  # 173 days out
        goal_dt = date(2026, 9, 12)  # 124 days — race past goal
        with (
            patch.object(race_predict, "local_today", return_value=today),
            patch.object(race_predict.Wellness, "get", AsyncMock(return_value=self._wellness(ctl=40.0))),
            patch.object(
                race_predict.AthleteGoal, "get_by_category", AsyncMock(return_value=self._goal(goal_dt, 68.0))
            ),
            patch.object(race_predict.FitnessProjection, "get", AsyncMock(return_value=None)),
        ):
            overrides = await race_predict._mode2_overrides(user_id=1, race_date=race_dt.isoformat())
        # Ratio capped at 1.0 → projected = ctl_target exactly (68/40 = 1.7 < cap)
        assert overrides["ctl"] == pytest.approx(68.0)

    @pytest.mark.asyncio
    async def test_no_wellness_returns_none(self):
        """No current Wellness row → can't anchor → cold-start (None)."""
        today = date(2026, 5, 11)
        with (
            patch.object(race_predict, "local_today", return_value=today),
            patch.object(race_predict.Wellness, "get", AsyncMock(return_value=None)),
            patch.object(
                race_predict.AthleteGoal, "get_by_category", AsyncMock(return_value=self._goal(date(2026, 9, 15)))
            ),
        ):
            assert await race_predict._mode2_overrides(user_id=1, race_date="2026-09-15") is None

    @pytest.mark.asyncio
    async def test_no_goal_returns_none(self):
        """No RACE_A goal → no target to extrapolate to → cold-start."""
        today = date(2026, 5, 11)
        with (
            patch.object(race_predict, "local_today", return_value=today),
            patch.object(race_predict.Wellness, "get", AsyncMock(return_value=self._wellness())),
            patch.object(race_predict.AthleteGoal, "get_by_category", AsyncMock(return_value=None)),
        ):
            assert await race_predict._mode2_overrides(user_id=1, race_date="2026-09-15") is None

    @pytest.mark.asyncio
    async def test_goal_without_ctl_target_returns_none(self):
        """Goal exists but no ctl_target set → can't extrapolate."""
        today = date(2026, 5, 11)
        goal_dt = date(2026, 9, 15)
        with (
            patch.object(race_predict, "local_today", return_value=today),
            patch.object(race_predict.Wellness, "get", AsyncMock(return_value=self._wellness())),
            patch.object(
                race_predict.AthleteGoal,
                "get_by_category",
                AsyncMock(return_value=self._goal(goal_dt, ctl_target=None)),
            ),
        ):
            assert await race_predict._mode2_overrides(user_id=1, race_date="2026-09-15") is None

    @pytest.mark.asyncio
    async def test_goal_in_past_returns_none(self):
        """Goal date already passed → no positive ratio possible → cold-start."""
        today = date(2026, 5, 11)
        past_goal = date(2026, 4, 1)  # already passed
        with (
            patch.object(race_predict, "local_today", return_value=today),
            patch.object(race_predict.Wellness, "get", AsyncMock(return_value=self._wellness())),
            patch.object(race_predict.AthleteGoal, "get_by_category", AsyncMock(return_value=self._goal(past_goal))),
        ):
            assert await race_predict._mode2_overrides(user_id=1, race_date="2026-09-15") is None

    @pytest.mark.asyncio
    async def test_eftp_from_projection_when_available(self):
        """If FitnessProjection has eFTP data → include in overrides.

        eFTP-from-Intervals stays trustworthy even when ctl-decay is wrong:
        Intervals computes eFTP independently of training-load decay.
        """
        today = date(2026, 5, 11)
        goal_dt = date(2026, 9, 15)
        projection = MagicMock()
        projection.sport_info_by_type = MagicMock(return_value=225.0)  # eftp for Ride
        with (
            patch.object(race_predict, "local_today", return_value=today),
            patch.object(race_predict.Wellness, "get", AsyncMock(return_value=self._wellness())),
            patch.object(race_predict.AthleteGoal, "get_by_category", AsyncMock(return_value=self._goal(goal_dt))),
            patch.object(race_predict.FitnessProjection, "get", AsyncMock(return_value=projection)),
        ):
            overrides = await race_predict._mode2_overrides(user_id=1, race_date="2026-09-15")
        assert overrides["current_eftp"] == 225.0

    @pytest.mark.asyncio
    async def test_no_projection_still_returns_overrides(self):
        """FitnessProjection.get returns None → eFTP absent but ctl/atl/_ctl_ratio still set.

        Regression guard: prior version returned None entirely if projection missing.
        Now the linear extrapolation drives the result independently.
        """
        today = date(2026, 5, 11)
        goal_dt = date(2026, 9, 15)
        with (
            patch.object(race_predict, "local_today", return_value=today),
            patch.object(race_predict.Wellness, "get", AsyncMock(return_value=self._wellness())),
            patch.object(race_predict.AthleteGoal, "get_by_category", AsyncMock(return_value=self._goal(goal_dt))),
            patch.object(race_predict.FitnessProjection, "get", AsyncMock(return_value=None)),
        ):
            overrides = await race_predict._mode2_overrides(user_id=1, race_date="2026-09-15")
        assert overrides is not None
        assert "current_eftp" not in overrides
        assert overrides["ctl"] > 0  # linear extrapolation still produces ctl
