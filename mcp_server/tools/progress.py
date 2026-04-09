"""MCP tools for aerobic efficiency and progress tracking."""

import logging
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from data.db import Activity, ActivityDetail, AthleteSettings
from data.db.dto import AthleteThresholdsDTO
from data.metrics import decoupling_status, is_valid_for_decoupling
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id

logger = logging.getLogger(__name__)

# Sport type groupings
_BIKE_TYPES = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide", "EBikeRide"}
_RUN_TYPES = {"Run", "VirtualRun", "TrailRun"}
_SWIM_TYPES = {"Swim", "OpenWaterSwim"}

# Minimum duration (seconds) for meaningful steady-state comparison
_MIN_DURATION = {"bike": 30 * 60, "run": 20 * 60, "swim": 15 * 60}

# Z2 HR ranges as fraction of LTHR (from CLAUDE.md)
_Z2_BIKE = (0.68, 0.83)
_Z2_RUN = (0.72, 0.82)


def _sport_group(activity_type: str) -> str | None:
    """Map Intervals.icu activity type to sport group."""
    if activity_type in _BIKE_TYPES:
        return "bike"
    if activity_type in _RUN_TYPES:
        return "run"
    if activity_type in _SWIM_TYPES:
        return "swim"
    return None


def _is_z2(avg_hr: float | None, sport: str, thresholds: AthleteThresholdsDTO) -> bool:
    """Check if average HR is in Z2 range for the sport."""
    if not avg_hr:
        return False
    if sport == "bike":
        lthr = thresholds.lthr_bike
        if not lthr:
            return False
        lo, hi = _Z2_BIKE
    elif sport == "run":
        lthr = thresholds.lthr_run
        if not lthr:
            return False
        lo, hi = _Z2_RUN
    else:
        return True  # Swim: no HR filter
    ratio = avg_hr / lthr
    return lo <= ratio <= hi


def _calc_swolf(pace: float, avg_stride: float, pool_length: float) -> float | None:
    """Calculate SWOLF from pace, stride and pool length.

    pace: m/s, avg_stride: m/stroke, pool_length: meters.
    SWOLF = time_per_length + strokes_per_length.
    """
    if not pace or pace <= 0 or not avg_stride or avg_stride <= 0 or not pool_length or pool_length <= 0:
        return None
    time_per_length = pool_length / pace
    strokes_per_length = pool_length / avg_stride
    return round(time_per_length + strokes_per_length, 1)


def _week_key(dt: date) -> str:
    """ISO week string: 2026-W12."""
    return f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"


def _trend_pct(values: list[float]) -> dict:
    """Calculate trend from first to last value."""
    if len(values) < 2 or values[0] == 0:
        return {"direction": "insufficient_data", "pct": 0}
    change = (values[-1] - values[0]) / abs(values[0]) * 100
    direction = "rising" if change > 1 else "falling" if change < -1 else "stable"
    return {"direction": direction, "pct": round(change, 1)}


@mcp.tool()
async def get_efficiency_trend(
    sport: str = "",
    days_back: int = 90,
    group_by: str = "week",
    strict_filter: bool = False,
) -> dict:
    """Get aerobic efficiency trend over time.

    Bike: EF = Normalized Power / Avg HR (higher = fitter). From Intervals.icu icu_efficiency_factor.
    Run: EF = Speed / Avg HR (higher = fitter). From Intervals.icu icu_efficiency_factor.
    Swim: Pace per 100m trend (lower = faster) + SWOLF (lower = more efficient).

    Only includes Z2 steady-state sessions for meaningful comparison.
    Minimum duration: bike 30min, run 20min, swim 15min.

    With strict_filter=True: applies stricter filtering for decoupling analysis
    (VI <= 1.10, >70% Z1+Z2, bike >= 60min / run >= 45min, swim excluded).
    Adds decoupling_trend with last-5 median and traffic light status.

    Args:
        sport: "bike", "run", or "swim". Empty string = all three sports.
        days_back: Lookback period in days (default: 90).
        group_by: "week" for weekly averages, "activity" for individual data points.
        strict_filter: Apply strict decoupling-analysis filter (VI, zone adherence, duration).
    """
    user_id = get_current_user_id()
    return await compute_efficiency_trend(
        user_id=user_id,
        sport=sport,
        days_back=days_back,
        group_by=group_by,
        strict_filter=strict_filter,
    )


