"""Race-projection feature engineering — per-discipline regression features.

Builds state (common §6.1) + discipline-specific (§6.2) feature rows from
Intervals.icu wellness + activity history. See `docs/ML_RACE_PROJECTION_SPEC.md`.

Phase 1 MVP — focused feature set; XGBoost handles missing values natively,
so optional sources (Garmin sleep/stress, activity_details for some metrics)
that may be sparse are passed through as NaN rather than imputed.
"""

import logging
import math
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import text

from data.db.common import get_sync_session
from data.ml.noise_classifier import RECOVERY_TSS_CEILING, Z1_RECOVERY_THRESHOLD, is_run_recovery_jog

logger = logging.getLogger(__name__)

# Mapping from MCP input mode → canonical Activity.type
DISCIPLINE_TO_SPORT = {"run": "Run", "ride": "Ride", "swim": "Swim"}

# Min moving_time for an activity to be a training example (§6.3).
MIN_DURATION_SEC = 25 * 60

# Min training set size before XGBoost actually has a chance.
MIN_EXAMPLES = 30

# Phase 1.5 z1-filter + Phase 1.6 webhook tag (§6.3 / §6.4): helpers and
# constants live in `data.ml.noise_classifier` (single source of truth for the
# rule). Phase 1.6 promotes the train-time filter to a persisted
# `activities.noise_reason` tag written from `actor_update_activity_details` —
# read-side here prefers the persisted tag, falls back to the live check only
# for legacy rows where `noise_scored_at IS NULL`.


class InsufficientDataError(Exception):
    """Raised when training set is too small for the discipline."""


# ---------------------------------------------------------------------------
# Data fetch — one query per concern, joined in pandas
# ---------------------------------------------------------------------------


def _fetch_activities(user_id: int, sport: str) -> pd.DataFrame:
    """All activities of a sport + joined detail metrics."""
    with get_sync_session() as s:
        df = pd.read_sql(
            text(
                """
                SELECT
                    a.id              AS activity_id,
                    a.start_date_local AS date,
                    a.type            AS sport,
                    a.sub_type        AS sub_type,
                    a.moving_time     AS moving_time,
                    a.icu_training_load AS tss,
                    a.average_hr      AS avg_hr,
                    a.is_race         AS is_race,
                    a.noise_reason    AS noise_reason,
                    a.noise_scored_at AS noise_scored_at,
                    ad.distance       AS distance,
                    ad.elevation_gain AS elevation_gain,
                    ad.avg_power      AS avg_power,
                    ad.normalized_power AS normalized_power,
                    ad.pace           AS pace_mps,
                    ad.hr_zone_times  AS hr_zone_times
                FROM activities a
                LEFT JOIN activity_details ad ON ad.activity_id = a.id
                WHERE a.user_id = :uid AND a.type = :sport
                  AND a.moving_time IS NOT NULL
                ORDER BY a.start_date_local
                """
            ),
            s.connection(),
            params={"uid": user_id, "sport": sport},
        )
    df["date"] = df["date"].astype(str)
    return df


def _fetch_wellness(user_id: int) -> pd.DataFrame:
    with get_sync_session() as s:
        df = pd.read_sql(
            text(
                """
                SELECT date, ctl, atl, hrv, resting_hr, sleep_score, recovery_score
                FROM wellness WHERE user_id = :uid ORDER BY date
                """
            ),
            s.connection(),
            params={"uid": user_id},
        )
    df["date"] = df["date"].astype(str)
    return df


def _fetch_garmin_daily(user_id: int) -> pd.DataFrame:
    with get_sync_session() as s:
        df = pd.read_sql(
            text(
                """
                SELECT calendar_date AS date, avg_stress
                FROM garmin_daily_summary WHERE user_id = :uid
                ORDER BY calendar_date
                """
            ),
            s.connection(),
            params={"uid": user_id},
        )
    df["date"] = df["date"].astype(str)
    return df


def _fetch_training_log(user_id: int) -> pd.DataFrame:
    with get_sync_session() as s:
        df = pd.read_sql(
            text(
                """
                SELECT date, compliance
                FROM training_log WHERE user_id = :uid AND compliance IS NOT NULL
                ORDER BY date
                """
            ),
            s.connection(),
            params={"uid": user_id},
        )
    df["date"] = df["date"].astype(str)
    # Numericise compliance (label-encoded). "completed"→1, "partial"→0.5, "skipped"→0.
    mapping = {"completed": 1.0, "partial": 0.5, "skipped": 0.0, "missed": 0.0}
    df["compliance_num"] = df["compliance"].map(mapping).fillna(0.5)
    return df


