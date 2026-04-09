"""Level 2: Post-activity HRV analysis — DFA alpha 1 pipeline.

Processes FIT files from bike/run activities to extract RR intervals,
compute DFA alpha 1 timeseries, detect aerobic/anaerobic thresholds,
and calculate Readiness (Ra) and Durability (Da).

References:
- Gronwald et al. 2020 — DFA a1 as exercise intensity biomarker
- Rogers et al. 2021 — DFA a1 for aerobic threshold detection
- Lipponen & Tarvainen 2019 — RR artifact correction
"""

import bisect
import io
import logging
from typing import Any

import numpy as np
from fitparse import FitFile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. RR extraction from FIT
# ---------------------------------------------------------------------------


def parse_fit(fit_bytes: bytes) -> tuple[list[float], list[dict]]:
    """Parse FIT file once, extracting both RR intervals and Record messages.

    Returns:
        (rr_ms, records) where:
        - rr_ms: list of RR intervals in milliseconds (from HRV messages)
        - records: list of dicts with timestamp_s, heart_rate, power, speed
    """
    fit = FitFile(io.BytesIO(fit_bytes))
    rr_ms: list[float] = []
    records: list[dict] = []
    start_ts = None

    for msg in fit.get_messages():
        msg_name = msg.name
        if msg_name == "hrv":
            for field in msg.fields:
                if field.name == "time" and field.value is not None:
                    values = field.value if isinstance(field.value, (list, tuple)) else [field.value]
                    for v in values:
                        if v is not None and v < 60.0:
                            rr_ms.append(v * 1000.0)
        elif msg_name == "record":
            rec: dict[str, Any] = {}
            for field in msg.fields:
                if field.name == "timestamp" and field.value is not None:
                    if start_ts is None:
                        start_ts = field.value
                    rec["timestamp_s"] = (field.value - start_ts).total_seconds()
                elif field.name == "heart_rate":
                    rec["heart_rate"] = field.value
                elif field.name == "power":
                    rec["power"] = field.value
                elif field.name in ("speed", "enhanced_speed"):
                    rec["speed"] = field.value
            if "timestamp_s" in rec:
                records.append(rec)

    return rr_ms, records


def extract_rr_intervals(fit_bytes: bytes) -> list[float]:
    """Extract RR intervals (ms) from FIT file HRV messages.

    Convenience wrapper around parse_fit() for cases where only RR is needed.
    """
    rr_ms, _ = parse_fit(fit_bytes)
    return rr_ms


def extract_records(fit_bytes: bytes) -> list[dict]:
    """Extract Record messages from FIT file.

    Convenience wrapper around parse_fit() for cases where only records are needed.
    """
    _, records = parse_fit(fit_bytes)
    return records


# ---------------------------------------------------------------------------
# 2. Artifact correction
# ---------------------------------------------------------------------------


def correct_rr_artifacts(
    rr_ms: list[float],
    threshold_pct: float = 0.10,
) -> dict:
    """Artifact correction for RR intervals.

    Uses percentage-based filter (Lipponen & Tarvainen 2019 simplified):
    if an RR interval deviates more than threshold_pct from the local median,
    it is replaced by the median.

    Returns:
        {
            "rr_corrected": [...],
            "artifact_count": int,
            "artifact_pct": float,
            "quality": "good" | "moderate" | "poor"
        }
    """
    if len(rr_ms) < 10:
        return {
            "rr_corrected": list(rr_ms),
            "artifact_count": 0,
            "artifact_pct": 0.0,
            "quality": "poor",
        }

    rr = np.array(rr_ms, dtype=np.float64)
    corrected = rr.copy()
    artifact_count = 0

    # Sliding median with window of 5 beats
    half_win = 2
    for i in range(len(rr)):
        lo = max(0, i - half_win)
        hi = min(len(rr), i + half_win + 1)
        local_median = np.median(rr[lo:hi])

        if local_median > 0 and abs(rr[i] - local_median) / local_median > threshold_pct:
            corrected[i] = local_median
            artifact_count += 1

    artifact_pct = (artifact_count / len(rr)) * 100.0

    if artifact_pct < 5:
        quality = "good"
    elif artifact_pct < 10:
        quality = "moderate"
    else:
        quality = "poor"

    return {
        "rr_corrected": corrected.tolist(),
        "artifact_count": artifact_count,
        "artifact_pct": round(artifact_pct, 2),
        "quality": quality,
    }


