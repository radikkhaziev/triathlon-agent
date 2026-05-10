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

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_data_user_id, require_athlete
from data.db import User, WeeklyReport
from data.weekly_preview import extract_weekly_preview

router = APIRouter()

_LIST_LIMIT_DEFAULT = 20
_LIST_LIMIT_MAX = 50


def _format_list_item(row: WeeklyReport) -> dict:
    """Card shape for the history grid: enough to render a tap-target.

    Computes ``preview`` server-side instead of returning ``content_md`` so
    the list payload stays small (21 cards × ~220-char preview ≈ 5 KB vs
    21 × full report ≈ 80+ KB). Detail view fetches the full markdown via
    the per-week endpoint when the athlete taps in.
    """
    # ``generated_at`` is NOT NULL in the schema (default ``now()`` on insert,
    # always set in upsert), so the `.isoformat()` call is safe — no
    # ``else None`` defensive branch needed. Letting AttributeError surface
    # if the invariant ever breaks is more useful than silently emitting
    # ``null`` and pushing the contract mismatch to the client.
    return {
        "week_start": row.week_start.isoformat(),
        "preview": extract_weekly_preview(row.content_md),
        "generated_at": row.generated_at.isoformat(),
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
    return {
        "items": [_format_list_item(r) for r in rows],
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
        "week_start": row.week_start.isoformat(),
        "content_md": row.content_md,
        "generated_at": row.generated_at.isoformat(),
        "model": row.model,
    }
