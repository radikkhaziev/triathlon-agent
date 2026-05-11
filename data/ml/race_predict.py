"""Race-projection inference — predict_splits_with_ci.

Loads per-discipline `.joblib` model artefacts (model + residuals + feature_names)
and returns the §9.2 envelope (per-discipline pace/power + CI low/high + total).

Heavy imports (joblib, sklearn) deferred to runtime.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data.db.fitness_projection import FitnessProjection
from data.db.wellness import Wellness
from data.ml.race_features import DISCIPLINE_TO_SPORT, build_inference_features
from data.ml.race_train import MODELS_DIR
from tasks.dto import local_today

logger = logging.getLogger(__name__)

# CI percentile envelope (90% prediction interval — spec §10.1).
CI_LOW_PCT = 5
CI_HIGH_PCT = 95
# Mode 2 inflation reference window (§10.2) — 30 days = scale factor 1.0.
INFLATION_DAYS_BASE = 30

# Per-discipline (units, physiological-floor) so the envelope is self-describing
# and the CI clamp doesn't fall into negative-time territory on tiny-n models.
# Floor values are conservative lower bounds for any human athlete — anything
# below is unphysiological. Used to clip ``ci_low`` before duration conversion.
_DISCIPLINE_META = {
    "run": {"units": "sec_per_km", "floor": 150.0},  # 2:30/km absolute floor
    "swim": {"units": "sec_per_100m", "floor": 50.0},  # 0:50/100m (world-class)
    "ride": {"units": "watts", "floor": 50.0},  # 50W (recovery spin)
}


class ModelNotTrained(Exception):
    """Raised on Mode 1/2 call when the discipline's .joblib is absent."""


class ModelBelowAcceptance(Exception):
    """Raised when a loaded model's walk-forward CV metrics are too poor.

    Distinct from :class:`ModelNotTrained` so the envelope can communicate
    «модель ещё калибруется» (not «не существует»). Threshold values in
    :data:`_QUALITY_FLOORS` deliberately lenient — let through ranking-grade
    signal, reject only catastrophic models (R² < 0 / MAE order-of-magnitude
    above realistic).
    """


# Per-discipline acceptance floors for live inference. Set well below the
# spec §12.3 deploy bar (Run MAE 10 / R² 0.50, Ride 15 / 0.40, Swim 8 / 0.30)
# so that ranking-grade models still serve users — but catastrophe cases
# (R² ≪ 0 from broken data, MAE 2+ orders too high) are blocked from output.
# Calibrated against actual user 1/14/23/39/62 train results 2026-05-12.
_QUALITY_FLOORS = {
    "run": {"r2": 0.20, "max_mae": 40.0},  # sec/km
    "ride": {"r2": 0.20, "max_mae": 25.0},  # watts
    "swim": {"r2": 0.05, "max_mae": 15.0},  # sec/100m — Swim weaker by spec §12.3
}


def _model_path(user_id: int, discipline: str) -> Path:
    return MODELS_DIR / f"race_{user_id}_{discipline.lower()}.joblib"


def _load_model(user_id: int, discipline: str) -> dict[str, Any]:
    import joblib

    path = _model_path(user_id, discipline)
    if not path.exists():
        raise ModelNotTrained(f"No trained model at {path}")
    bundle = joblib.load(path)
    _enforce_quality_gate(bundle, discipline, user_id=user_id)
    return bundle


def _enforce_quality_gate(bundle: dict[str, Any], discipline: str, *, user_id: int) -> None:
    """Reject inference when CV metrics fall below per-discipline floors.

    Old bundles without a ``metrics`` field (pre-quality-gate artifacts) are
    let through unchanged — backwards compatibility with models trained before
    this guard landed. Future retrains write the metrics dict back into the
    bundle so the gate engages on next load.

    ``user_id`` is passed explicitly rather than read from the bundle: the
    caller (``_load_model``) already has it, and not every bundle carries the
    field (tests sometimes omit it). Keeping the error message authoritative.
    """
    metrics = bundle.get("metrics") or {}
    if not metrics:
        return  # legacy bundle — trust it
    floors = _QUALITY_FLOORS.get(discipline.lower())
    if floors is None:
        return  # unknown discipline — fail open, caller validates upstream

    r2 = metrics.get("r2")
    mae = metrics.get("mae")
    if r2 is None or mae is None:
        return  # incomplete metrics — trust the model
    # NaN-guard: `nan < anything` is False, so bare comparison would silently
    # admit a broken model with NaN metrics through the floor. Treat NaN the
    # same as missing — trust the model rather than reject (consistent with
    # the legacy-bundle path).
    if math.isnan(r2) or math.isnan(mae):
        return

    if r2 < floors["r2"] or mae > floors["max_mae"]:
        raise ModelBelowAcceptance(
            f"race_{discipline} model below acceptance floor for user_id={user_id}: "
            f"R²={r2:.3f} (floor {floors['r2']:.2f}), MAE={mae:.2f} (cap {floors['max_mae']:.1f}). "
            "Retrain with more / cleaner data, or wait for Phase 1.5 z1-filter."
        )