async def compute_efficiency_trend(
    user_id: int,
    sport: str = "",
    days_back: int = 90,
    group_by: str = "week",
    strict_filter: bool = False,
) -> dict:
    """Core efficiency trend logic — usable from API and MCP."""
    since = date.today() - timedelta(days=days_back)
    activities, _ = await Activity.get_range(user_id, since, date.today())

    # Filter by sport
    target_sports = {sport.lower()} if sport else {"bike", "run", "swim"}
    if strict_filter:
        target_sports -= {"swim"}

    thresholds: AthleteThresholdsDTO = await AthleteSettings.get_thresholds(user_id)

    # Pre-filter activities before bulk DB fetch
    filtered = []
    for act in activities:
        sg = _sport_group(act.type)
        if not sg or sg not in target_sports:
            continue
        min_dur = _MIN_DURATION.get(sg, 0)
        if strict_filter:
            pass  # strict duration handled by is_valid_for_decoupling below
        elif (act.moving_time or 0) < min_dur:
            continue
        # Z2 HR filter only in strict mode — non-strict includes all activities with EF
        if strict_filter and sg in ("bike", "run") and not _is_z2(act.average_hr, sg, thresholds):
            continue
        filtered.append((act, sg))

    if not filtered:
        return {"data_points": 0, "activities": []}

    # Bulk fetch details (single query)
    detail_map = await ActivityDetail.get_bulk([act.id for act, _ in filtered])

    # Collect matching activities with details
    results: dict[str, list[dict]] = defaultdict(list)

    for act, sg in filtered:
        detail = detail_map.get(act.id)
        if not detail:
            continue

        # Strict decoupling filter: skip activities that don't meet criteria
        if strict_filter and sg in ("bike", "run"):
            if not is_valid_for_decoupling(
                activity_type=act.type,
                moving_time=act.moving_time,
                variability_index=detail.variability_index,
                hr_zone_times=detail.hr_zone_times,
                decoupling=detail.decoupling,
            ):
                continue

        act_date = act.start_date_local.date() if hasattr(act.start_date_local, "date") else act.start_date_local
        entry = {
            "date": str(act_date),
            "id": act.id,
            "duration_min": round((act.moving_time or 0) / 60),
            "avg_hr": act.average_hr,
        }

        if sg in ("bike", "run"):
            ef = detail.efficiency_factor
            # Fallback: compute EF from speed/HR when Intervals.icu doesn't provide it
            if (not ef or ef <= 0) and detail.pace and detail.pace > 0 and act.average_hr and act.average_hr > 0:
                ef = detail.pace / act.average_hr
            if not ef or ef <= 0:
                continue
            entry["ef"] = round(ef, 4)
            entry["decoupling"] = round(detail.decoupling, 1) if detail.decoupling else None
            entry["np"] = detail.normalized_power if sg == "bike" else None
            entry["pace"] = round(detail.pace, 4) if detail.pace else None
            if strict_filter and detail.decoupling is not None:
                entry["decoupling_status"] = decoupling_status(detail.decoupling)
        elif sg == "swim":
            if not detail.pace or detail.pace <= 0:
                continue
            pace_100m = 100 / detail.pace  # seconds per 100m
            entry["pace_100m"] = round(pace_100m, 1)
            entry["distance"] = detail.distance
            pool_length = detail.pool_length or 25.0
            entry["pool_length"] = pool_length
            swolf = _calc_swolf(detail.pace, detail.avg_stride, pool_length)
            entry["swolf"] = swolf

        results[sg].append(entry)

    # Build response per sport
    response = {}
    for sg in sorted(results.keys()):
        entries = sorted(results[sg], key=lambda e: e["date"])
        sport_resp: dict = {
            "sport": sg,
            "period": f"{entries[0]['date']} to {entries[-1]['date']}" if entries else "",
            "data_points": len(entries),
            "activities": entries,
        }

        if group_by == "week":
            weekly = _group_weekly(entries, sg)
            sport_resp["weekly"] = weekly

            # Trend
            if sg in ("bike", "run"):
                ef_values = [w["ef_mean"] for w in weekly if w["ef_mean"]]
                sport_resp["metric"] = "efficiency_factor"
                sport_resp["unit"] = "W/bpm" if sg == "bike" else "(m/s)/bpm"
                sport_resp["trend"] = _trend_pct(ef_values)
            elif sg == "swim":
                pace_values = [w["pace_mean"] for w in weekly if w["pace_mean"]]
                swolf_values = [w["swolf_mean"] for w in weekly if w["swolf_mean"]]
                sport_resp["metrics"] = {
                    "pace_100m": {"unit": "sec/100m", "trend": _trend_pct(pace_values)},
                    "swolf": {"unit": "points", "trend": _trend_pct(swolf_values)},
                }

        # Decoupling trend summary (last-5 median) when strict filter is on
        if strict_filter and sg in ("bike", "run"):
            dec_entries = [(e["decoupling"], e["date"]) for e in entries if e.get("decoupling") is not None]
            last_5 = [v for v, _ in dec_entries[-5:]]
            if last_5:
                med = round(median(last_5), 1)
                last_val, last_date = dec_entries[-1]
                sport_resp["decoupling_trend"] = {
                    "last_n": len(last_5),
                    "median": med,
                    "status": decoupling_status(med),
                    "values": last_5,
                    "latest": {
                        "value": last_val,
                        "status": decoupling_status(last_val),
                        "date": last_date,
                        "days_since": (date.today() - date.fromisoformat(last_date)).days,
                    },
                }

        response[sg] = sport_resp

    if len(response) == 1:
        return next(iter(response.values()))
    return response


def _group_weekly(entries: list[dict], sport: str) -> list[dict]:
    """Group activity entries by ISO week."""
    weeks: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        dt = date.fromisoformat(e["date"])
        weeks[_week_key(dt)].append(e)

    result = []
    for week in sorted(weeks.keys()):
        items = weeks[week]
        row: dict = {"week": week, "sessions": len(items)}

        if sport in ("bike", "run"):
            efs = [e["ef"] for e in items if e.get("ef")]
            decs = [e["decoupling"] for e in items if e.get("decoupling") is not None]
            row["ef_mean"] = round(sum(efs) / len(efs), 4) if efs else None
            row["decoupling_mean"] = round(sum(decs) / len(decs), 1) if decs else None
            if decs:
                row["decoupling_median"] = round(median(decs), 1)
        elif sport == "swim":
            paces = [e["pace_100m"] for e in items if e.get("pace_100m")]
            swolfs = [e["swolf"] for e in items if e.get("swolf")]
            row["pace_mean"] = round(sum(paces) / len(paces), 1) if paces else None
            row["swolf_mean"] = round(sum(swolfs) / len(swolfs), 1) if swolfs else None

        result.append(row)

    return result
