"""Dashboard API routes — real per-user data for the Mini App Dashboard.

Backs the **Load**, **Goal**, and **Week** tabs, plus the Wellness page's
manual refresh job (``POST /api/jobs/refresh-wellness``). The legacy mock
router (``api/dashboard_routes.py``) was deleted in the Halo cleanup pass —
all dashboard endpoints now live here.
"""

import asyncio
import json
import logging
import time
import zoneinfo
from datetime import date, datetime, timedelta

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from api.deps import get_data_user_id, require_athlete, require_viewer
from config import settings
from data.db import (
    Activity,
    ActivityDetail,
    AthleteGoal,
    EnduranceScore,
    Race,
    ScheduledWorkout,
    User,
    UserDTO,
    Wellness,
    get_session,
)
from data.db.dto import AthleteGoalDTO
from data.endurance_score import classify_zone
from data.endurance_score_service import compute_for as compute_endurance_for
from data.marathon_shape import DAYS_FOR_WEEK_KM, MIN_KM_FOR_LONGJOG, RunActivity, calculate_marathon_shape
from data.metrics import PROJECTION_WINDOW_DAYS, project_ctl_target, project_sport_load_forward, recompute_today_loads
from data.ml.race_predict import predict_splits_with_ci
from data.redis_client import get_redis
from data.taper_service import get_taper_plan_for_user
from data.training_strain import acwr, acwr_status, compute_training_strain
from data.utils import extract_sport_atl, extract_sport_ctl, normalize_sport
from mcp_server.tools.progress import compute_efficiency_trend
from tasks.actors import actor_user_wellness
from tasks.dto import local_today

logger = logging.getLogger(__name__)

router = APIRouter()

# Per-user cooldown between manual wellness-refresh calls (the Wellness page
# "Refresh" button). Guards against spam → wasted Intervals.icu API calls plus
# downstream fan-out. In-process dict — same multi-worker caveat as
# ``_retry_backfill_last_success`` in auth.py (migrate to Redis INCR+EXPIRE
# when scaling to multi-worker uvicorn).
_REFRESH_COOLDOWN_SEC = 60
_refresh_last: dict[int, float] = {}


# Canonical Intervals.icu type → React-tab sport key. Anything that doesn't
# normalize into Swim/Ride/Run gets dropped (matches the stacked-TSS chart,
# which only has three series): an "Other" bucket — yoga, hike, weights,
# mobility — never lands in `by_sport`. Acceptable trade-off for triathletes;
# revisit when adding a strength tab. `weekly_reports.py:_SPORT_BUCKET` mirrors
# this map for the Recap tab's per-week volume.
_SPORT_MAP = {
    "Swim": "swimming",
    "Ride": "cycling",
    "Run": "running",
}


# When the user has no future workouts scheduled, the per-sport forecast still
# draws forward as zero-load decay over this window. 28 days covers a full ATL
# collapse (4×τ_ATL) and ~half a CTL half-life — visible answer to "what if
# I stop training" without bloating the chart axis.
# CONTRACT: must stay >= data/metrics.py:_TAPER_EARLY_HORIZON_DAYS (21) — the
# LoadDetail taper overlay renders only when the race is within that horizon,
# and race day must land on the chart axis even with zero planned workouts.
_FORECAST_FALLBACK_DAYS = 28


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


