"""Dashboard API routes — real per-user data for the Mini App Dashboard.

Backs the **Load**, **Goal**, and **Week** tabs. The legacy mocks in
``api/dashboard_routes.py`` (``/api/dashboard`` + job-trigger stubs) are still
in place for the Today tab and the as-yet-unwired job buttons; the path
collisions that existed during the END-12/13 cut-over are gone.
"""

import json
import logging
import zoneinfo
from datetime import date, datetime, timedelta

import sentry_sdk
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from api.deps import get_data_user_id, require_viewer
from config import settings
from data.db import Activity, ActivityDetail, AthleteGoal, User, Wellness, get_session
from data.db.dto import AthleteGoalDTO
from data.marathon_shape import DAYS_FOR_WEEK_KM, RunActivity, calculate_marathon_shape
from data.metrics import PROJECTION_WINDOW_DAYS, project_ctl_target
from data.ml.race_predict import predict_splits_with_ci
from data.redis_client import get_redis
from data.utils import extract_sport_ctl, normalize_sport

logger = logging.getLogger(__name__)

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


def _ttl_until_midnight_local() -> int:
    """Seconds until next midnight in `settings.TIMEZONE`. Used as TTL for
    same-day caches (e.g. marathon-shape predicted_times) so the cache flushes
    exactly when «today» rolls over. Floor at 60s to avoid pathological
    sub-minute TTLs near midnight from a clock skew."""
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    now = datetime.now(tz)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((midnight - now).total_seconds()))


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


def _vo2max_at(
    vo2max_by_date: dict[str, float | None],
    anchor: date,
    *,
    back_days: int = 30,
) -> float | None:
    """Most recent non-null vo2max on/before `anchor` within `back_days`.

    Wellness can have missing or NULL vo2max rows even when the surrounding
    days are populated (Intervals.icu pushes vo2max only on activity days
    with a Garmin estimate). Walk back up to 30 days so the widget renders a
    stable value rather than oscillating between weeks that happen to have
    a fitness test and weeks that don't.
    """
    for delta in range(back_days + 1):
        key = (anchor - timedelta(days=delta)).isoformat()
        v = vo2max_by_date.get(key)
        if v is not None:
            return v
    return None


