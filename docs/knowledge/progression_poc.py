"""Training Progression POC — Phase 1.

Feature extraction + XGBoost + SHAP analysis for predicting threshold changes.
Run: poetry run python docs/knowledge/progression_poc.py

Data source: activity_hrv.hrvt1_hr changes as proxy for threshold improvements.
"""

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd
from sqlalchemy import text

from data.db.common import get_sync_session


def fetch_data(user_id: int = 1) -> dict:
    """Fetch all data needed for feature extraction."""
    with get_sync_session() as s:
        # HRVT1 threshold history (our target variable)
        hrvt = pd.read_sql(
            text(
                """
                SELECT a.start_date_local as date, a.type as sport,
                       ah.hrvt1_hr, ah.hrvt1_power
                FROM activity_hrv ah
                JOIN activities a ON a.id = ah.activity_id
                WHERE a.user_id = :uid AND ah.hrvt1_hr IS NOT NULL
                ORDER BY a.start_date_local
            """
            ),
            s.connection(),
            params={"uid": user_id},
        )

        # Activities with zone times
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

        # Wellness
        wellness = pd.read_sql(
            text(
                """
                SELECT date, ctl, atl, hrv, resting_hr, sleep_score,
                       recovery_score, recovery_category
                FROM wellness WHERE user_id = :uid
                ORDER BY date
            """
            ),
            s.connection(),
            params={"uid": user_id},
        )

    return {"hrvt": hrvt, "activities": activities, "wellness": wellness}


def find_pbs(hrvt: pd.DataFrame, sport: str) -> list[dict]:
    """Find threshold improvements (PBs) for a sport.

    For Run: lower HRVT1 HR at same effort = better (inverted).
    For Ride: higher HRVT1 power = better.
    """
    sport_data = hrvt[hrvt["sport"] == sport].copy()
    if len(sport_data) < 3:
        return []

    pbs = []
    if sport == "Run":
        # Lower HR threshold = improvement (heart more efficient)
        best = sport_data.iloc[0]["hrvt1_hr"]
        for _, row in sport_data.iterrows():
            if row["hrvt1_hr"] < best:
                pbs.append({"date": row["date"], "value": row["hrvt1_hr"], "delta": best - row["hrvt1_hr"]})
                best = row["hrvt1_hr"]
    elif sport == "Ride":
        # Higher power threshold = improvement
        best = sport_data.iloc[0]["hrvt1_power"]
        if best is None:
            return []
        for _, row in sport_data.iterrows():
            if row["hrvt1_power"] is not None and row["hrvt1_power"] > best:
                pbs.append({"date": row["date"], "value": row["hrvt1_power"], "delta": row["hrvt1_power"] - best})
                best = row["hrvt1_power"]

    return pbs


