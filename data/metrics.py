"""Training metrics calculations for triathlon training load management.

Implements TSS (Training Stress Score) calculations for swimming, cycling,
and running, along with CTL/ATL/TSB (fitness/fatigue/form) tracking and
a composite readiness score based on physiological signals.
"""

from data.models import HRVData, SleepData, ReadinessLevel


# Heart Rate Zones as percentage of LTHR (Lactate Threshold Heart Rate)
HR_ZONES: dict[str, dict[int, tuple[float, float]]] = {
    "run": {
        1: (0.00, 0.72),   # Recovery
        2: (0.72, 0.82),   # Aerobic base
        3: (0.82, 0.87),   # Tempo
        4: (0.87, 0.92),   # Sub-threshold
        5: (0.92, 1.00),   # VO2max
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

    Args:
        duration_sec: Activity duration in seconds.
        avg_hr: Average heart rate during the activity.
        resting_hr: Athlete's resting heart rate.
        max_hr: Athlete's maximum heart rate.
        lthr: Lactate threshold heart rate.

    Returns:
        Estimated TSS value rounded to 1 decimal place.
    """
    if lthr == resting_hr:
        return 0.0
    intensity_factor = (avg_hr - resting_hr) / (lthr - resting_hr)
    tss = (duration_sec / 3600) * intensity_factor ** 2 * 100
    return round(tss, 1)


def calc_power_tss(
    duration_sec: float,
    normalized_power: float,
    ftp: float,
) -> float:
    """Standard TSS formula used by TrainingPeaks.

    Requires a power meter on the bike.
    Falls back to hrTSS if power data is unavailable.

    Args:
        duration_sec: Activity duration in seconds.
        normalized_power: Normalized power output in watts.
        ftp: Functional Threshold Power in watts.

    Returns:
        TSS value rounded to 1 decimal place.
    """
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
    """Swim-Specific TSS based on Critical Swim Speed (CSS).

    CSS is the anaerobic threshold pace for swimming (sec per 100m).
    Faster than CSS = above threshold.

    Args:
        distance_m: Total distance swum in meters.
        duration_sec: Activity duration in seconds.
        css_per_100m: Critical Swim Speed in seconds per 100 meters.

    Returns:
        Swim TSS value rounded to 1 decimal place, or 0.0 if distance is zero.
    """
    if distance_m == 0 or duration_sec == 0 or css_per_100m == 0:
        return 0.0
    pace_per_100m = (duration_sec / distance_m) * 100
    intensity_factor = css_per_100m / pace_per_100m
    tss = (duration_sec / 3600) * intensity_factor ** 2 * 100
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

    Args:
        tss_history: List of daily TSS values in chronological order.
        ctl_days: Number of days for chronic training load EMA (default 42).
        atl_days: Number of days for acute training load EMA (default 7).

    Returns:
        Tuple of (CTL, ATL, TSB), each rounded to 1 decimal place.
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
    sleep: SleepData,
    body_battery: int,
    resting_hr: float,
    resting_hr_baseline: float,
) -> tuple[int, ReadinessLevel]:
    """Calculate composite readiness score from physiological signals.

    Weighted from 4 components:
    - HRV delta from baseline (35%)
    - Sleep score (30%)
    - Body Battery morning value (20%)
    - Resting HR deviation from baseline (15%)

    Args:
        hrv: HRV data including last night's value and weekly average.
        sleep: Sleep data including sleep score.
        body_battery: Morning Body Battery value (0-100).
        resting_hr: Current resting heart rate.
        resting_hr_baseline: Baseline resting heart rate for comparison.

    Returns:
        Tuple of (score, ReadinessLevel) where score is clamped to 0-100.
    """
    score = 100

    # HRV component (weight: 35%)
    hrv_delta = (
        (hrv.hrv_last_night - hrv.hrv_weekly_avg) / hrv.hrv_weekly_avg
        if hrv.hrv_weekly_avg != 0
        else 0.0
    )
    if hrv_delta < -0.20:
        score -= 35
    elif hrv_delta < -0.10:
        score -= 20
    elif hrv_delta < -0.05:
        score -= 10
    elif hrv_delta > +0.10:
        score += 5  # bonus for good recovery

    # Sleep component (weight: 30%)
    if sleep.sleep_score < 50:
        score -= 30
    elif sleep.sleep_score < 65:
        score -= 15
    elif sleep.sleep_score < 75:
        score -= 7

    # Body Battery component (weight: 20%)
    if body_battery < 30:
        score -= 20
    elif body_battery < 50:
        score -= 10
    elif body_battery < 65:
        score -= 5

    # Resting HR component (weight: 15%)
    hr_delta = resting_hr - resting_hr_baseline
    if hr_delta > 7:
        score -= 15
    elif hr_delta > 4:
        score -= 8
    elif hr_delta > 2:
        score -= 3

    score = max(0, min(100, score))

    if score >= 80:
        level = ReadinessLevel.GREEN
    elif score >= 60:
        level = ReadinessLevel.YELLOW
    else:
        level = ReadinessLevel.RED

    return score, level
