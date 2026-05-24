"""Training metrics calculations for triathlon training load management.

HRV recovery analysis (dual-algorithm), RHR baseline, trend analysis,
ESS (Banister TRIMP), per-sport CTL, combined recovery scoring,
and cardiac drift (decoupling) analysis.
"""

import math
import statistics
from collections import defaultdict
from datetime import date as date_type
from datetime import timedelta

import numpy as np

from data.db import Activity, HrvAnalysis, RhrAnalysis
from data.intervals.dto import RecoveryScoreDTO, RecoveryStateDTO, RmssdStatusDTO, TrendResultDTO
from data.utils import is_bike, is_run

# ---------------------------------------------------------------------------
# Trend Analysis
# ---------------------------------------------------------------------------


def calculate_trend(
    values: list[float],
    window: int = 7,
    threshold_weak: float = 0.5,
    threshold_strong: float = 2.0,
) -> TrendResultDTO:
    """Calculate trend direction using linear regression (least squares).

    Fits a straight line through the last `window` data points.
    The slope tells us how many units the metric changes per day.
    """
    data = values[-window:]

    if len(data) < 3:
        return TrendResultDTO(direction="stable", slope=0.0, r_squared=0.0, emoji="→")

    x = np.arange(len(data), dtype=float)
    y = np.array(data, dtype=float)

    slope, intercept = np.polyfit(x, y, 1)

    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    if slope > threshold_strong:
        direction, emoji = "rising_fast", "↑↑"
    elif slope > threshold_weak:
        direction, emoji = "rising", "↑"
    elif slope >= -threshold_weak:
        direction, emoji = "stable", "→"
    elif slope >= -threshold_strong:
        direction, emoji = "declining", "↓"
    else:
        direction, emoji = "declining_fast", "↓↓"

    return TrendResultDTO(
        direction=direction,
        slope=round(slope, 3),
        r_squared=round(r_squared, 3),
        emoji=emoji,
    )


# Thresholds tuned per metric — pass as **TREND_THRESHOLDS["hrv"] etc.
TREND_THRESHOLDS: dict[str, dict[str, float]] = {
    "hrv": {"threshold_weak": 0.5, "threshold_strong": 2.0},
    "ctl": {"threshold_weak": 0.3, "threshold_strong": 1.5},
    "atl": {"threshold_weak": 0.5, "threshold_strong": 2.5},
    "sleep_score": {"threshold_weak": 0.5, "threshold_strong": 2.0},
    "resting_hr": {"threshold_weak": 0.2, "threshold_strong": 0.8},
}


# ---------------------------------------------------------------------------
# HRV Recovery Classification
# ---------------------------------------------------------------------------


def _classify_recovery(
    hrv_today: float,
    mean_7: float,
    lower_bound: float,
    upper_bound: float,
) -> str:
    """Classify recovery based on HRV deviation from 7-day baseline.

    Asymmetric bounds (Flatt & Esco, 2016):
        lower = mean_7 - 1.0 * std_7
        upper = mean_7 + 0.5 * std_7

    Returns: "red" | "yellow" | "green"
    """
    if hrv_today < lower_bound:
        return "red"
    elif hrv_today <= upper_bound:
        return "yellow"
    else:
        return "green"


def rmssd_flatt_esco(hrv_history: list[float]) -> RmssdStatusDTO:
    """Flatt & Esco (2016) — today's RMSSD vs 7-day baseline, asymmetric bounds.

    Fast response — detects acute changes within 1-2 days.
    """
    n = len(hrv_history)
    last_7 = hrv_history[-7:]

    mean_7 = statistics.mean(last_7)
    std_7 = statistics.stdev(last_7) if len(last_7) >= 2 else 1.0

    lower_bound = mean_7 - 1.0 * std_7
    upper_bound = mean_7 + 0.5 * std_7

    today_rmssd = hrv_history[-1]
    status = _classify_recovery(today_rmssd, mean_7, lower_bound, upper_bound)

    # Long-term baseline (needs 60 days) — context only
    last_60 = hrv_history[-60:]
    rmssd_60d = statistics.mean(last_60) if n >= 60 else None
    rmssd_sd_60d = statistics.stdev(last_60) if n >= 60 else None
    swc = 0.5 * rmssd_sd_60d if rmssd_sd_60d is not None else None

    cv_7d = (std_7 / mean_7 * 100) if mean_7 > 0 else None
    trend = calculate_trend(last_7, window=7, **TREND_THRESHOLDS["hrv"])

    return RmssdStatusDTO(
        status=status,
        days_available=n,
        days_needed=0,
        rmssd_7d=round(mean_7, 1),
        rmssd_sd_7d=round(std_7, 2),
        rmssd_60d=round(rmssd_60d, 1) if rmssd_60d else None,
        rmssd_sd_60d=round(rmssd_sd_60d, 2) if rmssd_sd_60d else None,
        lower_bound=round(lower_bound, 1),
        upper_bound=round(upper_bound, 1),
        cv_7d=round(cv_7d, 1) if cv_7d is not None else None,
        swc=round(swc, 2) if swc is not None else None,
        trend=trend,
    )


