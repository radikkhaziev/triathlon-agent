"""MCP tool for HR/power/pace zone configuration."""

from data.db import AthleteSettings
from data.db.dto import AthleteThresholdsDTO
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id

# Zone definitions: (name, min_pct, max_pct) of LTHR
_HR_ZONES_RUN = [
    ("Recovery", 0, 0.72),
    ("Endurance", 0.72, 0.82),
    ("Tempo", 0.82, 0.87),
    ("Threshold", 0.87, 0.92),
    ("VO2max", 0.92, 1.00),
]

_HR_ZONES_BIKE = [
    ("Recovery", 0, 0.68),
    ("Endurance", 0.68, 0.83),
    ("Tempo", 0.83, 0.94),
    ("Threshold", 0.94, 1.05),
    ("VO2max", 1.05, 1.20),
]

# Power zones: (name, min_pct, max_pct) of FTP
_POWER_ZONES = [
    ("Active Recovery", 0, 0.55),
    ("Endurance", 0.55, 0.75),
    ("Tempo", 0.75, 0.90),
    ("Threshold", 0.90, 1.05),
    ("VO2max", 1.05, 1.20),
]


def _build_hr_zones(lthr: int, zone_defs: list[tuple]) -> list[dict]:
    return [
        {"zone": i + 1, "name": name, "min_hr": int(lthr * lo), "max_hr": int(lthr * hi)}
        for i, (name, lo, hi) in enumerate(zone_defs)
    ]


def _build_power_zones(ftp: int) -> list[dict]:
    return [
        {"zone": i + 1, "name": name, "min_w": int(ftp * lo), "max_w": int(ftp * hi)}
        for i, (name, lo, hi) in enumerate(_POWER_ZONES)
    ]


@mcp.tool()
async def get_zones() -> dict:
    """Get HR, power, and pace zone boundaries for Run, Bike, and Swim."""
    user_id = get_current_user_id()
    t: AthleteThresholdsDTO = await AthleteSettings.get_thresholds(user_id)

    result: dict = {
        "max_hr": t.max_hr,
        "age": t.age,
    }

    if t.lthr_run:
        result["hr_zones_run"] = {
            "lthr": t.lthr_run,
            "zones": _build_hr_zones(t.lthr_run, _HR_ZONES_RUN),
        }

    if t.lthr_bike:
        result["hr_zones_bike"] = {
            "lthr": t.lthr_bike,
            "zones": _build_hr_zones(t.lthr_bike, _HR_ZONES_BIKE),
        }

    if t.ftp:
        result["power_zones"] = {
            "ftp": t.ftp,
            "zones": _build_power_zones(t.ftp),
        }

    if t.css:
        result["pace_zones_swim"] = {
            "css": t.css,
            "css_formatted": f"{int(t.css // 60)}:{int(t.css % 60):02d}/100m",
        }

    if t.threshold_pace_run:
        result["pace_run"] = {
            "threshold_pace_sec_km": t.threshold_pace_run,
            "threshold_pace_formatted": f"{int(t.threshold_pace_run // 60)}:{int(t.threshold_pace_run % 60):02d}/km",
        }

    return result