def _fetch_athlete_state(user_id: int) -> dict:
    """One-row snapshot: per-sport thresholds + current_eftp / CP / W' / pMax."""
    with get_sync_session() as s:
        row = s.execute(
            text(
                """
                SELECT sport, lthr, max_hr, ftp, threshold_pace,
                       critical_power, w_prime, p_max
                FROM athlete_settings WHERE user_id = :uid
                """
            ),
            {"uid": user_id},
        ).all()
    out = {}
    for r in row:
        out[r.sport] = dict(r._mapping)
    return out


# ---------------------------------------------------------------------------
# Per-sport CTL helper — local because we need it at arbitrary historical dates
# ---------------------------------------------------------------------------


def _compute_sport_ctl_series(activities_all_sports: pd.DataFrame, sport: str, tau: int = 42) -> pd.Series:
    """Daily CTL EMA for ``sport`` indexed by ISO date string. Returns the full
    decay curve from earliest activity through the latest date. Sport label
    matches `Activity.type` ("Run" / "Ride" / "Swim").
    """
    sub = activities_all_sports[(activities_all_sports["sport"] == sport) & activities_all_sports["tss"].notna()]
    if sub.empty:
        return pd.Series(dtype="float64")
    daily = sub.groupby("date")["tss"].sum().to_dict()
    earliest = date.fromisoformat(min(daily.keys()))
    latest = date.fromisoformat(max(daily.keys()))
    decay = math.exp(-1.0 / tau)
    ctl = 0.0
    rows: dict[str, float] = {}
    cur = earliest
    while cur <= latest:
        ds = cur.isoformat()
        tss = daily.get(ds, 0.0)
        ctl = ctl * decay + tss * (1.0 - decay)
        rows[ds] = round(ctl, 2)
        cur += timedelta(days=1)
    return pd.Series(rows, name=f"ctl_{sport.lower()}")


def _fetch_all_sports_activities(user_id: int) -> pd.DataFrame:
    """All activities (all sports) — needed for per-sport CTL split."""
    with get_sync_session() as s:
        df = pd.read_sql(
            text(
                """
                SELECT start_date_local AS date, type AS sport,
                       icu_training_load AS tss
                FROM activities WHERE user_id = :uid AND icu_training_load IS NOT NULL
                ORDER BY start_date_local
                """
            ),
            s.connection(),
            params={"uid": user_id},
        )
    df["date"] = df["date"].astype(str)
    return df


# ---------------------------------------------------------------------------
# Feature row builders
# ---------------------------------------------------------------------------


def _state_row(
    target_date: str,
    wellness: pd.DataFrame,
    ctl_per_sport: dict[str, pd.Series],
    garmin: pd.DataFrame,
    training_log: pd.DataFrame,
) -> dict:
    """Common features (§6.1) at ``target_date`` (ISO string)."""
    w = wellness[wellness["date"] <= target_date]
    last_w = w.iloc[-1] if not w.empty else None
    f: dict = {
        "ctl": float(last_w["ctl"]) if last_w is not None and pd.notna(last_w["ctl"]) else float("nan"),
        "atl": float(last_w["atl"]) if last_w is not None and pd.notna(last_w["atl"]) else float("nan"),
        "hrv": float(last_w["hrv"]) if last_w is not None and pd.notna(last_w["hrv"]) else float("nan"),
        "resting_hr": (
            float(last_w["resting_hr"]) if last_w is not None and pd.notna(last_w["resting_hr"]) else float("nan")
        ),
        "recovery_score": (
            float(last_w["recovery_score"])
            if last_w is not None and pd.notna(last_w["recovery_score"])
            else float("nan")
        ),
    }
    f["tsb"] = f["ctl"] - f["atl"] if not (math.isnan(f["ctl"]) or math.isnan(f["atl"])) else float("nan")

    for sport_key in ("Run", "Ride", "Swim"):
        series = ctl_per_sport.get(sport_key, pd.Series(dtype="float64"))
        if not series.empty:
            valid_dates = series.index[series.index <= target_date]
            f[f"ctl_{sport_key.lower()}"] = float(series.loc[valid_dates[-1]]) if len(valid_dates) > 0 else 0.0
        else:
            f[f"ctl_{sport_key.lower()}"] = 0.0

    # 7-day sleep / stress means
    start_7d = (date.fromisoformat(target_date) - timedelta(days=7)).isoformat()
    w7 = wellness[(wellness["date"] >= start_7d) & (wellness["date"] <= target_date)]
    f["sleep_score_7d_mean"] = (
        float(w7["sleep_score"].dropna().mean()) if not w7["sleep_score"].dropna().empty else float("nan")
    )
    g7 = garmin[(garmin["date"] >= start_7d) & (garmin["date"] <= target_date)]
    f["stress_avg_7d_mean"] = (
        float(g7["avg_stress"].dropna().mean()) if not g7["avg_stress"].dropna().empty else float("nan")
    )

    # 28-day compliance mean
    start_28d = (date.fromisoformat(target_date) - timedelta(days=28)).isoformat()
    tl28 = training_log[(training_log["date"] >= start_28d) & (training_log["date"] <= target_date)]
    f["compliance_28d_mean"] = float(tl28["compliance_num"].mean()) if not tl28.empty else float("nan")

    return f


