"""Dashboard API routes — real per-user data for the Mini App Dashboard.

These handlers replace the seeded mocks in ``api/dashboard_routes.py`` for the
**Load** tab. They are mounted *before* the mock router in ``api/server.py`` so
FastAPI's first-match-wins routing picks the real handlers; the mock module
keeps serving Goal/Week tabs until [END-12] / [END-13] cut over.
"""

import zoneinfo
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from api.deps import get_data_user_id, require_viewer
from config import settings
from data.db import Activity, ActivityDetail, User, Wellness, get_session
from data.utils import normalize_sport

router = APIRouter()


# Canonical Intervals.icu type → React-tab sport key. Anything that doesn't
# normalize into Swim/Ride/Run gets dropped (matches the stacked-TSS chart,
# which only has three series).
_SPORT_MAP = {
    "Swim": "swimming",
    "Ride": "cycling",
    "Run": "running",
}


def _today_local() -> date:
    return datetime.now(zoneinfo.ZoneInfo(settings.TIMEZONE)).date()


def _nearest_wellness(
    wellness_by_date: dict[str, tuple[float | None, float | None]],
    anchor: date,
    *,
    back_days: int,
) -> tuple[float | None, float | None]:
    """Return the most recent (ctl, atl) on/before anchor within back_days."""
    for delta in range(back_days + 1):
        key = (anchor - timedelta(days=delta)).isoformat()
        row = wellness_by_date.get(key)
        if row is not None and row[0] is not None and row[1] is not None:
            return row
    return None, None


@router.get("/api/training-load")
async def training_load(
    days: int = Query(default=84, ge=1, le=365),
    user: User = Depends(require_viewer),
) -> dict:
    """CTL/ATL/TSB time series from the user's wellness rows.

    Per-sport CTL keys (``ctl_swim`` / ``ctl_ride`` / ``ctl_run``) are
    intentionally omitted here — GoalTab consumes them and is wired up in
    [END-12].
    """
    today = _today_local()
    start = today - timedelta(days=days - 1)

    uid = get_data_user_id(user)
    async with get_session() as session:
        result = await session.execute(
            select(Wellness.date, Wellness.ctl, Wellness.atl)
            .where(
                Wellness.user_id == uid,
                Wellness.date >= start.isoformat(),
                Wellness.date <= today.isoformat(),
                Wellness.ctl.isnot(None),
                Wellness.atl.isnot(None),
            )
            .order_by(Wellness.date.asc())
        )
        rows = result.all()

    dates: list[str] = []
    ctl: list[float] = []
    atl: list[float] = []
    tsb: list[float] = []
    for d, c, a in rows:
        dates.append(d)
        ctl.append(round(float(c), 1))
        atl.append(round(float(a), 1))
        tsb.append(round(float(c) - float(a), 1))

    return {"dates": dates, "ctl": ctl, "atl": atl, "tsb": tsb}


@router.get("/api/activities")
async def activities(
    days: int = Query(default=28, ge=1, le=180),
    user: User = Depends(require_viewer),
) -> dict:
    """Per-activity TSS bars for the stacked-by-sport chart.

    Drops activities with NULL TSS or with sports that don't bucket into
    swim/ride/run (yoga, hike, weights, etc.) — they don't show on the chart
    and would only add a hidden "other" bucket the frontend ignores.
    Races are kept; race TSS is real load.
    """
    today = _today_local()
    start = today - timedelta(days=days - 1)

    uid = get_data_user_id(user)
    async with get_session() as session:
        result = await session.execute(
            select(Activity.start_date_local, Activity.type, Activity.icu_training_load)
            .where(
                Activity.user_id == uid,
                Activity.start_date_local >= start.isoformat(),
                Activity.start_date_local <= today.isoformat(),
                Activity.icu_training_load.isnot(None),
            )
            .order_by(Activity.start_date_local.asc(), Activity.id.asc())
        )
        rows = result.all()

    out: list[dict] = []
    for dt, raw_type, tss in rows:
        sport = _SPORT_MAP.get(normalize_sport(raw_type) or "")
        if not sport:
            continue
        out.append({"date": dt, "sport": sport, "tss": round(float(tss), 1)})

    return {"activities": out}


