"""Race-projection model training — XGBRegressor + bootstrap residuals.

One model per discipline (Run/Ride/Swim), saved to
``static/models/race_{user_id}_{discipline}.joblib`` with bootstrap residuals
for prediction-interval construction (§10.1 in spec).

Heavy imports (xgboost / sklearn / shap / joblib) are deferred to ``train_user_model``
so import-time cost is paid only by the weekly retrain actor / CLI, not by
every API/bot process that touches this module's namespace.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

from data.db import Activity, Race, get_sync_session
from data.ml.bias_constants import (
    BIAS_FIT_HORIZONS,
    MIN_RACES_FOR_PER_ATHLETE_BIAS,
    POOL_BIAS_INTERCEPT,
    POOL_BIAS_SLOPE,
)
from data.ml.race_features import (
    DISCIPLINE_TO_SPORT,
    MIN_EXAMPLES,
    InsufficientDataError,
    build_dataset,
    build_inference_features,
)

logger = logging.getLogger(__name__)

MODELS_DIR = Path("static/models")
BOOTSTRAP_ROUNDS = 500
RANDOM_STATE = 42


def _feature_columns(df: pd.DataFrame) -> list[str]:
    """Columns we feed to the regressor — everything except target / metadata."""
    return [c for c in df.columns if c not in ("target", "activity_id", "date")]


def _fit_bias_model(
    user_id: int,
    discipline: str,
    final_model,
    feature_cols: list[str],
    target_hr: float,
) -> tuple[float, float, int, str]:
    """Phase 2.0β2 — Per-athlete bias correction fit (spec §10.5.6).

    Mini-simulation across athlete's historical races: for each (race × horizon),
    build inference features as-of `race_date - days_out`, run trained model,
    collect residual = pred - actual. Fit `bias(d) = a + b * d` via OLS.

    Cold-start fallback (n_races < :data:`MIN_RACES_FOR_PER_ATHLETE_BIAS`):
    returns pool constants from ``data/ml/bias_constants.py`` — honest "we don't
    have enough personal data, using cross-athlete defaults" path.

    Returns (intercept, slope, n_races_fit, method). `method` ∈ {
    "per_athlete_linear", "pool_fallback", "out_of_scope"}.
    """
    # Phase 2.0β2 scope: Run only. Ride/Swim need their own simulation harness
    # (penalty table different, fewer races, smaller signal). Out-of-scope
    # surface uses pool constants for backwards-compat — but caller writes
    # n_races_fit=0 and method=out_of_scope so consumers can tell.
    if discipline.lower() != "run":
        return POOL_BIAS_INTERCEPT, POOL_BIAS_SLOPE, 0, "out_of_scope"

    with get_sync_session() as session:
        rows = session.execute(
            select(Race, Activity.start_date_local)
            .join(Activity, Activity.id == Race.activity_id)
            .where(
                Race.user_id == user_id,
                Activity.type == "Run",
                Race.avg_pace_sec_km.is_not(None),
                Race.distance_m.is_not(None),
            )
        ).all()

    valid_races = []
    for race, race_date_str in rows:
        try:
            race_date = date.fromisoformat(race_date_str)
        except (TypeError, ValueError):
            continue
        valid_races.append((race, race_date))

    n_races = len(valid_races)
    if n_races < MIN_RACES_FOR_PER_ATHLETE_BIAS:
        logger.info(
            "user_id=%d: bias fit cold-start (n_races=%d < %d) — using pool constants",
            user_id,
            n_races,
            MIN_RACES_FOR_PER_ATHLETE_BIAS,
        )
        return POOL_BIAS_INTERCEPT, POOL_BIAS_SLOPE, n_races, "pool_fallback"

    days_out_arr: list[int] = []
    residuals_arr: list[float] = []
    for race, race_date in valid_races:
        for d in BIAS_FIT_HORIZONS:
            target_date = race_date - timedelta(days=d)
            try:
                features = build_inference_features(
                    user_id=user_id,
                    discipline=discipline,
                    target_date=target_date,
                    target_hr=target_hr,
                    distance_m=race.distance_m,
                )
            except Exception:
                continue
            row = {col: features.get(col, float("nan")) for col in feature_cols}
            X_row = pd.DataFrame([row], columns=feature_cols)
            try:
                pred = float(final_model.predict(X_row)[0])
            except Exception:
                continue
            residual = pred - float(race.avg_pace_sec_km)
            days_out_arr.append(d)
            residuals_arr.append(residual)

    # Safety floor — `polyfit` on too few points overfits. ~10 points = 2 races × 5 horizons.
    if len(residuals_arr) < 10:
        logger.warning(
            "user_id=%d: bias fit failed (only %d simulation points) — pool fallback",
            user_id,
            len(residuals_arr),
        )
        return POOL_BIAS_INTERCEPT, POOL_BIAS_SLOPE, n_races, "pool_fallback"

    coeffs = np.polyfit(np.array(days_out_arr), np.array(residuals_arr), 1)
    slope = float(coeffs[0])
    intercept = float(coeffs[1])
    logger.info(
        "user_id=%d: bias fit per-athlete: intercept=%.3f, slope=%.4f sec/km/day (n_races=%d, n_points=%d)",
        user_id,
        intercept,
        slope,
        n_races,
        len(residuals_arr),
    )
    return intercept, slope, n_races, "per_athlete_linear"


def train_user_model(user_id: int, discipline: str) -> dict:
    """Train one model + bootstrap residuals; persist joblib. Returns metrics.

    Raises :class:`InsufficientDataError` if fewer than :data:`MIN_EXAMPLES`
    qualifying activities exist — caller (actor / CLI) is expected to log+skip.
    """
    import joblib
    from sklearn.metrics import mean_absolute_error, r2_score
    from sklearn.model_selection import TimeSeriesSplit
    from xgboost import XGBRegressor

    if discipline.lower() not in DISCIPLINE_TO_SPORT:
        raise ValueError(f"Unknown discipline {discipline!r}; expected one of {list(DISCIPLINE_TO_SPORT)}")

    df = build_dataset(user_id, discipline)
    if len(df) < MIN_EXAMPLES:
        raise InsufficientDataError(
            f"Not enough examples for race_{discipline} model (user_id={user_id}): " f"{len(df)} < {MIN_EXAMPLES}"
        )

    feature_cols = _feature_columns(df)
    X = df[feature_cols].copy()
    y = df["target"].astype(float)

    # Walk-forward CV for honest out-of-sample metrics
    n_splits = min(5, len(df) - 2)
    tscv = TimeSeriesSplit(n_splits=n_splits)
    predictions = np.full(len(y), np.nan)

    for train_idx, test_idx in tscv.split(X):
        model = XGBRegressor(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=RANDOM_STATE,
            verbosity=0,
        )
        model.fit(X.iloc[train_idx], y.iloc[train_idx])
        predictions[test_idx] = model.predict(X.iloc[test_idx])

    valid_mask = ~np.isnan(predictions)
    mae = float(mean_absolute_error(y[valid_mask], predictions[valid_mask]))
    r2 = float(r2_score(y[valid_mask], predictions[valid_mask]))

    # Phase 1 issue #359 (b): record p90 of the discipline's CTL feature in the
    # training set. At predict-time, Mode 2 scales `ctl_<disc>` by the global
    # CTL ratio (projected / current); if the scaled value exceeds p90, the
    # XGBoost tree leaf becomes extrapolation territory (trees clip to nearest
    # observed leaf). Warning surfaces to the caller so Claude can tell the
    # athlete «model n=400 saw ctl_run 15-45, projecting to 66 is out of sample».
    ctl_key = f"ctl_{discipline.lower()}"
    ctl_feature_p90: float | None = None
    if ctl_key in df.columns:
        q = df[ctl_key].dropna().quantile(0.90)
        if pd.notna(q):
            ctl_feature_p90 = float(q)

    # Final model on all data
    final_model = XGBRegressor(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=RANDOM_STATE,
        verbosity=0,
    )
    final_model.fit(X, y)

    # Bootstrap residuals — resample (X, y) with replacement, fit a quick model,
    # record per-resample residuals against the held-out original sample. The
    # union of residuals is the empirical prediction-interval distribution.
    rng = np.random.default_rng(RANDOM_STATE)
    n = len(X)
    residuals_all: list[float] = []
    for _ in range(BOOTSTRAP_ROUNDS):
        idx = rng.integers(0, n, n)
        oob_mask = np.setdiff1d(np.arange(n), np.unique(idx))
        if len(oob_mask) == 0:
            continue
        m = XGBRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            random_state=RANDOM_STATE,
            verbosity=0,
        )
        m.fit(X.iloc[idx], y.iloc[idx])
        pred = m.predict(X.iloc[oob_mask])
        residuals_all.extend((y.iloc[oob_mask].to_numpy() - pred).tolist())

    residuals = np.array(residuals_all, dtype=float)

    # Phase 2.0β2 — per-athlete bias model fit. Mini-simulation across user's
    # historical races × horizons. Cold-start (n_races < 5) falls back to pool
    # constants from `bias_constants.py`. Out-of-scope disciplines (Ride/Swim)
    # also use pool constants for backwards-compat, marked as `out_of_scope`.
    # target_hr=150 hardcoded — bias fit is target_hr-invariant in practice
    # (same value passed across all simulation points; differences cancel out
    # in the linear residual fit). Future calibration may switch to per-athlete
    # `lthr_run × 0.88` heuristic; until then 150 bpm is a stable placeholder.
    # **Wrapped in try/except**: bias fit is a downstream nice-to-have; if it
    # raises (DB hiccup mid-simulation, polyfit edge case, corrupt Race rows),
    # we MUST NOT discard the trained main model. Fall through to pool fallback +
    # sentry capture so retrain still ships and ops can investigate.
    try:
        bias_intercept, bias_slope, bias_n_races_fit, bias_fit_method = _fit_bias_model(
            user_id=user_id,
            discipline=discipline,
            final_model=final_model,
            feature_cols=feature_cols,
            target_hr=150.0,
        )
    except Exception:
        import sentry_sdk

        logger.exception(
            "user_id=%d %s: bias fit failed unexpectedly — pool fallback applied",
            user_id,
            discipline,
        )
        sentry_sdk.capture_exception()
        bias_intercept = POOL_BIAS_INTERCEPT
        bias_slope = POOL_BIAS_SLOPE
        bias_n_races_fit = 0
        bias_fit_method = "pool_fallback"

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODELS_DIR / f"race_{user_id}_{discipline.lower()}.joblib"
    joblib.dump(
        {
            "model": final_model,
            "residuals": residuals,
            "feature_names": feature_cols,
            "discipline": discipline.lower(),
            "user_id": user_id,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "metrics": {
                "mae": mae,
                "r2": r2,
                "n_examples": int(len(df)),
                "ctl_feature_p90": ctl_feature_p90,
                "bias_intercept": bias_intercept,
                "bias_slope": bias_slope,
                "bias_n_races_fit": bias_n_races_fit,
                "bias_fit_method": bias_fit_method,
            },
        },
        model_path,
    )

    logger.info(
        "Trained race_%s for user_id=%d: n=%d, MAE=%.3f, R²=%.3f → %s",
        discipline.lower(),
        user_id,
        len(df),
        mae,
        r2,
        model_path,
    )
    return {
        "user_id": user_id,
        "discipline": discipline.lower(),
        "n_examples": int(len(df)),
        "mae": mae,
        "r2": r2,
        "model_path": str(model_path),
    }
