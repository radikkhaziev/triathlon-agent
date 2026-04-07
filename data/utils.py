"""Shared utilities for sport type mapping, CTL extraction, and serialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.db import ActivityDetail, ActivityHrv

# Canonical mapping: Intervals.icu activity/sport type → swim/bike/run
# TODO: consider using a more robust approach (e.g. fuzzy matching) if we encounter more variations in the future.
SPORT_MAP: dict[str, str] = {
    "swim": "swim",
    "swimming": "swim",
    "openwaterswim": "swim",
    "ride": "bike",
    "bike": "bike",
    "cycling": "bike",
    "virtualride": "bike",
    "mountainbikeride": "bike",
    "gravelride": "bike",
    "ebikeride": "bike",
    "emountainbikeride": "bike",
    "trackride": "bike",
    "run": "run",
    "running": "run",
    "virtualrun": "run",
    "trailrun": "run",
}


def tsb_zone(tsb: float | None) -> str | None:
    """Classify TSB value into a training zone.

    Zones (calibrated for Intervals.icu):
    >+10 under_training, -10..+10 optimal, -10..-25 productive_overreach, <-25 overtraining_risk.
    """
    if tsb is None:
        return None
    if tsb > 10:
        return "under_training"
    if tsb >= -10:
        return "optimal"
    if tsb >= -25:
        return "productive_overreach"
    return "overtraining_risk"


def extract_sport_ctl(sport_info: list[dict] | None) -> dict[str, float | None]:
    """Extract per-sport CTL from sport_info JSON stored in wellness.

    Looks for 'ctl' field inside each sport entry. Returns dict with
    swim/bike/run CTL values, or None if not available.

    Works with both the original Intervals.icu format (type + eftp/wPrime/pMax)
    enriched with 'ctl' field by our pipeline, and any legacy formats.
    """
    result: dict[str, float | None] = {"swim": None, "bike": None, "run": None}
    if not sport_info:
        return result
    if not isinstance(sport_info, list):
        return result
    for entry in sport_info:
        raw_type = (entry.get("type") or entry.get("sport") or "").lower()
        sport = SPORT_MAP.get(raw_type)
        if not sport:
            continue
        ctl_val = entry.get("ctl")
        if ctl_val is None:
            ctl_val = entry.get("ctlLoad")
        if ctl_val is None:
            continue
        result[sport] = round(float(ctl_val), 1)
    return result


def extract_sport_ctl_tuple(sport_info: list[dict] | None) -> tuple[float, float, float]:
    """Same as extract_sport_ctl but returns (swim, bike, run) tuple.

    Returns 0.0 instead of None for missing values — used in AI prompt formatting.
    """
    d = extract_sport_ctl(sport_info)
    return (d["swim"] or 0.0, d["bike"] or 0.0, d["run"] or 0.0)


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
        "dfa_a1_warmup": hrv.dfa_a1_warmup,
        "hrv_quality": hrv.hrv_quality,
        "ra_pct": hrv.ra_pct,
        "da_pct": hrv.da_pct,
        "hrvt1_hr": hrv.hrvt1_hr,
        "hrvt1_power": hrv.hrvt1_power,
        "hrvt1_pace": hrv.hrvt1_pace,
        "hrvt2_hr": hrv.hrvt2_hr,
        "processing_status": hrv.processing_status,
    }
