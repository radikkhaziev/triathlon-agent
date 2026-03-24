"""Training metrics calculations for triathlon training load management.

Implements TSS (Training Stress Score) calculations for swimming, cycling,
and running, along with CTL/ATL/TSB (fitness/fatigue/form) tracking,
a composite readiness score, HRV recovery analysis (dual-algorithm),
RHR baseline, ESS (Banister TRIMP), and combined recovery scoring.
"""

import math
import statistics
from collections import defaultdict
from datetime import date as date_type
from datetime import timedelta

import numpy as np

from config import settings
from data.models import (
    Activity,
    HRVData,
    ReadinessLevel,
    RecoveryScore,
    RecoveryState,
    RhrStatus,
    RmssdStatus,
    TrendResult,
    Wellness,
)
from data.utils import SPORT_MAP

# Heart Rate Zones as percentage of LTHR (Lactate Threshold Heart Rate)
HR_ZONES: dict[str, dict[int, tuple[float, float]]] = {
    "run": {
        1: (0.00, 0.72),  # Recovery
        2: (0.72, 0.82),  # Aerobic base
        3: (0.82, 0.87),  # Tempo
        4: (0.87, 0.92),  # Sub-threshold
        5: (0.92, 1.00),  # VO2max
    },
    "bike": {
        1: (0.00, 0.68),
        2: (0.68, 0.83),
        3: (0.83, 0.94),
        4: (0.94, 1.05),
        5: (1.05, 1.20),
    },
}


def calc_hr_tss(
    duration_sec: float,
    avg_hr: float,
    resting_hr: float,
    max_hr: float,
    lthr: float,
) -> float:
    """Heart Rate TSS calculation.

    Uses the ratio of average HR to lactate threshold HR
    to estimate training stress similar to power-based TSS.
    """
    if lthr == resting_hr:
        return 0.0
    intensity_factor = (avg_hr - resting_hr) / (lthr - resting_hr)
    tss = (duration_sec / 3600) * intensity_factor**2 * 100
    return round(tss, 1)


def calc_power_tss(
    duration_sec: float,
    normalized_power: float,
    ftp: float,
) -> float:
    """Standard TSS formula used by TrainingPeaks."""
    if ftp == 0:
        return 0.0
    intensity_factor = normalized_power / ftp
    tss = (duration_sec * normalized_power * intensity_factor) / (ftp * 3600) * 100
    return round(tss, 1)


def calc_swim_tss(
    distance_m: float,
    duration_sec: float,
    css_per_100m: float,
) -> float:
    """Swim-Specific TSS based on Critical Swim Speed (CSS)."""
    if distance_m == 0 or duration_sec == 0 or css_per_100m == 0:
        return 0.0
    pace_per_100m = (duration_sec / distance_m) * 100
    intensity_factor = css_per_100m / pace_per_100m
    tss = (duration_sec / 3600) * intensity_factor**2 * 100
    return round(tss, 1)


def update_ctl_atl(
    tss_history: list[float],
    ctl_days: int = 42,
    atl_days: int = 7,
) -> tuple[float, float, float]:
    """Fitness / Fatigue / Form model (Performance Manager Chart).

    CTL (Chronic Training Load)   = 42-day EMA of TSS -> "fitness"
    ATL (Acute Training Load)     = 7-day EMA of TSS  -> "fatigue"
    TSB (Training Stress Balance) = CTL - ATL         -> "form"

    TSB Interpretation:
        TSB > +10     -> under-training, fitness declining
        TSB -10..+10  -> optimal zone, good form
        TSB -10..-25  -> productive overreach
        TSB < -25     -> overtraining risk, injury/illness risk
    """
    ctl_k = 2 / (ctl_days + 1)
    atl_k = 2 / (atl_days + 1)

    ctl, atl = 0.0, 0.0
    for tss in tss_history:
        ctl = tss * ctl_k + ctl * (1 - ctl_k)
        atl = tss * atl_k + atl * (1 - atl_k)

    tsb = ctl - atl
    return round(ctl, 1), round(atl, 1), round(tsb, 1)


