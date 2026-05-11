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


class ModelNotTrained(Exception):
    """Raised on Mode 1/2 call when the discipline's .joblib is absent."""


def _model_path(user_id: int, discipline: str) -> Path:
    return MODELS_DIR / f"race_{user_id}_{discipline.lower()}.joblib"


def _load_model(user_id: int, discipline: str) -> dict[str, Any]:
    import joblib

    path = _model_path(user_id, discipline)
    if not path.exists():
        raise ModelNotTrained(f"No trained model at {path}")
    return joblib.load(path)


# ---------------------------------------------------------------------------
# Mode 1/2 state assembly
# ---------------------------------------------------------------------------


def _mode2_overrides(user_id: int, race_date: str) -> dict | None:
    """Read FitnessProjection at race_date → CTL/ATL + per-sport eFTP overrides.

    Returns None if no projection row exists (cold-start / no Premium Intervals).
    Per-sport CTL is **scaled proportionally** (spec §8.1) — webhook sportInfo
    doesn't carry per-sport CTL, only eFTP/wPrime/pMax.
    """
    projection = FitnessProjection.get(user_id, race_date)
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
    today_w = Wellness.get(user_id, local_today())
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

    duration_sec = _duration_sec(discipline, pred, distance_m)
    out: dict[str, Any] = {
        "pred": round(pred, 2),
        "ci_low": round(ci_low, 2),
        "ci_high": round(ci_high, 2),
    }
    if duration_sec is not None:
        out["total_sec"] = int(duration_sec)
        # CI bounds may be non-positive (very wide residual distribution + small pred)
        # → `_duration_sec` returns None. Omit the field rather than coerce to 0,
        # which would render as «finish 00:00» downstream.
        ci_low_sec = _duration_sec(discipline, ci_low, distance_m)
        ci_high_sec = _duration_sec(discipline, ci_high, distance_m)
        if ci_low_sec is not None:
            out["total_sec_ci_low"] = int(ci_low_sec)
        if ci_high_sec is not None:
            out["total_sec_ci_high"] = int(ci_high_sec)
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


def predict_splits_with_ci(
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

    Returns §9.2 envelope. Missing distances → discipline pruned with warning.
    Cold-start models → returned in ``not_available`` so caller can render
    «Run модель не обучена» without faking output.
    """
    _race_iso = race_date if isinstance(race_date, str) else race_date.isoformat()
    today = local_today()
    target_dt = date.fromisoformat(_race_iso)
    days_to_race = (target_dt - today).days

    overrides: dict | None = None
    inflation = 1.0
    if mode == "race_day":
        overrides = _mode2_overrides(user_id, _race_iso)
        inflation = max(1.0, math.sqrt(max(days_to_race, 0) / INFLATION_DAYS_BASE))

    splits: dict[str, Any] = {}
    not_available: list[str] = []
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

    if mode == "race_day" and overrides is None:
        warnings.append("no_fitness_projection for race_date — Mode 2 fell back to Mode 1 state")

    envelope: dict[str, Any] = {
        "mode": mode,
        "race_date": _race_iso,
        "days_to_race": days_to_race,
        "splits": splits,
        "not_available": not_available,
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if mode == "race_day" and overrides:
        envelope["projected_ctl"] = overrides.get("ctl")
        envelope["projected_atl"] = overrides.get("atl")
        envelope["inflation"] = round(inflation, 3)
    return envelope