def _activity_row_features(
    row: pd.Series,
    sport: str,
    cumulative_90d: float,
    recent_high_intensity_14d: int,
    athlete_state: dict,
) -> dict:
    """Per-row features (§6.2). Inputs that come from the activity directly are
    used as proxies for the "what would the athlete do" inference inputs at
    train time. At inference the caller substitutes target_hr/distance.
    """
    f: dict = {
        "target_hr": float(row["avg_hr"]) if pd.notna(row["avg_hr"]) else float("nan"),
        "distance_m": float(row["distance"]) if pd.notna(row["distance"]) else float("nan"),
        "is_race": 1 if bool(row["is_race"]) else 0,
        "cumulative_90d": float(cumulative_90d),
        "recent_high_intensity_14d": int(recent_high_intensity_14d),
    }

    # elevation_per_km (Run / Ride relevant; Swim irrelevant but XGBoost ignores constants)
    dist_km = (row["distance"] / 1000.0) if pd.notna(row["distance"]) and row["distance"] > 0 else None
    if dist_km and pd.notna(row["elevation_gain"]):
        f["elevation_per_km"] = float(row["elevation_gain"]) / dist_km
    else:
        f["elevation_per_km"] = 0.0

    if sport == "Ride":
        ride_state = athlete_state.get("Ride", {})
        f["current_eftp"] = float(ride_state.get("ftp") or 0.0) or float("nan")
        f["critical_power"] = float(ride_state.get("critical_power") or 0.0) or float("nan")
        f["w_prime"] = float(ride_state.get("w_prime") or 0.0) or float("nan")
        f["p_max"] = float(ride_state.get("p_max") or 0.0) or float("nan")
        # is_indoor — heuristic: no elevation gain → likely trainer
        f["is_indoor"] = 1 if (pd.notna(row["elevation_gain"]) and row["elevation_gain"] == 0) else 0
    elif sport == "Swim":
        # is_pool heuristic — most short Intervals.icu Swim rows are pool sessions.
        f["is_pool"] = 1 if (pd.notna(row["distance"]) and row["distance"] < 3000) else 0
    return f


def _target_value(row: pd.Series, sport: str) -> float | None:
    """Compute training target (§6.3) from a completed activity."""
    moving = row["moving_time"]
    dist = row["distance"]
    if pd.isna(moving) or moving < MIN_DURATION_SEC:
        return None
    if sport == "Run":
        if pd.isna(dist) or dist <= 0:
            return None
        return float(moving) / (float(dist) / 1000.0)  # sec/km
    if sport == "Ride":
        # Prefer normalized_power, fall back to avg_power
        power = row["normalized_power"] if pd.notna(row["normalized_power"]) else row["avg_power"]
        if pd.isna(power) or power <= 0:
            return None
        return float(power)
    if sport == "Swim":
        if pd.isna(dist) or dist <= 0:
            return None
        return float(moving) / (float(dist) / 100.0)  # sec/100m
    return None


# ---------------------------------------------------------------------------
# Public API — build training dataset
# ---------------------------------------------------------------------------


