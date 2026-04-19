"""Training Progression POC v2 — Continuous EF target.

Instead of discrete PB events (too few), use weekly Δ EF as continuous target.
Each week = one training example. 2.5 years ≈ 130 examples.

Target: EF_mean(next 4 weeks) - EF_mean(current week)
Features: polarization, volume, wellness, load — computed over previous 4-8 weeks.

Run: poetry run python docs/knowledge/progression_poc_v2.py
"""

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd
from sqlalchemy import text

from data.db.common import get_sync_session


def fetch_data(user_id: int = 1) -> dict:
    """Fetch all data needed for feature extraction."""
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
                SELECT date, ctl, atl, hrv, resting_hr, sleep_score,
                       recovery_score
                FROM wellness WHERE user_id = :uid
                ORDER BY date
            """
            ),
            s.connection(),
            params={"uid": user_id},
        )

    return {"activities": activities, "wellness": wellness}


def compute_weekly_ef(activities: pd.DataFrame, sport: str) -> pd.DataFrame:
    """Compute weekly mean EF for a sport."""
    sport_acts = activities[activities["sport"] == sport].copy()
    sport_acts = sport_acts[sport_acts["ef"].notna() & (sport_acts["ef"] > 0)]
    if sport_acts.empty:
        return pd.DataFrame()

    sport_acts["date"] = pd.to_datetime(sport_acts["date"])
    sport_acts["week"] = (
        sport_acts["date"].dt.isocalendar().year.astype(str)
        + "-W"
        + sport_acts["date"].dt.isocalendar().week.astype(str).str.zfill(2)
    )
    sport_acts["week_start"] = sport_acts["date"] - pd.to_timedelta(sport_acts["date"].dt.dayofweek, unit="D")

    weekly = (
        sport_acts.groupby("week_start")
        .agg(
            ef_mean=("ef", "mean"),
            ef_count=("ef", "count"),
        )
        .reset_index()
    )
    weekly = weekly[weekly["ef_count"] >= 1]  # at least 1 session
    return weekly.sort_values("week_start").reset_index(drop=True)


def extract_window_features(
    activities: pd.DataFrame, wellness: pd.DataFrame, end_date, window_days: int, sport: str
) -> dict:
    """Extract features for a training window ending at end_date."""
    start = end_date - timedelta(days=window_days)
    start_str = str(start)[:10]
    end_str = str(end_date)[:10]

    # Filter activities
    acts = activities[
        (activities["date"] >= start_str) & (activities["date"] <= end_str) & (activities["sport"] == sport)
    ]
    all_acts = activities[(activities["date"] >= start_str) & (activities["date"] <= end_str)]
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

    # All-sport volume (cross-training)
    f["total_sessions_all"] = len(all_acts)
    f["total_tss_all"] = all_acts["tss"].sum() if len(all_acts) > 0 else 0

    # Polarization
    low_total, mid_total, high_total = 0, 0, 0
    for _, row in acts.iterrows():
        zt = row.get("hr_zone_times")
        if not zt or not isinstance(zt, list):
            continue
        for i, secs in enumerate(zt):
            s_val = secs or 0
            if i < 2:
                low_total += s_val
            elif i == 2:
                mid_total += s_val
            else:
                high_total += s_val
    zone_total = low_total + mid_total + high_total
    f["low_pct"] = round(low_total / zone_total * 100, 1) if zone_total > 0 else 0
    f["mid_pct"] = round(mid_total / zone_total * 100, 1) if zone_total > 0 else 0
    f["high_pct"] = round(100 - f["low_pct"] - f["mid_pct"], 1) if zone_total > 0 else 0

    # Efficiency trend within window
    ef_vals = acts["ef"].dropna()
    f["ef_mean"] = ef_vals.mean() if len(ef_vals) > 0 else 0
    f["ef_std"] = ef_vals.std() if len(ef_vals) > 1 else 0

    # Decoupling
    dec = acts["decoupling"].dropna()
    f["decoupling_median"] = dec.median() if len(dec) > 0 else 0
    f["decoupling_pct_red"] = (dec.abs() > 10).mean() * 100 if len(dec) > 0 else 0

    # Wellness
    well = wellness[(wellness["date"] >= start_str) & (wellness["date"] <= end_str)]
    if len(well) > 0:
        f["ctl_mean"] = well["ctl"].mean() if "ctl" in well else 0
        f["ctl_max"] = well["ctl"].max() if "ctl" in well else 0
        f["ctl_delta"] = (well["ctl"].iloc[-1] - well["ctl"].iloc[0]) if len(well) > 1 and "ctl" in well else 0
        tsb = well["ctl"] - well["atl"]
        f["tsb_mean"] = tsb.mean()
        f["tsb_min"] = tsb.min()
        f["hrv_mean"] = well["hrv"].mean() if "hrv" in well else 0
        f["rhr_mean"] = well["resting_hr"].mean() if "resting_hr" in well else 0
        f["sleep_mean"] = well["sleep_score"].mean() if "sleep_score" in well else 0
        f["recovery_mean"] = well["recovery_score"].mean() if "recovery_score" in well else 0
        f["recovery_below_40"] = (well["recovery_score"] < 40).sum() if "recovery_score" in well else 0
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


def build_ef_dataset(data: dict, sport: str, lookback_weeks: int = 8, target_weeks: int = 4) -> pd.DataFrame:
    """Build dataset: features from past N weeks → target = Δ EF over next M weeks."""
    weekly_ef = compute_weekly_ef(data["activities"], sport)
    if len(weekly_ef) < lookback_weeks + target_weeks + 1:
        print(f"  Not enough weekly EF data: {len(weekly_ef)} weeks (need {lookback_weeks + target_weeks + 1})")
        return pd.DataFrame()

    data["activities"]["date"] = data["activities"]["date"].astype(str)
    data["wellness"]["date"] = data["wellness"]["date"].astype(str)

    rows = []
    for i in range(lookback_weeks, len(weekly_ef) - target_weeks):
        current_week = weekly_ef.iloc[i]
        future_ef = weekly_ef.iloc[i + 1 : i + 1 + target_weeks]["ef_mean"].mean()
        current_ef = current_week["ef_mean"]
        delta_ef = future_ef - current_ef

        end_date = current_week["week_start"]
        features = extract_window_features(
            data["activities"],
            data["wellness"],
            end_date=end_date,
            window_days=lookback_weeks * 7,
            sport=sport,
        )
        features["target"] = delta_ef
        features["week"] = str(current_week["week_start"])[:10]
        features["current_ef"] = current_ef

        rows.append(features)

    return pd.DataFrame(rows)


def run_poc():
    print("=== Training Progression POC v2 — Continuous EF ===\n")

    print("1. Fetching data...")
    data = fetch_data(user_id=1)
    print(f"   Activities with zones: {len(data['activities'])}")
    print(f"   Wellness days: {len(data['wellness'])}")

    for sport in ["Run", "Ride"]:
        print(f"\n{'=' * 60}")
        print(f"2. Building EF dataset for {sport}...")

        weekly_ef = compute_weekly_ef(data["activities"], sport)
        print(f"   Weeks with EF data: {len(weekly_ef)}")
        if len(weekly_ef) > 0:
            print(f"   EF range: {weekly_ef['ef_mean'].min():.4f} — {weekly_ef['ef_mean'].max():.4f}")

        df = build_ef_dataset(data, sport)
        if df.empty:
            continue

        feature_cols = [c for c in df.columns if c not in ("target", "week", "current_ef")]
        X = df[feature_cols].fillna(0)
        y = df["target"]

        print(f"   Dataset: {len(df)} examples, {len(feature_cols)} features")
        print(f"   Target Δ EF: mean={y.mean():.4f}, std={y.std():.4f}")

        print(f"\n3. Training XGBoost for {sport}...")
        from sklearn.metrics import mean_absolute_error, r2_score
        from sklearn.model_selection import TimeSeriesSplit
        from xgboost import XGBRegressor

        # Walk-forward CV
        n_splits = min(5, len(df) - 2)
        if n_splits < 2:
            print("   Not enough data for CV")
            continue

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

        valid_mask = ~np.isnan(predictions)
        if valid_mask.sum() < 3:
            print("   Not enough predictions for metrics")
            continue

        mae = mean_absolute_error(y[valid_mask], predictions[valid_mask])
        r2 = r2_score(y[valid_mask], predictions[valid_mask])
        corr = np.corrcoef(y[valid_mask], predictions[valid_mask])[0, 1]

        print(f"   Walk-forward CV ({n_splits} folds):")
        print(f"     MAE  = {mae:.4f}")
        print(f"     R²   = {r2:.3f}")
        print(f"     Corr = {corr:.3f}")

        if r2 >= 0.3:
            print(f"   ✅ R² >= 0.3 — model shows signal!")
        elif corr >= 0.3:
            print(f"   ✅ Correlation >= 0.3 — ranking signal present!")
        else:
            print(f"   ⚠️  Weak signal — R²={r2:.3f}, Corr={corr:.3f}")

        # SHAP
        print(f"\n4. SHAP analysis for {sport}...")
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

        try:
            import shap

            explainer = shap.TreeExplainer(final_model)
            shap_values = explainer.shap_values(X)

            print("\n   Top features (by mean |SHAP|):")
            mean_abs_shap = np.abs(shap_values).mean(axis=0)
            sorted_idx = np.argsort(mean_abs_shap)[::-1]
            for i in sorted_idx[:10]:
                direction = "↑" if np.mean(shap_values[:, i]) > 0 else "↓"
                print(f"     {direction} {feature_cols[i]:30s} |SHAP|={mean_abs_shap[i]:.4f}")

            # Recent weeks analysis
            print(f"\n   Last 4 weeks — what's driving EF trend:")
            for offset in range(-4, 0):
                idx = len(X) + offset
                if idx < 0:
                    continue
                top_feat = np.argsort(np.abs(shap_values[idx]))[::-1][:3]
                drivers = []
                for fi in top_feat:
                    if abs(shap_values[idx][fi]) > 0.0001:
                        sign = "+" if shap_values[idx][fi] > 0 else "-"
                        drivers.append(f"{sign}{feature_cols[fi]}={X.iloc[idx][feature_cols[fi]]:.1f}")
                week_str = df.iloc[idx]["week"]
                actual = y.iloc[idx]
                drivers_str = ", ".join(drivers) if drivers else "no strong drivers"
                print(f"     {week_str}: Δ EF={actual:+.4f} | {drivers_str}")

        except ImportError:
            print("   shap not installed")

    print(f"\n{'=' * 60}")
    print("POC v2 complete.")


if __name__ == "__main__":
    run_poc()
