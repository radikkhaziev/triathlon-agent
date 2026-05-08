"""MCP tool for HR/power/pace zone configuration.

Zone boundaries come from Intervals.icu sport-settings (synced to athlete_settings).
Fallback: compute from LTHR/FTP if zone boundaries are not yet synced.

Per ``data/db/athlete.py`` zones contract:
  - ``hr_zones``    — absolute bpm, ascending
  - ``power_zones`` — **%FTP**, ascending  (NOT absolute watts)
  - ``pace_zones``  — **%threshold** where 100.0 = threshold, ascending. Higher
                      pct = faster pace (since pace and speed are reciprocals).

Output for power/pace zones carries **both** representations: raw percentage
(``min_pct`` / ``max_pct``) and absolute units (``min_w``/``max_w`` for power,
``min_sec_per_km``/``max_sec_per_km`` or ``_per_100m`` for pace). Consumers
don't have to recompute or guess units.
"""

from data.db import AthleteSettings
from data.db.dto import AthleteThresholdsDTO
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id

# Intervals.icu marks the open-upward edge of the top zone with this sentinel
# value in the pct boundary array. Treat as «no upper bound»; phantom zones
# whose lower bound IS the sentinel get dropped.
_SENTINEL_PCT = 999


def _hr_zones_from_boundaries(boundaries: list, names: list | None) -> list[dict]:
    """Build HR zone list from absolute bpm boundaries (ascending)."""
    zones: list[dict] = []
    for i in range(len(boundaries) + 1):
        name = names[i] if names and i < len(names) else f"Z{i + 1}"
        lo = 0 if i == 0 else boundaries[i - 1] + 1
        hi = boundaries[i] if i < len(boundaries) else None
        zone: dict = {"zone": i + 1, "name": name, "min_hr": lo}
        if hi is not None:
            zone["max_hr"] = hi
        zones.append(zone)
    return zones


def _dual_unit_power_zones(boundaries: list, names: list | None, ftp: int) -> list[dict]:
    """Build power zone list from %FTP boundaries.

    Each zone carries both percentage and absolute-watt bounds. Sentinel
    (>=999) markers translate to «no upper bound»; phantom zones whose lower
    bound equals the sentinel are dropped.
    """
    zones: list[dict] = []
    for i in range(len(boundaries) + 1):
        low_pct = 0.0 if i == 0 else boundaries[i - 1]
        high_pct = boundaries[i] if i < len(boundaries) else None
        if low_pct >= _SENTINEL_PCT:
            continue
        if high_pct is not None and high_pct >= _SENTINEL_PCT:
            high_pct = None
        name = names[i] if names and i < len(names) else f"Z{i + 1}"
        zone: dict = {
            "zone": i + 1,
            "name": name,
            "min_pct": int(low_pct) if float(low_pct).is_integer() else round(low_pct, 1),
            "min_w": int(round(ftp * low_pct / 100)),
        }
        if high_pct is not None:
            zone["max_pct"] = int(high_pct) if float(high_pct).is_integer() else round(high_pct, 1)
            zone["max_w"] = int(round(ftp * high_pct / 100))
        zones.append(zone)
    return zones