def build_dataset(user_id: int, discipline: str) -> pd.DataFrame:
    """Build training set (features + target) for one discipline.

    Returns a DataFrame with feature columns + ``target`` column. Empty
    DataFrame if not enough qualifying activities (caller handles cold-start).
    """
    sport = DISCIPLINE_TO_SPORT.get(discipline.lower())
    if sport is None:
        raise ValueError(f"Unknown discipline {discipline!r}; expected one of {list(DISCIPLINE_TO_SPORT)}")

    activities = _fetch_activities(user_id, sport)
    if activities.empty:
        return pd.DataFrame()

    all_acts = _fetch_all_sports_activities(user_id)
    wellness = _fetch_wellness(user_id)
    garmin = _fetch_garmin_daily(user_id)
    training_log = _fetch_training_log(user_id)
    athlete_state = _fetch_athlete_state(user_id)

    ctl_per_sport = {
        "Run": _compute_sport_ctl_series(all_acts, "Run"),
        "Ride": _compute_sport_ctl_series(all_acts, "Ride"),
        "Swim": _compute_sport_ctl_series(all_acts, "Swim"),
    }

    rows: list[dict] = []
    n_filtered_persisted = 0  # dropped via persisted activities.noise_reason (Phase 1.6)
    n_filtered_legacy = 0  # dropped via live fallback for un-backfilled rows
    for _, act in activities.iterrows():
        target = _target_value(act, sport)
        if target is None:
            continue
        if pd.isna(act["avg_hr"]):
            continue  # need HR for target_hr feature

        # Phase 1.6 (§6.4): prefer persisted webhook-time classification.
        # noise_reason='run_*' → drop unconditionally (already classified).
        # noise_scored_at IS NOT NULL with NULL reason → checked clean, keep.
        # noise_scored_at IS NULL → legacy row, fall back to live check.
        # Empty/whitespace strings DON'T count as noise — column is TEXT without
        # CHECK constraint, so a future accidental empty-string write must not
        # silently drop legitimate rows from training.
        noise_reason = act.get("noise_reason")
        is_classified_noise = bool(isinstance(noise_reason, str) and noise_reason.strip())
        if is_classified_noise:
            n_filtered_persisted += 1
            continue
        scored_at = act.get("noise_scored_at")
        is_legacy = scored_at is None or pd.isna(scored_at)
        if is_legacy and sport == "Run" and is_run_recovery_jog(act.get("hr_zone_times"), act.get("tss")):
            n_filtered_legacy += 1
            continue

        dt_str = act["date"]
        # Cumulative + recent metrics. Use TSS (training load), NOT distance —
        # `_fetch_all_sports_activities` (inference path) only selects `tss`, so
        # using distance here would create a train/infer semantic mismatch on
        # the `cumulative_90d` feature. TSS works across all 3 disciplines.
        start_90 = (date.fromisoformat(dt_str) - timedelta(days=90)).isoformat()
        prior_90 = activities[(activities["date"] >= start_90) & (activities["date"] < dt_str)]
        cumulative_90 = float(prior_90["tss"].dropna().sum()) if not prior_90.empty else 0.0

        start_14 = (date.fromisoformat(dt_str) - timedelta(days=14)).isoformat()
        prior_14 = activities[(activities["date"] >= start_14) & (activities["date"] < dt_str)]
        # "High intensity" proxy = top-quartile pace/power within window.
        if sport == "Ride":
            metric = prior_14["normalized_power"].dropna()
        elif sport == "Run":
            # For pace, "high intensity" = fast pace = low sec/km.
            paces = []
            for _, r in prior_14.iterrows():
                if pd.notna(r["moving_time"]) and pd.notna(r["distance"]) and r["distance"] > 0:
                    paces.append(r["moving_time"] / (r["distance"] / 1000.0))
            metric = pd.Series(paces) if paces else pd.Series(dtype="float64")
        else:
            metric = pd.Series(dtype="float64")
        if len(metric) >= 4:
            if sport == "Run":
                threshold = metric.quantile(0.25)  # fast = low pace
                recent_hi = int((metric <= threshold).sum())
            else:
                threshold = metric.quantile(0.75)  # fast = high power
                recent_hi = int((metric >= threshold).sum())
        else:
            recent_hi = 0

        f = _state_row(dt_str, wellness, ctl_per_sport, garmin, training_log)
        f.update(_activity_row_features(act, sport, cumulative_90, recent_hi, athlete_state))
        f["target"] = target
        f["activity_id"] = act["activity_id"]
        f["date"] = dt_str
        rows.append(f)

    if n_filtered_persisted or n_filtered_legacy:
        # Persisted path = `actor_update_activity_details` set noise_reason at
        # webhook time. Legacy path = no scored_at yet, fell back to live check
        # (`is_run_recovery_jog` — Z1≥70% AND TSS<40). Legacy count >0 means
        # backfill hasn't been run (or hit only the rows that were already
        # noise on the live rule); after `python -m cli classify-noise` it
        # should drop to ~0 for the 365d window.
        logger.info(
            "noise filter: dropped %d %s activities (persisted=%d, legacy_fallback=%d) "
            "for user_id=%d. Live rule: Z1≥%.0f%% AND TSS<%.0f",
            n_filtered_persisted + n_filtered_legacy,
            sport,
            n_filtered_persisted,
            n_filtered_legacy,
            user_id,
            Z1_RECOVERY_THRESHOLD * 100,
            RECOVERY_TSS_CEILING,
        )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Public API — build inference state row
