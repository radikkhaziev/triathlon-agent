"""Training metrics calculations for triathlon training load management.

HRV recovery analysis (dual-algorithm), RHR baseline, trend analysis,
ESS (Banister TRIMP), per-sport CTL, combined recovery scoring,
cardiac drift (decoupling) analysis, and the deterministic taper planner.
"""

import math
import statistics
from collections import defaultdict
from datetime import date as date_type
from datetime import timedelta

import numpy as np
from sqlalchemy import select

from data.db import Activity, ActivityDetail, HrvAnalysis, RhrAnalysis, Wellness, get_sync_session
from data.intervals.dto import RecoveryScoreDTO, RecoveryStateDTO, RhrStatusDTO, RmssdStatusDTO, TrendResultDTO
from data.utils import is_bike, is_run, tsb_zone
from tasks.dto import local_today

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


RMSSD_MIN_DAYS = 14
RMSSD_SMOOTH_DAYS = 3


def rmssd_flatt_esco(hrv_history: list[float], smooth: int = RMSSD_SMOOTH_DAYS) -> RmssdStatusDTO:
    """Flatt & Esco (2016) — today's RMSSD vs 7-day baseline, asymmetric bounds.

    Optical RMSSD is noisy day-to-day (sleep position, sensor contact, hydration),
    so the value used for status classification is `mean(last `smooth` days)`, and
    the baseline window is shifted to the 7 days BEFORE the smoothing window to
    avoid leakage. The smoothed-today value and bounds are used internally only;
    the DTO exposes recency-7d stats (last 7 days inclusive) for downstream
    consumers' delta calculations.
    """
    n = len(hrv_history)
    if n < RMSSD_MIN_DAYS:
        return RmssdStatusDTO(
            status="insufficient_data",
            days_available=n,
            days_needed=RMSSD_MIN_DAYS - n,
        )

    if smooth < 1:
        raise ValueError(f"smooth must be >= 1, got {smooth}")
    smooth = min(smooth, n - 7)  # leave ≥7 days for the shifted baseline

    # Smoothed "today" — rolling mean of last `smooth` days. Exposed in the DTO
    # as ``rmssd_today_smoothed`` so consumers can explain status decisions.
    today_smoothed = statistics.mean(hrv_history[-smooth:])

    # Baseline: 7 days BEFORE the smoothing window (no leakage with today).
    # Guard above ensures n >= 14 so this slice has 7 elements.
    baseline_window = hrv_history[-(7 + smooth) : -smooth]
    mean_baseline = statistics.mean(baseline_window)
    std_baseline = statistics.stdev(baseline_window)

    lower_bound = mean_baseline - 1.0 * std_baseline
    upper_bound = mean_baseline + 0.5 * std_baseline

    status = _classify_recovery(today_smoothed, mean_baseline, lower_bound, upper_bound)

    # DTO stats — recency (last 7 days inclusive). Different window than the
    # bounds: rmssd_7d describes the recent week, bounds classify against the
    # pre-smoothing baseline. See DTO comment.
    last_7 = hrv_history[-7:]
    mean_7 = statistics.mean(last_7)
    std_7 = statistics.stdev(last_7)

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
        rmssd_today_smoothed=round(today_smoothed, 1),
        rmssd_7d=round(mean_7, 1),
        rmssd_sd_7d=round(std_7, 2),
        rmssd_60d=round(rmssd_60d, 1) if rmssd_60d is not None else None,
        rmssd_sd_60d=round(rmssd_sd_60d, 2) if rmssd_sd_60d is not None else None,
        lower_bound=round(lower_bound, 1),
        upper_bound=round(upper_bound, 1),
        cv_7d=round(cv_7d, 1) if cv_7d is not None else None,
        swc=round(swc, 2) if swc is not None else None,
        trend=trend,
    )


# ---------------------------------------------------------------------------
# RHR Baseline
# ---------------------------------------------------------------------------


RHR_MIN_DAYS = 7
RHR_SMOOTH_DAYS = 3