# ---------------------------------------------------------------------------
# Mode 1/2 state assembly
# ---------------------------------------------------------------------------


async def _mode2_overrides(user_id: int, race_date: str) -> dict | None:
    """Read FitnessProjection at race_date → CTL/ATL + per-sport eFTP overrides.

    Returns None if no projection row exists (cold-start / no Premium Intervals).
    Per-sport CTL is **scaled proportionally** (spec §8.1) — webhook sportInfo
    doesn't carry per-sport CTL, only eFTP/wPrime/pMax.

    ``async`` because the caller (`predict_splits_with_ci`) runs from an MCP
    tool inside an event loop; ``@dual`` ORM methods (`FitnessProjection.get`,
    `Wellness.get`) detect the running loop and return coroutines — we MUST
    await them. Calling sync-style returns a coroutine and crashes on
    attribute access.
    """
    projection = await FitnessProjection.get(user_id, race_date)
    if projection is None or projection.ctl is None:
        return None

    overrides: dict = {
        "ctl": float(projection.ctl) if projection.ctl is not None else None,
        "atl": float(projection.atl) if projection.atl is not None else None,
    }

    # Per-sport eFTP from sportInfo array (may be NULL for pre-migration rows)
    eftp_ride = projection.sport_info_by_type("Ride", "eftp")
    if eftp_ride is not None:
        overrides["current_eftp"] = eftp_ride

    # Proportional per-sport CTL scaling — current_per_sport × (proj_global / current_global).
    # `Wellness.get(today)` is the anchor; if today's row hasn't been synced yet
    # the ratio is skipped (caller still gets the global CTL/ATL overrides).
    today_w = await Wellness.get(user_id, local_today())
    if today_w and today_w.ctl and projection.ctl:
        overrides["_ctl_ratio"] = float(projection.ctl) / float(today_w.ctl)
    return overrides


# ---------------------------------------------------------------------------
# Public predict
# ---------------------------------------------------------------------------


def _predict_one(
    user_id: int,
    discipline: str,
    target_date: date,
    target_hr: float | None,
    distance_m: float,
    overrides: dict | None,
    inflation: float,
) -> dict:
    """Predict + CI for one discipline. Returns leg envelope per §7.1/§8.2."""
    bundle = _load_model(user_id, discipline)
    model = bundle["model"]
    residuals = bundle["residuals"]
    feature_names: list[str] = bundle["feature_names"]

    features = build_inference_features(
        user_id=user_id,
        discipline=discipline,
        target_date=target_date,
        target_hr=target_hr,
        distance_m=distance_m,
        overrides=overrides,
    )

    # Mode 2 per-sport CTL scaling
    if overrides and "_ctl_ratio" in overrides:
        ratio = overrides["_ctl_ratio"]
        for key in ("ctl_run", "ctl_ride", "ctl_swim"):
            if key in features and not (features[key] is None or math.isnan(features[key])):
                features[key] = features[key] * ratio

    # Build single-row DataFrame in the exact feature order used at training
    row = {col: features.get(col, float("nan")) for col in feature_names}
    X_row = pd.DataFrame([row], columns=feature_names)
    pred = float(model.predict(X_row)[0])

    ci_low_raw = float(np.percentile(residuals, CI_LOW_PCT)) * inflation
    ci_high_raw = float(np.percentile(residuals, CI_HIGH_PCT)) * inflation
    ci_low = pred + ci_low_raw
    ci_high = pred + ci_high_raw

    # Clip CI bounds to physiological floors. Bootstrap residuals on tiny-n
    # models (e.g. Swim n=44) can drag `ci_low` to negative seconds — meaningless
    # downstream. Floor is unit-aware: Run/Swim seconds-per-distance, Ride watts.
    meta = _DISCIPLINE_META.get(discipline.lower(), {})
    floor = meta.get("floor")
    if floor is not None:
        ci_low = max(ci_low, floor)
        ci_high = max(ci_high, floor)
        pred_clipped = max(pred, floor)
    else:
        pred_clipped = pred

    duration_sec = _duration_sec(discipline, pred_clipped, distance_m)
    out: dict[str, Any] = {
        "pred": round(pred_clipped, 2),
        "ci_low": round(ci_low, 2),
        "ci_high": round(ci_high, 2),
        "units": meta.get("units", "unknown"),
    }
    if duration_sec is not None:
        out["total_sec"] = int(duration_sec)
        ci_low_sec = _duration_sec(discipline, ci_low, distance_m)
        ci_high_sec = _duration_sec(discipline, ci_high, distance_m)
        if ci_low_sec is not None:
            out["total_sec_ci_low"] = int(ci_low_sec)
        if ci_high_sec is not None:
            out["total_sec_ci_high"] = int(ci_high_sec)
    elif discipline.lower() == "ride":
        # Ride is power-only in Phase 1 — duration isn't derivable from watts
        # without a speed sub-model. Emit explicit marker so callers don't
        # silently render an empty card.
        out["total_sec_unavailable"] = True
        out["total_sec_reason"] = "power_only_phase1"
    return out