def extract_features(
    activities: pd.DataFrame, wellness: pd.DataFrame, start_date: str, end_date: str, sport: str
) -> dict:
    """Extract training features for a window [start_date, end_date]."""
    mask = (activities["date"] >= start_date) & (activities["date"] <= end_date)
    sport_map = {"Run": "Run", "Ride": "Ride"}
    sport_mask = activities["sport"] == sport_map.get(sport, sport)
    window = activities[mask & sport_mask]
    all_sports = activities[mask]

    w_mask = (wellness["date"] >= start_date) & (wellness["date"] <= end_date)
    well = wellness[w_mask]

    weeks = max(1, (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days / 7)

    features = {}

    # Volume
    features["n_sessions"] = len(window)
    features["sessions_per_week"] = len(window) / weeks
    features["total_tss"] = window["tss"].sum() if "tss" in window else 0
    features["weekly_tss_mean"] = features["total_tss"] / weeks
    features["total_hours"] = window["moving_time"].sum() / 3600 if len(window) > 0 else 0
    features["weekly_hours"] = features["total_hours"] / weeks
    features["longest_session_min"] = window["moving_time"].max() / 60 if len(window) > 0 else 0

    # Polarization (from zone times)
    low_total, mid_total, high_total = 0, 0, 0
    for _, row in window.iterrows():
        zt = row.get("hr_zone_times")
        if not zt or not isinstance(zt, list):
            continue
        for i, secs in enumerate(zt):
            if i < 2:
                low_total += secs or 0
            elif i == 2:
                mid_total += secs or 0
            else:
                high_total += secs or 0

    zone_total = low_total + mid_total + high_total
    features["low_pct"] = round(low_total / zone_total * 100, 1) if zone_total > 0 else 0
    features["mid_pct"] = round(mid_total / zone_total * 100, 1) if zone_total > 0 else 0
    features["high_pct"] = round(100 - features["low_pct"] - features["mid_pct"], 1) if zone_total > 0 else 0

    # Training load
    if len(well) > 0:
        features["ctl_mean"] = well["ctl"].mean() if "ctl" in well else 0
        features["ctl_max"] = well["ctl"].max() if "ctl" in well else 0
        atl_vals = well["atl"].dropna()
        features["atl_max"] = atl_vals.max() if len(atl_vals) > 0 else 0
        tsb = well["ctl"] - well["atl"] if "atl" in well else pd.Series([0])
        features["tsb_min"] = tsb.min()
        features["tsb_days_below_minus20"] = (tsb < -20).sum()
    else:
        features.update({"ctl_mean": 0, "ctl_max": 0, "atl_max": 0, "tsb_min": 0, "tsb_days_below_minus20": 0})

    # Wellness / Recovery
    if len(well) > 0:
        features["hrv_mean"] = well["hrv"].mean() if "hrv" in well else 0
        features["rhr_mean"] = well["resting_hr"].mean() if "resting_hr" in well else 0
        features["sleep_mean"] = well["sleep_score"].mean() if "sleep_score" in well else 0
        features["recovery_mean"] = well["recovery_score"].mean() if "recovery_score" in well else 0
        features["days_recovery_below_40"] = (well["recovery_score"] < 40).sum() if "recovery_score" in well else 0
    else:
        features.update(
            {"hrv_mean": 0, "rhr_mean": 0, "sleep_mean": 0, "recovery_mean": 0, "days_recovery_below_40": 0}
        )

    # Efficiency
    ef_vals = window["ef"].dropna()
    features["ef_mean"] = ef_vals.mean() if len(ef_vals) > 0 else 0
    dec_vals = window["decoupling"].dropna()
    features["decoupling_median"] = dec_vals.median() if len(dec_vals) > 0 else 0

    return features


def build_dataset(data: dict, sport: str) -> pd.DataFrame:
    """Build training dataset: features + target for each PB pair."""
    pbs = find_pbs(data["hrvt"], sport)
    if len(pbs) < 2:
        print(f"  {sport}: only {len(pbs)} PBs — need at least 2")
        return pd.DataFrame()

    rows = []
    for i in range(1, len(pbs)):
        pb_prev = pbs[i - 1]
        pb_curr = pbs[i]

        start = pb_prev["date"]
        # End 7 days before PB to avoid leakage
        end_dt = pd.to_datetime(pb_curr["date"]) - timedelta(days=7)
        end = end_dt.strftime("%Y-%m-%d")

        if pd.to_datetime(start) >= end_dt:
            continue  # too short window

        features = extract_features(data["activities"], data["wellness"], start, end, sport)
        features["target"] = pb_curr["delta"]
        features["weeks_since_last_pb"] = (pd.to_datetime(pb_curr["date"]) - pd.to_datetime(pb_prev["date"])).days / 7
        features["pb_date"] = pb_curr["date"]

        rows.append(features)

    return pd.DataFrame(rows)


def run_poc():
    print("=== Training Progression POC ===\n")

    print("1. Fetching data...")
    data = fetch_data(user_id=1)
    print(f"   HRVT1 records: {len(data['hrvt'])}")
    print(f"   Activities with zones: {len(data['activities'])}")
    print(f"   Wellness days: {len(data['wellness'])}")

    for sport in ["Run", "Ride"]:
        print(f"\n{'='*50}")
        print(f"2. Building dataset for {sport}...")
        pbs = find_pbs(data["hrvt"], sport)
        print(f"   PBs found: {len(pbs)}")
        for pb in pbs:
            print(f"     {pb['date']}: {pb['value']:.1f} (delta={pb['delta']:.1f})")

        df = build_dataset(data, sport)
        if df.empty:
            print(f"   Skipping {sport} — not enough data")
            continue

        print(f"   Dataset: {len(df)} examples, {len(df.columns)} features")

        # Feature columns (exclude metadata)
        feature_cols = [c for c in df.columns if c not in ("target", "pb_date")]
        X = df[feature_cols].fillna(0)
        y = df["target"]

        print(f"\n3. Training XGBoost for {sport}...")
        try:
            from sklearn.metrics import mean_absolute_error, r2_score
            from sklearn.model_selection import LeaveOneOut
            from xgboost import XGBRegressor

            # Leave-one-out CV (small dataset)
            loo = LeaveOneOut()
            predictions = np.zeros(len(y))

            for train_idx, test_idx in loo.split(X):
                model = XGBRegressor(
                    n_estimators=50,
                    max_depth=3,
                    learning_rate=0.1,
                    random_state=42,
                    verbosity=0,
                )
                model.fit(X.iloc[train_idx], y.iloc[train_idx])
                predictions[test_idx] = model.predict(X.iloc[test_idx])

            mae = mean_absolute_error(y, predictions)
            r2 = r2_score(y, predictions)
            print(f"   LOO CV: MAE={mae:.3f}, R²={r2:.3f}")

            if r2 >= 0.5:
                print(f"   ✅ R² >= 0.5 — model shows signal!")
            else:
                print(f"   ⚠️  R² < 0.5 — weak signal, needs more data or better features")

            # Train final model on all data for SHAP
            print(f"\n4. SHAP analysis for {sport}...")
            final_model = XGBRegressor(n_estimators=50, max_depth=3, learning_rate=0.1, random_state=42, verbosity=0)
            final_model.fit(X, y)

            try:
                import shap

                explainer = shap.TreeExplainer(final_model)
                shap_values = explainer.shap_values(X)

                print("\n   Top features (by mean |SHAP|):")
                mean_abs_shap = np.abs(shap_values).mean(axis=0)
                sorted_idx = np.argsort(mean_abs_shap)[::-1]
                for i in sorted_idx[:10]:
                    print(f"     {feature_cols[i]:30s} SHAP={mean_abs_shap[i]:.4f}")

                # Last PB waterfall
                print(f"\n   Last PB waterfall (what drove the latest improvement):")
                last_idx = len(X) - 1
                sorted_last = np.argsort(np.abs(shap_values[last_idx]))[::-1]
                for i in sorted_last[:5]:
                    direction = "+" if shap_values[last_idx][i] > 0 else "-"
                    print(
                        f"     {direction} {feature_cols[i]:30s} SHAP={shap_values[last_idx][i]:+.4f} (value={X.iloc[last_idx][feature_cols[i]]:.1f})"
                    )

            except ImportError:
                print("   shap not installed — pip install shap")

        except ImportError:
            print("   xgboost not installed — pip install xgboost")

    print(f"\n{'='*50}")
    print("POC complete.")


if __name__ == "__main__":
    run_poc()