# ---------------------------------------------------------------------------
# 3. DFA alpha 1
# ---------------------------------------------------------------------------


def calculate_dfa_alpha1(
    rr_ms: np.ndarray,
    window_beats: tuple[int, int] = (4, 16),
) -> float:
    """Detrended Fluctuation Analysis — short-term scaling exponent (alpha 1).

    Algorithm:
    1. Integrate: y[i] = cumsum(RR - mean(RR))
    2. For each window size n (from 4 to 16 beats):
       a. Split y into non-overlapping windows of size n
       b. Detrend each window (linear fit), compute residuals
       c. F(n) = sqrt(mean(residuals²))
    3. alpha1 = slope(log(n), log(F(n)))

    Interpretation:
    - a1 > 1.0:  low intensity (rest/easy)
    - a1 ≈ 0.75: aerobic threshold (HRVT1)
    - a1 ≈ 0.50: anaerobic threshold (HRVT2)
    - a1 < 0.50: max effort

    Returns alpha1 value, or NaN if insufficient data.
    """
    if len(rr_ms) < window_beats[1] * 2:
        return float("nan")

    # Step 1: Integrate
    y = np.cumsum(rr_ms - np.mean(rr_ms))

    n_min, n_max = window_beats
    scales = list(range(n_min, n_max + 1))
    fluctuations = []

    for n in scales:
        # Number of complete windows
        n_windows = len(y) // n
        if n_windows < 2:
            continue

        y_trimmed = y[: n_windows * n].reshape(n_windows, n)
        x = np.arange(n)

        residuals_sq = []
        for window in y_trimmed:
            # Linear detrend
            coeffs = np.polyfit(x, window, 1)
            trend = np.polyval(coeffs, x)
            residuals_sq.append(np.mean((window - trend) ** 2))

        f_n = np.sqrt(np.mean(residuals_sq))
        if f_n > 0:
            fluctuations.append((np.log(n), np.log(f_n)))

    if len(fluctuations) < 3:
        return float("nan")

    log_n = np.array([f[0] for f in fluctuations])
    log_f = np.array([f[1] for f in fluctuations])

    # Linear regression: log(F(n)) = alpha * log(n) + b
    coeffs = np.polyfit(log_n, log_f, 1)
    return float(coeffs[0])


# ---------------------------------------------------------------------------
# 4. DFA timeseries (sliding window)
# ---------------------------------------------------------------------------


class _RecordIndex:
    """Pre-built index for O(log N) lookups of FIT records by time."""

    def __init__(self, records: list[dict]) -> None:
        self._times = [r.get("timestamp_s", 0.0) for r in records]
        self._records = records

    def _closest(self, time_sec: float, max_gap: float = 10.0) -> dict | None:
        if not self._times:
            return None
        idx = bisect.bisect_left(self._times, time_sec)
        best = None
        best_dist = max_gap + 1
        for candidate in (idx - 1, idx):
            if 0 <= candidate < len(self._times):
                dist = abs(self._times[candidate] - time_sec)
                if dist < best_dist:
                    best_dist = dist
                    best = self._records[candidate]
        return best if best_dist <= max_gap else None

    def hr_at(self, time_sec: float) -> float | None:
        rec = self._closest(time_sec)
        return rec.get("heart_rate") if rec else None

    def power_at(self, time_sec: float) -> float | None:
        rec = self._closest(time_sec)
        return rec.get("power") if rec else None

    def speed_at(self, time_sec: float) -> float | None:
        rec = self._closest(time_sec)
        return rec.get("speed") if rec else None