# ---------------------------------------------------------------------------


def build_inference_features(
    user_id: int,
    discipline: str,
    target_date: date | str,
    target_hr: float | None,
    distance_m: float,
    *,
    overrides: dict | None = None,
) -> dict:
    """Build a single feature row for inference (Mode 1 today / Mode 2 race-day).

    ``overrides`` allows the caller to substitute computed state (CTL/eFTP/CP)
    from FitnessProjection / future state — used by Mode 2. Keys map directly
    to feature names (``ctl``, ``atl``, ``tsb``, ``ctl_run``, ``ctl_ride``,
    ``ctl_swim``, ``current_eftp``).
    """
    sport = DISCIPLINE_TO_SPORT.get(discipline.lower())
    if sport is None:
        raise ValueError(f"Unknown discipline {discipline!r}")

    _target_iso = target_date if isinstance(target_date, str) else target_date.isoformat()

    all_acts = _fetch_all_sports_activities(user_id)
    wellness = _fetch_wellness(user_id)
    garmin = _fetch_garmin_daily(user_id)
    training_log = _fetch_training_log(user_id)
    athlete_state = _fetch_athlete_state(user_id)
    ctl_per_sport = {
        "Run": _compute_sport_ctl_series(all_acts, "Run"),
        "Ride": _compute_sport_ctl_series(all_acts, "Ride"),
        "Swim": _compute_sport_ctl_series(all_acts, "Swim"),
    }

    f = _state_row(_target_iso, wellness, ctl_per_sport, garmin, training_log)

    # Discipline features — use placeholders for non-applicable fields; XGBoost
    # ignores them at inference if they weren't in training feature_names.
    f["target_hr"] = float(target_hr) if target_hr is not None else float("nan")
    f["distance_m"] = float(distance_m)
    f["is_race"] = 1  # race inference
    f["elevation_per_km"] = 0.0  # not known at inference; PR4 will read race_conditions
    sport_acts = all_acts[all_acts["sport"] == sport]
    f["cumulative_90d"] = float(
        sport_acts[sport_acts["date"] >= (date.fromisoformat(_target_iso) - timedelta(days=90)).isoformat()]["tss"]
        .dropna()
        .sum()
    )
    f["recent_high_intensity_14d"] = 0  # not derivable forward; placeholder

    if sport == "Ride":
        ride_state = athlete_state.get("Ride", {})
        f["current_eftp"] = float(ride_state.get("ftp") or 0.0) or float("nan")
        f["critical_power"] = float(ride_state.get("critical_power") or 0.0) or float("nan")
        f["w_prime"] = float(ride_state.get("w_prime") or 0.0) or float("nan")
        f["p_max"] = float(ride_state.get("p_max") or 0.0) or float("nan")
        f["is_indoor"] = 0  # race assumption
    elif sport == "Swim":
        f["is_pool"] = 0  # race assumption

    # Apply overrides (Mode 2: projected CTL / eFTP).
    # ``_<key>`` entries are control-flow markers consumed by the caller
    # (e.g. ``_ctl_ratio`` for per-sport CTL scaling in predict._predict_one) —
    # never feature values. Skip them here so they don't pollute ``f``.
    if overrides:
        feature_overrides = {k: v for k, v in overrides.items() if not k.startswith("_") and v is not None}
        for k, v in feature_overrides.items():
            f[k] = float(v)
        # Recompute TSB only when both halves are real (no NaN, no marker key).
        if {"ctl", "atl"} & feature_overrides.keys():
            if not (math.isnan(f["ctl"]) or math.isnan(f["atl"])):
                f["tsb"] = f["ctl"] - f["atl"]

    return f