def _dual_unit_pace_zones(boundaries: list, names: list | None, threshold_pace_sec: float, sport: str) -> list[dict]:
    """Build pace zone list from %threshold boundaries.

    Higher pct = faster pace. Output keys are sport-aware: ``sec_per_km`` for
    Run, ``sec_per_100m`` for Swim. ``threshold_pace_sec`` is already in the
    sport-native unit (Run sec/km, Swim sec/100m).

    Inverted asymmetry vs power: low_pct → ``max_sec`` (slow side),
    high_pct → ``min_sec`` (fast side), because pace × speed = const.
    """
    sec_unit = "sec_per_100m" if sport == "Swim" else "sec_per_km"

    zones: list[dict] = []
    for i in range(len(boundaries) + 1):
        low_pct = 0.0 if i == 0 else boundaries[i - 1]
        high_pct = boundaries[i] if i < len(boundaries) else None
        if low_pct >= _SENTINEL_PCT:
            continue
        if high_pct is not None and high_pct >= _SENTINEL_PCT:
            high_pct = None
        name = names[i] if names and i < len(names) else f"Z{i + 1}"
        zone: dict = {
            "zone": i + 1,
            "name": name,
            "min_pct": int(low_pct) if float(low_pct).is_integer() else round(low_pct, 1),
        }
        # low_pct == 0 → no slower limit (zone extends to infinitely slow pace).
        # Skip max_sec_per_X to signal that.
        if low_pct > 0:
            zone[f"max_{sec_unit}"] = int(round(threshold_pace_sec * 100 / low_pct))
        if high_pct is not None:
            zone["max_pct"] = int(high_pct) if float(high_pct).is_integer() else round(high_pct, 1)
            # high_pct > 0 guard: ZeroDivisionError on a malformed (`high_pct == 0`)
            # boundary. Intervals.icu doesn't emit such values, but the MCP layer
            # ingests data after sync/migrations and shouldn't crash on garbage.
            if high_pct > 0:
                zone[f"min_{sec_unit}"] = int(round(threshold_pace_sec * 100 / high_pct))
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
    """Computed Coggan zones when Intervals.icu hasn't synced sport_settings yet.

    Emits the same dual-unit shape as :func:`_dual_unit_power_zones` so
    consumers don't branch on ``source``. Rounding is unified
    (``int(round(...))``) with the synced path — pure-truncation diverges on
    boundaries like ``0.5``.
    """
    return [
        {
            "zone": i + 1,
            "name": name,
            "min_pct": int(round(lo * 100)),
            "max_pct": int(round(hi * 100)),
            "min_w": int(round(ftp * lo)),
            "max_w": int(round(ftp * hi)),
        }
        for i, (name, lo, hi) in enumerate(_FALLBACK_POWER)
    ]


_SPORT_KEY = {"Ride": "bike", "Run": "run", "Swim": "swim"}


def _build_sport_zones(s: AthleteSettings, sport: str, result: dict) -> None:
    """Add HR/power/pace zones for a sport to the result dict.

    Each block is sport-tagged (``power_zones_bike`` / ``power_zones_run`` etc.)
    so athletes with both running power (Stryd / Garmin RP) and cycling power
    don't lose one side to last-write-wins.
    """
    prefix = _SPORT_KEY.get(sport, sport.lower())

    # HR zones — absolute bpm, no dual-unit treatment needed
    if s.hr_zones:
        result[f"hr_zones_{prefix}"] = {
            "lthr": s.lthr,
            "source": "intervals.icu",
            "zones": _hr_zones_from_boundaries(s.hr_zones, s.hr_zone_names),
        }
    elif s.lthr:
        fallback = _FALLBACK_HR_RUN if sport == "Run" else _FALLBACK_HR_BIKE
        result[f"hr_zones_{prefix}"] = {
            "lthr": s.lthr,
            "source": "calculated",
            "zones": _fallback_hr_zones(s.lthr, fallback),
        }

    # Power zones — sport-tagged. Bike + Run can both have power.
    if s.power_zones and s.ftp:
        result[f"power_zones_{prefix}"] = {
            "ftp": s.ftp,
            "source": "intervals.icu",
            "zones": _dual_unit_power_zones(s.power_zones, s.power_zone_names, s.ftp),
        }
    elif s.ftp:
        result[f"power_zones_{prefix}"] = {
            "ftp": s.ftp,
            "source": "calculated",
            "zones": _fallback_power_zones(s.ftp),
        }

    # Pace zones — sport-tagged. Run + Swim both have pace; Ride doesn't.
    if s.pace_zones and s.threshold_pace:
        result[f"pace_zones_{prefix}"] = {
            "threshold_pace": s.threshold_pace,
            "source": "intervals.icu",
            "zones": _dual_unit_pace_zones(s.pace_zones, s.pace_zone_names, s.threshold_pace, sport),
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

    Power and pace zone objects carry **both** percentage (``min_pct`` /
    ``max_pct``) and absolute units (``min_w``/``max_w`` or
    ``min_sec_per_km``/``max_sec_per_km``). Per-sport tagging
    (``power_zones_bike`` / ``power_zones_run``) keeps Stryd-style running
    power separate from cycling power.
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
