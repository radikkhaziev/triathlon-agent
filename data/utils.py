"""Shared utilities for sport type normalization, CTL extraction, and serialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.db import ActivityDetail, ActivityHrv

# ---------------------------------------------------------------------------
# Sport type normalization: Intervals.icu raw types → 4 canonical types
# ---------------------------------------------------------------------------

_RAW_TO_CANONICAL: dict[str, str] = {
    # Swim
    "Swim": "Swim",
    "OpenWaterSwim": "Swim",
    # Bike
    "Ride": "Ride",
    "VirtualRide": "Ride",
    "GravelRide": "Ride",
    "MountainBikeRide": "Ride",
    "EBikeRide": "Ride",
    "EMountainBikeRide": "Ride",
    "TrackRide": "Ride",
    "Velomobile": "Ride",
    "Handcycle": "Ride",
    # Run
    "Run": "Run",
    "VirtualRun": "Run",
    "TrailRun": "Run",
}

HRV_ELIGIBLE_TYPES = frozenset({"Ride", "Run"})


def normalize_sport(raw_type: str | None) -> str | None:
    """Normalize Intervals.icu activity type to canonical: Ride, Run, Swim, Other."""
    if raw_type is None:
        return None
    return _RAW_TO_CANONICAL.get(raw_type, "Other")


def is_bike(t: str | None) -> bool:
    return t == "Ride"


def is_run(t: str | None) -> bool:
    return t == "Run"


# Legacy alias: lowercase sport key for CTL extraction.
# After normalization, activity types are already canonical — just .lower().
SPORT_MAP: dict[str, str] = {v.lower(): v.lower() for v in _RAW_TO_CANONICAL.values()}
# Add legacy aliases that may appear in sport_info JSON from Intervals.icu
SPORT_MAP.update({"bike": "ride", "cycling": "ride", "swimming": "swim", "running": "run"})


def tsb_zone(tsb: float | None) -> str | None:
    """Classify TSB value into a 5-band training zone.

    Source of truth: ``webapp/src/pages/LoadDetail.tsx::TSB_ZONES``. Ids mirror
    the frontend constant exactly so cross-stack discussion uses one vocabulary.

    Zones (calibrated for Intervals.icu, not TrainingPeaks):
        < -30     risk        High risk — gates Z2-cap on adapted workouts
        -30..-10  optimal     Productive training zone (no warning)
        -10..+5   gray        Neutral / maintenance
        +5..+25   fresh       Well-rested
        >= +25    transition  Under-training / peaked

    Boundary semantics match ``tsbZoneOf`` on the frontend: upper bound is
    EXCLUSIVE (`v < hi`), so TSB = +25 maps to ``transition``, not ``fresh``.
    """
    if tsb is None:
        return None
    if tsb < -30:
        return "risk"
    if tsb < -10:
        return "optimal"
    if tsb < 5:
        return "gray"
    if tsb < 25:
        return "fresh"
    return "transition"


def extract_sport_ctl(sport_info: list[dict] | None) -> dict[str, float | None]:
    """Extract per-sport CTL from sport_info JSON stored in wellness.

    Looks for 'ctl' field inside each sport entry. Returns dict with
    swim/ride/run CTL values, or None if not available.

    Works with both the original Intervals.icu format (type + eftp/wPrime/pMax)
    enriched with 'ctl' field by our pipeline, and any legacy formats.
    """
    return _extract_sport_field(sport_info, "ctl", legacy_key="ctlLoad")


def extract_sport_atl(sport_info: list[dict] | None) -> dict[str, float | None]:
    """Extract per-sport ATL from sport_info JSON. Symmetric to extract_sport_ctl."""
    return _extract_sport_field(sport_info, "atl", legacy_key="atlLoad")


def extract_sport_eftp(sport_info: list[dict] | None) -> dict[str, float | None]:
    """Extract per-sport eFTP from sport_info JSON.

    Intervals.icu fills `eftp` only for sports with power data (Ride when a
    power meter exists, Run for run-power users) — missing sports stay None.
    """
    return _extract_sport_field(sport_info, "eftp")


def _extract_sport_field(
    sport_info: list[dict] | None,
    key: str,
    *,
    legacy_key: str | None = None,
) -> dict[str, float | None]:
    result: dict[str, float | None] = {"swim": None, "ride": None, "run": None}
    if not sport_info or not isinstance(sport_info, list):
        return result
    for entry in sport_info:
        raw_type = (entry.get("type") or entry.get("sport") or "").lower()
        sport = SPORT_MAP.get(raw_type)
        if not sport:
            continue
        val = entry.get(key)
        if val is None and legacy_key is not None:
            val = entry.get(legacy_key)
        if val is None:
            continue
        result[sport] = round(float(val), 1)
    return result


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_duration(secs: int | None) -> str | None:
    """Format seconds into a human-readable duration string (e.g. '1h 30m')."""
    if secs is None:
        return None
    if secs <= 0:
        return "0m"
    h, remainder = divmod(secs, 3600)
    m = remainder // 60
    if h:
        return f"{h}h {m:02d}m" if m else f"{h}h"
    return f"{m}m"


# ---------------------------------------------------------------------------
# Serialization helpers (activity details / HRV)
# ---------------------------------------------------------------------------


def serialize_activity_details(detail: ActivityDetail) -> dict:
    """Convert ActivityDetail to a plain dict for JSON response."""
    return {
        "max_hr": detail.max_hr,
        "avg_power": detail.avg_power,
        "normalized_power": detail.normalized_power,
        "avg_speed": detail.avg_speed,
        "max_speed": detail.max_speed,
        "pace": detail.pace,
        "gap": detail.gap,
        "distance": detail.distance,
        "elevation_gain": detail.elevation_gain,
        "avg_cadence": detail.avg_cadence,
        "avg_stride": detail.avg_stride,
        "calories": detail.calories,
        "intensity_factor": detail.intensity_factor,
        "variability_index": detail.variability_index,
        "efficiency_factor": detail.efficiency_factor,
        "power_hr": detail.power_hr,
        "decoupling": detail.decoupling,
        "trimp": detail.trimp,
        "hr_zones": detail.hr_zones,
        "power_zones": detail.power_zones,
        "pace_zones": detail.pace_zones,
        "hr_zone_times": detail.hr_zone_times,
        "power_zone_times": detail.power_zone_times,
        "pace_zone_times": detail.pace_zone_times,
        "intervals": detail.intervals,
    }


def serialize_activity_hrv(hrv: ActivityHrv) -> dict:
    """Convert ActivityHrv to a plain dict for JSON response."""
    return {
        "dfa_a1_mean": hrv.dfa_a1_mean,
        "hrv_quality": hrv.hrv_quality,
        "ra_pct": hrv.ra_pct,
        "da_pct": hrv.da_pct,
        "hrvt1_hr": hrv.hrvt1_hr,
        "hrvt1_power": hrv.hrvt1_power,
        "hrvt1_pace": hrv.hrvt1_pace,
        "hrvt2_hr": hrv.hrvt2_hr,
    }