def calculate_dfa_timeseries(
    rr_ms: list[float],
    records: list[dict] | None = None,
    window_sec: int = 120,
    step_sec: int = 5,
) -> list[dict]:
    """Sliding-window DFA alpha 1 across an activity.

    For each window position:
    1. Collect RR intervals spanning the last window_sec seconds
    2. Check artifact quality
    3. Calculate DFA alpha 1
    4. Pair with HR/power from FIT records at the same time

    Args:
        rr_ms: Corrected RR intervals in milliseconds.
        records: FIT Record messages (from extract_records).
        window_sec: Window size in seconds (default 120 = 2 min).
        step_sec: Step size in seconds (default 5).

    Returns list of dicts:
        [{"time_sec": 120, "dfa_a1": 1.05, "hr_avg": 118, "power": 150, "artifact_pct": 1.2}, ...]
    """
    if not rr_ms:
        return []

    idx = _RecordIndex(records or [])

    # Build cumulative time array from RR intervals
    cum_time = np.cumsum(rr_ms) / 1000.0  # convert to seconds
    rr_arr = np.array(rr_ms)

    total_time = cum_time[-1]
    timeseries: list[dict] = []

    # Start after first window
    t = float(window_sec)
    while t <= total_time:
        # Find RR indices within [t - window_sec, t]
        window_start = t - window_sec
        mask = (cum_time > window_start) & (cum_time <= t)
        window_rr = rr_arr[mask]

        if len(window_rr) >= 30:  # Need enough beats for DFA (at least ~30)
            a1 = calculate_dfa_alpha1(window_rr)

            if not np.isnan(a1):
                hr_avg = 60000.0 / np.mean(window_rr) if np.mean(window_rr) > 0 else None
                point: dict[str, Any] = {
                    "time_sec": round(t),
                    "dfa_a1": round(a1, 3),
                    "hr_avg": round(hr_avg, 1) if hr_avg else None,
                }

                # Add power/speed from records if available (O(log N) lookup)
                record_hr = idx.hr_at(t)
                if record_hr is not None:
                    point["hr_avg"] = record_hr  # prefer record HR over RR-derived

                power = idx.power_at(t)
                if power is not None:
                    point["power"] = power

                speed = idx.speed_at(t)
                if speed is not None:
                    point["speed"] = round(speed, 2)

                timeseries.append(point)

        t += step_sec

    return timeseries


# ---------------------------------------------------------------------------
# 5. Threshold detection (HRVT1/HRVT2)
# ---------------------------------------------------------------------------