# ---------------------------------------------------------------------------
# ESS (External Stress Score) — Banister TRIMP
# ---------------------------------------------------------------------------


def calculate_ess(
    duration_min: float,
    avg_hr: float,
    hr_rest: float,
    hr_max: float,
    lthr: int = 153,
) -> float:
    """Banister TRIMP-based External Stress Score.

    Normalised so 1 hour at LTHR ≈ ESS 100.

    Args:
        duration_min: Activity duration in minutes.
        avg_hr: Average heart rate during activity.
        hr_rest: Athlete's resting heart rate.
        hr_max: Athlete's maximum heart rate.
        lthr: Lactate threshold heart rate for TRIMP normalisation.
    """
    if hr_max <= hr_rest or avg_hr <= hr_rest or duration_min <= 0:
        return 0.0

    hr_ratio = (avg_hr - hr_rest) / (hr_max - hr_rest)
    trimp = duration_min * hr_ratio * 0.64 * math.exp(1.92 * hr_ratio)

    # Normalise: TRIMP for 60 min at LTHR = ESS 100
    lthr_ratio = (lthr - hr_rest) / (hr_max - hr_rest)
    trimp_threshold = 60 * lthr_ratio * 0.64 * math.exp(1.92 * lthr_ratio)

    if trimp_threshold == 0:
        return 0.0

    return round(trimp / trimp_threshold * 100, 1)


# ---------------------------------------------------------------------------
# Daily ESS Aggregation
# ---------------------------------------------------------------------------


def calculate_daily_ess(activities: list, hr_rest: float, hr_max: float, lthr: int = 153) -> float:
    """Sum ESS for all activities on a given day.

    Args:
        activities: List of Activity/Activity for one day.
            Each must have `moving_time` (int, seconds) and `average_hr` (float|None).
        hr_rest: Athlete resting HR.
        hr_max: Athlete max HR.
        lthr: Lactate threshold HR for TRIMP normalisation.

    Returns:
        Total ESS for the day. 0.0 if no activities or no HR data.
    """
    total = 0.0
    for act in activities:
        if act.moving_time and act.average_hr and act.average_hr > 0:
            duration_min = act.moving_time / 60.0
            total += calculate_ess(duration_min, act.average_hr, hr_rest, hr_max, lthr)
    return round(total, 1)


# ---------------------------------------------------------------------------
# Banister Recovery Model
# ---------------------------------------------------------------------------


def calculate_banister_recovery(
    training_log: list[dict],
    k: float = 0.1,
    tau: float = 2.0,
    initial_recovery: float = 100.0,
) -> list[RecoveryStateDTO]:
    """Banister recovery model with exponential return to baseline.

    R(t+1) = R(t) + (100 - R(t)) * (1 - exp(-1/τ)) - k * ESS(t)

    On rest days (ESS=0), R recovers toward 100%.
    On training days, k*ESS pulls R down proportionally to load.

    Args:
        training_log: List of {"date": ..., "ess": ...} dicts.
        k: Load sensitivity (0.01–1.0). Higher = more fatigue per session.
        tau: Recovery time constant in days (0.5–7.0). Higher = slower recovery.
        initial_recovery: Starting recovery % (default 100).
    """
    recovery_rate = 1.0 - math.exp(-1.0 / tau)
    r = initial_recovery
    results: list[RecoveryStateDTO] = []

    for entry in training_log:
        ess = entry.get("ess", 0)
        r = r + (100.0 - r) * recovery_rate - k * ess
        r = max(0.0, min(100.0, r))

        dt = entry["date"]
        if isinstance(dt, str):
            dt = date_type.fromisoformat(dt)

        results.append(
            RecoveryStateDTO(
                date=dt,
                recovery_pct=round(r, 1),
                ess=round(ess, 1),
            )
        )

    return results


