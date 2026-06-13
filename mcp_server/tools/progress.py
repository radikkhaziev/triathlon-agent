"""MCP tools for aerobic efficiency and progress tracking."""

import logging
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from sqlalchemy import select

from data.db import Activity, ActivityDetail, AthleteSettings
from data.db.common import get_sync_session
from data.db.dto import AthleteThresholdsDTO
from data.metrics import decoupling_status, is_valid_for_decoupling
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id

logger = logging.getLogger(__name__)

# Minimum duration (seconds) for meaningful steady-state comparison
_MIN_DURATION = {"bike": 30 * 60, "run": 20 * 60, "swim": 15 * 60}

# Z2 HR ranges as fraction of LTHR
_Z2_BIKE = (0.68, 0.83)
_Z2_RUN = (0.72, 0.89)


def _sport_group(activity_type: str) -> str | None:
    """Map canonical activity type to sport group."""
    _MAP = {"Ride": "bike", "Run": "run", "Swim": "swim"}
    return _MAP.get(activity_type)


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
    """Get aerobic efficiency trend: EF (bike/run) or pace+SWOLF (swim) over time.

    Args:
        strict_filter: Apply strict decoupling filter (VI, zone adherence, min duration).
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

    thresholds: AthleteThresholdsDTO = await AthleteSettings.get_thresholds(user_id)

    # Pre-filter activities before bulk DB fetch
    filtered = []
    for act in activities:
        sg = _sport_group(act.type)
        if not sg or sg not in target_sports:
            continue
        # Race-effort excluded: peak race load is not a training-base signal.
        # All three callers (BR-1, /api/progress, AI MCP tool) analyse
        # training efficiency, not race performance.
        if act.is_race:
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
                ef = (detail.pace * 60) / act.average_hr  # m/s → m/min / HR
            if not ef or ef <= 0:
                continue
            entry["ef"] = round(ef, 4)
            entry["decoupling"] = round(detail.decoupling, 1) if detail.decoupling else None
            entry["np"] = detail.normalized_power if sg == "bike" else None
            entry["pace"] = round(detail.pace, 4) if detail.pace else None
            if detail.decoupling is not None:
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

        # Decoupling trend summary (last-5 median)
        if sg in ("bike", "run"):
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


# Loosened "similar session" match for the per-activity comparison block.
# Coverage over precision: the strict same-bucket + dominant-zone + TSB match
# left long/notable sessions at pool 0-1 (validated on real data 2026-06-03).
# ±30% duration + ±12 IF, no zone/TSB filter, 120d lifts routine sessions to
# pool 5-14 while keeping "similar" honest. Genuinely unique long efforts stay
# below _CMP_MIN_POOL and render an empty state rather than a 1-sample "norm".
_CMP_WINDOW_DAYS = 120
_CMP_DUR_TOL = 0.30
_CMP_IF_TOL = 12.0
_CMP_MIN_POOL = 3
# `activity_details.pace` is unit-ambiguous (sec/km or m/s, by source). Anything
# below this is m/s and gets inverted to sec/km — mirrors the webapp's
# `normalizePaceSecPerKm` (PACE_UNIT_THRESHOLD_SEC_PER_KM = 30).
_PACE_MS_THRESHOLD = 30.0


def _pace_to_sec_per_km(value: float | None) -> float | None:
    """Normalize the unit-ambiguous `pace` field to sec/km so the comparison is
    always lower-is-better. Values below `_PACE_MS_THRESHOLD` are treated as m/s
    and inverted; sec/km values pass through."""
    if not value or value <= 0:
        return None
    return 1000.0 / value if value < _PACE_MS_THRESHOLD else value


def _cmp_marker(
    key: str,
    value: float,
    norm: float,
    pool_n: int,
    *,
    lower_is_better: bool | None,
    status: str | None = None,
) -> dict:
    """One "this vs norm" row. `band` colours the delta; `lower_is_better=None`
    means the marker is neutral (avg HR, VI) — show the delta without a verdict."""
    delta = value - norm
    band = "neutral"
    if lower_is_better is not None and norm:
        if abs(delta) / abs(norm) < 0.05:
            band = "neutral"
        else:
            worse = (delta > 0) if lower_is_better else (delta < 0)
            band = "worse" if worse else "better"
    marker = {
        "key": key,
        "value": round(value, 3),
        "norm_median": round(norm, 3),
        "pool_n": pool_n,
        "delta": round(delta, 3),
        "band": band,
    }
    if status is not None:
        marker["status"] = status
    return marker


def _comparison_precheck(activity, detail) -> dict | None:
    """Cheap guards before any DB query. Returns an `available=False` dict to
    short-circuit, or None to proceed. Shared by the async + sync variants."""
    if activity.is_race:
        # Race effort vs an easy-session norm is apples-to-oranges (the pool is
        # non-race by design). The webapp also hides the block for races.
        return {"available": False, "pool_n": 0, "reason": "race"}
    sport = _sport_group(activity.type)
    ref_if = detail.intensity_factor if detail else None
    ref_dur = activity.moving_time
    if sport not in ("bike", "run") or detail is None or ref_if is None or not ref_dur:
        return {"available": False, "pool_n": 0, "reason": "unsupported"}
    return None


def _comparison_candidates(activity, activities) -> list:
    """Same-sport, non-race, duration ±30% peers from the window (pure filter)."""
    sport = _sport_group(activity.type)
    ref_dur = activity.moving_time
    dur_lo, dur_hi = ref_dur * (1 - _CMP_DUR_TOL), ref_dur * (1 + _CMP_DUR_TOL)
    return [
        a
        for a in activities
        if a.id != activity.id
        and not a.is_race
        and _sport_group(a.type) == sport
        and a.moving_time
        and dur_lo <= a.moving_time <= dur_hi
    ]


async def compute_activity_comparison(user_id: int, activity, detail) -> dict:
    """Deterministic "this session vs your norm" markers for the activity page.

    No Claude, no migration — pure aggregation over existing `activity_details`.
    Pool = same sport, non-race, duration ±30%, IF ±12, last 120d (see
    `_CMP_*`). Decoupling median is taken over valid-for-decoupling pool members
    only (short/high-VI sessions would poison it). Returns
    `{available, pool_n, markers}`; `available=False` with a `reason` when the
    sport is unsupported or the pool is too thin to be a meaningful norm.

    Sync twin for Dramatiq actors: `compute_activity_comparison_sync`.
    """
    early = _comparison_precheck(activity, detail)
    if early is not None:
        return early

    since = date.today() - timedelta(days=_CMP_WINDOW_DAYS)
    activities, _ = await Activity.get_range(user_id, since, date.today())
    candidates = _comparison_candidates(activity, activities)
    if not candidates:
        return {"available": False, "pool_n": 0, "reason": "no_similar"}

    detail_map = await ActivityDetail.get_bulk([a.id for a in candidates])
    return _assemble_comparison(activity, detail, candidates, detail_map)


def compute_activity_comparison_sync(user_id: int, activity, detail) -> dict:
    """Synchronous twin of `compute_activity_comparison` for Dramatiq actors.

    `Activity.get_range` / `ActivityDetail.get_bulk` are async-only (`@with_session`),
    and the global async engine's asyncpg pool is event-loop-bound — running it via
    `asyncio.run` from a multi-threaded sync worker reuses connections across loops
    and breaks. So this mirror reads through the thread-safe sync engine instead and
    delegates to the same pure `_assemble_comparison` core.
    """
    early = _comparison_precheck(activity, detail)
    if early is not None:
        return early

    since = date.today() - timedelta(days=_CMP_WINDOW_DAYS)
    with get_sync_session() as session:
        activities = (
            session.execute(
                select(Activity).where(
                    Activity.user_id == user_id,
                    Activity.start_date_local >= str(since),
                    Activity.start_date_local <= str(date.today()),
                )
            )
            .scalars()
            .all()
        )
        candidates = _comparison_candidates(activity, activities)
        if not candidates:
            return {"available": False, "pool_n": 0, "reason": "no_similar"}

        detail_rows = (
            session.execute(select(ActivityDetail).where(ActivityDetail.activity_id.in_([a.id for a in candidates])))
            .scalars()
            .all()
        )
        detail_map = {d.activity_id: d for d in detail_rows}
    return _assemble_comparison(activity, detail, candidates, detail_map)


def _assemble_comparison(activity, detail, candidates: list, detail_map: dict) -> dict:
    """Pure marker assembly from a candidate set + their details. Shared by the
    async + sync variants — no DB access, no Claude."""
    sport = _sport_group(activity.type)
    ref_if = detail.intensity_factor
    ref_dur = activity.moving_time
    pool: list[tuple] = []
    for a in candidates:
        d = detail_map.get(a.id)
        if d is None or d.intensity_factor is None:
            continue
        if abs(d.intensity_factor - ref_if) <= _CMP_IF_TOL:
            pool.append((a, d))

    if len(pool) < _CMP_MIN_POOL:
        return {"available": False, "pool_n": len(pool), "reason": "thin_pool"}

    markers: list[dict] = []

    # Decoupling — lower is better, median over valid-for-decoupling pool only.
    ref_dec_valid = is_valid_for_decoupling(
        activity_type=activity.type,
        moving_time=ref_dur,
        variability_index=detail.variability_index,
        hr_zone_times=detail.hr_zone_times,
        decoupling=detail.decoupling,
    )
    valid_dec = [
        d.decoupling
        for a, d in pool
        if d.decoupling is not None
        and is_valid_for_decoupling(
            activity_type=a.type,
            moving_time=a.moving_time,
            variability_index=d.variability_index,
            hr_zone_times=d.hr_zone_times,
            decoupling=d.decoupling,
        )
    ]
    if ref_dec_valid and detail.decoupling is not None and len(valid_dec) >= _CMP_MIN_POOL:
        markers.append(
            _cmp_marker(
                "decoupling",
                detail.decoupling,
                median(valid_dec),
                len(valid_dec),
                lower_is_better=True,
                status=decoupling_status(detail.decoupling),
            )
        )

    # Efficiency factor — higher is better.
    ef_vals = [d.efficiency_factor for _, d in pool if d.efficiency_factor and d.efficiency_factor > 0]
    if detail.efficiency_factor and detail.efficiency_factor > 0 and len(ef_vals) >= _CMP_MIN_POOL:
        markers.append(
            _cmp_marker("ef", detail.efficiency_factor, median(ef_vals), len(ef_vals), lower_is_better=False)
        )

    # Pace (run) — normalize the unit-ambiguous field to sec/km, then lower is
    # better. NP (bike) — higher is better.
    if sport == "run":
        ref_pace = _pace_to_sec_per_km(detail.pace)
        pace_vals = [p for _, d in pool if (p := _pace_to_sec_per_km(d.pace)) is not None]
        if ref_pace is not None and len(pace_vals) >= _CMP_MIN_POOL:
            markers.append(_cmp_marker("pace", ref_pace, median(pace_vals), len(pace_vals), lower_is_better=True))
    else:
        np_vals = [d.normalized_power for _, d in pool if d.normalized_power]
        if detail.normalized_power and len(np_vals) >= _CMP_MIN_POOL:
            markers.append(
                _cmp_marker("np", detail.normalized_power, median(np_vals), len(np_vals), lower_is_better=False)
            )

    # Average HR — neutral context (lower at same pace is good, but only paired
    # with EF; on its own it's just "where today sat").
    hr_vals = [a.average_hr for a, _ in pool if a.average_hr]
    if activity.average_hr and len(hr_vals) >= _CMP_MIN_POOL:
        markers.append(_cmp_marker("avg_hr", activity.average_hr, median(hr_vals), len(hr_vals), lower_is_better=None))

    # Variability index — neutral (closer to 1.0 = steadier, but not "better").
    vi_vals = [d.variability_index for _, d in pool if d.variability_index]
    if detail.variability_index and len(vi_vals) >= _CMP_MIN_POOL:
        markers.append(_cmp_marker("vi", detail.variability_index, median(vi_vals), len(vi_vals), lower_is_better=None))

    if not markers:
        return {"available": False, "pool_n": len(pool), "reason": "no_markers"}

    return {"available": True, "pool_n": len(pool), "markers": markers, "window_days": _CMP_WINDOW_DAYS}


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
