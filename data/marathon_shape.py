"""Marathon Shape — Runalyze-style basic endurance metric.

Pure formulas, no DB IO. Caller passes a list of Run activities + vo2max +
reference date; gets back shape % + breakdown for the rendering layer.

Source: ``inc/core/Calculation/BasicEndurance.php`` in Runalyze
(https://github.com/Runalyze/Runalyze).

The metric answers «hatte ich genug Volumen» — given today's VO2max, am I
running enough kilometres and long enough runs to be ready for marathon
(=100%), HM (~42.5%), 10K (~17%).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import log
from typing import Sequence

MINIMAL_EFFECTIVE_VO2MAX = 25.0
MIN_KM_FOR_LONGJOG = 13.0
DAYS_FOR_WEEK_KM = 182  # 26 weeks — window for weekly volume
DAYS_FOR_WEEK_KM_MIN = 70  # clamp denominator if athlete trains <70 days
DAYS_FOR_LONGJOGS = 70  # 10 weeks — window for long-run scoring
PERCENTAGE_WEEK_KM = 0.67
PERCENTAGE_LONGJOGS = 0.33


@dataclass(frozen=True)
class RunActivity:
    """Minimal projection of an activity for shape calculation."""

    dt: date
    distance_km: float


@dataclass(frozen=True)
class MarathonShapeResult:
    shape_pct: float
    target_weekly_km: float
    actual_weekly_km: float
    target_longjog_km: float
    longjog_score: float
    actual_longjog_km: float  # max distance in last DAYS_FOR_LONGJOGS, for display
    vo2max_used: float


def target_weekly_km(vo2max: float) -> float:
    return max(vo2max, MINIMAL_EFFECTIVE_VO2MAX) ** 1.135


def target_longjog_km(vo2max: float) -> float:
    return log(max(vo2max, MINIMAL_EFFECTIVE_VO2MAX) / 4) * 12 - 13


def required_shape_for_distance(distance_km: float) -> float:
    """Marathon (42.195) ≈ 100, HM (21.0975) ≈ 42.5, 10K ≈ 17."""
    return distance_km**1.23


def calculate_marathon_shape(
    runs: Sequence[RunActivity],
    vo2max: float,
    *,
    reference_date: date,
) -> MarathonShapeResult:
    """Compute MS as of ``reference_date``, looking back at the provided runs.

    Callers are responsible for filtering to Run-typed activities; this module
    does not know about sports. Runs older than the longest window
    (``DAYS_FOR_WEEK_KM`` = 182d) are silently ignored.
    """
    vo2 = max(vo2max, MINIMAL_EFFECTIVE_VO2MAX)
    twk = target_weekly_km(vo2)
    tlj = target_longjog_km(vo2)

    # Weekly component — sum km in last 182 days, normalise by actual training
    # span (clamped 70..182). A beginner with only 80 days of data is divided
    # by 80, not by 182, so a beginner with 25 km/week shows realistic weekly_ratio.
    runs_in_window = [r for r in runs if 0 <= (reference_date - r.dt).days < DAYS_FOR_WEEK_KM]
    total_km = sum(r.distance_km for r in runs_in_window)
    if runs_in_window:
        oldest = min(r.dt for r in runs_in_window)
        actual_days = (reference_date - oldest).days + 1
        days_for_week = max(DAYS_FOR_WEEK_KM_MIN, min(DAYS_FOR_WEEK_KM, actual_days))
    else:
        days_for_week = DAYS_FOR_WEEK_KM
    # `days_for_week` is clamped ≥ DAYS_FOR_WEEK_KM_MIN (70) above, and `twk`
    # uses `max(vo2, 25)**1.135 ≈ 38.6` minimum — both are mathematically > 0.
    # Guards kept as defence-in-depth against future refactors that change clamps.
    actual_weekly = (total_km * 7) / days_for_week if days_for_week else 0.0
    weekly_ratio = actual_weekly / twk if twk > 0 else 0.0

    # Long-run component — time-decayed score from runs >13km in last 70 days.
    # PHP uses strict `distance > 13` (not >=) — the normalized score at exactly
    # 13km is (13-13)/target = 0 either way, so the choice has zero numeric effect.
    # We mirror PHP's strict comparison for fidelity to the reference impl.
    longjog_score = 0.0
    actual_longjog_km = 0.0
    for r in runs:
        days_ago = (reference_date - r.dt).days
        if not (0 <= days_ago < DAYS_FOR_LONGJOGS):
            continue
        if r.distance_km <= MIN_KM_FOR_LONGJOG:
            continue
        actual_longjog_km = max(actual_longjog_km, r.distance_km)
        weight = 2 - (2 / DAYS_FOR_LONGJOGS) * days_ago
        normalized = (r.distance_km - MIN_KM_FOR_LONGJOG) / tlj if tlj > 0 else 0.0
        longjog_score += weight * normalized**2
    longjog_ratio = (longjog_score * 7) / DAYS_FOR_LONGJOGS

    shape = 100 * (PERCENTAGE_WEEK_KM * weekly_ratio + PERCENTAGE_LONGJOGS * longjog_ratio)

    return MarathonShapeResult(
        shape_pct=round(shape, 1),
        target_weekly_km=round(twk, 1),
        actual_weekly_km=round(actual_weekly, 1),
        target_longjog_km=round(tlj, 1),
        longjog_score=round(longjog_score, 3),
        actual_longjog_km=round(actual_longjog_km, 1),
        vo2max_used=vo2,
    )