def calculate_banister_for_date(
    activities_by_date: dict[str, list],
    *,
    hr_rest: float,
    hr_max: float,
    lthr: int = 153,
    dt: date_type,
    lookback_days: int = 90,
    k: float = 0.1,
    tau: float = 2.0,
) -> tuple[float, float]:
    """Calculate Banister recovery and today's ESS for a specific date.

    Args:
        activities_by_date: Mapping "YYYY-MM-DD" → list of activity objects.
        dt: The date to calculate recovery for.
        hr_rest: Athlete resting HR.
        hr_max: Athlete max HR.
        lookback_days: How many days of history to use.
        k: Load sensitivity (0.01-1.0).
        tau: Recovery time constant in days (0.5-7.0).

    Returns:
        (banister_recovery_pct, ess_today)
    """
    start = dt - timedelta(days=lookback_days)

    training_log = []
    current = start
    while current <= dt:
        date_str = current.isoformat()
        day_acts = activities_by_date.get(date_str, [])

        ess = calculate_daily_ess(day_acts, hr_rest, hr_max, lthr)
        training_log.append({"date": date_str, "ess": ess})
        current += timedelta(days=1)

    states = calculate_banister_recovery(training_log, k=k, tau=tau)

    if not states:
        return 50.0, 0.0

    last = states[-1]
    return last.recovery_pct, last.ess


# ---------------------------------------------------------------------------
# Per-Sport CTL from Activities
# ---------------------------------------------------------------------------


def _calculate_sport_load_ema(
    activities: list[Activity],
    tau: int,
    as_of: date_type | None = None,
) -> dict[str, float]:
    """Per-sport exponential moving average of daily TSS with constant `tau`.

    Shared core for `calculate_sport_ctl` (τ=42) and `calculate_sport_atl` (τ=7).
    Iterates one date at a time so days without activities contribute zero load —
    necessary for the EMA to decay correctly during rest periods.

    `as_of` is the target evaluation date. The EMA runs from the first activity
    up to and including `as_of`, so a rest gap between the last activity and
    `as_of` still decays. Default `None` uses the last activity date (legacy
    behavior — only safe when caller guarantees recent activity).
    """
    if not activities:
        return {"swim": 0.0, "ride": 0.0, "run": 0.0}

    daily_load: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for act in activities:
        sport = (act.type or "").lower()
        if sport not in ("swim", "ride", "run"):
            continue
        if act.icu_training_load is None:
            continue
        daily_load[sport][str(act.start_date_local)] += act.icu_training_load

    if not daily_load:
        return {"swim": 0.0, "ride": 0.0, "run": 0.0}

    all_dates: set[str] = set()
    for sport_dates in daily_load.values():
        all_dates.update(sport_dates.keys())

    min_date = date_type.fromisoformat(min(all_dates))
    last_activity_date = date_type.fromisoformat(max(all_dates))
    end_date = as_of if as_of is not None else last_activity_date
    if end_date < min_date:
        end_date = min_date

    decay = math.exp(-1.0 / tau)

    result: dict[str, float] = {}
    for sport in ("swim", "ride", "run"):
        value = 0.0
        sport_loads = daily_load.get(sport, {})
        current = min_date
        while current <= end_date:
            tss = sport_loads.get(current.strftime("%Y-%m-%d"), 0.0)
            value = value * decay + tss * (1 - decay)
            current += timedelta(days=1)
        result[sport] = round(value, 1)

    return result


def calculate_sport_ctl(
    activities: list[Activity],
    tau: int = 42,
    as_of: date_type | None = None,
) -> dict[str, float]:
    """Per-sport CTL (Chronic Training Load) — EMA with τ=42, matching Intervals.icu.

    Caller must supply enough history for the EMA to warm up: at least ~3τ (126 days)
    for <5% bias, ideally 5τ (210 days) for <1%. Pass `as_of` so the EMA decays
    correctly through any rest gap between the last activity and the target date.
    """
    return _calculate_sport_load_ema(activities, tau, as_of=as_of)