@router.get("/api/marathon-shape")
async def marathon_shape(
    weeks: int = Query(default=12, ge=1, le=24),
    user: User = Depends(require_viewer),
) -> dict:
    """Weekly Marathon Shape time-series for the Progress page widget.

    For each of the last ``weeks`` Mon-Sun weeks (ending at the most recent
    Sunday ≤ today), computes Runalyze-style basic-endurance shape % using
    ~26 weeks of Run history ending at that week's Sunday and the VO2max
    snapshot on that day (with 30-day backward fallback for sparse rows).

    Distance-specific required shape (HM / Marathon / 70.3) is computed
    CLIENT-side from ``distance_km ** 1.23`` — endpoint returns only the
    absolute shape %.
    """
    today = _today_local()
    # Most recent Sunday on/before today. weekday(): Mon=0..Sun=6.
    days_since_sunday = (today.weekday() + 1) % 7
    window_end = today - timedelta(days=days_since_sunday)
    window_start = window_end - timedelta(weeks=weeks - 1, days=6)
    # Need DAYS_FOR_WEEK_KM (182 days) of Run history before window_start so
    # the oldest week in the window has a full 26-week tail. Wellness needs
    # 30 days more for the back-fallback.
    history_start = window_start - timedelta(days=DAYS_FOR_WEEK_KM)
    wellness_start = window_start - timedelta(days=30)

    uid = get_data_user_id(user)
    async with get_session() as session:
        runs_result = await session.execute(
            # OUTER join: a Run without an `activity_details` row (the pipeline
            # writes the detail seconds after `Activity.save_bulk` —
            # `_actor_update_analityc_tables` in `tasks/actors/activities.py:383`)
            # is silently dropped by the `if dist_m is None: continue` below,
            # not by the JOIN. Outerjoin gives the SAME result for any window
            # outside the live pipeline gap and an immediately fresh view inside it.
            select(Activity.start_date_local, ActivityDetail.distance)
            .outerjoin(ActivityDetail, ActivityDetail.activity_id == Activity.id)
            .where(
                Activity.user_id == uid,
                Activity.type == "Run",
                Activity.is_race.is_(False),
                Activity.start_date_local >= history_start.isoformat(),
                Activity.start_date_local <= window_end.isoformat(),
            )
        )
        run_rows = runs_result.all()

        vo2_result = await session.execute(
            select(Wellness.date, Wellness.vo2max).where(
                Wellness.user_id == uid,
                Wellness.date >= wellness_start.isoformat(),
                Wellness.date <= window_end.isoformat(),
            )
        )
        vo2_rows = vo2_result.all()

    all_runs: list[RunActivity] = []
    for dt_str, dist_m in run_rows:
        if dist_m is None:
            continue
        try:
            dt = date.fromisoformat(dt_str)
        except (TypeError, ValueError):
            continue
        all_runs.append(RunActivity(dt=dt, distance_km=float(dist_m) / 1000.0))

    vo2max_by_date: dict[str, float | None] = {d: float(v) if v is not None else None for d, v in vo2_rows}

    weeks_out: list[dict] = []
    for i in range(weeks):
        wk_start = window_start + timedelta(weeks=i)
        wk_end = wk_start + timedelta(days=6)
        vo2 = _vo2max_at(vo2max_by_date, wk_end)
        if vo2 is None:
            weeks_out.append(
                {
                    "week_start": wk_start.isoformat(),
                    "week_end": wk_end.isoformat(),
                    "shape_pct": None,
                    "vo2max_used": None,
                    "components": None,
                }
            )
            continue
        result = calculate_marathon_shape(all_runs, vo2max=vo2, reference_date=wk_end)
        weeks_out.append(
            {
                "week_start": wk_start.isoformat(),
                "week_end": wk_end.isoformat(),
                "shape_pct": result.shape_pct,
                "vo2max_used": round(result.vo2max_used, 1),
                "components": {
                    "actual_weekly_km": result.actual_weekly_km,
                    "target_weekly_km": result.target_weekly_km,
                    "longjog_score": result.longjog_score,
                    "target_longjog_km": result.target_longjog_km,
                    "actual_longjog_km": result.actual_longjog_km,
                },
            }
        )

    # Newest first — consistent with `/api/weekly-recap` ordering.
    weeks_out.reverse()

    current_components: dict | None = None
    if weeks_out and weeks_out[0]["components"] is not None:
        c = weeks_out[0]["components"]
        current_components = {
            **c,
            "vo2max": weeks_out[0]["vo2max_used"],
        }

    # ── Phase 1.5: ML-based Predicted time + pace per distance.
    today_iso = today.isoformat()
    predicted_times = await _compute_predicted_times(uid, today_iso)

    return {
        "weeks": weeks_out,
        "current_components": current_components,
        "predicted_times": predicted_times,
    }


_MS_PREDICT_CACHE_KEY = "marathon_shape_pred:{user_id}:{today_iso}"
_MS_DISTANCES: tuple[tuple[str, int], ...] = (("10K", 10000), ("HM", 21097), ("Marathon", 42195))