def detect_hrv_thresholds(
    dfa_timeseries: list[dict],
    activity_type: str = "Ride",
) -> dict | None:
    """Detect HRVT1 (a1=0.75) and HRVT2 (a1=0.50) from DFA timeseries.

    Strategy:
    1. Filter points with valid HR and DFA a1
    2. Require sufficient range (a1 from >1.0 down to <0.75)
    3. Linear regression: DFA_a1 = f(HR)
    4. Interpolate HR where a1 = 0.75 (HRVT1) and a1 = 0.50 (HRVT2)
    5. Validate: R² > 0.5, physiological HR range

    Returns None if no valid ramp detected or insufficient quality.
    """
    # Filter valid points
    points = [
        p
        for p in dfa_timeseries
        if p.get("hr_avg") is not None
        and p.get("dfa_a1") is not None
        and 0.1 < p["dfa_a1"] < 2.0
        and 60 < p["hr_avg"] < 220
    ]

    if len(points) < 20:
        return None

    hr = np.array([p["hr_avg"] for p in points])
    a1 = np.array([p["dfa_a1"] for p in points])

    # Check sufficient range
    if np.max(a1) < 0.9 or np.min(a1) > 0.80:
        return None

    # Linear regression: a1 = slope * HR + intercept
    coeffs = np.polyfit(hr, a1, 1)
    slope, intercept = coeffs

    # a1 should decrease as HR increases (negative slope)
    if slope >= 0:
        return None

    # R² calculation
    a1_pred = np.polyval(coeffs, hr)
    ss_res = np.sum((a1 - a1_pred) ** 2)
    ss_tot = np.sum((a1 - np.mean(a1)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    if r_squared < 0.5:
        return None

    # Interpolate thresholds
    # a1 = slope * HR + intercept  =>  HR = (a1 - intercept) / slope
    hrvt1_hr = (0.75 - intercept) / slope
    hrvt2_hr = (0.50 - intercept) / slope

    # Sanity checks
    if not (80 < hrvt1_hr < 200 and hrvt1_hr < hrvt2_hr):
        return None

    # Confidence based on R²
    if r_squared > 0.7:
        confidence = "high"
    elif r_squared > 0.5:
        confidence = "moderate"
    else:
        confidence = "low"

    result: dict[str, Any] = {
        "hrvt1_hr": round(hrvt1_hr, 1),
        "hrvt2_hr": round(hrvt2_hr, 1) if 80 < hrvt2_hr < 220 else None,
        "r_squared": round(r_squared, 3),
        "confidence": confidence,
    }

    # Add power at HRVT1 if power data available
    if activity_type == "Ride":
        power_points = [p for p in points if p.get("power") is not None]
        if len(power_points) >= 10:
            p_hr = np.array([p["hr_avg"] for p in power_points])
            p_power = np.array([p["power"] for p in power_points])
            try:
                p_coeffs = np.polyfit(p_hr, p_power, 1)
                hrvt1_power = np.polyval(p_coeffs, hrvt1_hr)
                if 50 < hrvt1_power < 500:
                    result["hrvt1_power"] = round(hrvt1_power)
            except (np.linalg.LinAlgError, ValueError):
                pass

    # Add pace at HRVT1 for running
    if activity_type == "Run":
        speed_points = [p for p in points if p.get("speed") is not None and p["speed"] > 0]
        if len(speed_points) >= 10:
            s_hr = np.array([p["hr_avg"] for p in speed_points])
            s_speed = np.array([p["speed"] for p in speed_points])
            try:
                s_coeffs = np.polyfit(s_hr, s_speed, 1)
                hrvt1_speed = np.polyval(s_coeffs, hrvt1_hr)
                if hrvt1_speed > 0:
                    pace_sec_per_km = 1000.0 / hrvt1_speed
                    mins = int(pace_sec_per_km // 60)
                    secs = int(pace_sec_per_km % 60)
                    result["hrvt1_pace"] = f"{mins}:{secs:02d}"
            except (np.linalg.LinAlgError, ValueError):
                pass

    return result


# ---------------------------------------------------------------------------
# 6. Readiness (Ra) and Durability (Da)
# ---------------------------------------------------------------------------


def calculate_readiness_ra(
    dfa_timeseries: list[dict],
    baseline_pa: float,
    activity_type: str = "Ride",
    warmup_minutes: int = 15,
) -> dict | None:
    """Calculate Readiness (Ra) from warmup DFA a1 vs baseline.

    Ra = (Pa_today - Pa_baseline) / Pa_baseline * 100

    Pa = power (bike) or speed (run) at a stable DFA a1 level during warmup.
    Ra > +5%: excellent, -5..+5%: normal, < -5%: under-recovered.

    Returns dict with ra_pct, pa_today, status, or None if insufficient data.
    """
    warmup_sec = warmup_minutes * 60
    warmup_points = [p for p in dfa_timeseries if p["time_sec"] <= warmup_sec]

    if len(warmup_points) < 5:
        return None

    # Find points where DFA a1 is in the moderate zone (0.6 - 1.1)
    moderate_points = [p for p in warmup_points if 0.6 <= p.get("dfa_a1", 0) <= 1.1]
    if len(moderate_points) < 3:
        return None

    # Get the performance metric (power for bike, speed for run)
    if activity_type == "Ride":
        values = [p["power"] for p in moderate_points if p.get("power") is not None and p["power"] > 0]
    else:
        values = [p["speed"] for p in moderate_points if p.get("speed") is not None and p["speed"] > 0]

    if len(values) < 3:
        return None

    pa_today = float(np.mean(values))
    ra_pct = ((pa_today - baseline_pa) / baseline_pa) * 100.0

    if ra_pct > 5:
        status = "excellent"
    elif ra_pct > -5:
        status = "normal"
    else:
        status = "under_recovered"

    return {
        "ra_pct": round(ra_pct, 1),
        "pa_today": round(pa_today, 1),
        "status": status,
    }


def calculate_durability_da(
    dfa_timeseries: list[dict],
    activity_type: str = "Ride",
    min_duration_min: int = 40,
) -> dict | None:
    """Calculate Durability (Da) from first vs second half DFA a1.

    Da = (Pa_second_half - Pa_first_half) / Pa_first_half * 100

    Requires ≥40 min activity. Compares performance at similar DFA a1 levels.
    Da > 0: excellent endurance, < -5%: fatigue, < -15%: overreached.

    Returns dict with da_pct, status, or None if insufficient data.
    """
    if not dfa_timeseries:
        return None

    total_time = dfa_timeseries[-1]["time_sec"]
    min_duration_sec = min_duration_min * 60

    if total_time < min_duration_sec:
        return None

    mid = total_time / 2
    first_half = [p for p in dfa_timeseries if p["time_sec"] <= mid]
    second_half = [p for p in dfa_timeseries if p["time_sec"] > mid]

    if len(first_half) < 5 or len(second_half) < 5:
        return None

    # Get performance values
    if activity_type == "Ride":
        key = "power"
    else:
        key = "speed"

    first_vals = [p[key] for p in first_half if p.get(key) is not None and p[key] > 0]
    second_vals = [p[key] for p in second_half if p.get(key) is not None and p[key] > 0]

    if len(first_vals) < 3 or len(second_vals) < 3:
        return None

    pa_first = float(np.mean(first_vals))
    pa_second = float(np.mean(second_vals))

    if pa_first == 0:
        return None

    da_pct = ((pa_second - pa_first) / pa_first) * 100.0

    if da_pct > 0:
        status = "excellent"
    elif da_pct > -5:
        status = "normal"
    elif da_pct > -15:
        status = "fatigued"
    else:
        status = "overreached"

    return {
        "da_pct": round(da_pct, 1),
        "status": status,
    }


# ---------------------------------------------------------------------------
# 7. Pipeline function
# ---------------------------------------------------------------------------


def _compute_hrv(
    fit_bytes: bytes,
    activity_type: str,
    baseline_pa: float | None,
) -> dict[str, Any]:
    """CPU-bound HRV computation — runs in executor to avoid blocking the event loop.

    Returns a dict with processing results: status, row fields, pa_baseline info, etc.
    """
    # 2. Parse FIT once — extract both RR and records
    rr_ms, records = parse_fit(fit_bytes)
    if len(rr_ms) < 300:  # < ~5 min of data
        status = "too_short" if rr_ms else "no_rr_data"
        return {"status": status, "rr_count": len(rr_ms)}

    # 3. Artifact correction
    corrected = correct_rr_artifacts(rr_ms)
    if corrected["quality"] == "poor":
        return {
            "status": "low_quality",
            "hrv_quality": "poor",
            "artifact_pct": corrected["artifact_pct"],
            "rr_count": len(rr_ms),
        }

    # 5. DFA timeseries
    timeseries = calculate_dfa_timeseries(
        corrected["rr_corrected"],
        records=records,
    )

    if not timeseries:
        return {
            "status": "too_short",
            "hrv_quality": corrected["quality"],
            "artifact_pct": corrected["artifact_pct"],
            "rr_count": len(rr_ms),
        }

    # DFA a1 summary
    a1_values = [p["dfa_a1"] for p in timeseries]
    dfa_a1_mean = float(np.mean(a1_values))

    # Warmup a1 (first 15 min)
    warmup_points = [p for p in timeseries if p["time_sec"] <= 900]
    dfa_a1_warmup = float(np.mean([p["dfa_a1"] for p in warmup_points])) if warmup_points else None

    # 6. Threshold detection
    thresholds = detect_hrv_thresholds(timeseries, activity_type=activity_type)

    # 7. Readiness (Ra)
    ra_result = None
    pa_today = None
    if baseline_pa is not None:
        ra_result = calculate_readiness_ra(timeseries, baseline_pa, activity_type=activity_type)
        if ra_result:
            pa_today = ra_result["pa_today"]

    # Pa baseline data for saving
    pa_baseline_data = None
    if warmup_points:
        if activity_type == "Ride":
            warmup_perf = [
                p["power"]
                for p in warmup_points
                if p.get("power") is not None and p["power"] > 0 and 0.6 <= p.get("dfa_a1", 0) <= 1.1
            ]
        else:
            warmup_perf = [
                p["speed"]
                for p in warmup_points
                if p.get("speed") is not None and p["speed"] > 0 and 0.6 <= p.get("dfa_a1", 0) <= 1.1
            ]
        if len(warmup_perf) >= 3:
            pa_baseline_data = {
                "pa_value": float(np.mean(warmup_perf)),
                "dfa_a1_ref": dfa_a1_warmup,
                "quality": corrected["quality"],
            }

    # 8. Durability (Da)
    da_result = calculate_durability_da(timeseries, activity_type=activity_type)

    # Trim timeseries for storage (keep every 30s instead of 5s)
    stored_timeseries = [p for p in timeseries if p["time_sec"] % 30 == 0]

    return {
        "status": "processed",
        "hrv_quality": corrected["quality"],
        "artifact_pct": corrected["artifact_pct"],
        "rr_count": len(rr_ms),
        "dfa_a1_mean": round(dfa_a1_mean, 3),
        "dfa_a1_warmup": round(dfa_a1_warmup, 3) if dfa_a1_warmup is not None else None,
        "dfa_timeseries": stored_timeseries,
        "thresholds": thresholds,
        "ra_result": ra_result,
        "pa_today": pa_today,
        "pa_baseline_data": pa_baseline_data,
        "da_result": da_result,
    }