def calculate_sport_atl(
    activities: list[Activity],
    tau: int = 7,
    as_of: date_type | None = None,
) -> dict[str, float]:
    """Per-sport ATL (Acute Training Load) — EMA with τ=7, matching Intervals.icu."""
    return _calculate_sport_load_ema(activities, tau, as_of=as_of)


# ---------------------------------------------------------------------------
# CTL Target Projection
# ---------------------------------------------------------------------------

PROJECTION_WINDOW_DAYS = 14


_FLAT_RAMP_TOLERANCE = 0.05  # CTL/week — anything tighter is float noise


def project_ctl_target(
    ctl_series: list[tuple[date_type, float]],
    target: float | None,
    today: date_type,
    event_date: date_type | None = None,
) -> dict | None:
    """Estimate when CTL will reach ``target`` at the current ramp rate.

    Slope is a least-squares fit over the supplied window (numpy.polyfit).
    Two-endpoint slope was rejected because Intervals.icu CTL wobbles
    ±0.5 day-to-day during easy/hard sequencing — a single noisy oldest
    day would swing the projected_date by weeks. ``current_ctl`` for the
    gap calculation stays the actual newest reading (what the user sees
    on the bar), not the regression intercept.

    ``event_date`` is optional. When supplied, ``on_track`` is computed
    against the raw (unrounded) days-to-target to avoid display-rounding
    flipping the verdict day-over-day. Without it, ``on_track`` stays
    None for the success branch (caller decides).

    Returns ``None`` if there is no target. Otherwise a dict shaped
    ``{ramp_per_week, projected_date, reason, on_track}``.
    """
    if target is None or target <= 0:
        return None
    if len(ctl_series) < 2:
        return {
            "ramp_per_week": None,
            "projected_date": None,
            "reason": "insufficient_data",
            "on_track": None,
        }

    series = sorted(ctl_series, key=lambda x: x[0])
    oldest_dt, _ = series[0]
    newest_dt, newest_ctl = series[-1]
    days_span = (newest_dt - oldest_dt).days
    if days_span < 7:
        return {
            "ramp_per_week": None,
            "projected_date": None,
            "reason": "insufficient_data",
            "on_track": None,
        }

    days_arr = np.array([(d - oldest_dt).days for d, _ in series], dtype=float)
    ctls_arr = np.array([c for _, c in series], dtype=float)
    slope_per_day_np, _intercept = np.polyfit(days_arr, ctls_arr, 1)
    slope_per_day = float(slope_per_day_np)  # cast off numpy scalar early — keeps `>=` Python-native
    ramp_per_week = slope_per_day * 7
    gap = target - newest_ctl

    if gap <= 0:
        return {
            "ramp_per_week": round(ramp_per_week, 2),
            "projected_date": None,
            "reason": "already_at_target",
            "on_track": True,
        }
    if abs(ramp_per_week) < _FLAT_RAMP_TOLERANCE:
        return {
            "ramp_per_week": round(ramp_per_week, 2),
            "projected_date": None,
            "reason": "flat",
            "on_track": False,
        }
    if ramp_per_week < 0:
        return {
            "ramp_per_week": round(ramp_per_week, 2),
            "projected_date": None,
            "reason": "declining",
            "on_track": False,
        }

    days_to_target = gap / ramp_per_week * 7
    projected = today + timedelta(days=int(round(days_to_target)))
    # on_track via "predicted CTL at event_date >= target", not days comparison —
    # the inverse `gap / ramp` accumulates float error (e.g. 13.5/3.5*7 yields
    # 27.0000000000004) and would flip a true boundary case to False.
    on_track: bool | None
    if event_date is not None:
        days_remaining = (event_date - today).days
        predicted_at_event = newest_ctl + slope_per_day * days_remaining
        # 1e-6 epsilon — np.polyfit can return slope ±1e-15 from the true
        # value on perfectly linear input, which flips boundary cases (predicted
        # = target ± 1e-14) the wrong way. CTL precision is 0.1, so 1e-6 is
        # well below anything user-visible.
        on_track = predicted_at_event >= target - 1e-6
    else:
        on_track = None
    return {
        "ramp_per_week": round(ramp_per_week, 2),
        "projected_date": projected.isoformat(),
        "reason": None,
        "on_track": on_track,
    }


# ---------------------------------------------------------------------------
# Combined Recovery Score
# ---------------------------------------------------------------------------

_STATUS_TO_SCORE = {"green": 100, "yellow": 65, "red": 20, "insufficient_data": 50}


