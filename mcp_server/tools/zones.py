"""MCP tool for HR/power/pace zone configuration.

Zone boundaries come from Intervals.icu sport-settings (synced to athlete_settings).
Fallback: compute from LTHR/FTP if zone boundaries are not yet synced.
"""

from data.db import AthleteSettings
from data.db.dto import AthleteThresholdsDTO
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


def _zones_from_boundaries(boundaries: list, names: list | None, label: str) -> list[dict]:
    """Build zone list from Intervals.icu boundary array.

    Intervals.icu stores zones as N threshold values → N+1 zones.
    E.g. hr_zones=[129, 136, 144, 152, 157, 161] → 7 zones:
      Z1: 0-129, Z2: 130-136, ..., Z7: 162+
    """
    zones = []
    for i in range(len(boundaries) + 1):
        lo = 0 if i == 0 else boundaries[i - 1] + (1 if label != "pace" else 0)
        hi = boundaries[i] if i < len(boundaries) else None
        name = names[i] if names and i < len(names) else f"Z{i + 1}"
        zone: dict = {"zone": i + 1, "name": name}
        if label == "pace":
            zone["min"] = lo
            if hi is not None:
                zone["max"] = hi
        else:
            zone[f"min_{label}"] = lo
            if hi is not None:
                zone[f"max_{label}"] = hi
        zones.append(zone)
    return zones


# Fallback zone definitions when Intervals.icu boundaries not synced
_FALLBACK_HR_RUN = [
    ("Recovery", 0, 0.84),
    ("Aerobic", 0.85, 0.89),
    ("Tempo", 0.90, 0.94),
    ("SubThreshold", 0.95, 0.99),
    ("SuperThreshold", 1.00, 1.03),
    ("Aerobic Capacity", 1.03, 1.06),
    ("Anaerobic", 1.06, 1.20),
]

_FALLBACK_HR_BIKE = [
    ("Recovery", 0, 0.68),
    ("Endurance", 0.68, 0.83),
    ("Tempo", 0.83, 0.94),
    ("Threshold", 0.94, 1.05),
    ("VO2max", 1.05, 1.20),
]

_FALLBACK_POWER = [
    ("Active Recovery", 0, 0.55),
    ("Endurance", 0.55, 0.75),
    ("Tempo", 0.75, 0.90),
    ("Threshold", 0.90, 1.05),
    ("VO2max", 1.05, 1.20),
]


def _fallback_hr_zones(lthr: int, zone_defs: list[tuple]) -> list[dict]:
    return [
        {"zone": i + 1, "name": name, "min_hr": int(lthr * lo), "max_hr": int(lthr * hi)}
        for i, (name, lo, hi) in enumerate(zone_defs)
    ]


def _fallback_power_zones(ftp: int) -> list[dict]:
    return [
        {"zone": i + 1, "name": name, "min_w": int(ftp * lo), "max_w": int(ftp * hi)}
        for i, (name, lo, hi) in enumerate(_FALLBACK_POWER)
    ]


def _build_sport_zones(s: AthleteSettings, sport: str, result: dict) -> None:
    """Add HR/power/pace zones for a sport to the result dict."""
    prefix = sport.lower()

    # HR zones
    if s.hr_zones:
        result[f"hr_zones_{prefix}"] = {
            "lthr": s.lthr,
            "source": "intervals.icu",
            "zones": _zones_from_boundaries(s.hr_zones, s.hr_zone_names, "hr"),
        }
    elif s.lthr:
        fallback = _FALLBACK_HR_RUN if sport == "Run" else _FALLBACK_HR_BIKE
        result[f"hr_zones_{prefix}"] = {
            "lthr": s.lthr,
            "source": "calculated",
            "zones": _fallback_hr_zones(s.lthr, fallback),
        }

    # Power zones (Ride only)
    if s.power_zones:
        result["power_zones"] = {
            "ftp": s.ftp,
            "source": "intervals.icu",
            "zones": _zones_from_boundaries(s.power_zones, None, "w"),
        }
    elif s.ftp:
        result["power_zones"] = {
            "ftp": s.ftp,
            "source": "calculated",
            "zones": _fallback_power_zones(s.ftp),
        }

    # Pace zones
    if s.pace_zones:
        result[f"pace_zones_{prefix}"] = {
            "threshold_pace": s.threshold_pace,
            "source": "intervals.icu",
            "zones": _zones_from_boundaries(s.pace_zones, s.pace_zone_names, "pace"),
        }
    elif s.threshold_pace:
        sec = s.threshold_pace
        result[f"pace_zones_{prefix}"] = {
            "threshold_pace_sec": sec,
            "threshold_pace_formatted": (
                f"{int(sec // 60)}:{int(sec % 60):02d}" + (f"/{s.pace_units}" if s.pace_units else "")
            ),
        }


@mcp.tool()
async def get_zones() -> dict:
    """Get HR, power, and pace zone boundaries for Run, Bike, and Swim.

    Zone boundaries are synced from Intervals.icu sport-settings.
    If not yet synced, falls back to calculated zones from LTHR/FTP.
    """
    user_id = get_current_user_id()
    all_settings = await AthleteSettings.get_all(user_id)

    t: AthleteThresholdsDTO = await AthleteSettings.get_thresholds(user_id)
    result: dict = {"max_hr": t.max_hr, "age": t.age}

    for s in all_settings:
        if s.sport in ("Run", "Ride", "Swim"):
            _build_sport_zones(s, s.sport, result)

    # CSS for swim (always from thresholds)
    if t.css:
        result.setdefault("pace_zones_swim", {})["css"] = t.css
        result.setdefault("pace_zones_swim", {})["css_formatted"] = f"{int(t.css // 60)}:{int(t.css % 60):02d}/100m"

    return result