def calculate_readiness(
    hrv: HRVData,
    sleep: Wellness,
    resting_hr: float,
    resting_hr_baseline: float,
) -> tuple[int, ReadinessLevel]:
    """Calculate composite readiness score from physiological signals.

    Weighted from 3 components:
    - HRV delta from baseline (35%)
    - Sleep score (40%)
    - Resting HR deviation from baseline (25%)
    """
    score = 100

    # HRV component (weight: 35%)
    hrv_delta = (hrv.hrv_last_night - hrv.hrv_weekly_avg) / hrv.hrv_weekly_avg if hrv.hrv_weekly_avg != 0 else 0.0
    if hrv_delta < -0.20:
        score -= 35
    elif hrv_delta < -0.10:
        score -= 20
    elif hrv_delta < -0.05:
        score -= 10
    elif hrv_delta > +0.10:
        score += 5  # bonus for good recovery

    # Sleep component (weight: 40%)
    sleep_score = sleep.sleep_score or 0
    if sleep_score < 50:
        score -= 40
    elif sleep_score < 65:
        score -= 20
    elif sleep_score < 75:
        score -= 10

    # Resting HR component (weight: 25%)
    hr_delta = resting_hr - resting_hr_baseline
    if hr_delta > 7:
        score -= 25
    elif hr_delta > 4:
        score -= 13
    elif hr_delta > 2:
        score -= 5

    score = max(0, min(100, score))

    if score >= 80:
        level = ReadinessLevel.GREEN
    elif score >= 60:
        level = ReadinessLevel.YELLOW
    else:
        level = ReadinessLevel.RED

    return score, level


# ---------------------------------------------------------------------------
# Trend Analysis
# ---------------------------------------------------------------------------