def combined_recovery_score(
    rmssd: HrvAnalysis,
    rhr: RhrAnalysis,
    banister_recovery: float | None,
    sleep_score: int | None,
) -> RecoveryScoreDTO:
    """Weighted integration of 4 recovery signals into a single 0-100 score.

    Weights (when all signals available):
        RMSSD status      35%
        Banister R(t)     25%
        Resting HR status 20%
        Sleep score       20%

    Missing signals are excluded and remaining weights renormalised.

    Modifiers:
        sleep_start > 23:00  →  -10 pts
        CV 7d > 15%          →  -5 pts
    """
    rmssd_score = _STATUS_TO_SCORE.get(rmssd.status, 50)
    rhr_score = _STATUS_TO_SCORE.get(rhr.status, 50)

    components: list[tuple[float, float]] = [
        (rmssd_score, 0.35),
        (rhr_score, 0.20),
    ]
    if banister_recovery is not None:
        components.append((max(0.0, min(100.0, banister_recovery)), 0.25))
    if sleep_score is not None:
        components.append((max(0.0, min(100.0, float(sleep_score))), 0.20))

    total_weight = sum(w for _, w in components)
    score = sum(v * w for v, w in components) / total_weight

    flags: list[str] = []
    if rmssd.cv_7d and rmssd.cv_7d > 15:
        score -= 5
        flags.append("hrv_unstable")
    trend_dir = getattr(rmssd, "trend_direction", None) or (
        rmssd.trend.direction if getattr(rmssd, "trend", None) else None
    )
    if trend_dir in ("declining", "declining_fast"):
        flags.append("rmssd_declining")  # warning only, no score penalty

    score = max(0.0, min(100.0, score))

    if score > 85:
        category = "excellent"
    elif score > 70:
        category = "good"
    elif score > 40:
        category = "moderate"
    else:
        category = "low"

    # Red RMSSD always overrides recommendation
    if rmssd.status == "red":
        recommendation = "skip"
    elif category in ("excellent", "good"):
        recommendation = "zone2_ok"
    elif category == "moderate":
        recommendation = "zone1_long"
    else:
        recommendation = "zone1_short"

    return RecoveryScoreDTO(
        score=round(score, 1),
        category=category,
        recommendation=recommendation,
        flags=flags,
        components={
            "rmssd": rmssd_score,
            "banister": round(banister_recovery, 1) if banister_recovery is not None else None,
            "rhr": rhr_score,
            "sleep": round(float(sleep_score), 1) if sleep_score is not None else None,
        },
    )


# ---------------------------------------------------------------------------
# Cardiac drift (decoupling) analysis
# ---------------------------------------------------------------------------

# Minimum duration (seconds) for valid decoupling analysis
_DECOUPLING_MIN_DURATION = {"bike": 60 * 60, "run": 45 * 60}

# Maximum variability index for steady-state filter
_DECOUPLING_MAX_VI = 1.10

# Minimum fraction of time in Z1+Z2
_DECOUPLING_MIN_Z12_FRACTION = 0.70


def decoupling_sport_group(activity_type: str) -> str | None:
    """Map activity type to sport group for decoupling analysis. Swim excluded."""
    if is_bike(activity_type):
        return "bike"
    if is_run(activity_type):
        return "run"
    return None


def is_valid_for_decoupling(
    activity_type: str,
    moving_time: int | None,
    variability_index: float | None,
    hr_zone_times: list | None,
    decoupling: float | None,
) -> bool:
    """Check if an activity is suitable for decoupling analysis.

    Criteria: sport is bike/run (swim excluded), sufficient duration,
    low variability index (steady-state), >70% time in Z1+Z2, decoupling available.
    """
    sport = decoupling_sport_group(activity_type)
    if not sport:
        return False

    if decoupling is None:
        return False

    min_dur = _DECOUPLING_MIN_DURATION.get(sport, 0)
    if not moving_time or moving_time < min_dur:
        return False

    if variability_index is not None and variability_index > _DECOUPLING_MAX_VI:
        return False

    if hr_zone_times and len(hr_zone_times) >= 2:
        total = sum(hr_zone_times)
        if total > 0:
            z12_fraction = (hr_zone_times[0] + hr_zone_times[1]) / total
            if z12_fraction < _DECOUPLING_MIN_Z12_FRACTION:
                return False

    return True


