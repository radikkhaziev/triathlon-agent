"""Webhook-time noise classification — `docs/ML_RACE_PROJECTION_SPEC.md` §6.4.

Tags activities with a `noise_reason` so ML retrain can drop them upstream
instead of re-checking ~365 days of history on every Sunday cron.

Phase 1.6 scope: Run only.

Two reasons exist, checked in priority order (severity first):

* ``run_walk``        — walk-paced low-HR Run = mistagged sport
                        (pace > ``threshold_pace × WALK_PACE_MULT`` AND
                         avg_hr < ``lthr × WALK_HR_MULT``)
* ``run_recovery_jog`` — Z1 ≥ ``Z1_RECOVERY_THRESHOLD`` AND
                          tss < ``RECOVERY_TSS_CEILING``

If both fire, ``run_walk`` wins — it's a more fundamental data-quality issue.

Lenient defaults: missing fields → don't classify (return None, caller writes
``noise_scored_at`` with ``noise_reason=NULL`` meaning "checked, signal kept").
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

# ---------------------------------------------------------------------------
# Public type
# ---------------------------------------------------------------------------

NoiseReason = Literal["run_walk", "run_recovery_jog"]


# ---------------------------------------------------------------------------
# Thresholds — global constants, not per-user (avoids drift; recalibration =
# code change + re-run backfill). Personalization comes via `thresholds` arg.
# ---------------------------------------------------------------------------

# run_recovery_jog (existing Phase 1.5 logic, relocated here)
Z1_RECOVERY_THRESHOLD = 0.70
RECOVERY_TSS_CEILING = 40.0

# run_walk
WALK_PACE_MULT = 1.6  # pace slower than 1.6× threshold_pace
WALK_HR_MULT = 0.65  # avg HR below 0.65× LTHR

# run_walk fallback for athletes without synced settings (LTHR / threshold
# pace = None). Onboarding window — usually resolved within days of first
# Intervals sync via SPORT_SETTINGS_UPDATED webhook.
WALK_FALLBACK_PACE_SEC_PER_KM = 6.5 * 60  # 6:30/km
WALK_FALLBACK_HR_BPM = 120


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_zone_seconds(value) -> float:
    """Normalize a single zone-time entry to a non-negative finite float.

    None / NaN / non-numeric → 0.0. Truthiness shortcuts (``x or 0``) don't
    work because ``bool(float('nan')) is True`` — a NaN slips through and
    poisons the sum to NaN, disabling the filter.
    """
    if value is None:
        return 0.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(v) or v < 0:
        return 0.0
    return v


def _is_z1_dominated(hr_zone_times) -> bool:
    """True iff Z1 ≥ ``Z1_RECOVERY_THRESHOLD`` of recorded HR zone time.

    Zone-composition primitive — does NOT alone mean "drop this activity".
    Pro athletes running 80/20 base have Z1-dominated long sessions which
    are real signal. Combine with TSS gate in :func:`is_run_recovery_jog`.

    Unknown / empty zones → False (don't filter what we can't measure).
    """
    if hr_zone_times is None or isinstance(hr_zone_times, (str, bytes)):
        return False
    if not isinstance(hr_zone_times, Sequence):
        try:
            hr_zone_times = list(hr_zone_times)
        except TypeError:
            return False
    if len(hr_zone_times) == 0:
        return False
    seconds = [_coerce_zone_seconds(z) for z in hr_zone_times]
    total = sum(seconds)
    if total <= 0:
        return False
    return (seconds[0] / total) >= Z1_RECOVERY_THRESHOLD


def is_run_recovery_jog(hr_zone_times, tss) -> bool:
    """True iff activity is a recovery jog worth dropping from train-set.

    Both conditions required:
      1. Z1-dominated zone time (:func:`_is_z1_dominated`).
      2. ``tss < RECOVERY_TSS_CEILING`` — short / low-load.

    A 90-min structured Z1-base session is also Z1-dominated but has TSS 60+
    and carries real aerobic signal — those stay in.

    Missing TSS / zones → return False (lenient).
    """
    if not _is_z1_dominated(hr_zone_times):
        return False
    if tss is None:
        return False
    try:
        tss_value = float(tss)
    except (TypeError, ValueError):
        return False
    if math.isnan(tss_value):
        return False
    return tss_value < RECOVERY_TSS_CEILING


def is_run_walk(
    avg_pace_sec_per_km: float | None,
    avg_hr: float | None,
    lthr: int | None,
    threshold_pace_sec_per_km: float | None,
) -> bool:
    """True iff Run activity looks like a walk logged as Run.

    Definition: pace slower than ``threshold_pace × WALK_PACE_MULT`` AND
    avg HR below ``lthr × WALK_HR_MULT``. Both conditions required — a
    slow but elevated-HR session is fatigued running, not a walk.

    Per-athlete baseline via ``threshold_pace`` + ``lthr`` from
    ``AthleteSettings`` (synced via ``SPORT_SETTINGS_UPDATED`` webhook).
    Fixed multipliers (not per-user) keep recalibration centralized.

    Missing pace / HR → False (can't classify).
    Missing thresholds → fallback to global constants (onboarding window).
    """
    if avg_pace_sec_per_km is None or avg_hr is None:
        return False
    try:
        pace_v = float(avg_pace_sec_per_km)
        hr_v = float(avg_hr)
    except (TypeError, ValueError):
        return False
    if math.isnan(pace_v) or math.isnan(hr_v) or pace_v <= 0 or hr_v <= 0:
        return False

    if threshold_pace_sec_per_km is not None and lthr is not None:
        pace_floor = float(threshold_pace_sec_per_km) * WALK_PACE_MULT
        hr_ceil = float(lthr) * WALK_HR_MULT
    else:
        pace_floor = WALK_FALLBACK_PACE_SEC_PER_KM
        hr_ceil = WALK_FALLBACK_HR_BPM

    return pace_v > pace_floor and hr_v < hr_ceil


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def classify_noise(
    *,
    sport: str | None,
    avg_hr: float | None,
    avg_pace_sec_per_km: float | None,
    hr_zone_times,
    tss: float | None,
    lthr: int | None = None,
    threshold_pace_sec_per_km: float | None = None,
) -> NoiseReason | None:
    """Classify an activity into a noise category, or ``None`` if signal.

    Priority order — first match wins:
      1. ``run_walk`` — mistagged sport (most severe data-quality issue).
      2. ``run_recovery_jog`` — legit low-intensity, but noise for ML.

    Non-Run activities → ``None`` (Phase 1.6 scope is Run only; Ride/Swim
    deferred per spec §6.4.2). Caller still writes ``noise_scored_at`` so
    the row is marked "checked, signal kept".
    """
    if sport != "Run":
        return None

    if is_run_walk(
        avg_pace_sec_per_km=avg_pace_sec_per_km,
        avg_hr=avg_hr,
        lthr=lthr,
        threshold_pace_sec_per_km=threshold_pace_sec_per_km,
    ):
        return "run_walk"

    if is_run_recovery_jog(hr_zone_times, tss):
        return "run_recovery_jog"

    return None


def classify_activity_row(activity, detail, thresholds) -> NoiseReason | None:
    """Convenience wrapper for callers holding ORM rows (Activity + ActivityDetail).

    Extracts fields, derives ``avg_pace_sec_per_km`` from ``moving_time`` /
    ``distance`` (matches ``race_features.py`` line 486 formula), forwards to
    :func:`classify_noise`. Both Activity and ActivityDetail attributes are
    accessed by name — works with ORM rows, DTOs, or mocks.

    ``thresholds`` is an ``AthleteThresholdsDTO`` (has ``lthr_run`` and
    ``threshold_pace_run`` attributes; both may be None for new athletes).
    """
    avg_pace_sec_per_km: float | None = None
    moving_time = getattr(activity, "moving_time", None)
    distance = getattr(detail, "distance", None)
    if moving_time and distance and moving_time > 0 and distance > 0:
        avg_pace_sec_per_km = moving_time / (distance / 1000.0)

    return classify_noise(
        sport=getattr(activity, "type", None),
        avg_hr=getattr(activity, "average_hr", None),
        avg_pace_sec_per_km=avg_pace_sec_per_km,
        hr_zone_times=getattr(detail, "hr_zone_times", None),
        tss=getattr(activity, "icu_training_load", None),
        lthr=getattr(thresholds, "lthr_run", None),
        threshold_pace_sec_per_km=getattr(thresholds, "threshold_pace_run", None),
    )
