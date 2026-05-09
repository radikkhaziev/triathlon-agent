"""Dashboard API routes — real per-user data for the Mini App Dashboard.

Backs the **Load**, **Goal**, and **Week** tabs. The legacy mocks in
``api/dashboard_routes.py`` (``/api/dashboard`` + job-trigger stubs) are still
in place for the Today tab and the as-yet-unwired job buttons; the path
collisions that existed during the END-12/13 cut-over are gone.
"""

import zoneinfo
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from api.deps import get_data_user_id, require_viewer
from config import settings
from data.db import Activity, ActivityDetail, AthleteGoal, User, Wellness, get_session
from data.db.dto import AthleteGoalDTO
from data.metrics import PROJECTION_WINDOW_DAYS, project_ctl_target
from data.utils import extract_sport_ctl, normalize_sport

router = APIRouter()


# Canonical Intervals.icu type → React-tab sport key. Anything that doesn't
# normalize into Swim/Ride/Run gets dropped (matches the stacked-TSS chart,
# which only has three series). Knock-on for `/api/weekly-recap`: an "Other"
# bucket — yoga, hike, weights, mobility — never lands in `by_sport`, so its
# TSS is also missing from the per-week total the frontend sums client-side.
# Acceptable trade-off for triathletes; revisit when adding a strength tab.
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
    intentionally omitted: GoalTab takes the latest snapshot from
    ``wellness.sport_info``, not a time series — re-add only if a future
    tab needs the 84d trend.
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
            select(Wellness.date, Wellness.ctl, Wellness.atl).where(
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
        d: (float(c) if c is not None else None, float(a) if a is not None else None) for d, c, a in wellness_rows
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


def _goal_progress_dict(
    g: AthleteGoalDTO,
    today: date,
    overall_series: list[tuple[date, float]],
    sport_series: dict[str, list[tuple[date, float]]],
) -> dict:
    """Build a single goal's progress block for the Goal tab.

    Extracted so the list endpoint (`/api/goal`) can map over all active
    goals (#323 Strand C extension to Dashboard) — the formula is identical
    for each row and only the input DTO differs.
    """
    # Clamp to 0 if the athlete forgot to deactivate a past goal — a
    # negative "days_remaining" in the JSON is misleading. Floor-divide
    # before clamping so day-of-event reads "0 weeks" instead of rounding
    # up to "1 week to go" the morning of.
    days_remaining = max(0, (g.event_date - today).days)
    weeks_remaining = max(0, days_remaining // 7)
    current_ctl = overall_series[-1][1] if overall_series else None

    # Explicit zero-guard — `g.ctl_target=0` is a legit value (full-taper anchor)
    # but division by it would crash. ``is not None and > 0`` keeps the intent
    # obvious and protects against any future negative-target bug.
    overall_pct = (
        round(100 * current_ctl / g.ctl_target)
        if (current_ctl is not None and g.ctl_target is not None and g.ctl_target > 0)
        else None
    )

    block: dict = {
        "id": g.id,
        "category": g.category,
        "event_name": g.event_name,
        "event_date": str(g.event_date),
        "sport_type": g.sport_type,
        "weeks_remaining": weeks_remaining,
        "days_remaining": days_remaining,
        "ctl_current": round(current_ctl, 1) if current_ctl is not None else None,
        "ctl_target": g.ctl_target,
        "overall_pct": overall_pct,
        "projection": project_ctl_target(overall_series, g.ctl_target, today, g.event_date),
    }

    # Per-sport bars: only emit sports that actually have a target. A sport
    # with target=0 or missing is treated as "not part of this race plan"
    # and dropped, not rendered as 0% (which would look like a regression).
    targets: dict = g.per_sport_targets or {}
    per_sport: dict[str, dict] = {}
    for sport in ("swim", "ride", "run"):
        target = targets.get(sport)
        if target is None or target <= 0:
            continue
        s_series = sport_series.get(sport, [])
        # ``cur`` is already 1-dp from extract_sport_ctl; the round() here is
        # idempotent — kept explicit so a future change to that helper doesn't
        # silently leak full-precision floats into the API response.
        cur = s_series[-1][1] if s_series else None
        per_sport[sport] = {
            "ctl_current": round(cur, 1) if cur is not None else None,
            "ctl_target": target,
            "pct": round(100 * cur / target) if cur is not None else None,
            "projection": project_ctl_target(s_series, target, today, g.event_date),
        }
    if per_sport:
        block["per_sport"] = per_sport

    return block


@router.get("/api/goal")
async def goal(user: User = Depends(require_viewer)) -> dict:
    """Race-goal progress list for the Goal tab.

    Returns ``{"has_goals": false, "goals": []}`` when the athlete has no
    active future race — the React Dashboard hides the Goal tab in that
    case (per the [END-12] scoping decision; we don't push users into a
    CTA they didn't ask for, and the tab list is shorter and clearer
    with it gone).

    When goals exist, returns ``{"has_goals": true, "goals": [...]}`` with
    one block per active future goal (sort: ``event_date ASC`` so the
    nearest race is first). Each block always includes the overall CTL
    bar (``ctl_current`` / ``ctl_target`` / ``overall_pct``); per-sport
    bars are only included when ``per_sport_targets`` is set on the goal
    — auto-splitting an overall target by canonical 70.3 ratios was
    rejected because the per-sport mix varies too much between athletes
    to fake.

    Shape changed from single-goal to list in #323 Strand C — Dashboard's
    Goal tab now mirrors Settings' all-goals view.
    """
    uid = get_data_user_id(user)
    today = _today_local()
    goals = await AthleteGoal.get_goals_for_settings(uid, today)
    if not goals:
        return {"has_goals": False, "goals": []}

    # Pull a 14-day window so _goal_progress_dict can compute a ramp-rate
    # projection. Asc order is what project_ctl_target expects (and the dict
    # builder also uses [-1] for "current" — keeps a single ordering).
    # Wellness.date is a String column ("YYYY-MM-DD"); ISO lex-sort matches
    # date order so .isoformat() compare and date.fromisoformat() readback
    # are intentional, not a Date-column accident.
    window_start = today - timedelta(days=PROJECTION_WINDOW_DAYS - 1)
    async with get_session() as session:
        result = await session.execute(
            select(Wellness.date, Wellness.ctl, Wellness.sport_info)
            .where(
                Wellness.user_id == uid,
                Wellness.ctl.isnot(None),
                Wellness.date >= window_start.isoformat(),
            )
            .order_by(Wellness.date.asc())
        )
        rows = result.all()

    overall_series: list[tuple[date, float]] = [(date.fromisoformat(d), float(ctl)) for d, ctl, _ in rows]
    sport_series: dict[str, list[tuple[date, float]]] = {"swim": [], "ride": [], "run": []}
    for d, _ctl, sport_info in rows:
        per = extract_sport_ctl(sport_info)
        dt = date.fromisoformat(d)
        for sport in ("swim", "ride", "run"):
            v = per.get(sport)
            if v is not None:
                sport_series[sport].append((dt, float(v)))

    return {
        "has_goals": True,
        "goals": [_goal_progress_dict(g, today, overall_series, sport_series) for g in goals],
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