def rhr_baseline(rhr_history: list[float], smooth: int = RHR_SMOOTH_DAYS) -> RhrStatusDTO:
    """Resting HR baseline — smoothed today vs 30-day baseline (shifted).

    Optical RHR is noisy night-to-night (sleep position, sensor contact). Status
    is computed against `mean(last `smooth` days)` vs a baseline of the
    `(30 - smooth)` days that come BEFORE the smoothing window — shifted to
    avoid leakage of today's noise into the comparator. Inverted vs RMSSD:
    elevated smoothed RHR = under-recovered = red.

    DTO stats are recency (last 7 / 30 / 60 days inclusive), NOT the shifted
    baseline — downstream consumers read `rhr_30d` as "average of the last
    30 days", and `lower_bound`/`upper_bound` as classification thresholds.
    """
    n = len(rhr_history)
    if n < RHR_MIN_DAYS:
        return RhrStatusDTO(
            status="insufficient_data",
            days_available=n,
            days_needed=RHR_MIN_DAYS - n,
        )

    if smooth < 1:
        raise ValueError(f"smooth must be >= 1, got {smooth}")
    smooth = min(smooth, n - 1)  # always leave ≥1 day for the baseline window

    today_smoothed = statistics.mean(rhr_history[-smooth:])

    # Baseline window: up to 30 days BEFORE the smoothing window. For early-
    # athlete histories (RHR_MIN_DAYS=7, smooth=3 → only 4 days available),
    # the slice will have fewer than 30 elements — accept the narrower bounds.
    # `RHR_MIN_DAYS` + the `smooth=min(smooth, n-1)` clamp above together
    # guarantee `len(baseline_window) >= 1`; ``stdev`` needs ≥2 so we still
    # have a defensive fallback to sd=0 (single sample → degenerate band).
    baseline_window = rhr_history[-(30 + smooth) : -smooth]
    mean_baseline = statistics.mean(baseline_window)
    sd_baseline = statistics.stdev(baseline_window) if len(baseline_window) >= 2 else 0.0

    lower_bound = mean_baseline - 0.5 * sd_baseline
    upper_bound = mean_baseline + 0.5 * sd_baseline

    # Inverted: high RHR = red, low RHR = green
    if today_smoothed > upper_bound:
        status = "red"
    elif today_smoothed < lower_bound:
        status = "green"
    else:
        status = "yellow"

    # DTO recency stats — last 7 / 30 / 60 days inclusive
    last_7 = rhr_history[-7:]
    mean_7 = statistics.mean(last_7)
    sd_7 = statistics.stdev(last_7)

    last_30 = rhr_history[-30:] if n >= 30 else rhr_history
    mean_30 = statistics.mean(last_30)
    sd_30 = statistics.stdev(last_30) if len(last_30) >= 2 else 1.0

    rhr_60d = statistics.mean(rhr_history[-60:]) if n >= 60 else None
    rhr_sd_60d = statistics.stdev(rhr_history[-60:]) if n >= 60 else None

    cv_7d = (sd_7 / mean_7 * 100) if mean_7 > 0 else None
    trend = calculate_trend(last_7, window=7, **TREND_THRESHOLDS["resting_hr"])

    return RhrStatusDTO(
        status=status,
        days_available=n,
        days_needed=0,
        rhr_today=round(rhr_history[-1], 1),
        rhr_today_smoothed=round(today_smoothed, 1),
        rhr_7d=round(mean_7, 1),
        rhr_sd_7d=round(sd_7, 2),
        rhr_30d=round(mean_30, 1),
        rhr_sd_30d=round(sd_30, 2),
        rhr_60d=round(rhr_60d, 1) if rhr_60d is not None else None,
        rhr_sd_60d=round(rhr_sd_60d, 2) if rhr_sd_60d is not None else None,
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


def _project_loads_one_day(
    prev_ctl: float,
    prev_atl: float,
    tss_today: float,
    *,
    tau_ctl: int = 42,
    tau_atl: int = 7,
) -> tuple[float, float, float]:
    """Roll yesterday's (CTL, ATL) forward one day with `tss_today` actual load.

    Pure-math sibling of `recompute_today_loads` — split out so unit tests
    don't need DB fixtures. Returns (ctl, atl, tsb) rounded to 1 decimal.
    """
    decay_ctl = math.exp(-1.0 / tau_ctl)
    decay_atl = math.exp(-1.0 / tau_atl)
    ctl = prev_ctl * decay_ctl + tss_today * (1 - decay_ctl)
    atl = prev_atl * decay_atl + tss_today * (1 - decay_atl)
    return round(ctl, 1), round(atl, 1), round(ctl - atl, 1)


async def recompute_today_loads(user_id: int, today: date_type | None = None) -> tuple[float, float, float] | None:
    """Today's (CTL, ATL, TSB) from yesterday's wellness + today's completed TSS.

    Intervals.icu reports today's CTL/ATL with **planned** workouts baked in,
    so morning-time reads look as if the day's session is already done.
    This helper rolls yesterday's loads forward by one day and adds only
    activities that actually appear in `activities` for today.

    Pass `today` to pin the reference day — callers that already snapshotted
    `local_today()` avoid a midnight race where the second internal call rolls
    over to the next day. Defaults to `local_today()`.

    Returns None if yesterday's wellness row is missing or has no CTL/ATL —
    caller should fall back to whatever Intervals.icu reported.
    """
    today = today or local_today()
    yesterday = today - timedelta(days=1)

    prev = await Wellness.get(user_id, yesterday)
    if prev is None or prev.ctl is None or prev.atl is None:
        return None

    activities = await Activity.get_for_date(user_id, today)
    tss_today = sum(a.icu_training_load or 0.0 for a in activities)

    return _project_loads_one_day(prev.ctl, prev.atl, tss_today)


def recompute_today_loads_sync(user_id: int, today: date_type | None = None) -> tuple[float, float, float] | None:
    """Sync twin of `recompute_today_loads` for dramatiq actors.

    Same actual-only de-planning: rolls yesterday's wellness loads forward one
    day and adds only the TSS of activities that actually appear for today, so
    callers don't surface Intervals.icu's planned-workout-inflated CTL/ATL.
    Pass `today` to pin the reference day (avoids a midnight race when the caller
    already snapshotted `local_today()`); defaults to `local_today()`.
    `Wellness.get` / `Activity.get_for_date` are @dual — called without await
    here they dispatch to their sync paths. Returns None if yesterday's wellness
    row is missing or has no CTL/ATL.
    """
    today = today or local_today()
    yesterday = today - timedelta(days=1)

    prev = Wellness.get(user_id, yesterday)
    if prev is None or prev.ctl is None or prev.atl is None:
        return None

    activities = Activity.get_for_date(user_id, today)
    tss_today = sum(a.icu_training_load or 0.0 for a in activities)

    return _project_loads_one_day(prev.ctl, prev.atl, tss_today)


async def recompute_today_ramp(user_id: int, ctl_today: float) -> float | None:
    """Projected weekly ramp consistent with the de-planned today CTL.

    Intervals.icu's ``rampRate`` is the 7-day CTL change (``CTL_today − CTL_7d_ago``,
    verified empirically), so the value it ships for today carries the same
    planned-workout inflation that `recompute_today_loads` strips out of CTL/ATL.
    Recompute it against the projected CTL so ramp stays consistent with the
    de-planned CTL/ATL/TSB shown next to it.

    `ctl_today` is the projected (de-planned) CTL from `recompute_today_loads`.
    Returns None if the 7-day-ago wellness row is missing — caller should fall
    back to the raw `ramp_rate` Intervals.icu reported.
    """
    week_ago = local_today() - timedelta(days=7)
    prev = await Wellness.get(user_id, week_ago)
    if prev is None or prev.ctl is None:
        return None
    # 1 dp to match the de-planned CTL/ATL/TSB it sits beside — ramp is derived
    # from the 1-dp projected CTL, so extra precision would be spurious.
    return round(ctl_today - prev.ctl, 1)


def project_sport_load_forward(
    today_ctl: float,
    today_atl: float,
    daily_planned_load: dict[date_type, float],
    horizon: date_type,
    today: date_type,
    *,
    tau_ctl: int = 42,
    tau_atl: int = 7,
) -> tuple[list[tuple[date_type, float]], list[tuple[date_type, float]]]:
    """Forward-iterate per-sport CTL/ATL EMAs from `today` to `horizon` (inclusive)
    using planned daily TSS. Days without a scheduled workout contribute zero
    load — natural decay during rest gaps.

    Returns (ctl_series, atl_series) — each a list of (date, value) starting at
    `today + 1`. Empty if horizon <= today. Mathematically equivalent to what
    `calculate_sport_ctl` / `calculate_sport_atl` would produce on the same date
    if the planned workouts are executed exactly — it's the same EMA
    continuation, just driven by planned instead of actual TSS.
    """
    decay_ctl = math.exp(-1.0 / tau_ctl)
    decay_atl = math.exp(-1.0 / tau_atl)

    ctl, atl = today_ctl, today_atl
    ctl_series: list[tuple[date_type, float]] = []
    atl_series: list[tuple[date_type, float]] = []
    cur = today + timedelta(days=1)
    while cur <= horizon:
        load = daily_planned_load.get(cur, 0.0)
        ctl = ctl * decay_ctl + load * (1 - decay_ctl)
        atl = atl * decay_atl + load * (1 - decay_atl)
        ctl_series.append((cur, round(ctl, 1)))
        atl_series.append((cur, round(atl, 1)))
        cur += timedelta(days=1)

    return ctl_series, atl_series


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
# Base Building Protocol — chronic decoupling check (issue #157)
# ---------------------------------------------------------------------------

# The "2 of 3 red (>10%)" rule is also stated in prose to Claude in the chat
# system prompt (`bot/prompts.py`, ## Workout generation → Base Building Protocol)
# so the LLM-steered chat path caps to Z2 too. Keep the two in sync if the
# threshold/window changes.
# abs drift % above this counts as "red" — mirrors the decoupling_status red boundary.
_DECOUPLING_RED_THRESHOLD = 10.0
# Classify over the most recent N valid-for-decoupling efforts.
_DECOUPLING_CHECK_WINDOW = 3
# >= this many reds in the window → chronic (the "2 of 3" rule).
_DECOUPLING_CHRONIC_MIN_RED = 2


def classify_decoupling(values: list[float]) -> str:
    """Base Building Protocol status from recent cardiac-drift values.

    `values`: decoupling % of the most recent valid-for-decoupling bike/run efforts,
    oldest→newest. Only the last 3 are weighed:
      - chronic           — >=2 of 3 red (abs drift >10%): aerobic base compromised
      - acute             — exactly 1 of 3 red: single bad session, advisory only
      - normal            — 0 of 3 red
      - insufficient_data — fewer than 3 valid efforts

    Deterministic and DB-free: the whole point of issue #157 is to take the
    chronic/acute call away from the LLM. `abs()` matches decoupling_status —
    negative drift (pulse drops) is graded the same as positive.
    """
    if len(values) < _DECOUPLING_CHECK_WINDOW:
        return "insufficient_data"
    window = values[-_DECOUPLING_CHECK_WINDOW:]
    reds = sum(1 for v in window if abs(v) > _DECOUPLING_RED_THRESHOLD)
    if reds >= _DECOUPLING_CHRONIC_MIN_RED:
        return "chronic"
    if reds == 1:
        return "acute"
    return "normal"


def _valid_decoupling_values(rows: list[tuple], sport_group: str) -> list[float]:
    """Extract decoupling % of valid steady-state efforts, oldest→newest.

    `rows`: tuples of (start_date_local, type, moving_time, variability_index,
    hr_zone_times, decoupling, is_race), any order. Races are excluded (peak
    effort is not a base signal) and `is_valid_for_decoupling` applies the
    steady-state / duration / zone-adherence filter.
    """
    points: list[tuple] = []
    for start, atype, moving_time, vi, hzt, decoupling, is_race in rows:
        if is_race:
            continue
        if decoupling_sport_group(atype) != sport_group:
            continue
        if not is_valid_for_decoupling(atype, moving_time, vi, hzt, decoupling):
            continue
        points.append((start, decoupling))
    points.sort(key=lambda p: p[0])
    return [v for _, v in points]


def _decoupling_result(sport: str, values: list[float]) -> dict:
    """Wrap the classifier verdict for one sport group.

    No deactivation/transition flag on purpose: the report is stateless and runs
    daily, so a structural "just flipped out of chronic" signal would re-fire
    every morning until the next valid effort shifts `values`. Auto-deactivation
    is expressed by the standing chronic banner simply ceasing to appear once the
    trend recovers — no separate one-time announcement to get wrong."""
    return {
        "sport": sport,
        "status": classify_decoupling(values),
        "values": [round(v, 1) for v in values[-_DECOUPLING_CHECK_WINDOW:]],
        "valid_count": len(values),
    }


# Sport groups that carry a decoupling signal (swim excluded — see decoupling_sport_group).
_DECOUPLING_SPORT_GROUPS = ("bike", "run")


def decoupling_check_sync(user_id: int, sport: str, days_back: int = 90) -> dict:
    """Base Building Protocol check for one sport GROUP (``"bike"`` / ``"run"``;
    swim has no decoupling). Read-only, sync — the only consumer is the
    morning-report actor (Dramatiq is sync). Mirrors the `data/taper_service.py`
    twin pattern (raw sync session). Returns the `_decoupling_result` dict."""
    if sport not in _DECOUPLING_SPORT_GROUPS:
        return _decoupling_result(sport, [])
    today = local_today()
    with get_sync_session() as session:
        rows = session.execute(
            select(
                Activity.start_date_local,
                Activity.type,
                Activity.moving_time,
                ActivityDetail.variability_index,
                ActivityDetail.hr_zone_times,
                ActivityDetail.decoupling,
                Activity.is_race,
            )
            .join(ActivityDetail, ActivityDetail.activity_id == Activity.id)
            .where(
                Activity.user_id == user_id,
                Activity.start_date_local >= str(today - timedelta(days=days_back)),
                Activity.start_date_local <= str(today),
            )
            .order_by(Activity.start_date_local, Activity.id)
        ).all()
    return _decoupling_result(sport, _valid_decoupling_values([tuple(r) for r in rows], sport))


# ---------------------------------------------------------------------------
# Polarization Index
# ---------------------------------------------------------------------------


_PI_POLARIZED_THRESHOLD = 2.0


def polarization_index(low_pct: float, mid_pct: float, high_pct: float) -> float | None:
    """Treff et al. (2019) polarization index.

    PI = log10((Z1 / Z2) × Z3), with Z1/Z2/Z3 the easy/moderate/hard share of time
    in percent. PI > 2.0 → polarized (hard share dominates moderate).
    Returns None when Z2 or Z3 ≈ 0 (degenerate — fall back to the %-pattern).

    Note: the classic «80/12/8» polarized label (Esteve-Lanao) has Z2 > Z3 and scores
    PI ≈ 1.73 — pyramidal by the strict index. True polarized needs Z3 > Z2.
    """
    if low_pct <= 0 or mid_pct <= 0 or high_pct <= 0:
        return None
    return round(math.log10((low_pct / mid_pct) * high_pct), 2)


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
    Returns {low_pct, mid_pct, high_pct, total_hours, pattern, polarization_index, by_zone}.

    Note: `polarization_index` here is the AGGREGATE window index over all activities
    (Treff 2019, see polarization_index()), distinct from Intervals.icu's per-activity
    `ActivityDetail.polarization_index` column.
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
            "polarization_index": None,
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
        "polarization_index": polarization_index(low_pct, mid_pct, high_pct),
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


# Sport- and phase-calibrated target intensity distribution.
# Band = (low_target, mid_max, high_target, high_max) in % of time.
# base/build → pyramidal (more easy, Z3 < Z2); peak/race → polarized (Z3 > Z2, PI > 2).
# Cycling naturally carries more Z2, so its easy target is lower / Z2 ceiling higher.
# Sources: Esteve-Lanao 2007, Stöggl & Sperlich 2015, Sperlich 2023 — see
# docs/knowledge/intensity-distribution.md.
_TID_BANDS: dict[str, dict[str, tuple[float, float, float, float]]] = {
    "run": {"pyramidal": (84.0, 14.0, 6.0, 10.0), "polarized": (80.0, 8.0, 12.0, 15.0)},
    "swim": {"pyramidal": (84.0, 14.0, 6.0, 10.0), "polarized": (80.0, 8.0, 12.0, 15.0)},
    "ride": {"pyramidal": (72.0, 32.0, 6.0, 10.0), "polarized": (66.0, 24.0, 10.0, 15.0)},
}
_PHASE_MODEL = {
    "base": "pyramidal",
    "build": "pyramidal",
    "peak": "polarized",
    "race": "polarized",
    "taper": "polarized",
}
# PI > 2.0 is a meaningful «polarized» gate only for run/swim. Cycling naturally carries
# more Z2 (Sperlich 2023), so a realistic ride-polarized target (Z2 ≈ 24%) scores PI ≈ 1.4
# and could never satisfy a 2.0 gate — no PI target for ride.
_PI_TARGET_SPORTS = {"run", "swim"}


def _tid_band(sport_key: str, model: str) -> dict:
    low_t, mid_max, high_t, high_max = _TID_BANDS[sport_key][model]
    pi_target = _PI_POLARIZED_THRESHOLD if (model == "polarized" and sport_key in _PI_TARGET_SPORTS) else None
    return {
        "model": model,
        "low_pct_target": low_t,
        "mid_pct_max": mid_max,
        "high_pct_target": high_t,
        "high_pct_max": high_max,
        "pi_target_min": pi_target,
    }


def target_distribution(sport: str, phase: str | None = None) -> dict:
    """Target easy/moderate/hard band for a sport + training phase.

    sport: "run" | "ride" | "swim" (unknown → run band).
    phase: "base"/"build" → pyramidal target, "peak"/"race"/"taper" → polarized.
        None → returns both bands (base + race), flagged phase-dependent, since the
        athlete's macrocycle phase isn't resolved yet (auto-derivation = Phase 3).
    """
    sport_key = sport.lower()
    if sport_key not in _TID_BANDS:
        sport_key = "run"
    if phase is None:
        return {
            "sport": sport_key,
            "phase": "unspecified",
            "model": "phase-dependent",
            "base": _tid_band(sport_key, "pyramidal"),
            "race": _tid_band(sport_key, "polarized"),
        }
    model = _PHASE_MODEL.get(phase.lower(), "pyramidal")
    return {"sport": sport_key, "phase": phase.lower(), **_tid_band(sport_key, model)}


def delta_vs_target(low_pct: float, mid_pct: float, high_pct: float, band: dict) -> dict:
    """Gaps between an actual distribution and a single target band.

    band: a flat band dict from target_distribution(..., phase=<concrete>) or one of its
    base/race sub-bands. Positive mid_over / high_over = over the gray-zone / hard ceiling;
    negative low_gap = not enough easy volume.

    `verdict` is the single headline issue, ordered by coaching severity (most dangerous
    first): too_much_hard → too_little_easy → too_much_z2. `issues` keeps that order.
    """
    if "low_pct_target" not in band:
        raise ValueError(
            "delta_vs_target needs a concrete-phase band; got a dual-band dict "
            "(target_distribution with phase=None) — pass band['base'] or band['race']"
        )
    low_gap = round(low_pct - band["low_pct_target"], 1)
    mid_over = round(mid_pct - band["mid_pct_max"], 1)
    high_over = round(high_pct - band["high_pct_max"], 1)
    # Append in severity order so issues[0] (the headline verdict) surfaces the most
    # dangerous problem: overtraining risk first, then aerobic-base deficit, then gray zone.
    issues: list[str] = []
    if high_over > 0:
        issues.append("too_much_hard")
    if low_gap < -5:
        issues.append("too_little_easy")
    if mid_over > 0:
        issues.append("too_much_z2")
    return {
        "low_gap": low_gap,
        "mid_over": mid_over,
        "high_over": high_over,
        "issues": issues,
        "verdict": issues[0] if issues else "on_target",
    }


# ---------------------------------------------------------------------------
# Taper Planner
# ---------------------------------------------------------------------------

# Per-class corridors from docs/knowledge/taper.md (Bosquet / Le Meur / Smyth /
# Fortes / Divsalar). `days` is the taper length corridor INCLUSIVE of race day
# (the spec's example counts 2026-06-15..2026-06-28 as 14 days with race day
# last). `reduction` is the overall volume cut vs peak daily load, in %, over
# the training days only (race day's zero is not a training choice). `min_ctl`
# is the coarse "fitness actually banked" guard (spec §4.4) — below it a deep
# taper has nothing to release, so the grid clamps to the shortest class length.
_TAPER_CLASS_PARAMS: dict[str, dict] = {
    "long": {"days": (14, 21), "reduction": (50.0, 65.0), "min_ctl": 60.0},
    "standard": {"days": (10, 14), "reduction": (41.0, 60.0), "min_ctl": 45.0},
    "short": {"days": (7, 14), "reduction": (50.0, 70.0), "min_ctl": 35.0},
}
_TAPER_TAU_GRID = (3, 4, 5)
# Past this horizon the plan is an estimate: CTL at taper start is unknowable,
# so daily targets are withheld (false precision) — see spec §6, decision 2026-06-12.
# CONTRACT: api/routers/dashboard.py:_FORECAST_FALLBACK_DAYS (28) must stay >=
# this value so race day is always on the LoadDetail chart axis when the
# taper overlay is visible.
_TAPER_EARLY_HORIZON_DAYS = 21
_TAPER_LANDING_ZONES = ("fresh", "transition")
_TAPER_DEGENERATE_TAU = 4

# User-facing rules stay Russian (spec §3 output contract); Claude translates
# per user language downstream. Two-phase bump and sprint priming ship as
# optional hints, not daily_targets entries — evidence base is weaker (§7).
_TAPER_RULES = [
    "Держи интенсивность: race-pace/качественные сессии оставь, режь объём через длительность.",
    "Держи частоту сессий — не выкидывай тренировочные дни.",
    "Опционально: +20–30% load за 3 дня до гонки (two-phase bump) — доказательная база слабее основного консенсуса.",
    "Опционально: раз в неделю 3–6×10–30с спринтов с полным отдыхом внутри лёгкой сессии "
    "(нейромышечный priming; данные из off-season, экстраполировать осторожно).",
]


def _simulate_taper_candidate(
    ctl_now: float,
    atl_now: float,
    today: date_type,
    race_date: date_type,
    length: int,
    tau: int,
    peak: float,
) -> dict:
    """Forward-simulate one (length, tau) taper candidate to race morning.

    The schedule runs `length - 1` training days `w(i) = peak·e^(−(i+1)/τ)`
    from `taper_start = race_date − (length−1)`, then race day at zero load.
    The decay starts at i+1 so day 0 is already reduced (~78% of peak at τ=4) —
    prescribing a full peak-load session as the first taper day would
    contradict the "cut duration" rule. The race-day series entry (zero load)
    IS race-morning state: one decay step past the last training day.
    Pre-taper days hold steady at `ctl_now` — the EMA steady-state assumption
    ("keep training as before").
    """
    taper_start = race_date - timedelta(days=length - 1)
    schedule = [(taper_start + timedelta(days=i), peak * math.exp(-(i + 1) / tau)) for i in range(length - 1)]
    schedule.append((race_date, 0.0))

    ctl, atl = ctl_now, atl_now
    loads = {d: w for d, w in schedule if d > today}
    if taper_start == today:
        # `project_sport_load_forward` starts at today+1; today's planned
        # opener must roll the morning state forward first.
        ctl, atl, _ = _project_loads_one_day(ctl, atl, schedule[0][1])
    cur = today + timedelta(days=1)
    while cur < taper_start:
        loads[cur] = ctl_now
        cur += timedelta(days=1)

    ctl_series, atl_series = project_sport_load_forward(ctl, atl, loads, race_date, today)
    ctl_race, atl_race = ctl_series[-1][1], atl_series[-1][1]
    train_loads = [w for _, w in schedule[:-1]]
    reduction = round((1 - statistics.fmean(train_loads) / peak) * 100, 1)

    daily_targets = []
    for i, (d, w) in enumerate(schedule):
        note = None
        if i == 0:
            note = "режь длительность, не интенсивность"
        elif d == race_date:
            note = "race day"
        daily_targets.append({"date": d, "target_tss": round(w), "pct_of_peak": round(w / peak * 100), "note": note})

    tsb_race = round(ctl_race - atl_race, 1)
    return {
        "taper_start_date": taper_start,
        "taper_days": length,
        "tau_taper": tau,
        "reduction": reduction,
        "daily_targets": daily_targets,
        "ctl_race": ctl_race,
        "atl_race": atl_race,
        "tsb_race": tsb_race,
        "tsb_zone": tsb_zone(tsb_race),
        "p_race": round(ctl_race - 2 * atl_race, 1),
    }


def _degenerate_taper_plan(
    ctl_now: float,
    atl_now: float,
    today: date_type,
    race_date: date_type,
    peak: float,
    params: dict,
    warnings: list[str],
) -> dict:
    """Race is tomorrow — no grid-search, just the tail of a canonical taper.

    Today's target is what the last training day of the shortest class-length
    taper (τ=4) would prescribe — a small opener (~10% of peak), then race.
    """
    min_days = params["days"][0]
    opener = peak * math.exp(-(min_days - 1) / _TAPER_DEGENERATE_TAU)
    ctl, atl, _ = _project_loads_one_day(ctl_now, atl_now, opener)
    ctl_race, atl_race, tsb_race = _project_loads_one_day(ctl, atl, 0.0)
    return {
        "taper_start_date": today,
        "taper_days": 2,
        "tau_taper": _TAPER_DEGENERATE_TAU,
        # Single-training-day window: reduction == the opener's cut, not a
        # multi-day mean like the grid path produces.
        "volume_reduction_pct": round((1 - opener / peak) * 100, 1),
        "daily_targets": [
            {"date": today, "target_tss": round(opener), "pct_of_peak": round(opener / peak * 100), "note": None},
            {"date": race_date, "target_tss": 0, "pct_of_peak": 0, "note": "race day"},
        ],
        "projected_race_day": {
            "ctl": ctl_race,
            "atl": atl_race,
            "tsb": tsb_race,
            "p_banister": round(ctl_race - 2 * atl_race, 1),
            "tsb_zone": tsb_zone(tsb_race),
        },
        "rules": list(_TAPER_RULES),
        "confidence": "late",
        "warnings": warnings + ["degenerate_window"],
    }


def build_taper_plan(
    *,
    race_date: date_type,
    today: date_type,
    ctl_now: float,
    atl_now: float,
    peak_daily_load: float,
    race_distance_class: str = "standard",
) -> dict:
    """Deterministic taper schedule: daily TSS targets from today to race day.

    Pure function (no I/O) per docs/TAPER_PLANNER_SPEC.md Phase 1. Grid-search
    over taper length (class corridor, §5) × τ_taper (3–5). EMA math is the
    exp-decay form shared with `project_sport_load_forward` (NOT the linearised
    `1/τ` recursion the spec sketches — that would drift from Intervals.icu
    values).

    Selection tiers: TSB landing constraint first (else best-by-p + warning),
    then the volume-reduction corridor — candidates outside it are dropped in
    favour of the nearest ones (spec §4.5: clamp length/τ, not volume), then
    max Banister form `p = CTL − 2·ATL` on race morning. In practice the
    corridor dominates: reduction is a near-injective function of (length, τ)
    alone, so the corridor filter usually collapses the pool to one candidate
    and p acts as a tie-break — the chosen (length, τ) is a class property,
    largely independent of the athlete's CTL/ATL (which drive the projection,
    not the choice).

    `confidence`: "early" (>21d out — `daily_targets` AND `projected_race_day`
    withheld as false precision, start date is an estimate), "ok", or "late"
    (window too short for the class corridor).
    Raises ValueError on race in the past, non-positive peak, unknown class.
    """
    if race_distance_class not in _TAPER_CLASS_PARAMS:
        raise ValueError(f"unknown race_distance_class: {race_distance_class!r}")
    if race_date <= today:
        raise ValueError("race_date must be in the future")
    if peak_daily_load <= 0:
        raise ValueError("peak_daily_load must be positive")

    params = _TAPER_CLASS_PARAMS[race_distance_class]
    days_to_race = (race_date - today).days
    warnings: list[str] = []
    if ctl_now < params["min_ctl"]:
        warnings.append("low_ctl")

    if days_to_race < 2:
        return _degenerate_taper_plan(ctl_now, atl_now, today, race_date, peak_daily_load, params, warnings)

    lo_days, hi_days = params["days"]
    if "low_ctl" in warnings:
        hi_days = lo_days  # shallowest taper — nothing banked to release; τ is still grid-searched
    hi_days = min(hi_days, days_to_race + 1)  # taper_start can't precede today
    lo_days = min(lo_days, hi_days)
    if hi_days < params["days"][0]:
        warnings.append("short_window")

    candidates = [
        _simulate_taper_candidate(ctl_now, atl_now, today, race_date, length, tau, peak_daily_load)
        for length in range(lo_days, hi_days + 1)
        for tau in _TAPER_TAU_GRID
    ]

    pool = [c for c in candidates if c["tsb_zone"] in _TAPER_LANDING_ZONES]
    if not pool:
        pool = candidates
        warnings.append("tsb_lands_outside_target")
    red_lo, red_hi = params["reduction"]

    def corridor_dist(c: dict) -> float:
        return max(red_lo - c["reduction"], c["reduction"] - red_hi, 0.0)

    best_dist = min(corridor_dist(c) for c in pool)
    pool = [c for c in pool if corridor_dist(c) <= best_dist + 1e-9]
    best = max(pool, key=lambda c: c["p_race"])

    early = days_to_race > _TAPER_EARLY_HORIZON_DAYS
    if early:
        # tsb_lands_outside_target is a verdict of the same flat-held-CTL
        # simulation whose projection is withheld below — leaking it would
        # re-assert the suppressed precision. low_ctl stays: it's computed
        # from today's actual CTL, not the simulation.
        if "tsb_lands_outside_target" in warnings:
            warnings.remove("tsb_lands_outside_target")
        warnings.append("early_estimate")
        confidence = "early"
    elif days_to_race + 1 < params["days"][0]:
        confidence = "late"
    else:
        confidence = "ok"

    return {
        "taper_start_date": best["taper_start_date"],
        "taper_days": best["taper_days"],
        "tau_taper": best["tau_taper"],
        "volume_reduction_pct": best["reduction"],
        "daily_targets": [] if early else best["daily_targets"],
        # Early mode withholds the projection too: it's simulated from today's
        # CTL/ATL held flat for 20+ days — the same false precision the gate
        # exists to suppress. Phase 2 re-runs closer to race day for real numbers.
        "projected_race_day": (
            None
            if early
            else {
                "ctl": best["ctl_race"],
                "atl": best["atl_race"],
                "tsb": best["tsb_race"],
                "p_banister": best["p_race"],
                "tsb_zone": best["tsb_zone"],
            }
        ),
        "rules": list(_TAPER_RULES),
        "confidence": confidence,
        "warnings": warnings,
    }
