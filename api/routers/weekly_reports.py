"""REST endpoints for weekly-report history (PR2 of the weekly-report feature).

Backs the webapp ``/weekly`` archive (PR3): list of past Mon-Sun summaries with
short previews, and per-week full markdown for the detail view. Telegram chat
gets only a notification + WebApp button — the canonical archive lives here.

Auth: ``require_athlete`` on both endpoints — own-history-only, no demo
read-through. Weekly summaries reference ``user_facts`` (injuries, family
context, schedule notes) so demo cross-read would leak athlete-private
context that the rest of the dashboard already gates on athlete identity.

Pagination: cursor by ``week_start`` (DESC), strict ``<`` semantics so the
cursor row isn't returned twice. Limit is hard-capped at 50 to bound the
worst-case response size; the UI uses 20.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from api.deps import get_data_user_id, require_athlete
from data.db import Activity, ActivityDetail, User, WeeklyReport, Wellness, get_session
from data.utils import normalize_sport
from data.weekly_preview import extract_weekly_headline, extract_weekly_preview

router = APIRouter()

_LIST_LIMIT_DEFAULT = 20
_LIST_LIMIT_MAX = 50

# Canonical Intervals.icu sport name → recap bucket key. Mirrors ``_SPORT_MAP``
# in ``api/routers/dashboard.py`` — anything that doesn't normalize into one of
# the three (yoga, hike, strength) is dropped from the per-week volume, exactly
# as the dashboard charts drop it.
_SPORT_BUCKET = {"Swim": "swimming", "Ride": "cycling", "Run": "running"}


def _nearest_load(
    wellness_by_date: dict[str, tuple[float | None, float | None, float | None]],
    anchor: date,
    *,
    back_days: int = 6,
) -> tuple[float | None, float | None, float | None]:
    """Most recent ``(ctl, atl, ramp_rate)`` on/before ``anchor``.

    Anchors on a row that has both CTL and ATL present — a week's bookend day
    can land on a bootstrap gap, so we walk back up to ``back_days``.
    ``ramp_rate`` is read off that same row and may still be ``None``
    independently (Intervals doesn't always populate it).
    """
    for delta in range(back_days + 1):
        key = (anchor - timedelta(days=delta)).isoformat()
        row = wellness_by_date.get(key)
        if row is not None and row[0] is not None and row[1] is not None:
            return row
    return None, None, None


async def _week_training_stats(uid: int, week_starts: list[date]) -> dict[str, dict]:
    """Per-week training volume + CTL/ramp/TSB bookends for the Recap cards.

    Keyed by an explicit set of week-start Mondays (the report weeks in the
    current page) rather than a contiguous window — reports can have gaps if a
    Sunday cron was missed. Returns one entry per ``week_start`` (empty
    ``by_sport`` / null load when the week has no activity or wellness rows).

    Same bucketing the retired ``/api/weekly-recap`` endpoint did; folded here
    once the Recap tab became weekly-report-driven.
    """
    if not week_starts:
        return {}
    ordered = sorted(week_starts)
    window_start = ordered[0]
    window_end = ordered[-1] + timedelta(days=6)
    # _nearest_load walks back up to 6 days, so reach a week before the oldest
    # week for its "entering CTL" anchor to resolve across a bootstrap gap.
    wellness_start = window_start - timedelta(days=7)
    want = {s.isoformat() for s in week_starts}

    async with get_session() as session:
        act_rows = (
            await session.execute(
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
        ).all()
        wellness_rows = (
            await session.execute(
                select(Wellness.date, Wellness.ctl, Wellness.atl, Wellness.ramp_rate).where(
                    Wellness.user_id == uid,
                    Wellness.date >= wellness_start.isoformat(),
                    Wellness.date <= window_end.isoformat(),
                )
            )
        ).all()

    wellness_by_date: dict[str, tuple[float | None, float | None, float | None]] = {
        d: (
            float(c) if c is not None else None,
            float(a) if a is not None else None,
            float(r) if r is not None else None,
        )
        for d, c, a, r in wellness_rows
    }

    # week_start ISO → sport bucket → running totals.
    buckets: dict[str, dict[str, dict[str, float]]] = {wk: {} for wk in want}
    for dt_str, raw_type, moving_time, tss, dist in act_rows:
        sport = _SPORT_BUCKET.get(normalize_sport(raw_type) or "")
        if not sport:
            continue
        try:
            dt = date.fromisoformat(dt_str)
        except (TypeError, ValueError):
            continue
        wk = (dt - timedelta(days=dt.weekday())).isoformat()
        bucket = buckets.get(wk)
        if bucket is None:
            continue
        totals = bucket.setdefault(sport, {"duration_sec": 0.0, "distance_m": 0.0, "tss": 0.0})
        if moving_time is not None:
            totals["duration_sec"] += float(moving_time)
        if dist is not None:
            totals["distance_m"] += float(dist)
        if tss is not None:
            totals["tss"] += float(tss)

    out: dict[str, dict] = {}
    for wk_start in week_starts:
        wk_iso = wk_start.isoformat()
        wk_end = wk_start + timedelta(days=6)
        # "Entering" CTL = day before the Monday; "exiting" CTL/ATL/ramp = the
        # Sunday — same bookend convention the weekly-recap endpoint used.
        ctl_start, _, _ = _nearest_load(wellness_by_date, wk_start - timedelta(days=1))
        ctl_end, atl_end, ramp_end = _nearest_load(wellness_by_date, wk_end)
        by_sport = {
            sport: {
                "duration_sec": int(t["duration_sec"]),
                "distance_m": round(t["distance_m"], 1),
                "tss": round(t["tss"], 1),
            }
            for sport, t in buckets[wk_iso].items()
        }
        ctl_delta = round(ctl_end - ctl_start, 1) if ctl_start is not None and ctl_end is not None else None
        tsb_end = round(ctl_end - atl_end, 1) if ctl_end is not None and atl_end is not None else None
        out[wk_iso] = {
            "by_sport": by_sport,
            "ctl_start": round(ctl_start, 1) if ctl_start is not None else None,
            "ctl_end": round(ctl_end, 1) if ctl_end is not None else None,
            "ctl_delta": ctl_delta,
            "ramp": round(ramp_end, 1) if ramp_end is not None else None,
            "tsb_end": tsb_end,
        }
    return out


def _format_list_item(row: WeeklyReport, stats: dict) -> dict:
    """Card shape for the Recap tab / history grid: enough to render a tap-target.

    Carries the AI ``headline`` (leading H1, ``None`` for legacy reports), a
    server-rendered ``preview`` fallback, plus that week's training volume and
    CTL/ramp/TSB bookends so the card renders without a second round-trip.
    ``content_md`` itself stays out of the list payload — the detail endpoint
    serves the full markdown when the athlete taps in.
    """
    # ``generated_at`` is NOT NULL in the schema (default ``now()`` on insert,
    # always set in upsert), so the `.isoformat()` call is safe — no
    # ``else None`` defensive branch needed. Letting AttributeError surface
    # if the invariant ever breaks is more useful than silently emitting
    # ``null`` and pushing the contract mismatch to the client.
    return {
        "week_start": row.week_start.isoformat(),
        "headline": extract_weekly_headline(row.content_md),
        "preview": extract_weekly_preview(row.content_md),
        "generated_at": row.generated_at.isoformat(),
        **stats,
    }


@router.get("/api/weekly-reports")
async def list_weekly_reports(
    limit: int = Query(_LIST_LIMIT_DEFAULT, ge=1, le=_LIST_LIMIT_MAX),
    before: date | None = Query(None, description="ISO Monday — return rows strictly older"),
    user: User = Depends(require_athlete),
) -> dict:
    """Cursor-paginated list of the athlete's weekly reports, newest first.

    Returns ``{items: [...], next_before: ISO|null}``. ``next_before`` is the
    ``week_start`` of the oldest row in this page when the page filled to the
    limit (more history available); ``null`` when fewer than ``limit`` rows
    came back, meaning the client has reached the end and should stop
    fetching.
    """
    uid = get_data_user_id(user)
    rows = await WeeklyReport.list_for_user(uid, limit=limit, before=before)
    next_before = rows[-1].week_start.isoformat() if len(rows) == limit else None
    # _week_training_stats returns an entry for every week_start passed, so the
    # per-row lookup below is total — no KeyError branch needed.
    stats = await _week_training_stats(uid, [r.week_start for r in rows])
    return {
        "items": [_format_list_item(r, stats[r.week_start.isoformat()]) for r in rows],
        "next_before": next_before,
    }


@router.get("/api/weekly-reports/{week_start}")
async def get_weekly_report(
    week_start: date,
    user: User = Depends(require_athlete),
) -> dict:
    """Full markdown for a specific week, or 404.

    ``week_start`` must be a Monday in practice — if the client passes a
    non-Monday date the lookup just returns 404 because the upsert never
    writes anything else. We don't pre-validate the weekday: cheaper to let
    the DB miss than reject mid-week ISO dates that may exist in some
    edge-case backfill.
    """
    uid = get_data_user_id(user)
    row = await WeeklyReport.get_one(uid, week_start)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No weekly report for {week_start.isoformat()}")
    return {
        "content_md": row.content_md,
    }