async def _compute_predicted_times(user_id: int, today_iso: str) -> dict[str, dict | None]:
    """Return per-distance ML predictions (10K / HM / Marathon) for user.

    Backed by a Redis cache keyed on ``(user_id, today_iso)`` with TTL to the
    next local midnight — predictions shift slowly within a day (CTL drifts
    ~1 unit/day), so per-day staleness is acceptable for this diagnostic view.
    Cache miss / Redis unavailable / Redis errors fall through to fresh
    computation. Cache write failures are logged but don't break the response.
    """
    cache_key = _MS_PREDICT_CACHE_KEY.format(user_id=user_id, today_iso=today_iso)
    redis_client = get_redis()
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                return json.loads(cached)
        except Exception as e:  # noqa: BLE001 — Redis errors must never break the endpoint
            logger.warning("Marathon-shape predict cache read failed: %s", e)

    predicted_times = await _predict_times_fresh(user_id, today_iso)

    if redis_client is not None:
        try:
            await redis_client.set(
                cache_key,
                json.dumps(predicted_times),
                ex=_ttl_until_midnight_local(),
            )
        except Exception as e:  # noqa: BLE001 — same: cache-write failure must not break the response
            logger.warning("Marathon-shape predict cache write failed: %s", e)

    return predicted_times


async def _predict_times_fresh(user_id: int, today_iso: str) -> dict[str, dict | None]:
    """Cache-free per-distance ML prediction. Sequential await — see notes below.

    Sequential reasoning: `predict_splits_with_ci` is async at the outer layer
    but `_predict_one` (joblib + XGBoost + bootstrap) is sync and blocks the
    loop; `asyncio.gather` wouldn't give parallelism (spec §13 Latency).
    Each call ~80ms × 3 = ~240ms total. `predict_splits_with_ci` catches
    ModelNotTrained / ModelBelowAcceptance internally and surfaces them via
    `not_available` / `below_acceptance` lists — we just check whether the
    "run" split landed in the envelope.

    Minor inefficiency: `_load_model` (data/ml/race_predict.py:97) has no
    cache, so the same `race_run_{user_id}.joblib` is read from disk 3× per
    request. ~50ms each is the dominant cost. Decoupling load from quality-
    gate enforcement would let us cache the bundle, but it's a cross-cutting
    change in shared infra — defer until joblib I/O becomes hot.
    """
    predicted_times: dict[str, dict | None] = {}
    for label, dist_m in _MS_DISTANCES:
        try:
            env = await predict_splits_with_ci(
                user_id=user_id,
                mode="today",
                race_date=today_iso,  # spec §13: today_iso → days_to_race=0 → only intercept bias applied
                race_distance_run_m=dist_m,
            )
        except Exception as e:  # noqa: BLE001 — defensive against joblib I/O / feature build failures
            # `predict_splits_with_ci` already filters expected ML errors into
            # envelope lists, so anything reaching here is unexpected and worth
            # a Sentry alert (corrupt joblib, missing feature column, etc.).
            # `push_scope` keeps the tags/context scoped to this one capture
            # — doesn't leak into Sentry events fired by later requests on the
            # same worker.
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("endpoint", "marathon_shape")
                scope.set_context(
                    "predict",
                    {
                        "user_id": user_id,
                        "label": label,
                        "race_distance_m": dist_m,
                    },
                )
                sentry_sdk.capture_exception(e)
            predicted_times[label] = None
            continue

        # Run leg is always pace-based — `total_sec_unavailable` flag is a
        # Ride-only marker (power_only_phase1, see race_predict.py:449-451).
        # Defensive `.get()` across the full key set: if the envelope is ever
        # partial (e.g. upstream contract change leaves one CI bound missing),
        # we degrade to null instead of raising KeyError into the outer except
        # (which would over-fire Sentry on a *malformed* envelope rather than
        # a *crashed* model — different signal entirely).
        run = env.get("splits", {}).get("run") or {}
        expected = ("total_sec", "total_sec_ci_low", "total_sec_ci_high", "pred", "ci_low", "ci_high")
        values = {k: run.get(k) for k in expected}
        if any(v is None for v in values.values()):
            predicted_times[label] = None
            continue

        predicted_times[label] = {
            "total_sec": values["total_sec"],
            "total_sec_ci_low": values["total_sec_ci_low"],
            "total_sec_ci_high": values["total_sec_ci_high"],
            "pace_sec_per_km": round(values["pred"], 1),
            "pace_ci_low": round(values["ci_low"], 1),
            "pace_ci_high": round(values["ci_high"], 1),
        }
    return predicted_times
