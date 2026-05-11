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
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from data.ml.race_features import DISCIPLINE_TO_SPORT, MIN_EXAMPLES, InsufficientDataError, build_dataset

logger = logging.getLogger(__name__)

MODELS_DIR = Path("static/models")
BOOTSTRAP_ROUNDS = 500
RANDOM_STATE = 42


def _feature_columns(df: pd.DataFrame) -> list[str]:
    """Columns we feed to the regressor — everything except target / metadata."""
    return [c for c in df.columns if c not in ("target", "activity_id", "date")]


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
            "metrics": {"mae": mae, "r2": r2, "n_examples": int(len(df))},
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