def _calculate_trend(
    values: list[float],
    window: int = 7,
    threshold_weak: float = 0.5,
    threshold_strong: float = 2.0,
) -> TrendResult:
    """Calculate trend direction using linear regression (least squares).

    Fits a straight line through the last `window` data points.
    The slope tells us how many units the metric changes per day.
    """
    data = values[-window:]

    if len(data) < 3:
        return TrendResult(direction="stable", slope=0.0, r_squared=0.0, emoji="→")

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

    return TrendResult(
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


def _rmssd_flatt_esco(hrv_history: list[float]) -> RmssdStatus:
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
    trend = _calculate_trend(last_7, window=7, **TREND_THRESHOLDS["hrv"])

    return RmssdStatus(
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


def _rmssd_ai_endurance(hrv_history: list[float]) -> RmssdStatus:
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
    trend = _calculate_trend(last_7, window=7, **TREND_THRESHOLDS["hrv"])

    return RmssdStatus(
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


async def calculate_rmssd_status(algorithm: str | None = None, *, session=None) -> RmssdStatus:
    """Dispatcher: loads HRV history from DB, delegates to selected algorithm.

    Args:
        algorithm: "flatt_esco" or "ai_endurance". Defaults to settings.HRV_ALGORITHM.
        session: optional AsyncSession to reuse an existing transaction.
    """
    from data.database import get_hrv_history

    algo = algorithm or settings.HRV_ALGORITHM
    hrv_history = await get_hrv_history(days=60, session=session)
    n = len(hrv_history)
    MIN_DAYS = 14

    if n < MIN_DAYS:
        return RmssdStatus(
            status="insufficient_data",
            days_available=n,
            days_needed=MIN_DAYS - n,
        )

    if algo == "ai_endurance":
        return _rmssd_ai_endurance(hrv_history)

    return _rmssd_flatt_esco(hrv_history)


# ---------------------------------------------------------------------------
# Resting HR Analysis
# ---------------------------------------------------------------------------


async def calculate_rhr_status(*, session=None) -> RhrStatus:
    """Resting HR baseline analysis.

    Compares today's RHR vs 30-day rolling baseline.
    Inverted vs RMSSD: elevated RHR = under-recovered.
    Computes 7d, 30d, and 60d baselines.

    Args:
        session: optional AsyncSession to reuse an existing transaction.
    """
    from data.database import get_rhr_history

    rhr_history = await get_rhr_history(days=60, session=session)
    n = len(rhr_history)
    MIN_DAYS = 7

    if n < MIN_DAYS:
        return RhrStatus(
            status="insufficient_data",
            days_available=n,
            days_needed=MIN_DAYS - n,
        )

    today_rhr = rhr_history[-1]

    # 7-day baseline
    last_7 = rhr_history[-7:]
    mean_7 = statistics.mean(last_7)
    sd_7 = statistics.stdev(last_7) if len(last_7) >= 2 else 1.0

    # 30-day baseline (used for status bounds)
    last_30 = rhr_history[-30:] if n >= 30 else rhr_history
    mean_30 = statistics.mean(last_30)
    sd_30 = statistics.stdev(last_30) if len(last_30) >= 2 else 1.0

    lower_bound = mean_30 - 0.5 * sd_30
    upper_bound = mean_30 + 0.5 * sd_30

    # 60-day baseline (context only)
    rhr_60d = statistics.mean(rhr_history[-60:]) if n >= 60 else None
    rhr_sd_60d = statistics.stdev(rhr_history[-60:]) if n >= 60 else None

    # Inverted: high RHR = red, low RHR = green
    if today_rhr > upper_bound:
        status = "red"
    elif today_rhr < lower_bound:
        status = "green"
    else:
        status = "yellow"

    cv_7d = (sd_7 / mean_7 * 100) if mean_7 > 0 else None
    trend = _calculate_trend(last_7, window=7, **TREND_THRESHOLDS["resting_hr"])

    return RhrStatus(
        status=status,
        days_available=n,
        days_needed=0,
        rhr_today=round(today_rhr, 1),
        rhr_7d=round(mean_7, 1),
        rhr_sd_7d=round(sd_7, 2),
        rhr_30d=round(mean_30, 1),
        rhr_sd_30d=round(sd_30, 2),
        rhr_60d=round(rhr_60d, 1) if rhr_60d else None,
        rhr_sd_60d=round(rhr_sd_60d, 2) if rhr_sd_60d else None,
        lower_bound=round(lower_bound, 1),
        upper_bound=round(upper_bound, 1),
        cv_7d=round(cv_7d, 1) if cv_7d is not None else None,
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
) -> float:
    """Banister TRIMP-based External Stress Score.

    Normalised so 1 hour at LTHR ≈ ESS 100.

    Args:
        duration_min: Activity duration in minutes.
        avg_hr: Average heart rate during activity.
        hr_rest: Athlete's resting heart rate.
        hr_max: Athlete's maximum heart rate.
    """
    if hr_max <= hr_rest or avg_hr <= hr_rest or duration_min <= 0:
        return 0.0

    hr_ratio = (avg_hr - hr_rest) / (hr_max - hr_rest)
    trimp = duration_min * hr_ratio * 0.64 * math.exp(1.92 * hr_ratio)

    # Normalise: TRIMP for 60 min at LTHR = ESS 100
    lthr_ratio = (settings.ATHLETE_LTHR_RUN - hr_rest) / (hr_max - hr_rest)
    trimp_threshold = 60 * lthr_ratio * 0.64 * math.exp(1.92 * lthr_ratio)

    if trimp_threshold == 0:
        return 0.0

    return round(trimp / trimp_threshold * 100, 1)


# ---------------------------------------------------------------------------
# Daily ESS Aggregation
# ---------------------------------------------------------------------------


def calculate_daily_ess(activities: list, hr_rest: float, hr_max: float) -> float:
    """Sum ESS for all activities on a given day.

    Args:
        activities: List of Activity/ActivityRow for one day.
            Each must have `moving_time` (int, seconds) and `average_hr` (float|None).
        hr_rest: Athlete resting HR.
        hr_max: Athlete max HR.

    Returns:
        Total ESS for the day. 0.0 if no activities or no HR data.

    Note:
        Delegates to calculate_ess() which reads settings.ATHLETE_LTHR_RUN
        for TRIMP normalisation.
    """
    total = 0.0
    for act in activities:
        if act.moving_time and act.average_hr and act.average_hr > 0:
            duration_min = act.moving_time / 60.0
            total += calculate_ess(duration_min, act.average_hr, hr_rest, hr_max)
    return round(total, 1)


# ---------------------------------------------------------------------------
# Banister Recovery Model
# ---------------------------------------------------------------------------


def calculate_banister_recovery(
    training_log: list[dict],
    k: float = 0.1,
    tau: float = 2.0,
    initial_recovery: float = 100.0,
) -> list[RecoveryState]:
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
    results: list[RecoveryState] = []

    for entry in training_log:
        ess = entry.get("ess", 0)
        r = r + (100.0 - r) * recovery_rate - k * ess
        r = max(0.0, min(100.0, r))

        dt = entry["date"]
        if isinstance(dt, str):
            dt = date_type.fromisoformat(dt)

        results.append(
            RecoveryState(
                date=dt,
                recovery_pct=round(r, 1),
                ess=round(ess, 1),
            )
        )

    return results


def calculate_banister_for_date(
    activities_by_date: dict[str, list],
    target_date: date_type,
    hr_rest: float,
    hr_max: float,
    lookback_days: int = 90,
    k: float = 0.1,
    tau: float = 2.0,
) -> tuple[float, float]:
    """Calculate Banister recovery and today's ESS for a specific date.

    Args:
        activities_by_date: Mapping "YYYY-MM-DD" → list of activity objects.
        target_date: The date to calculate recovery for.
        hr_rest: Athlete resting HR.
        hr_max: Athlete max HR.
        lookback_days: How many days of history to use.
        k: Load sensitivity (0.01-1.0).
        tau: Recovery time constant in days (0.5-7.0).

    Returns:
        (banister_recovery_pct, ess_today)
    """
    start = target_date - timedelta(days=lookback_days)

    training_log = []
    current = start
    while current <= target_date:
        date_str = current.isoformat()
        day_acts = activities_by_date.get(date_str, [])
        ess = calculate_daily_ess(day_acts, hr_rest, hr_max)
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
                    start_date_local (str|date). Accepts Activity model or ActivityRow ORM.
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
        date_str = str(act.start_date_local)[:10]
        daily_load[sport][date_str] += act.icu_training_load

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
    rmssd_status: RmssdStatus,
    rhr_status: RhrStatus,
    banister_recovery: float,
    sleep_score: int | None,
    sleep_start_hour: float | None = None,
) -> RecoveryScore:
    """Weighted integration of 4 recovery signals into a single 0-100 score.

    Weights (when all signals available):
        RMSSD status      35%
        Banister R(t)     25%
        Resting HR status 20%
        Sleep score       20%

    When sleep_score is None, sleep is excluded and remaining weights are
    renormalised (RMSSD 43.75%, Banister 31.25%, RHR 25%).

    Modifiers:
        sleep_start > 23:00  →  -10 pts
        CV 7d > 15%          →  -5 pts
    """
    rmssd_score = _STATUS_TO_SCORE.get(rmssd_status.status, 50)
    rhr_score = _STATUS_TO_SCORE.get(rhr_status.status, 50)
    banister_pct = max(0.0, min(100.0, banister_recovery))

    if sleep_score is not None:
        sleep_pct = max(0.0, min(100.0, float(sleep_score)))
        score = rmssd_score * 0.35 + banister_pct * 0.25 + rhr_score * 0.20 + sleep_pct * 0.20
    else:
        # No sleep data — renormalise weights (0.35 + 0.25 + 0.20 = 0.80)
        score = (rmssd_score * 0.35 + banister_pct * 0.25 + rhr_score * 0.20) / 0.80

    flags: list[str] = []
    if sleep_start_hour is not None and sleep_start_hour > 23.0:
        score -= 10
        flags.append("late_sleep")
    if rmssd_status.cv_7d and rmssd_status.cv_7d > 15:
        score -= 5
        flags.append("hrv_unstable")
    if rmssd_status.trend and rmssd_status.trend.direction in ("declining", "declining_fast"):
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
    if rmssd_status.status == "red":
        recommendation = "skip"
    elif category in ("excellent", "good"):
        recommendation = "zone2_ok"
    elif category == "moderate":
        recommendation = "zone1_long"
    else:
        recommendation = "zone1_short"

    return RecoveryScore(
        score=round(score, 1),
        category=category,
        recommendation=recommendation,
        flags=flags,
        components={
            "rmssd": rmssd_score,
            "banister": round(banister_pct, 1),
            "rhr": rhr_score,
            "sleep": round(sleep_pct, 1) if sleep_score is not None else None,
        },
    )
