"""Training Progression Model — feature extraction, training, SHAP analysis.

Phase 2: Ride only (Run EF too noisy, see POC results in TRAINING_PROGRESSION_SPEC.md).
Target: Δ EF (weekly efficiency factor change over next 4 weeks).
"""

import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from data.db.common import get_sync_session

logger = logging.getLogger(__name__)

MODELS_DIR = Path("static/models")
LOOKBACK_WEEKS = 8
TARGET_WEEKS = 4
MIN_EXAMPLES = 10


def _fetch_data(user_id: int) -> dict:
    """Fetch activities + wellness for feature extraction."""
    with get_sync_session() as s:
        activities = pd.read_sql(
            text(
                """
                SELECT a.start_date_local as date, a.type as sport,
                       a.moving_time, a.icu_training_load as tss, a.average_hr,
                       ad.hr_zone_times, ad.efficiency_factor as ef,
                       ad.decoupling, ad.variability_index as vi
                FROM activities a
                JOIN activity_details ad ON ad.activity_id = a.id
                WHERE a.user_id = :uid AND ad.hr_zone_times IS NOT NULL
                ORDER BY a.start_date_local
            """
            ),
            s.connection(),
            params={"uid": user_id},
        )
        wellness = pd.read_sql(
            text(
                """
                SELECT date, ctl, atl, hrv, resting_hr, sleep_score, recovery_score
                FROM wellness WHERE user_id = :uid ORDER BY date
            """
            ),
            s.connection(),
            params={"uid": user_id},
        )
    return {"activities": activities, "wellness": wellness}


def _compute_weekly_ef(activities: pd.DataFrame, sport: str) -> pd.DataFrame:
    """Compute weekly mean EF for a sport."""
    sport_acts = activities[(activities["sport"] == sport) & activities["ef"].notna() & (activities["ef"] > 0)].copy()
    if sport_acts.empty:
        return pd.DataFrame()
    sport_acts["date"] = pd.to_datetime(sport_acts["date"])
    sport_acts["week_start"] = sport_acts["date"] - pd.to_timedelta(sport_acts["date"].dt.dayofweek, unit="D")
    weekly = sport_acts.groupby("week_start").agg(ef_mean=("ef", "mean"), ef_count=("ef", "count")).reset_index()
    return weekly[weekly["ef_count"] >= 1].sort_values("week_start").reset_index(drop=True)


def _extract_features(activities: pd.DataFrame, wellness: pd.DataFrame, end_date, window_days: int, sport: str) -> dict:
    """Extract training features for a window."""
    start_str = str(end_date - timedelta(days=window_days))[:10]
    end_str = str(end_date)[:10]
    acts = activities[
        (activities["date"] >= start_str) & (activities["date"] <= end_str) & (activities["sport"] == sport)
    ]
    all_acts = activities[(activities["date"] >= start_str) & (activities["date"] <= end_str)]
    well = wellness[(wellness["date"] >= start_str) & (wellness["date"] <= end_str)]
    weeks = max(1, window_days / 7)

    f = {}
    # Volume
    f["n_sessions"] = len(acts)
    f["sessions_per_week"] = len(acts) / weeks
    f["total_tss"] = acts["tss"].sum() if len(acts) > 0 else 0
    f["weekly_tss"] = f["total_tss"] / weeks
    f["total_hours"] = acts["moving_time"].sum() / 3600 if len(acts) > 0 else 0
    f["weekly_hours"] = f["total_hours"] / weeks
    f["longest_min"] = acts["moving_time"].max() / 60 if len(acts) > 0 else 0
    f["total_sessions_all"] = len(all_acts)
    f["total_tss_all"] = all_acts["tss"].sum() if len(all_acts) > 0 else 0

    # Polarization
    low, mid, high = 0, 0, 0
    for _, row in acts.iterrows():
        zt = row.get("hr_zone_times")
        if not zt or not isinstance(zt, list):
            continue
        for i, secs in enumerate(zt):
            v = secs or 0
            if i < 2:
                low += v
            elif i == 2:
                mid += v
            else:
                high += v
    zt_total = low + mid + high
    f["low_pct"] = round(low / zt_total * 100, 1) if zt_total > 0 else 0
    f["mid_pct"] = round(mid / zt_total * 100, 1) if zt_total > 0 else 0
    f["high_pct"] = round(100 - f["low_pct"] - f["mid_pct"], 1) if zt_total > 0 else 0

    # Efficiency
    ef_vals = acts["ef"].dropna()
    f["ef_mean"] = ef_vals.mean() if len(ef_vals) > 0 else 0
    f["ef_std"] = ef_vals.std() if len(ef_vals) > 1 else 0
    dec = acts["decoupling"].dropna()
    f["decoupling_median"] = dec.median() if len(dec) > 0 else 0

    # Wellness
    if len(well) > 0:
        f["ctl_mean"] = well["ctl"].mean()
        f["ctl_max"] = well["ctl"].max()
        f["ctl_delta"] = well["ctl"].iloc[-1] - well["ctl"].iloc[0] if len(well) > 1 else 0
        tsb = well["ctl"] - well["atl"]
        f["tsb_mean"] = tsb.mean()
        f["tsb_min"] = tsb.min()
        f["hrv_mean"] = well["hrv"].mean()
        f["rhr_mean"] = well["resting_hr"].mean()
        f["sleep_mean"] = well["sleep_score"].mean()
        f["recovery_mean"] = well["recovery_score"].mean()
        f["recovery_below_40"] = (well["recovery_score"] < 40).sum()
    else:
        for k in [
            "ctl_mean",
            "ctl_max",
            "ctl_delta",
            "tsb_mean",
            "tsb_min",
            "hrv_mean",
            "rhr_mean",
            "sleep_mean",
            "recovery_mean",
            "recovery_below_40",
        ]:
            f[k] = 0
    return f


