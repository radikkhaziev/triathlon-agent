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
from data.utils import SPORT_MAP

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


def rmssd_ai_endurance(hrv_history: list[float]) -> RmssdStatusDTO:
    """AIEndurance / Kiviniemi — 7d mean vs 60d baseline, symmetric bounds.

    Slower response — takes ~3-4 days of low HRV to trigger "red".
    Better for chronic fatigue detection.
    """
    n = len(hrv_history)
    last_7 = hrv_history[-7:]
    last_60 = hrv_history[-60:] if n >= 60 else hrv_history

    mean_7 = statistics.mean(last_7)
    mean_60 = statistics.mean(last_60)
    sd_60 = statistics.stdev(last_60) if len(last_60) >= 2 else 1.0

    lower_bound = mean_60 - 0.5 * sd_60
    upper_bound = mean_60 + 0.5 * sd_60

    # Classify the weekly mean (not today's single value)
    status = _classify_recovery(mean_7, mean_60, lower_bound, upper_bound)

    swc = 0.5 * sd_60
    std_7 = statistics.stdev(last_7) if len(last_7) >= 2 else 1.0
    cv_7d = (std_7 / mean_7 * 100) if mean_7 > 0 else None
    trend = calculate_trend(last_7, window=7, **TREND_THRESHOLDS["hrv"])

    return RmssdStatusDTO(
        status=status,
        days_available=n,
        days_needed=0,
        rmssd_7d=round(mean_7, 1),
        rmssd_sd_7d=round(std_7, 2),
        rmssd_60d=round(mean_60, 1),
        rmssd_sd_60d=round(sd_60, 2),
        lower_bound=round(lower_bound, 1),
        upper_bound=round(upper_bound, 1),
        cv_7d=round(cv_7d, 1) if cv_7d else None,
        swc=round(swc, 2),
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


def calculate_sport_ctl(
    activities: list[Activity],
    tau: int = 42,
) -> dict[str, float]:
    """Calculate per-sport CTL (Chronic Training Load) from activity history.

    Uses exponential moving average (EMA) with tau=42 days, matching Intervals.icu's
    impulse-response model. Activities must span at least 42+ days for reliable values.

    Args:
        activities: Objects with attrs: type (str|None), icu_training_load (float|None),
                    start_date_local (str|date). Accepts Activity model or Activity ORM.
        tau: Time constant in days (default 42, matching Intervals.icu CTL).

    Returns:
        {"swim": float, "bike": float, "run": float} — CTL per sport.
        Returns 0.0 for sports with no activities.
    """
    if not activities:
        return {"swim": 0.0, "bike": 0.0, "run": 0.0}

    # Group daily load by sport
    daily_load: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for act in activities:
        raw_type = (act.type or "").lower().replace(" ", "")
        sport = SPORT_MAP.get(raw_type)
        if not sport:
            continue

        if act.icu_training_load is None:
            continue

        daily_load[sport][str(act.start_date_local)] += act.icu_training_load

    if not daily_load:
        return {"swim": 0.0, "bike": 0.0, "run": 0.0}

    # Find date range across all sports
    all_dates = set()
    for sport_dates in daily_load.values():
        all_dates.update(sport_dates.keys())

    min_date = date_type.fromisoformat(min(all_dates))
    max_date = date_type.fromisoformat(max(all_dates))

    # Calculate EMA for each sport day by day
    k = 1.0 / tau  # rate constant

    decay = math.exp(-k)

    result = {}
    for sport in ("swim", "bike", "run"):
        ctl = 0.0
        sport_loads = daily_load.get(sport, {})
        current = min_date
        while current <= max_date:
            ds = current.strftime("%Y-%m-%d")
            tss = sport_loads.get(ds, 0.0)
            ctl = ctl * decay + tss * (1 - decay)
            current += timedelta(days=1)
        result[sport] = round(ctl, 1)

    return result


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

_DECOUPLING_BIKE_TYPES = {"Ride", "VirtualRide"}
_DECOUPLING_RUN_TYPES = {"Run", "TrailRun"}


def decoupling_sport_group(activity_type: str) -> str | None:
    """Map activity type to sport group for decoupling analysis. Swim excluded."""
    if activity_type in _DECOUPLING_BIKE_TYPES:
        return "bike"
    if activity_type in _DECOUPLING_RUN_TYPES:
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
