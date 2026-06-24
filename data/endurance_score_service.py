"""Endurance Score IO orchestration — fetch + compute, no scoring formulas.

Shared service layer between three callers (per spec §7.0):
  · `tasks/actors/endurance.py` — Dramatiq actor (per-user + all-users wrapper)
  · `api/routers/dashboard.py` — `/api/endurance-score` endpoint fallback path
  · `cli.py` — `backfill-endurance-scores` per-day per-user loop

All three need the same answer: "given user_id and ref_date, what's the
EnduranceScoreResult?" — so the fetch logic lives here. The pure formulas
stay in `data/endurance_score.py` (no DB, no clock).

Sync API only — actors/CLI run synchronously, and the async endpoint can
afford `asyncio.to_thread` for fallback compute that bypasses the table.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from data.db import (
    Activity,
    ActivityDetail,
    ActivityHrv,
    AthleteSettings,
    EnduranceScore,
    User,
    Wellness,
    get_sync_session,
)
from data.endurance_score import (
    AthleteProfile,
    Badge,
    EnduranceActivity,
    EnduranceComponents,
    EnduranceScoreResult,
    PerSport,
    WellnessSnapshot,
    classify_zone,
    compute_endurance_score,
)
from data.utils import extract_sport_ctl, extract_sport_eftp

logger = logging.getLogger(__name__)

# Debounce window for force=True calls — Level-1 hooks fire from wellness
# (~8/day) and activities (~10/day) sync, so a busy day can trigger ~20 ES
# recomputes/user. Compute is ~50ms, but at scale that's wasted DB+CPU. Skip
# if last computed within this window; CLI/Level-2 cron with `--force` also
# respects this (Level-2 fires once/day so doesn't hit it anyway).
_ACTOR_DEBOUNCE_SECONDS = 300

# Look-back window for the activity fetch — needs the longest component
# window (Consistency = 8 weeks) plus 4 extra weeks of margin so the oldest
# trend point (12 weeks back) still has its full 8-week tail.
_ACTIVITY_LOOKBACK_DAYS = 8 * 7 + 4 * 7 + 28  # 8w + 4w buffer + 28d (Duration/Recovery)
# Wellness — only need 8 weeks back from ref_date for LongTermBonus avg.
_WELLNESS_LOOKBACK_DAYS = 8 * 7
# Detrain decay (spec §13.1) — CTL peak window. 26 weeks (182d) is wide
# enough to catch the athlete's last real fitness peak before a long break,
# narrow enough that a multi-year-old peak doesn't anchor today's decay. A
# dedicated scalar MAX query, not a widening of the 56d fetch above (which
# would pull ~3× the rows just to throw most away).
_CTL_PEAK_LOOKBACK_DAYS = 26 * 7
# Badge engine — `top_10_percentile` needs 365 daily snapshots, `in_form_3m`
# needs 84, `best_90d` needs 30+ minimum. Pulling 365 covers all three.
_BADGE_HISTORY_DAYS = 365


@dataclass
class EndurancePersistResult:
    """Outcome of a `recompute_and_upsert` call."""

    result: EnduranceScoreResult
    written: bool  # False if the row already existed and --force was not set


def _z2plus_share(detail: ActivityDetail | None) -> float | None:
    """Share of session time ≥Z2 (HR zones). None for sessions without HR data.

    Returns None on `len(times) < 2` — trust-by-default policy: a session with
    a degenerate zone array (e.g. all time in Z1 only, or detail row missing
    zones) shouldn't be filtered out of Duration; the duration-threshold check
    in `duration_bonus` still applies. Caller treats None as «no quality
    filter applied» and counts the session if it's long enough.
    """
    if detail is None or detail.hr_zone_times is None:
        return None
    times = [float(t or 0) for t in detail.hr_zone_times]
    total = sum(times)
    if total <= 0 or len(times) < 2:
        return None
    return sum(times[1:]) / total


def _build_activities(
    activity_rows: Sequence[tuple[Activity, ActivityDetail | None]],
    dfa_by_activity: dict[str, float],
) -> list[EnduranceActivity]:
    out: list[EnduranceActivity] = []
    for a, d in activity_rows:
        try:
            dt = date.fromisoformat(str(a.start_date_local)[:10])
        except (TypeError, ValueError):
            continue
        out.append(
            EnduranceActivity(
                dt=dt,
                type=a.type or "",
                moving_time_sec=int(a.moving_time or 0),
                training_load=float(a.icu_training_load) if a.icu_training_load is not None else None,
                z2plus_time_pct=_z2plus_share(d),
                dfa_a1_mean=dfa_by_activity.get(a.id),
            )
        )
    return out


def _build_wellness_snapshots(rows) -> list[WellnessSnapshot]:
    out: list[WellnessSnapshot] = []
    for w_date, ctl, ramp, sport_info in rows:
        try:
            dt = date.fromisoformat(str(w_date)[:10])
        except (TypeError, ValueError):
            continue
        sc = extract_sport_ctl(sport_info)
        ride_eftp = extract_sport_eftp(sport_info).get("ride")
        out.append(
            WellnessSnapshot(
                dt=dt,
                ctl=float(ctl) if ctl is not None else None,
                ramp_rate=float(ramp) if ramp is not None else None,
                sport_ctl={
                    "Ride": float(sc.get("ride") or 0.0),
                    "Run": float(sc.get("run") or 0.0),
                    "Swim": float(sc.get("swim") or 0.0),
                },
                ride_eftp=float(ride_eftp) if ride_eftp is not None else None,
            )
        )
    return out


def _fetch_athlete_profile(user_id: int, session) -> AthleteProfile:
    user = session.get(User, user_id)
    settings_rows = session.execute(select(AthleteSettings).where(AthleteSettings.user_id == user_id)).scalars().all()
    ftp_w: float | None = None
    threshold_pace: float | None = None
    for row in settings_rows:
        if row.sport == "Ride" and row.ftp:
            ftp_w = float(row.ftp)
        elif row.sport == "Run" and row.threshold_pace:
            threshold_pace = float(row.threshold_pace)

    weight = Wellness.get_latest_weight(user_id, session=session)
    age = user.age if user else None
    return AthleteProfile(
        age=int(age) if age is not None else None,
        weight_kg=float(weight) if weight is not None else None,
        ftp_w=ftp_w,
        threshold_pace_sec_per_km=threshold_pace,
    )


def _fetch_activities(user_id: int, ref_date: date, session) -> list[EnduranceActivity]:
    start = ref_date - timedelta(days=_ACTIVITY_LOOKBACK_DAYS)
    rows = list(
        session.execute(
            # Tenant scope = `Activity.user_id == user_id` in the WHERE clause.
            # Detail rows are reachable only via the parent Activity (no
            # user_id column on ActivityDetail), so the outer JOIN inherits
            # scoping from the parent — no extra guard needed in the JOIN
            # condition itself.
            select(Activity, ActivityDetail)
            .outerjoin(ActivityDetail, ActivityDetail.activity_id == Activity.id)
            .where(
                Activity.user_id == user_id,
                Activity.start_date_local >= start.isoformat(),
                Activity.start_date_local <= ref_date.isoformat(),
            )
        ).all()
    )
    activity_ids = [a.id for a, _ in rows]
    dfa_by_activity: dict[str, float] = {}
    if activity_ids:
        # Defense-in-depth: explicit JOIN on Activity.user_id == user_id even
        # though `activity_ids` come from a user-filtered query. ActivityHrv
        # is keyed by activity_id only (no user_id column) — a future
        # refactor that splits the IN-clause filter from this lookup would
        # leak cross-tenant DFA without this explicit join.
        hrv_rows = session.execute(
            select(ActivityHrv.activity_id, ActivityHrv.dfa_a1_mean)
            .join(Activity, ActivityHrv.activity_id == Activity.id)
            .where(
                Activity.user_id == user_id,
                ActivityHrv.activity_id.in_(activity_ids),
                ActivityHrv.dfa_a1_mean.isnot(None),
            )
        ).all()
        dfa_by_activity = {aid: float(v) for aid, v in hrv_rows}
    return _build_activities(rows, dfa_by_activity)


def _fetch_wellness_snapshots(user_id: int, ref_date: date, session) -> list[WellnessSnapshot]:
    start = ref_date - timedelta(days=_WELLNESS_LOOKBACK_DAYS)
    rows = session.execute(
        select(Wellness.date, Wellness.ctl, Wellness.ramp_rate, Wellness.sport_info)
        .where(
            Wellness.user_id == user_id,
            Wellness.date >= start.isoformat(),
            Wellness.date <= ref_date.isoformat(),
        )
        .order_by(Wellness.date.asc())
    ).all()
    return _build_wellness_snapshots(rows)


def _fetch_ctl_peak_26w(user_id: int, ref_date: date, session) -> float | None:
    """Max CTL over the trailing 26 weeks — the detrain-decay reference peak.

    Date-specific (``Wellness.date <= ref_date``) so trend backfill anchors
    each historical point to the peak that preceded *it*, not today's. Returns
    None when there's no CTL history in the window (new user) → no decay.
    """
    start = ref_date - timedelta(days=_CTL_PEAK_LOOKBACK_DAYS)
    peak = session.execute(
        select(func.max(Wellness.ctl)).where(
            Wellness.user_id == user_id,
            Wellness.date >= start.isoformat(),
            Wellness.date <= ref_date.isoformat(),
            Wellness.ctl.isnot(None),
        )
    ).scalar()
    return float(peak) if peak is not None else None


def _fetch_badge_history(
    user_id: int,
    ref_date: date,
    session,
) -> tuple[str | None, list[int], list[int], list[str], list[str]]:
    """Pull score+zone+badge history from ``endurance_scores`` for the badge engine.

    Returns ``(zone_yesterday_id, scores_last_90d, scores_last_365d,
    zones_last_84d, recent_badge_ids)``.
    Empty collections are safe — badge engine treats them as "no history,
    skip rule" or "no cooldown".

    ``recent_badge_ids`` covers the cooldown window from spec §3.9 — 7 days
    for #2/#3/#4 + the 1-day window for #1 (both bounded by 7d here; the
    engine itself decides whether `new_zone` repeats are allowed via its
    own membership check, but since it always picks rule #1 first, the
    1d window collapses into the 7d window in practice).
    """
    history = EnduranceScore.get_history(
        user_id, _BADGE_HISTORY_DAYS, ref_date=ref_date - timedelta(days=1), session=session
    )
    # Order is ASC by snapshot_date (see EnduranceScore.get_range).
    scores_last_365d = [row.score for row in history]
    scores_last_90d = [row.score for row in history[-90:]] if len(history) >= 1 else []
    zones_last_84d = [classify_zone(row.score).id for row in history[-84:]] if len(history) >= 84 else []
    # Cooldown lookup per spec §3.9:
    #   · `new_zone` — 1-day cooldown (only yesterday's badge suppresses today)
    #   · others    — 7-day cooldown
    # We pass a single `recent_badge_ids` list to compute_badge — to honour
    # both windows we include `new_zone` ONLY if it fired yesterday, and all
    # other ids if they fired in the last 7 days. The engine just checks
    # membership; the windowing is built here.
    recent_badge_ids: list[str] = []
    for offset, row in enumerate(reversed(history)):  # offset=0 = yesterday
        components = row.components or {}
        b = components.get("badge")
        if not isinstance(b, dict):
            continue
        bid = b.get("id")
        if not bid:
            continue
        if bid == "new_zone" and offset == 0:
            recent_badge_ids.append(bid)
        elif bid != "new_zone" and offset < 7:
            recent_badge_ids.append(bid)
        # Stop once we're past both windows
        if offset >= 7:
            break

    yesterday_row = EnduranceScore.get_score_on(user_id, ref_date - timedelta(days=1), session=session)
    zone_yesterday_id = classify_zone(yesterday_row.score).id if yesterday_row else None
    return zone_yesterday_id, scores_last_90d, scores_last_365d, zones_last_84d, recent_badge_ids


def compute_for(user_id: int, ref_date: date, *, session=None) -> EnduranceScoreResult:
    """Compute the ES result for ``(user_id, ref_date)`` without persisting.

    If ``session`` is provided it must be a sync session — we reuse it for
    every query. Otherwise a fresh session is opened/closed internally. Used
    by the endpoint fallback path (today's row not yet in the table) and by
    `recompute_and_upsert` below.
    """
    if session is None:
        with get_sync_session() as s:
            return compute_for(user_id, ref_date, session=s)

    athlete = _fetch_athlete_profile(user_id, session)
    activities = _fetch_activities(user_id, ref_date, session)
    wellness_snapshots = _fetch_wellness_snapshots(user_id, ref_date, session)
    latest_wellness = (
        max(wellness_snapshots, key=lambda s: s.dt)
        if wellness_snapshots
        else WellnessSnapshot(dt=ref_date, ctl=None, ramp_rate=None, sport_ctl={})
    )

    # Slice down to per-component windows (the fetch is wider than needed).
    cutoff_28d = ref_date - timedelta(days=28)
    cutoff_8w = ref_date - timedelta(weeks=8)
    cutoff_56d = ref_date - timedelta(days=56)

    yesterday_id, scores_90d, scores_365d, zones_84d, recent_badge_ids = _fetch_badge_history(
        user_id, ref_date, session
    )
    # Detrain decay contract (spec §13.1): `ctl_now` = latest_wellness.ctl,
    # sourced from the 56d window above, while `ctl_peak_26w` spans 182d. If
    # the last 56d hold no wellness row, ctl_now is None → factor 1.0 (decay
    # off). This does NOT hit the target layoff case — Intervals.icu writes
    # wellness daily regardless of training, so CTL keeps decaying and a
    # connected athlete always has a fresh ctl_now. The no-op only triggers on
    # a >56d gap in wellness data itself (Intervals disconnected / watch not
    # worn), where there's no current CTL to measure the drop against anyway.
    ctl_peak_26w = _fetch_ctl_peak_26w(user_id, ref_date, session)

    return compute_endurance_score(
        ref_date=ref_date,
        athlete=athlete,
        latest_wellness=latest_wellness,
        wellness_56d=[s for s in wellness_snapshots if cutoff_56d <= s.dt <= ref_date],
        activities_28d=[a for a in activities if cutoff_28d <= a.dt <= ref_date],
        activities_8w=[a for a in activities if cutoff_8w <= a.dt <= ref_date],
        ctl_peak_26w=ctl_peak_26w,
        zone_yesterday_id=yesterday_id,
        scores_last_90d=scores_90d,
        scores_last_365d=scores_365d,
        zones_last_84d=zones_84d,
        recent_badge_ids=recent_badge_ids,
    )


def recompute_and_upsert(
    user_id: int,
    ref_date: date,
    *,
    force: bool = False,
    session=None,
) -> EndurancePersistResult:
    """Compute + persist (idempotent upsert) one (user_id, ref_date) row.

    ``force=False`` (default + CLI default) skips dates already in the table —
    backfill doesn't burn compute on already-written historical rows.

    ``force=True`` (Dramatiq actor + CLI ``--force``) always re-computes and
    overwrites via `ON CONFLICT DO UPDATE`. The actor wants fresh data on
    every Level-1 hook fire (wellness/activities just changed → re-snapshot
    today), so skip-on-exists is wrong there.
    """
    if session is None:
        with get_sync_session() as s:
            return recompute_and_upsert(user_id, ref_date, force=force, session=s)

    existing = EnduranceScore.get_score_on(user_id, ref_date, session=session)
    if existing is not None:
        if not force:
            # Default: skip — backfill uses this to no-op already-computed days.
            return EndurancePersistResult(result=_result_from_row(existing), written=False)
        # force=True path — debounce burst-fires from Level-1 hooks (~20/day).
        # Compute is ~50ms but DB write + actor dispatch overhead is real at
        # scale. Within debounce window → reuse stored result, skip compute.
        computed_at = existing.computed_at
        if computed_at is not None:
            if computed_at.tzinfo is None:
                computed_at = computed_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - computed_at).total_seconds()
            if age < _ACTOR_DEBOUNCE_SECONDS:
                return EndurancePersistResult(result=_result_from_row(existing), written=False)

    result = compute_for(user_id, ref_date, session=session)
    EnduranceScore.upsert(
        user_id=user_id,
        snapshot_date=ref_date,
        score=result.score,
        vo2max_composite=float(result.vo2max_composite),
        components=_serialize_components(result),
        session=session,
    )
    return EndurancePersistResult(result=result, written=True)


def _serialize_components(result: EnduranceScoreResult) -> dict:
    """Flatten EnduranceScoreResult into the JSONB shape stored in components."""
    return {
        "base": result.components.base,
        "long_term": result.components.long_term,
        "recent": result.components.recent,
        "duration": result.components.duration,
        "consistency": result.components.consistency,
        "recovery": result.components.recovery,
        "per_sport": [{"name": p.name, "pct": p.pct, "sub_score": p.sub_score} for p in result.per_sport],
        "badge": (
            {"id": result.badge.id, "label": result.badge.label, "icon": result.badge.icon} if result.badge else None
        ),
        "detrain_factor": result.detrain_factor,
        "ctl_peak_26w": result.ctl_peak_26w,
        "insufficient_data": result.insufficient_data,
        "insufficient_reason": result.insufficient_reason,
    }


def _result_from_row(row: EnduranceScore) -> EnduranceScoreResult:
    """Reconstruct an `EnduranceScoreResult` from a stored row.

    Used by the endpoint when the table has today's row and we don't want to
    recompute. The components JSONB has all the fields we need; we rebuild
    the dataclasses for type-safe downstream rendering.
    """
    c = row.components or {}
    badge_d = c.get("badge")
    badge = Badge(id=badge_d["id"], label=badge_d["label"], icon=badge_d["icon"]) if badge_d else None
    per_sport = [PerSport(name=p["name"], pct=p["pct"], sub_score=p.get("sub_score")) for p in c.get("per_sport", [])]
    return EnduranceScoreResult(
        score=row.score,
        zone_id=classify_zone(row.score).id,
        vo2max_composite=float(row.vo2max_composite) if row.vo2max_composite is not None else 0.0,
        components=EnduranceComponents(
            base=c.get("base", 0),
            long_term=c.get("long_term", 0),
            recent=c.get("recent", 0),
            duration=c.get("duration", 0),
            consistency=c.get("consistency", 0),
            recovery=c.get("recovery", 0),
        ),
        per_sport=per_sport,
        badge=badge,
        detrain_factor=c.get("detrain_factor", 1.0),
        ctl_peak_26w=c.get("ctl_peak_26w"),
        insufficient_data=c.get("insufficient_data", False),
        insufficient_reason=c.get("insufficient_reason"),
    )