def _duration_sec(discipline: str, pred: float, distance_m: float) -> int | None:
    """Convert per-leg prediction into total seconds.

    Run: pred = sec/km → total = pred × (distance / 1000).
    Swim: pred = sec/100m → total = pred × (distance / 100).
    Ride: pred = avg power (W) — duration not derivable from power alone;
    return None and let the caller fall back to an external estimator (or the
    Ride model's speed sub-model in Phase 2).
    """
    sport = DISCIPLINE_TO_SPORT.get(discipline.lower())
    if sport is None or distance_m <= 0 or pred <= 0:
        return None
    if sport == "Run":
        return int(pred * (distance_m / 1000.0))
    if sport == "Swim":
        return int(pred * (distance_m / 100.0))
    return None  # Ride — power-only Phase 1


async def predict_splits_with_ci(
    user_id: int,
    mode: str,
    race_date: date | str,
    *,
    race_distance_run_m: int | None = None,
    race_distance_ride_m: int | None = None,
    race_distance_swim_m: int | None = None,
    target_hr_run: int | None = None,
    target_hr_ride: int | None = None,
) -> dict:
    """Build per-discipline split predictions with CI.

    ``mode``: ``"today"`` (uses current state) or ``"race_day"`` (overrides
    CTL/ATL + per-sport eFTP from FitnessProjection on race_date and inflates
    residuals by sqrt(days_to_race / 30)).

    Returns an envelope shaped as:

    ::

        {
          "mode": "today" | "race_day",
          "race_date": "YYYY-MM-DD",
          "days_to_race": int,
          "splits": {
            "<run|ride|swim>": {
              "pred": float,                   # rounded, clipped to floor
              "ci_low": float, "ci_high": float,
              "units": "sec_per_km" | "sec_per_100m" | "watts",
              # When duration is derivable (Run, Swim):
              "total_sec": int,
              "total_sec_ci_low": int, "total_sec_ci_high": int,
              # When not derivable (Ride is power-only in Phase 1):
              "total_sec_unavailable": True,
              "total_sec_reason": "power_only_phase1",
            },
            ...
          },
          "not_available": ["<discipline>", ...],     # joblib missing
          "below_acceptance": ["<discipline>", ...],  # CV metrics under floor
          "warnings": [str, ...],                     # incl. "no_fitness_projection"
          "generated_at": ISO timestamp,
          # race_day mode with projection available adds:
          "projected_ctl": float, "projected_atl": float, "inflation": float,
        }

    ``available`` / ``reason`` are added by the MCP wrapper layer
    (``mcp_server/tools/race_projection.py``) — not emitted here.

    ``async`` to await ``_mode2_overrides`` (which calls ``@dual`` ORM
    methods that return coroutines under a running loop). Heavy ML work
    (`_predict_one`, `build_inference_features`) stays sync — pandas /
    joblib don't benefit from async, and ``get_sync_session`` works inside
    an event loop because it uses a separate sync DB driver.
    """
    _race_iso = race_date if isinstance(race_date, str) else race_date.isoformat()
    today = local_today()
    target_dt = date.fromisoformat(_race_iso)
    days_to_race = (target_dt - today).days

    overrides: dict | None = None
    inflation = 1.0
    if mode == "race_day":
        overrides = await _mode2_overrides(user_id, _race_iso)
        inflation = max(1.0, math.sqrt(max(days_to_race, 0) / INFLATION_DAYS_BASE))

    splits: dict[str, Any] = {}
    not_available: list[str] = []
    below_acceptance: list[str] = []
    warnings: list[str] = []

    inputs = [
        ("run", race_distance_run_m, target_hr_run),
        ("ride", race_distance_ride_m, target_hr_ride),
        ("swim", race_distance_swim_m, None),
    ]
    for discipline, distance, target_hr in inputs:
        if not distance:
            continue
        try:
            splits[discipline] = _predict_one(
                user_id=user_id,
                discipline=discipline,
                target_date=target_dt,
                target_hr=target_hr,
                distance_m=float(distance),
                overrides=overrides,
                inflation=inflation,
            )
        except ModelNotTrained:
            not_available.append(discipline)
            warnings.append(f"race_{discipline} model not trained — call `train-race-models` first")
        except ModelBelowAcceptance as e:
            # CV metrics under the per-discipline floor — don't fake confident
            # output; tell the caller the model is still calibrating.
            below_acceptance.append(discipline)
            logger.info("Quality gate blocked race_%s for user_id=%d: %s", discipline, user_id, e)
            warnings.append(f"race_{discipline} model below acceptance floor — needs more / cleaner data")

    if mode == "race_day" and overrides is None:
        warnings.append("no_fitness_projection for race_date — Mode 2 fell back to Mode 1 state")

    envelope: dict[str, Any] = {
        "mode": mode,
        "race_date": _race_iso,
        "days_to_race": days_to_race,
        "splits": splits,
        "not_available": not_available,
        "below_acceptance": below_acceptance,
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if mode == "race_day" and overrides:
        envelope["projected_ctl"] = overrides.get("ctl")
        envelope["projected_atl"] = overrides.get("atl")
        envelope["inflation"] = round(inflation, 3)
    return envelope
