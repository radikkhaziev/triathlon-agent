"""Dashboard API routes — real per-user data for the Mini App Dashboard.

These handlers replace the seeded mocks in ``api/dashboard_routes.py`` for the
**Load** and **Goal** tabs. They are mounted *before* the mock router in
``api/server.py`` so FastAPI's first-match-wins routing picks the real
handlers; the mock module keeps serving the Week tab until [END-13] cuts
over.
"""

import zoneinfo
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from api.deps import get_data_user_id, require_viewer
from config import settings
from data.db import Activity, AthleteGoal, User, Wellness, get_session
from data.utils import extract_sport_ctl, normalize_sport

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


@router.get("/api/goal")
async def goal(user: User = Depends(require_viewer)) -> dict:
    """Race goal progress for the Goal tab.

    Returns ``{"has_goal": false}`` when the athlete has no active race —
    the React Dashboard hides the Goal tab entirely in that case (per the
    [END-12] scoping decision; we don't want to push users into a CTA they
    didn't ask for, and the tab list is shorter and clearer with it gone).

    When a goal exists, the response always includes the overall CTL bar
    (``ctl_current`` / ``ctl_target`` / ``overall_pct``). Per-sport bars
    are only included when ``per_sport_targets`` is set on the goal —
    auto-splitting an overall target by canonical 70.3 ratios was rejected
    because the per-sport mix varies too much between athletes to fake.
    """
    uid = get_data_user_id(user)
    g = await AthleteGoal.get_goal_dto(uid)
    if not g:
        return {"has_goal": False}

    today = _today_local()
    days_remaining = (g.event_date - today).days
    # Round toward zero so the day-of-event reads "0 weeks" rather than
    # rounding up to "1 week to go" the morning of.
    weeks_remaining = max(0, days_remaining // 7)

    async with get_session() as session:
        result = await session.execute(
            select(Wellness)
            .where(Wellness.user_id == uid, Wellness.ctl.isnot(None))
            .order_by(Wellness.date.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()

    current_ctl = float(row.ctl) if row and row.ctl is not None else None
    sport_ctl = extract_sport_ctl(row.sport_info) if row else {"swim": None, "ride": None, "run": None}
    targets: dict = g.per_sport_targets or {}

    overall_pct = (
        round(current_ctl / g.ctl_target * 100)
        if (current_ctl is not None and g.ctl_target)
        else None
    )

    response: dict = {
        "has_goal": True,
        "event_name": g.event_name,
        "event_date": str(g.event_date),
        "weeks_remaining": weeks_remaining,
        "days_remaining": days_remaining,
        "ctl_current": round(current_ctl, 1) if current_ctl is not None else None,
        "ctl_target": g.ctl_target,
        "overall_pct": overall_pct,
    }

    # Per-sport bars: only emit sports that actually have a target. A sport
    # with target=0 or missing is treated as "not part of this race plan"
    # and dropped, not rendered as 0% (which would look like a regression).
    per_sport: dict[str, dict] = {}
    for sport in ("swim", "ride", "run"):
        target = targets.get(sport)
        if not target or target <= 0:
            continue
        cur = sport_ctl.get(sport)
        per_sport[sport] = {
            "ctl_current": round(cur, 1) if cur is not None else None,
            "ctl_target": target,
            "pct": round(cur / target * 100) if cur is not None else None,
        }
    if per_sport:
        response["per_sport"] = per_sport

    return response


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
