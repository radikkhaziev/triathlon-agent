"""Training Strain — Foster monotony/strain + ACWR, a responsive read on
«how hard is my current build, and is it sustainable».

Pure formulas, no DB IO. Caller passes a daily-TSS series (one value per
calendar day, zeros for rest days) plus today's CTL/ATL; gets back the
current strain/monotony, an ACWR ratio, a personal-history strain zone, and
a rolling trend.

Complements the Endurance Score (VO2max-anchored, sticky) and CTL/ATL/TSB
(magnitude of load). What none of those see — and what this module adds — is
the *day-to-day variation* of load: Foster's monotony penalises «same load
every day with no recovery valleys», the mechanism Foster (1998) linked to
illness/injury in overload phases.

Definitions (Foster C. 1998, «Monitoring training in athletes…»):

    weekly_load = Σ daily_tss over the trailing 7 calendar days
    monotony    = mean(daily_tss_7d) / pstdev(daily_tss_7d)
    strain      = weekly_load · monotony

ACWR (acute:chronic workload ratio) is read straight off the EWMA loads we
already store: ATL (τ=7) is the acute side, CTL (τ=42) the chronic side. This
is the EWMA-ACWR variant (Williams et al. 2017), preferred over the rolling
-average form. Sweet spot 0.8–1.3, danger >1.5.

The strain zone is **personal-percentile**, not absolute — strain has no
literature threshold, so we band it against the athlete's own trailing-year
distribution («this build vs my usual»). Before enough history exists we fall
back to monotony's literature threshold (>2.0 = overload risk).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, pstdev
from typing import Sequence

# ─── Configuration ──────────────────────────────────────────────────

ACUTE_WINDOW_DAYS = 7  # Foster training-week window for monotony/strain

# When every day in the window carries an identical non-zero load, pstdev is 0
# and monotony is mathematically infinite. Foster reads that as maximal
# monotony — we cap it at a finite ceiling so strain stays a usable number.
MONOTONY_CAP = 2.5

# ACWR (ATL/CTL) — EWMA-ACWR risk bands (Gabbett sweet-spot, EWMA variant).
ACWR_SWEET_LO = 0.8
ACWR_SWEET_HI = 1.3
ACWR_DANGER = 1.5

# Monotony literature threshold — fallback zone driver before percentile
# history exists, and the danger line drawn on the detail-screen trend.
MONOTONY_CAUTION = 1.5
MONOTONY_DANGER = 2.0

# Personal-percentile strain zones: green below P_CALM, red at/above P_HARD.
STRAIN_PCT_CALM = 60.0
STRAIN_PCT_HARD = 85.0
# Roughly this many calendar days of training history before percentile bands
# are trustworthy; below it the zone falls back to monotony thresholds. Note
# the strain history is rolling-window values (overlapping 7-day windows), not
# independent samples — any week with load yields ~7 non-zero strain days, so
# this gate is satisfied by ≈8 weeks of training, which is the intent.
STRAIN_HISTORY_MIN_DAYS = 56


# Zone ids ("calm" | "building" | "overload") are returned as bare strings by
# `classify_strain` — the labels + colors live only on the frontend (no Python
# consumer needs them). SoT note: the zone ids, the monotony fallback
# thresholds (MONOTONY_CAUTION/DANGER), and the percentile cutoffs are mirrored
# in `webapp/src/components/halo/TrainingStrain.tsx` (colors + gauge bands) and
# `webapp/src/pages/StrainDetail.tsx` (`strainZoneAt`, a branch-for-branch copy
# of `classify_strain`). The FE can't import this module; if you retune any
# threshold here, sweep those two files too.


# ─── Core formulas ──────────────────────────────────────────────────


def monotony(daily_tss: Sequence[float]) -> float:
    """Foster monotony = mean / population-stdev of daily TSS over the window.

    Rest days (0 TSS) are kept — they are the recovery «valleys» that lower
    monotony, which is the whole point of the metric. Returns 0.0 when there
    was no training at all (mean 0), and ``MONOTONY_CAP`` when load was
    perfectly flat (stdev 0 but mean > 0).
    """
    if not daily_tss:
        return 0.0
    m = mean(daily_tss)
    if m <= 0:
        return 0.0
    sd = pstdev(daily_tss)
    if sd <= 0:
        return MONOTONY_CAP
    return min(m / sd, MONOTONY_CAP)


def weekly_load(daily_tss: Sequence[float]) -> float:
    """Sum of daily TSS over the window (Foster weekly load)."""
    return float(sum(daily_tss))


def strain(daily_tss: Sequence[float]) -> float:
    """Foster strain = weekly_load · monotony."""
    return weekly_load(daily_tss) * monotony(daily_tss)


def acwr(atl: float | None, ctl: float | None) -> float | None:
    """EWMA acute:chronic workload ratio = ATL / CTL.

    ``None`` when chronic load is missing or non-positive (ratio undefined,
    e.g. a brand-new athlete with CTL still at 0).
    """
    if atl is None or ctl is None or ctl <= 0:
        return None
    return atl / ctl


def acwr_status(ratio: float | None) -> str | None:
    """Map an ACWR ratio onto a risk band (for the UI accent colour).

    ``low`` <0.8 (detraining) · ``sweet`` 0.8–1.3 · ``caution`` 1.3–1.5
    (building hard) · ``danger`` ≥1.5. ``None`` when the ratio is undefined.
    """
    if ratio is None:
        return None
    if ratio >= ACWR_DANGER:
        return "danger"
    if ratio > ACWR_SWEET_HI:
        return "caution"
    if ratio < ACWR_SWEET_LO:
        return "low"
    return "sweet"


# ─── Daily-TSS windowing ────────────────────────────────────────────


def _window(daily_tss_by_date: dict[date, float], end: date, days: int) -> list[float]:
    """Daily TSS for the ``days`` calendar days ending at ``end`` (inclusive).

    Missing days (no activity) are filled with 0.0 — rest days must be present
    so monotony sees the recovery valleys.
    """
    return [daily_tss_by_date.get(end - timedelta(days=i), 0.0) for i in range(days - 1, -1, -1)]


def strain_series(
    daily_tss_by_date: dict[date, float],
    *,
    start: date,
    end: date,
    window: int = ACUTE_WINDOW_DAYS,
) -> list[tuple[date, float, float, float]]:
    """Rolling (date, strain, monotony, weekly_load) for each day in [start, end].

    Each point uses the trailing ``window``-day acute window ending on that
    day. Caller supplies a daily-TSS map covering at least
    ``start - window + 1 .. end``.
    """
    out: list[tuple[date, float, float, float]] = []
    d = start
    while d <= end:
        w = _window(daily_tss_by_date, d, window)
        out.append((d, strain(w), monotony(w), weekly_load(w)))
        d += timedelta(days=1)
    return out


# ─── Percentile zoning ──────────────────────────────────────────────


def percentile(values: Sequence[float], q: float) -> float:
    """``q``-percentile (0..100), linear interpolation. 0.0 for empty input.

    Assumes ``0 <= q <= 100`` (only 60/85 are passed in practice).
    """
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (q / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@dataclass(frozen=True)
class StrainBands:
    """Strain zone thresholds + how they were derived (for UI shading)."""

    calm_max: float  # strain below this → calm
    hard_min: float  # strain at/above this → overload
    source: str  # "percentile" | "monotony_fallback"


def strain_bands(strain_history: Sequence[float]) -> StrainBands:
    """Personal-percentile strain bands from the trailing-year strain series.

    Below ``STRAIN_HISTORY_MIN_DAYS`` of history the percentiles are unstable,
    so we return sentinel bands flagged ``monotony_fallback`` — the caller
    then classifies by monotony threshold instead.
    """
    usable = [s for s in strain_history if s > 0]
    if len(usable) < STRAIN_HISTORY_MIN_DAYS:
        return StrainBands(calm_max=0.0, hard_min=0.0, source="monotony_fallback")
    return StrainBands(
        calm_max=percentile(usable, STRAIN_PCT_CALM),
        hard_min=percentile(usable, STRAIN_PCT_HARD),
        source="percentile",
    )


def classify_strain(
    strain_today: float,
    monotony_today: float,
    bands: StrainBands,
) -> str:
    """Return strain zone id — by personal percentile, or monotony fallback."""
    if bands.source == "percentile":
        if strain_today >= bands.hard_min:
            return "overload"
        if strain_today >= bands.calm_max:
            return "building"
        return "calm"
    # Fallback: no reliable personal history yet → monotony literature bands.
    if monotony_today >= MONOTONY_DANGER:
        return "overload"
    if monotony_today >= MONOTONY_CAUTION:
        return "building"
    return "calm"


# ─── Top-level compute ──────────────────────────────────────────────


@dataclass(frozen=True)
class TrainingStrainPoint:
    dt: date
    strain: float
    monotony: float
    weekly_load: float


@dataclass(frozen=True)
class TrainingStrainResult:
    strain: float
    monotony: float
    weekly_load: float
    weekly_load_prev: float  # week-ago weekly load, for the Δ chip
    acwr: float | None
    zone_id: str  # "calm" | "building" | "overload"
    bands: StrainBands
    trend: list[TrainingStrainPoint]
    insufficient_data: bool
    insufficient_reason: str | None = None


def compute_training_strain(
    *,
    ref_date: date,
    daily_tss_by_date: dict[date, float],
    atl: float | None,
    ctl: float | None,
    trend_start: date,
    history_start: date,
) -> TrainingStrainResult:
    """Compute current Training Strain + ACWR + zone + rolling trend.

    Pure — no IO, no clock. The caller fetches a daily-TSS map covering
    ``history_start - ACUTE_WINDOW_DAYS .. ref_date`` (history_start drives the
    percentile baseline, trend_start the visible trend window), plus today's
    CTL/ATL for ACWR.

    Args:
      ref_date:       today (last point of every window).
      daily_tss_by_date: date → summed TSS; missing days treated as 0.
      atl, ctl:       today's EWMA loads, for ACWR.
      trend_start:    first day of the returned trend series.
      history_start:  first day of the strain history used for percentile bands.
    """
    today_window = _window(daily_tss_by_date, ref_date, ACUTE_WINDOW_DAYS)
    strain_today = strain(today_window)
    monotony_today = monotony(today_window)
    week_load = weekly_load(today_window)
    acwr_val = acwr(atl, ctl)

    prev_window = _window(daily_tss_by_date, ref_date - timedelta(days=ACUTE_WINDOW_DAYS), ACUTE_WINDOW_DAYS)
    week_load_prev = weekly_load(prev_window)

    # Strain history for percentile bands — one strain value per day across the
    # baseline window. Reuse the rolling series so today's bands reflect the
    # same definition the trend draws.
    hist_series = strain_series(daily_tss_by_date, start=history_start, end=ref_date)
    bands = strain_bands([s for (_, s, _, _) in hist_series])
    zone_id = classify_strain(strain_today, monotony_today, bands)

    # Visible trend slice (trend_start .. ref_date) — a sub-window of history.
    trend = [
        TrainingStrainPoint(dt=d, strain=s, monotony=mono, weekly_load=wl)
        for (d, s, mono, wl) in hist_series
        if d >= trend_start
    ]

    insufficient = week_load <= 0
    return TrainingStrainResult(
        strain=round(strain_today, 1),
        monotony=round(monotony_today, 2),
        weekly_load=round(week_load, 1),
        weekly_load_prev=round(week_load_prev, 1),
        acwr=round(acwr_val, 2) if acwr_val is not None else None,
        zone_id=zone_id,
        bands=bands,
        trend=trend,
        insufficient_data=insufficient,
        insufficient_reason="no_recent_load" if insufficient else None,
    )


__all__ = [
    "ACUTE_WINDOW_DAYS",
    "ACWR_DANGER",
    "ACWR_SWEET_HI",
    "ACWR_SWEET_LO",
    "MONOTONY_CAP",
    "MONOTONY_CAUTION",
    "MONOTONY_DANGER",
    "STRAIN_HISTORY_MIN_DAYS",
    "STRAIN_PCT_CALM",
    "STRAIN_PCT_HARD",
    "StrainBands",
    "TrainingStrainPoint",
    "TrainingStrainResult",
    "acwr",
    "acwr_status",
    "classify_strain",
    "compute_training_strain",
    "monotony",
    "percentile",
    "strain",
    "strain_bands",
    "strain_series",
    "weekly_load",
]