@router.get("/api/weekly-recap")
async def weekly_recap(
    weeks: int = Query(default=4, ge=1, le=12),
    offset: int = Query(default=0, ge=-52, le=0),
    user: User = Depends(require_viewer),
) -> dict:
    """Weekly training recap — N weeks of completed activity, freshest first.

    The window's most-recent week is ``today + offset*7``'s Mon–Sun (offset is
    non-positive: 0 = current week, -1 = week ending last Sunday, …). The
    response carries ``weeks`` buckets ending at that week, plus a Wellness
    snapshot at each week's bookends (CTL on the day before the week starts vs
    CTL on the week's last day) so the frontend can render a compact load
    card without a second round-trip. ``has_prev`` lets the UI hide the back
    button when the user has scrolled to before their first activity.
    """
    today = _today_local()
    anchor_monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    window_start = anchor_monday - timedelta(weeks=weeks - 1)
    window_end = anchor_monday + timedelta(days=6)
    # _nearest_wellness walks back up to 6 days from each bookend (covers
    # bootstrap-day gaps), so the cache must reach 7 days before window_start
    # for the oldest week's "entering CTL" to fall back correctly.
    wellness_start = window_start - timedelta(days=7)

    uid = get_data_user_id(user)
    async with get_session() as session:
        result = await session.execute(
            select(
                Activity.start_date_local,
                Activity.type,
                Activity.moving_time,
                Activity.icu_training_load,
                ActivityDetail.distance,
            )
            .outerjoin(ActivityDetail, ActivityDetail.activity_id == Activity.id)
            .where(
                Activity.user_id == uid,
                Activity.start_date_local >= window_start.isoformat(),
                Activity.start_date_local <= window_end.isoformat(),
            )
        )
        rows = result.all()

        wellness_result = await session.execute(
            select(Wellness.date, Wellness.ctl, Wellness.atl)
            .where(
                Wellness.user_id == uid,
                Wellness.date >= wellness_start.isoformat(),
                Wellness.date <= window_end.isoformat(),
            )
        )
        wellness_rows = wellness_result.all()

        prev_result = await session.execute(
            select(Activity.id)
            .where(
                Activity.user_id == uid,
                Activity.start_date_local < window_start.isoformat(),
            )
            .limit(1)
        )
        has_prev = prev_result.first() is not None

    # date string → (ctl, atl). Wellness rows can have NULL ctl/atl on bootstrap
    # gaps; we keep them so the frontend can still render the row, just with
    # "—" for the load card.
    wellness_by_date: dict[str, tuple[float | None, float | None]] = {
        d: (float(c) if c is not None else None, float(a) if a is not None else None)
        for d, c, a in wellness_rows
    }

    # Pre-bucket activities by week index (0 = oldest, weeks-1 = newest).
    buckets: list[dict[str, dict[str, float]]] = [{} for _ in range(weeks)]
    for dt_str, raw_type, mt, tss, dist in rows:
        sport = _SPORT_MAP.get(normalize_sport(raw_type) or "")
        if not sport:
            continue
        try:
            dt = date.fromisoformat(dt_str)
        except (TypeError, ValueError):
            continue
        idx = (dt - window_start).days // 7
        if idx < 0 or idx >= weeks:
            continue
        bucket = buckets[idx].setdefault(sport, {"duration_sec": 0.0, "distance_m": 0.0, "tss": 0.0})
        if mt is not None:
            bucket["duration_sec"] += float(mt)
        if dist is not None:
            bucket["distance_m"] += float(dist)
        if tss is not None:
            bucket["tss"] += float(tss)

    weeks_out: list[dict] = []
    for i in range(weeks):
        wk_start = window_start + timedelta(weeks=i)
        wk_end = wk_start + timedelta(days=6)
        # CTL/ATL "entering" the week = day before week_start (Sunday of prior week).
        # "Exiting" = day == week_end (Sunday). Walk back from each anchor up to
        # 6 days to absorb missing wellness rows on the exact bookends — Intervals
        # backfills wellness daily, but bootstrap can leave one-day gaps.
        ctl_start, _ = _nearest_wellness(wellness_by_date, wk_start - timedelta(days=1), back_days=6)
        ctl_end, atl_end = _nearest_wellness(wellness_by_date, wk_end, back_days=6)

        by_sport = {}
        for sport, totals in buckets[i].items():
            by_sport[sport] = {
                "duration_sec": int(totals["duration_sec"]),
                "distance_m": round(totals["distance_m"], 1),
                "tss": round(totals["tss"], 1),
            }

        ctl_delta = round(ctl_end - ctl_start, 1) if ctl_start is not None and ctl_end is not None else None
        tsb_end = round(ctl_end - atl_end, 1) if ctl_end is not None and atl_end is not None else None

        weeks_out.append(
            {
                "week_start": wk_start.isoformat(),
                "week_end": wk_end.isoformat(),
                "by_sport": by_sport,
                "ctl_start": round(ctl_start, 1) if ctl_start is not None else None,
                "ctl_end": round(ctl_end, 1) if ctl_end is not None else None,
                "ctl_delta": ctl_delta,
                "tsb_end": tsb_end,
            }
        )

    # Newest first — matches the wake-comment ask ("видеть последние 4 недели").
    weeks_out.reverse()

    return {
        "weeks": weeks_out,
        "offset": offset,
        "today": today.isoformat(),
        "has_prev": has_prev,
    }


@router.get("/api/recovery-trend")
async def recovery_trend(
    days: int = Query(default=21, ge=1, le=90),
    user: User = Depends(require_viewer),
) -> dict:
    """Recovery score + RMSSD trend, used by the Load tab's small chart."""
    today = _today_local()
    start = today - timedelta(days=days - 1)

    uid = get_data_user_id(user)
    async with get_session() as session:
        result = await session.execute(
            select(Wellness.date, Wellness.recovery_score, Wellness.hrv)
            .where(
                Wellness.user_id == uid,
                Wellness.date >= start.isoformat(),
                Wellness.date <= today.isoformat(),
            )
            .order_by(Wellness.date.asc())
        )
        rows = result.all()

    dates: list[str] = []
    recovery: list[float | None] = []
    hrv: list[float | None] = []
    for d, rec, h in rows:
        # Skip days with neither recovery nor HRV — they'd render as gaps anyway
        # and the contract is "omit dates without a wellness row." A wellness
        # row with both fields NULL is functionally the same as no row.
        if rec is None and h is None:
            continue
        dates.append(d)
        recovery.append(round(float(rec), 1) if rec is not None else None)
        hrv.append(round(float(h), 1) if h is not None else None)

    return {"dates": dates, "recovery": recovery, "hrv": hrv}