@router.get("/api/training-load")
async def training_load(
    days: int = Query(default=84, ge=1, le=365),
    user: User = Depends(require_viewer),
) -> dict:
    """CTL/ATL/TSB time series + per-sport CTL/ATL, with plan-aware forward
    forecast for the per-sport arrays.

    Past arrays come from ``wellness.sport_info`` (filled by the enrich actor).
    Future segment is computed on read by extending each sport's EMA forward
    using planned TSS from ``scheduled_workouts`` — natural decay on days
    without a workout. Horizon is the globally latest future workout date;
    sports with no plan beyond that just decay to it. If there are no future
    workouts at all, the response stays past-only and ``today_date`` equals
    the last past date.

    ``today_date`` (ISO) lets the frontend locate the actual/forecast split
    without depending on array order. See ``docs/PER_SPORT_LOAD_SPEC.md``
    Step 3.5 — why compute-on-read instead of persist.
    """
    today = local_today()
    start = today - timedelta(days=days - 1)
    today_iso = today.isoformat()

    uid = get_data_user_id(user)
    async with get_session() as session:
        result = await session.execute(
            select(Wellness.date, Wellness.ctl, Wellness.atl, Wellness.sport_info)
            .where(
                Wellness.user_id == uid,
                Wellness.date >= start.isoformat(),
                Wellness.date <= today_iso,
                Wellness.ctl.isnot(None),
                Wellness.atl.isnot(None),
            )
            .order_by(Wellness.date.asc())
        )
        rows = result.all()

        # Horizon ignores sport — any planned workout extends the chart axis.
        # If the athlete plans a strength session 10 days after their last Run,
        # the overall TSB forecast must reach it (strength still drives fatigue).
        horizon_row = await session.execute(
            select(func.max(ScheduledWorkout.start_date_local)).where(
                ScheduledWorkout.user_id == uid,
                ScheduledWorkout.start_date_local > today_iso,
                ScheduledWorkout.icu_training_load.isnot(None),
            )
        )
        planned_horizon_iso: str | None = horizon_row.scalar()

        # Forecast horizon: either the last scheduled workout (plan-aware) or a
        # 28-day window (zero-load decay fallback — "what happens if I stop").
        # 28d ≈ one mesocycle; covers full ATL collapse (4×τ_ATL) and ~half a
        # CTL half-life so the decay is visible without being a wall of noise.
        fallback_horizon = today + timedelta(days=_FORECAST_FALLBACK_DAYS)
        horizon_iso = planned_horizon_iso or fallback_horizon.isoformat()

        # Per-sport planned TSS — gated to Swim/Ride/Run only because per-sport
        # CTL/ATL is only defined for those three (`extract_sport_*`).
        planned_by_sport: dict[str, dict[date, float]] = {"swim": {}, "ride": {}, "run": {}}
        # Overall planned TSS — INCLUDES every sport (WeightTraining, Hike,
        # Yoga, …) with non-null TSS. Past CTL/ATL from Intervals.icu sums all
        # sports; the forecast must mirror that scope or it'll underestimate
        # fatigue for athletes with a regular strength block (code-review W2,
        # 2026-05-24).
        planned_overall: dict[date, float] = {}
        if planned_horizon_iso is not None:
            planned_rows = await session.execute(
                select(
                    ScheduledWorkout.start_date_local,
                    ScheduledWorkout.type,
                    func.sum(ScheduledWorkout.icu_training_load),
                )
                .where(
                    ScheduledWorkout.user_id == uid,
                    ScheduledWorkout.start_date_local > today_iso,
                    ScheduledWorkout.start_date_local <= planned_horizon_iso,
                    ScheduledWorkout.icu_training_load.isnot(None),
                )
                .group_by(ScheduledWorkout.start_date_local, ScheduledWorkout.type)
            )
            for date_str, sport_type, tss_sum in planned_rows.all():
                dt_key = date.fromisoformat(date_str)
                tss = float(tss_sum)
                planned_overall[dt_key] = planned_overall.get(dt_key, 0.0) + tss
                sport_key = (sport_type or "").lower()
                if sport_key in planned_by_sport:
                    planned_by_sport[sport_key][dt_key] = tss

    dates: list[str] = []
    # Past values are concrete floats; the forecast extension below pads with
    # ``None`` for the overall (non-per-sport) series, so the annotations have
    # to allow nulls. Without this `extend([None] * N)` needs a type-ignore.
    ctl: list[float | None] = []
    atl: list[float | None] = []
    tsb: list[float | None] = []
    ctl_swim: list[float | None] = []
    ctl_ride: list[float | None] = []
    ctl_run: list[float | None] = []
    atl_swim: list[float | None] = []
    atl_ride: list[float | None] = []
    atl_run: list[float | None] = []
    for d, c, a, sport_info in rows:
        dates.append(d)
        ctl.append(round(float(c), 1))
        atl.append(round(float(a), 1))
        tsb.append(round(float(c) - float(a), 1))
        per_ctl = extract_sport_ctl(sport_info)
        per_atl = extract_sport_atl(sport_info)
        ctl_swim.append(per_ctl["swim"])
        ctl_ride.append(per_ctl["ride"])
        ctl_run.append(per_ctl["run"])
        atl_swim.append(per_atl["swim"])
        atl_ride.append(per_atl["ride"])
        atl_run.append(per_atl["run"])

    # Intervals.icu bakes today's planned workouts into ctl/atl, so the
    # in-progress day's marker (and the forecast anchor) look as if the
    # session is already done. Override with yesterday-decayed + actually
    # completed TSS. Per-sport arrays are intentionally left untouched —
    # ``recompute_today_loads`` only returns overall ctl/atl/tsb, the
    # sport_info recompute is deferred (would need per-sport TSS attribution
    # of today's activities). Frontend renders the visible split.
    if dates and dates[-1] == today_iso:
        recomputed = await recompute_today_loads(uid)
        if recomputed is not None:
            ctl[-1], atl[-1], tsb[-1] = recomputed

    # ``min(today_iso, dates[-1])`` clamps any future-dated wellness row (edge
    # case, shouldn't happen — but if it does, anchoring forecast on a future
    # date would produce a negative-length extension).
    today_date_iso = min(today_iso, dates[-1]) if dates else today_iso

    # Forecast — always computed, even without scheduled workouts (decay-only
    # fallback shows "what if I stop training"). Skipped only when the anchor
    # is already at/past the horizon (empty extension).
    per_sport_series: dict[str, tuple[list[float | None], list[float | None]]] = {
        "swim": (ctl_swim, atl_swim),
        "ride": (ctl_ride, atl_ride),
        "run": (ctl_run, atl_run),
    }
    horizon_dt = date.fromisoformat(horizon_iso)
    anchor_dt = date.fromisoformat(today_date_iso)
    if horizon_dt > anchor_dt:
        # Extend dates axis to horizon (one entry per calendar day).
        cur = anchor_dt + timedelta(days=1)
        while cur <= horizon_dt:
            dates.append(cur.isoformat())
            cur += timedelta(days=1)
        future_len = (horizon_dt - anchor_dt).days

        # Overall CTL/ATL/TSB forecast — same EMA math as per-sport but driven
        # by total daily TSS across ALL sports (`planned_overall`). The TSB
        # chart needs this so the Form line continues into the forecast
        # region; without it the line just disappears after today.
        today_overall_ctl = ctl[-1] if ctl else None
        today_overall_atl = atl[-1] if atl else None
        if today_overall_ctl is not None and today_overall_atl is not None:
            ctl_proj_overall, atl_proj_overall = project_sport_load_forward(
                today_ctl=today_overall_ctl,
                today_atl=today_overall_atl,
                daily_planned_load=planned_overall,
                horizon=horizon_dt,
                today=anchor_dt,
            )
            for (_, cv), (_, av) in zip(ctl_proj_overall, atl_proj_overall):
                ctl.append(cv)
                atl.append(av)
                tsb.append(round(cv - av, 1))
        else:
            # No past wellness rows to anchor from → null-pad to keep arrays
            # length-aligned with dates. Frontend's chart skips null entries.
            ctl.extend([None] * future_len)
            atl.extend([None] * future_len)
            tsb.extend([None] * future_len)

        # Known limitation: per-sport forecast uses baked-in `today_ctl` /
        # `today_atl` (from `sport_info`), while overall top-line uses the
        # recompute'd value (above). A visible split at today→tomorrow is the
        # accepted trade-off until today's activities are attributed per sport
        # for a parallel per-sport recompute — adding that means redoing the
        # work `Wellness.update_sport_load` does, but without the planned-bake
        # contamination. Defer until the asymmetry shows up as a real bug.
        for sport, (ctl_arr, atl_arr) in per_sport_series.items():
            today_ctl = _last_non_null(ctl_arr)
            today_atl = _last_non_null(atl_arr)
            if today_ctl is None or today_atl is None:
                ctl_arr.extend([None] * future_len)
                atl_arr.extend([None] * future_len)
                continue
            ctl_proj, atl_proj = project_sport_load_forward(
                today_ctl=today_ctl,
                today_atl=today_atl,
                daily_planned_load=planned_by_sport[sport],
                horizon=horizon_dt,
                today=anchor_dt,
            )
            ctl_arr.extend(v for _, v in ctl_proj)
            atl_arr.extend(v for _, v in atl_proj)

    return {
        "dates": dates,
        "today_date": today_date_iso,
        "ctl": ctl,
        "atl": atl,
        "tsb": tsb,
        "ctl_swim": ctl_swim,
        "ctl_ride": ctl_ride,
        "ctl_run": ctl_run,
        "atl_swim": atl_swim,
        "atl_ride": atl_ride,
        "atl_run": atl_run,
    }