def build_dataset(user_id: int, sport: str = "Ride") -> pd.DataFrame:
    """Build training dataset for a user+sport."""
    data = _fetch_data(user_id)
    data["activities"]["date"] = data["activities"]["date"].astype(str)
    data["wellness"]["date"] = data["wellness"]["date"].astype(str)

    weekly_ef = _compute_weekly_ef(data["activities"], sport)
    needed = LOOKBACK_WEEKS + TARGET_WEEKS + 1
    if len(weekly_ef) < needed:
        logger.info("Not enough weekly EF data for %s: %d < %d", sport, len(weekly_ef), needed)
        return pd.DataFrame()

    rows = []
    for i in range(LOOKBACK_WEEKS, len(weekly_ef) - TARGET_WEEKS):
        current = weekly_ef.iloc[i]
        future_ef = weekly_ef.iloc[i + 1 : i + 1 + TARGET_WEEKS]["ef_mean"].mean()
        delta_ef = future_ef - current["ef_mean"]
        features = _extract_features(
            data["activities"],
            data["wellness"],
            end_date=current["week_start"],
            window_days=LOOKBACK_WEEKS * 7,
            sport=sport,
        )
        features["target"] = delta_ef
        features["week"] = str(current["week_start"])[:10]
        features["current_ef"] = current["ef_mean"]
        rows.append(features)

    return pd.DataFrame(rows)


def train_model(user_id: int, sport: str = "Ride") -> dict | None:
    """Train progression model and save to disk. Returns metrics or None.

    Heavy imports (xgboost, shap, sklearn, joblib) are deferred to this function
    so they don't slow down API/bot startup — only the weekly Dramatiq actor pays the cost.
    """
    import joblib
    import shap
    from sklearn.metrics import mean_absolute_error, r2_score
    from sklearn.model_selection import TimeSeriesSplit
    from xgboost import XGBRegressor

    df = build_dataset(user_id, sport)
    if len(df) < MIN_EXAMPLES:
        logger.info("Not enough examples for progression model: %d < %d", len(df), MIN_EXAMPLES)
        return None

    feature_cols = [c for c in df.columns if c not in ("target", "week", "current_ef")]
    X = df[feature_cols].fillna(0)
    y = df["target"]

    # Walk-forward CV
    n_splits = min(5, len(df) - 2)
    tscv = TimeSeriesSplit(n_splits=n_splits)
    predictions = np.full(len(y), np.nan)

    for train_idx, test_idx in tscv.split(X):
        model = XGBRegressor(
            n_estimators=100,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=0,
        )
        model.fit(X.iloc[train_idx], y.iloc[train_idx])
        predictions[test_idx] = model.predict(X.iloc[test_idx])

    valid = ~np.isnan(predictions)
    mae = mean_absolute_error(y[valid], predictions[valid])
    r2 = r2_score(y[valid], predictions[valid])
    corr = float(np.corrcoef(y[valid], predictions[valid])[0, 1])

    # Train final model
    final_model = XGBRegressor(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbosity=0,
    )
    final_model.fit(X, y)

    # SHAP
    shap_global = {}
    try:
        explainer = shap.TreeExplainer(final_model)
        shap_values = explainer.shap_values(X)
        mean_abs = np.abs(shap_values).mean(axis=0)
        sorted_idx = np.argsort(mean_abs)[::-1]

        shap_global = {
            "features": [
                {
                    "name": feature_cols[i],
                    "importance": round(float(mean_abs[i]), 6),
                    "direction": "positive" if np.mean(shap_values[:, i]) > 0 else "negative",
                }
                for i in sorted_idx[:10]
            ],
        }

        # Last week waterfall
        last_shap = shap_values[-1]
        last_sorted = np.argsort(np.abs(last_shap))[::-1][:5]
        shap_global["latest_drivers"] = [
            {
                "name": feature_cols[i],
                "shap": round(float(last_shap[i]), 6),
                "value": round(float(X.iloc[-1][feature_cols[i]]), 2),
            }
            for i in last_sorted
            if abs(last_shap[i]) > 0.0001
        ]
    except Exception:
        logger.warning("SHAP analysis failed", exc_info=True)

    # Save model to disk
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = date.today().isoformat()
    model_filename = f"{user_id}_{sport.lower()}_{timestamp}.joblib"
    model_path = str(MODELS_DIR / model_filename)
    joblib.dump(final_model, model_path)

    return {
        "user_id": user_id,
        "sport": sport,
        "n_examples": len(df),
        "mae": round(mae, 6),
        "r2": round(r2, 4),
        "correlation": round(corr, 4),
        "model_path": model_path,
        "feature_cols": feature_cols,
        "shap_global": shap_global,
    }


def get_latest_analysis(user_id: int, sport: str = "Ride") -> dict | None:
    """Get latest progression analysis from DB."""
    with get_sync_session() as s:
        row = s.execute(
            text(
                """
                SELECT sport, trained_at, n_examples, mae, r2, model_path, shap_global_json
                FROM progression_model_runs
                WHERE user_id = :uid AND sport = :sport
                ORDER BY trained_at DESC LIMIT 1
            """
            ),
            {"uid": user_id, "sport": sport},
        ).fetchone()

    if not row:
        return None

    shap_data = row[6] if row[6] else {}
    return {
        "sport": row[0],
        "trained_at": str(row[1]),
        "n_examples": row[2],
        "mae": row[3],
        "r2": row[4],
        "shap": shap_data,
    }