def decoupling_status(value: float) -> str:
    """Traffic light grading for decoupling value.

    Uses abs(value) — negative drift (pulse drops) is normal and graded same as positive.
    Returns: "green" (<5%), "yellow" (5-10%), "red" (>10%).
    """
    av = abs(value)
    if av < 5.0:
        return "green"
    if av <= 10.0:
        return "yellow"
    return "red"


# ---------------------------------------------------------------------------
# Polarization Index
# ---------------------------------------------------------------------------


def _classify_polarization(low: float, mid: float, high: float) -> str:
    if low > 90 and high < 3:
        return "too_easy"
    if low < 60 and high > 20:
        return "too_hard"
    if mid > 25:
        return "threshold"
    if low >= 75 and mid <= 15 and high >= 5:
        return "polarized"
    if low >= 70 and high >= 5:
        return "pyramidal"
    return "threshold"


def compute_polarization(hr_zone_times_list: list[list[int | float]]) -> dict:
    """Compute Polarization Index from a list of hr_zone_times arrays.

    Each entry is [Z1_secs, Z2_secs, Z3_secs, Z4_secs, Z5_secs, ...] (5-7 zones).
    Returns {low_pct, mid_pct, high_pct, total_hours, pattern, by_zone}.
    """
    totals: dict[str, float] = {}
    for zt in hr_zone_times_list:
        if not zt:
            continue
        for i, secs in enumerate(zt):
            key = f"Z{i + 1}"
            totals[key] = totals.get(key, 0) + (secs or 0)

    total = sum(totals.values())
    if total < 1:
        return {
            "low_pct": 0,
            "mid_pct": 0,
            "high_pct": 0,
            "total_hours": 0,
            "pattern": "insufficient_data",
            "n_activities": len(hr_zone_times_list),
            "by_zone": {},
        }

    # Aggregate: Low = Z1+Z2, Mid = Z3, High = remainder
    low = totals.get("Z1", 0) + totals.get("Z2", 0)
    mid = totals.get("Z3", 0)

    low_pct = round(low / total * 100, 1)
    mid_pct = round(mid / total * 100, 1)
    high_pct = max(0, round(100 - low_pct - mid_pct, 1))  # guarantee sum ≈ 100, never negative
    total_hours = round(total / 3600, 1)

    pattern = _classify_polarization(low_pct, mid_pct, high_pct)

    return {
        "low_pct": low_pct,
        "mid_pct": mid_pct,
        "high_pct": high_pct,
        "total_hours": total_hours,
        "pattern": pattern,
        "n_activities": len(hr_zone_times_list),
        "by_zone": {k: round(v / total * 100, 1) for k, v in sorted(totals.items())},
    }


def compute_polarization_trends(windows: dict[int, dict]) -> list[str]:
    """Detect trend signals by comparing polarization across windows.

    Args:
        windows: {days: compute_polarization_result} for multiple windows (e.g. 7, 14, 28, 56).

    Returns:
        List of coaching signal strings.
    """
    signals: list[str] = []
    w7 = windows.get(7)
    w14 = windows.get(14)
    w28 = windows.get(28)
    w56 = windows.get(56)

    # Gray zone drift: short-term mid is growing vs long-term
    if w7 and w28 and w7["mid_pct"] - w28["mid_pct"] > 5:
        signals.append(f"Gray zone growing: {w7['mid_pct']}% this week vs {w28['mid_pct']}% monthly avg")

    # Taper detection: intensity dropping
    if w14 and w56 and w56["high_pct"] - w14["high_pct"] > 5:
        signals.append(f"Taper mode: high intensity dropped to {w14['high_pct']}% (was {w56['high_pct']}%)")

    # Deload week: much more easy than usual
    if w7 and w28 and w7["low_pct"] - w28["low_pct"] > 10:
        signals.append(f"Deload week: {w7['low_pct']}% easy this week (avg {w28['low_pct']}%)")

    # Too much threshold (14d rule for morning report)
    if w14 and w14["mid_pct"] > 20:
        signals.append(f"Too much Z3 over 2 weeks ({w14['mid_pct']}%)")

    # Too easy (28d)
    if w28 and w28["pattern"] == "too_easy":
        signals.append("Not enough intensity — add 1-2 hard sessions per week")

    # Too hard (14d)
    if w14 and w14["pattern"] == "too_hard":
        signals.append("Overtraining risk — too much high intensity over 2 weeks")

    return signals