def _last_non_null(values: list[float | None]) -> float | None:
    for v in reversed(values):
        if v is not None:
            return v
    return None


@router.get("/api/taper-plan")
async def taper_plan(user: User = Depends(require_viewer)) -> dict:
    """Deterministic taper plan for the LoadDetail taper overlay.

    Same envelope as the `get_taper_plan` MCP tool (shared
    `data/taper_service.py` resolution — chat and webapp can never disagree).
    `{available: False, reason}` when there's no future race / no data; the
    frontend renders nothing in that case. `confidence="early"` ships empty
    `daily_targets` — the overlay stays hidden until the race is ≤3 weeks out.
    """
    return await get_taper_plan_for_user(get_data_user_id(user))


@router.get("/api/activities")
async def activities(
    days: int = Query(default=28, ge=1, le=365),
    user: User = Depends(require_viewer),
) -> dict:
    """Per-activity TSS bars for the stacked-by-sport chart, plus planned
    future workouts for the forecast region.

    Past activities (`activities`) come from completed Activity rows; planned
    future workouts (`planned`) come from ScheduledWorkout rows strictly past
    today. Both arrays use the same `{date, sport, tss}` shape so the
    frontend can render forecast bars distinctly without a different schema.

    Drops entries with NULL TSS or with sports that don't bucket into
    swim/ride/run (yoga, hike, weights, etc.) — they don't show on the chart
    and would only add a hidden "other" bucket the frontend ignores.
    Races are kept; race TSS is real load.
    """
    today = local_today()
    start = today - timedelta(days=days - 1)
    today_iso = today.isoformat()

    uid = get_data_user_id(user)
    async with get_session() as session:
        result = await session.execute(
            select(Activity.start_date_local, Activity.type, Activity.icu_training_load)
            .where(
                Activity.user_id == uid,
                Activity.start_date_local >= start.isoformat(),
                Activity.start_date_local <= today_iso,
                Activity.icu_training_load.isnot(None),
            )
            .order_by(Activity.start_date_local.asc(), Activity.id.asc())
        )
        past_rows = result.all()

        planned_result = await session.execute(
            select(
                ScheduledWorkout.start_date_local,
                ScheduledWorkout.type,
                ScheduledWorkout.icu_training_load,
            )
            .where(
                ScheduledWorkout.user_id == uid,
                ScheduledWorkout.start_date_local > today_iso,
                ScheduledWorkout.type.in_(("Swim", "Ride", "Run")),
                ScheduledWorkout.icu_training_load.isnot(None),
            )
            .order_by(ScheduledWorkout.start_date_local.asc(), ScheduledWorkout.id.asc())
        )
        planned_rows = planned_result.all()

    activities_out: list[dict] = []
    for dt, raw_type, tss in past_rows:
        sport = _SPORT_MAP.get(normalize_sport(raw_type) or "")
        if not sport:
            continue
        activities_out.append({"date": dt, "sport": sport, "tss": round(float(tss), 1)})

    planned_out: list[dict] = []
    for dt, raw_type, tss in planned_rows:
        sport = _SPORT_MAP.get(normalize_sport(raw_type) or "")
        if not sport:
            continue
        planned_out.append({"date": dt, "sport": sport, "tss": round(float(tss), 1)})

    return {"activities": activities_out, "planned": planned_out}


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
        "event_name": g.event_name,
        "event_date": str(g.event_date),
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

    # Intervals.icu bakes today's planned workouts into ctl, so the gauge's
    # "current" CTL (and the ramp-rate projection) would read as if today's
    # session is already done. De-plan the latest point to yesterday-decayed +
    # actually-completed TSS — same override the Load tab applies. Per-sport
    # series left untouched (recompute_today_loads only returns overall ctl).
    if overall_series and overall_series[-1][0] == today:
        recomputed = await recompute_today_loads(uid)
        if recomputed is not None:
            overall_series[-1] = (overall_series[-1][0], recomputed[0])

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
    days: int = Query(default=21, ge=1, le=365),
    user: User = Depends(require_viewer),
) -> dict:
    """Recovery score + RMSSD + RHR trend.

    Used by the Dashboard Load tab's small chart (``days=21``) and the
    Wellness "Recovery trend" detail screen (``days`` up to 180 — the 6m
    range pill).
    """
    today = _today_local()
    start = today - timedelta(days=days - 1)

    uid = get_data_user_id(user)
    async with get_session() as session:
        result = await session.execute(
            select(Wellness.date, Wellness.recovery_score, Wellness.hrv, Wellness.resting_hr)
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
    rhr: list[int | None] = []
    for d, rec, h, r in rows:
        # Intervals.icu reports restingHR = 0 on days it never captured a
        # reading — a sentinel, not a measurement. Normalise it to None so the
        # chart doesn't plot a phantom 0-bpm point (same convention as
        # Wellness.recent_resting_hr, which filters resting_hr > 0).
        rhr_val = r or None
        # Skip days with no metric at all — they'd render as gaps anyway and
        # the contract is "omit dates without a wellness row." A wellness row
        # with every charted field NULL is functionally the same as no row.
        if rec is None and h is None and rhr_val is None:
            continue
        dates.append(d)
        recovery.append(round(float(rec), 1) if rec is not None else None)
        hrv.append(round(float(h), 1) if h is not None else None)
        rhr.append(rhr_val)

    return {"dates": dates, "recovery": recovery, "hrv": hrv, "rhr": rhr}


@router.get("/api/sleep-trend")
async def sleep_trend(
    days: int = Query(default=90, ge=1, le=365),
    user: User = Depends(require_viewer),
) -> dict:
    """Sleep duration + score trend for the Wellness "Sleep trend" detail screen.

    ``duration_min`` is whole minutes (the screen's bar chart works in minutes
    against an 8h = 480-min goal line). ``days`` covers the 1m/3m/6m range
    pills (30/90/180).
    """
    today = _today_local()
    start = today - timedelta(days=days - 1)

    uid = get_data_user_id(user)
    async with get_session() as session:
        result = await session.execute(
            select(Wellness.date, Wellness.sleep_secs, Wellness.sleep_score)
            .where(
                Wellness.user_id == uid,
                Wellness.date >= start.isoformat(),
                Wellness.date <= today.isoformat(),
            )
            .order_by(Wellness.date.asc())
        )
        rows = result.all()

    dates: list[str] = []
    duration_min: list[int | None] = []
    score: list[float | None] = []
    for d, secs, sc in rows:
        # Intervals.icu writes sleep_secs = 0 for a night it never captured —
        # a no-data sentinel, not a real measurement (cf. resting_hr = 0).
        secs_val = secs or None
        if secs_val is None and sc is None:
            continue
        dates.append(d)
        duration_min.append(round(secs_val / 60) if secs_val is not None else None)
        score.append(round(float(sc), 1) if sc is not None else None)

    return {"dates": dates, "duration_min": duration_min, "score": score}


@router.get("/api/body-trend")
async def body_trend(
    days: int = Query(default=90, ge=1, le=365),
    user: User = Depends(require_viewer),
) -> dict:
    """Weight / body-fat / VO₂max / steps trend for the Wellness "Body trend"
    detail screen. ``days`` covers the 1m/3m/6m range pills (30/90/180)."""
    today = _today_local()
    start = today - timedelta(days=days - 1)

    uid = get_data_user_id(user)
    async with get_session() as session:
        result = await session.execute(
            select(
                Wellness.date,
                Wellness.weight,
                Wellness.body_fat,
                Wellness.vo2max,
                Wellness.steps,
            )
            .where(
                Wellness.user_id == uid,
                Wellness.date >= start.isoformat(),
                Wellness.date <= today.isoformat(),
            )
            .order_by(Wellness.date.asc())
        )
        rows = result.all()

    dates: list[str] = []
    weight: list[float | None] = []
    body_fat: list[float | None] = []
    vo2max: list[float | None] = []
    steps: list[int | None] = []
    # Unlike /api/recovery-trend (resting_hr=0) and /api/sleep-trend
    # (sleep_secs=0), body metrics get NO 0-sentinel normalisation — only NULL
    # is "missing". This matches Wellness.get_latest_weight / get_latest_vo2max
    # (both `isnot(None)` only), and is semantically correct: weight / body_fat
    # / VO₂max are physically impossible at 0 (Intervals stores them NULL when
    # absent, never 0), and steps = 0 is a *real* value — a genuine rest day —
    # that must not be hidden.
    for d, wt, bf, vo, st in rows:
        # Omit days with no body metric at all — same contract as the other
        # *-trend endpoints (a row with every charted field NULL == no row).
        if wt is None and bf is None and vo is None and st is None:
            continue
        dates.append(d)
        weight.append(round(float(wt), 1) if wt is not None else None)
        body_fat.append(round(float(bf), 1) if bf is not None else None)
        vo2max.append(round(float(vo), 1) if vo is not None else None)
        steps.append(int(st) if st is not None else None)

    return {
        "dates": dates,
        "weight": weight,
        "body_fat": body_fat,
        "vo2max": vo2max,
        "steps": steps,
    }


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
            #
            # Race-effort included intentionally (no `is_race` filter) — mirror
            # Runalyze upstream (spec §1 declarative stance, §7, §14 D1.A).
            # Race-day km are real basic-endurance volume; the 70-day time-decay
            # weight handles taper-phase anomalies.
            select(Activity.start_date_local, ActivityDetail.distance)
            .outerjoin(ActivityDetail, ActivityDetail.activity_id == Activity.id)
            .where(
                Activity.user_id == uid,
                Activity.type == "Run",
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

        # Max Run-race distance — used by the widget to decide whether a
        # predicted_time is extrapolated outside the user's training set.
        # XGBoost is tree-based and clamps predictions to the nearest leaf
        # when a feature falls outside training range; in practice this means
        # Marathon-distance predictions for users with no marathon race
        # history are unreliable. Widget renders a footnote when the picked
        # distance > max_race_distance * 1.3. Joins via `activity_id` because
        # `races.race_type` is the goal-priority class (A/B/C), not the sport.
        race_max_row = await session.execute(
            select(func.max(Race.distance_m))
            .join(Activity, Race.activity_id == Activity.id)
            .where(Race.user_id == uid, Activity.type == "Run")
        )
        max_run_race_distance_m = race_max_row.scalar()

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
    current_components: dict | None = None
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
                }
            )
            continue
        result = calculate_marathon_shape(all_runs, vo2max=vo2, reference_date=wk_end)
        # Per spec §3 + D2.A: scoring uses `target_longjog_km` (ln(V/4)*12-13);
        # UI shows `displayed_target_long_run_km = target_longjog_km + 13 =
        # ln(V/4)*12` (Runalyze parity). For V=37: scoring=13.7, displayed=26.7
        # ≈ «ca. 26 km» upstream.
        displayed_long_run = result.target_longjog_km + MIN_KM_FOR_LONGJOG
        weeks_out.append(
            {
                "week_start": wk_start.isoformat(),
                "week_end": wk_end.isoformat(),
                "shape_pct": result.shape_pct,
            }
        )
        # Last iteration produces the newest week → snapshot for `current_components`.
        # Frontend reads only these five fields (DashboardLoadTab.tsx).
        if i == weeks - 1:
            current_components = {
                "actual_weekly_km": result.actual_weekly_km,
                "target_weekly_km": result.target_weekly_km,
                "displayed_target_long_run_km": round(displayed_long_run, 1),
                "actual_longjog_km": result.actual_longjog_km,
                "vo2max": round(result.vo2max_used, 1),
            }

    # Newest first — freshest week leads the list.
    weeks_out.reverse()

    # ── Phase 1.5: ML-based Predicted time + pace per distance.
    today_iso = today.isoformat()
    predicted_times = await _compute_predicted_times(uid, today_iso)

    return {
        "weeks": weeks_out,
        "current_components": current_components,
        "predicted_times": predicted_times,
        "max_run_race_distance_m": (float(max_run_race_distance_m) if max_run_race_distance_m is not None else None),
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


_ENDURANCE_PERIOD_DAYS: dict[str, int] = {"1m": 30, "3m": 90, "6m": 180, "1y": 365}


@router.get("/api/endurance-score")
async def endurance_score(
    period: str = Query(default="3m", pattern="^(1m|3m|6m|1y)$"),
    user: User = Depends(require_viewer),
) -> dict:
    """Endurance Score + period-filtered daily trend for the Dashboard Load tab.

    Phase 2 implementation (per `docs/ENDURANCE_SCORE_SPEC.md` §9):
      · Reads daily snapshots from ``endurance_scores`` (populated by the
        Level-1 hooks in wellness/activities actors + Level-2 daily cron).
      · Period filter chooses the window: 1m=30d, 3m=90d (default), 6m=180d,
        1y=365d. Frontend chips drive this.
      · Fallback: if today's row is missing (cron hasn't fired yet on a fresh
        deploy, or backfill incomplete), compute on-the-fly via the sync
        service — kept inside ``asyncio.to_thread`` so we don't block the
        event loop on the chained DB queries.

    Per-sport breakdown, components, badge — all serialized from the stored
    ``components`` JSONB. Schema matches the Phase-1 response so the
    frontend doesn't need adjustment.
    """
    today = _today_local()
    uid = get_data_user_id(user)
    days = _ENDURANCE_PERIOD_DAYS[period]
    window_start = today - timedelta(days=days - 1)

    async with get_session() as session:
        rows = await EnduranceScore.get_range(uid, window_start, today, session=session)
        today_row = rows[-1] if rows and rows[-1].snapshot_date == today else None

    if today_row is None:
        # Fallback: today's snapshot not in the table yet. Recompute synchronously
        # off-thread. The actor will eventually persist it (Level 1 fires after
        # the next wellness/activities sync; Level 2 fires at 18:30 daily).
        current_result = await asyncio.to_thread(compute_endurance_for, uid, today)
        current_score = current_result.score
        current_zone_id = current_result.zone_id
        current_vo2max = current_result.vo2max_composite
        components = {
            "base": current_result.components.base,
            "long_term": current_result.components.long_term,
            "recent": current_result.components.recent,
            "duration": current_result.components.duration,
            "consistency": current_result.components.consistency,
            "recovery": current_result.components.recovery,
        }
        per_sport = [{"name": p.name, "pct": p.pct, "sub_score": p.sub_score} for p in current_result.per_sport]
        badge = (
            {"id": current_result.badge.id, "label": current_result.badge.label, "icon": current_result.badge.icon}
            if current_result.badge
            else None
        )
        insufficient = current_result.insufficient_data
    else:
        c = today_row.components or {}
        current_score = today_row.score
        current_zone_id = classify_zone(current_score).id
        current_vo2max = float(today_row.vo2max_composite) if today_row.vo2max_composite is not None else 0.0
        components = {
            "base": c.get("base", 0),
            "long_term": c.get("long_term", 0),
            "recent": c.get("recent", 0),
            "duration": c.get("duration", 0),
            "consistency": c.get("consistency", 0),
            "recovery": c.get("recovery", 0),
        }
        per_sport = c.get("per_sport", [])
        badge = c.get("badge")
        insufficient = c.get("insufficient_data", False)

    # Δ vs ~a week ago — strict `today - 7d` match was fragile: one skipped
    # cron-day = no badge, delta=0 looks misleadingly flat. Take the nearest
    # snapshot in the [today-10d, today-4d] window; if none exists (e.g. fresh
    # user, gap >3d) fall back to 0 — same neutral behaviour as before for
    # genuine «no data» cases, but resilient to single-day gaps.
    target = today - timedelta(days=7)
    window_lo = today - timedelta(days=10)
    window_hi = today - timedelta(days=4)
    candidates = [r for r in rows if window_lo <= r.snapshot_date <= window_hi]
    week_ago_row = min(candidates, key=lambda r: abs((r.snapshot_date - target).days)) if candidates else None
    delta_vs_week_ago = current_score - week_ago_row.score if week_ago_row is not None else 0

    trend = [{"date": r.snapshot_date.isoformat(), "score": r.score, "zone": classify_zone(r.score).id} for r in rows]
    # Fallback path computed today's score but didn't write it to the table
    # yet, so `rows` won't include a today-point. Synthesize one so the chart
    # line reaches today's gauge value instead of stopping at yesterday.
    if today_row is None:
        trend.append({"date": today.isoformat(), "score": current_score, "zone": current_zone_id})

    return {
        "current": {
            "score": current_score,
            "zone": current_zone_id,
            "vo2max_composite": current_vo2max,
            "components": components,
            "per_sport": per_sport,
            "delta_vs_week_ago": delta_vs_week_ago,
            "badge": badge,
            "insufficient_data": insufficient,
        },
        "trend": trend,
        "period": period,
    }


# Strain percentile bands always use the full available year of history (stable
# zones regardless of the viewed period); only the trend slice follows `period`.
_STRAIN_HISTORY_DAYS = 365


@router.get("/api/training-strain")
async def training_strain(
    period: str = Query(default="3m", pattern="^(1m|3m|6m|1y)$"),
    user: User = Depends(require_viewer),
) -> dict:
    """Foster training strain/monotony + ACWR — «how hard is the build, is it
    sustainable». Computed on-the-fly (no storage table): daily TSS rolled into
    7-day monotony/strain, zoned against the athlete's own trailing-year strain
    distribution. See ``data/training_strain.py``.

    Period chooses the visible trend window (1m/3m/6m/1y). The percentile bands
    are always derived from the full year so the zone doesn't flip when the user
    switches the chart range. Detail screen also gets the last-7-day daily TSS
    bars (which explain the monotony value visually).
    """
    today = local_today()
    uid = get_data_user_id(user)
    trend_days = _ENDURANCE_PERIOD_DAYS[period]
    trend_start = today - timedelta(days=trend_days - 1)
    history_start = today - timedelta(days=_STRAIN_HISTORY_DAYS - 1)
    # Fetch back an extra acute-window so the oldest trend/history point has a
    # full trailing 7-day window.
    fetch_start = (history_start - timedelta(days=7)).isoformat()
    today_iso = today.isoformat()

    async with get_session() as session:
        act_rows = (
            await session.execute(
                select(Activity.start_date_local, Activity.icu_training_load).where(
                    Activity.user_id == uid,
                    Activity.start_date_local >= fetch_start,
                    Activity.start_date_local <= today_iso,
                    Activity.icu_training_load.isnot(None),
                )
            )
        ).all()

        wellness_row = (
            await session.execute(
                select(Wellness.ctl, Wellness.atl)
                .where(
                    Wellness.user_id == uid,
                    Wellness.date <= today_iso,
                    Wellness.ctl.isnot(None),
                    Wellness.atl.isnot(None),
                )
                .order_by(Wellness.date.desc())
                .limit(1)
            )
        ).first()

    # Sum TSS per calendar day (start_date_local is an ISO string; the date is
    # its first 10 chars). Rest days simply never appear — the pure module fills
    # them with 0 inside each window.
    daily_tss: dict[date, float] = {}
    for sdl, load in act_rows:
        day = date.fromisoformat(sdl[:10])
        daily_tss[day] = daily_tss.get(day, 0.0) + float(load or 0.0)

    ctl = float(wellness_row.ctl) if wellness_row and wellness_row.ctl is not None else None
    atl = float(wellness_row.atl) if wellness_row and wellness_row.atl is not None else None

    res = compute_training_strain(
        ref_date=today,
        daily_tss_by_date=daily_tss,
        atl=atl,
        ctl=ctl,
        trend_start=trend_start,
        history_start=history_start,
    )

    week_delta = round(res.weekly_load - res.weekly_load_prev, 1)
    # Last-7-day daily TSS for the detail bar chart (oldest → newest).
    daily_load_7d = [
        {
            "date": (today - timedelta(days=i)).isoformat(),
            "tss": round(daily_tss.get(today - timedelta(days=i), 0.0), 1),
        }
        for i in range(6, -1, -1)
    ]

    return {
        "current": {
            "strain": res.strain,
            "monotony": res.monotony,
            "weekly_load": res.weekly_load,
            "weekly_load_delta": week_delta,
            "acwr": res.acwr,
            # Status from the UNrounded ratio — res.acwr is rounded to 2dp,
            # which would misclassify values just past a band edge (1.304 →
            # 1.30 → sweet, but it's actually caution).
            "acwr_status": acwr_status(acwr(atl, ctl)),
            "zone": res.zone_id,
            "bands": {
                "calm_max": round(res.bands.calm_max, 1),
                "hard_min": round(res.bands.hard_min, 1),
                "source": res.bands.source,
            },
            "insufficient_data": res.insufficient_data,
        },
        "daily_load_7d": daily_load_7d,
        "trend": [
            {
                "date": p.dt.isoformat(),
                "strain": round(p.strain, 1),
                "monotony": round(p.monotony, 2),
                "weekly_load": round(p.weekly_load, 1),
            }
            for p in res.trend
        ],
        "period": period,
    }


@router.get("/api/bike-readiness")
async def bike_readiness(
    weeks: int = Query(default=12, ge=1, le=24),
    user: User = Depends(require_viewer),
) -> dict:
    """Bike readiness — CTL_bike trend + current 3-signal snapshot.

    For each of the last ``weeks`` Mon-Sun weeks (ending at the most recent
    Sunday ≤ today), returns the CTL_bike value extracted from
    ``wellness.sport_info``. ``current_components`` carries the longest ride
    in the last 28 days, the median decoupling of the last 5 valid bike
    rides over 84 days, and the EF trend over that same window — the inputs
    to the widget's 3-signal traffic-light verdict.

    Distance-specific targets (Olympic / 70.3 / IM) and the verdict itself
    are computed CLIENT-side — endpoint returns absolute values only
    (spec §3, §5).
    """
    today = _today_local()
    days_since_sunday = (today.weekday() + 1) % 7
    window_end = today - timedelta(days=days_since_sunday)
    window_start = window_end - timedelta(weeks=weeks - 1, days=6)
    # 7-day back-walk per spec §7: a Sunday with NULL `sport_info` falls
    # back to the most recent earlier day with a value (CTL drifts slowly,
    # τ=42d, so 7d-stale is safe).
    wellness_start = window_start - timedelta(days=7)
    longest_window_start = today - timedelta(days=28)

    uid = get_data_user_id(user)
    async with get_session() as session:
        wellness_result = await session.execute(
            select(Wellness.date, Wellness.sport_info).where(
                Wellness.user_id == uid,
                Wellness.date >= wellness_start.isoformat(),
                Wellness.date <= window_end.isoformat(),
            )
        )
        wellness_rows = wellness_result.all()

        # Longest training ride in 28d: race-effort excluded (spec §3.2, §7) —
        # `is_race=True` is peak load, not training base. Activity.type is
        # canonical post-normalisation (data/intervals/dto.py:_normalize_type
        # maps VirtualRide / GravelRide / etc → "Ride"), so a single
        # `type == "Ride"` filter covers all bike variants. `start_date_local`
        # is a String column ("YYYY-MM-DD"), iso compare is correct.
        longest_result = await session.execute(
            select(Activity.start_date_local, Activity.moving_time)
            .where(
                Activity.user_id == uid,
                Activity.type == "Ride",
                Activity.is_race.is_(False),
                Activity.start_date_local >= longest_window_start.isoformat(),
            )
            .order_by(Activity.moving_time.desc())
            .limit(1)
        )
        longest_row = longest_result.first()

    ctl_bike_by_date: dict[str, float | None] = {
        dt_str: extract_sport_ctl(sport_info).get("ride") for dt_str, sport_info in wellness_rows
    }

    def _ctl_bike_at(anchor: date, back_days: int = 7) -> float | None:
        for delta in range(back_days + 1):
            v = ctl_bike_by_date.get((anchor - timedelta(days=delta)).isoformat())
            if v is not None:
                return v
        return None

    weeks_out: list[dict] = []
    for i in range(weeks):
        wk_start = window_start + timedelta(weeks=i)
        wk_end = wk_start + timedelta(days=6)
        weeks_out.append(
            {
                "week_start": wk_start.isoformat(),
                "week_end": wk_end.isoformat(),
                "ctl_bike": _ctl_bike_at(wk_end),
            }
        )
    # Newest first — consistent with `/api/marathon-shape` ordering.
    weeks_out.reverse()

    longest_ride_hours: float | None = None
    longest_ride_date: str | None = None
    if longest_row is not None:
        dt_str, mt = longest_row
        if mt is not None and mt > 0:
            longest_ride_hours = round(mt / 3600.0, 2)
            longest_ride_date = dt_str

    # Single helper call gives both Durability median and supplementary EF
    # trend (spec §5). `strict_filter=True` applies `is_valid_for_decoupling`
    # (VI ≤ 1.10, >70% Z1+Z2, ride ≥ 60 min) — no duplicate pipeline here.
    eff = await compute_efficiency_trend(
        user_id=uid,
        sport="bike",
        days_back=84,
        group_by="week",
        strict_filter=True,
    )
    # `compute_efficiency_trend` collapses to a single-sport dict when only
    # one sport is requested; `decoupling_trend` is absent when no valid
    # rides cleared the filter, and `trend` is `insufficient_data` when
    # there's < 2 weekly EF samples — guard both.
    decoup_trend = eff.get("decoupling_trend") if isinstance(eff, dict) else None
    trend = eff.get("trend") if isinstance(eff, dict) else None

    if isinstance(decoup_trend, dict):
        decoupling_median_pct = decoup_trend.get("median")
        decoupling_status = decoup_trend.get("status")
        decoupling_n = decoup_trend.get("last_n", 0)
    else:
        decoupling_median_pct = None
        decoupling_status = None
        decoupling_n = 0

    if isinstance(trend, dict) and trend.get("direction") != "insufficient_data":
        ef_trend_pct: float | None = trend.get("pct")
    else:
        ef_trend_pct = None

    current_components = {
        "ctl_bike": weeks_out[0]["ctl_bike"] if weeks_out else None,
        "longest_ride_hours": longest_ride_hours,
        "longest_ride_date": longest_ride_date,
        "decoupling_median_pct": decoupling_median_pct,
        "decoupling_status": decoupling_status,
        "decoupling_n": decoupling_n,
        "ef_trend_pct": ef_trend_pct,
    }

    return {
        "weeks": weeks_out,
        "current_components": current_components,
    }


@router.post("/api/jobs/refresh-wellness", status_code=202)
async def job_refresh_wellness(user: User = Depends(require_athlete)) -> dict:
    """Trigger an out-of-band wellness refresh for the authed athlete.

    Fire-and-forget: dispatches ``actor_user_wellness`` for today via Dramatiq
    with ``force=True`` so the downstream fan-out (HRV/RHR/recovery/banister)
    runs even if the source row hasn't changed. The frontend polls
    ``/api/wellness-day`` after a brief delay to surface the new state.

    Per-user 60s cooldown — protects Intervals.icu API quota and the worker
    queue from button-mash spam. 429 on hit; client just shows the disabled
    state until the next allowed call.

    ``require_athlete`` (not viewer) — refresh writes to *this* user's data,
    not a tenant they're viewing read-only; demo/viewer should not be able to
    burn the owner's API budget.
    """
    now = time.monotonic()
    last = _refresh_last.get(user.id, 0.0)
    remaining = int(_REFRESH_COOLDOWN_SEC - (now - last))
    if remaining > 0:
        raise HTTPException(
            status_code=429,
            detail={"error": "cooldown", "retry_after_sec": remaining},
            headers={"Retry-After": str(remaining)},
        )
    _refresh_last[user.id] = now

    user_dto = UserDTO.model_validate(user)
    today = _today_local().isoformat()
    actor_user_wellness.send(user=user_dto, dt=today, force=True)
    logger.info("Wellness refresh dispatched for user %s (dt=%s)", user.id, today)
    return {"status": "accepted", "job": "refresh-wellness", "dt": today}
