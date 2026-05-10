"""Tests for /api/weekly-reports endpoints (PR2).

Covers:
- Cursor pagination shape: ``next_before`` is the oldest row's week_start
  when the page filled, ``null`` otherwise.
- Per-page limit cap (50) and default (20).
- Cross-tenant isolation: athlete A cannot fetch athlete B's row even with
  the exact ISO path. Mirrors the auth-resolved ``user_id`` invariant.
- 404 for missing week.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest  # noqa: F401  — pytest-asyncio collects via marker auto-discovery
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import require_athlete
from api.routers.weekly_reports import router as weekly_reports_router
from data.db import User, WeeklyReport, get_session


def _build_client(*, user_id: int = 1) -> AsyncClient:
    """ASGI client with auth deps overridden to return a stub User row."""
    test_app = FastAPI()
    test_app.include_router(weekly_reports_router)

    mock_user = MagicMock()
    mock_user.id = user_id
    mock_user.role = "athlete"
    mock_user.is_active = True
    mock_user.athlete_id = "12345"
    test_app.dependency_overrides[require_athlete] = lambda: mock_user
    return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")


async def _ensure_user(user_id: int) -> None:
    async with get_session() as session:
        existing = await session.get(User, user_id)
        if existing is None:
            session.add(User(id=user_id, chat_id=str(user_id), role="athlete"))
            await session.commit()


SAMPLE_MD = "📊 **Итог недели (4–10 мая)**\n\nВыполнено 12 из 20 тренировок, compliance 55%."


# ---------------------------------------------------------------------------
# GET /api/weekly-reports (list)
# ---------------------------------------------------------------------------


class TestList:
    async def test_returns_empty_when_no_rows(self):
        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "next_before": None}

    async def test_returns_items_newest_first(self):
        for week in (date(2026, 4, 27), date(2026, 5, 4), date(2026, 4, 20)):
            await WeeklyReport.upsert(user_id=1, week_start=week, content_md=SAMPLE_MD, model="m")

        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports")
        body = resp.json()
        assert [it["week_start"] for it in body["items"]] == ["2026-05-04", "2026-04-27", "2026-04-20"]
        # Preview is server-rendered — no raw markdown markers in payload.
        assert "**" not in body["items"][0]["preview"]
        assert body["next_before"] is None  # 3 rows < default limit 20

    async def test_next_before_set_when_page_full(self):
        # Seed 5 weeks; ask for limit=2 → expect next_before = oldest of the 2.
        weeks = [date(2026, 4, 6) + timedelta(weeks=i) for i in range(5)]
        for week in weeks:
            await WeeklyReport.upsert(user_id=1, week_start=week, content_md=SAMPLE_MD, model="m")

        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports", params={"limit": 2})
        body = resp.json()
        assert len(body["items"]) == 2
        # Newest two: 2026-05-04 (week 4), 2026-04-27 (week 3).
        assert body["items"][0]["week_start"] == "2026-05-04"
        assert body["items"][1]["week_start"] == "2026-04-27"
        # Cursor for the next page = oldest in current page.
        assert body["next_before"] == "2026-04-27"

    async def test_before_cursor_returns_older_rows(self):
        weeks = [date(2026, 4, 6) + timedelta(weeks=i) for i in range(4)]
        for week in weeks:
            await WeeklyReport.upsert(user_id=1, week_start=week, content_md=SAMPLE_MD, model="m")

        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports", params={"before": "2026-04-20"})
        body = resp.json()
        # Strict ``<`` — 2026-04-20 itself is excluded.
        assert [it["week_start"] for it in body["items"]] == ["2026-04-13", "2026-04-06"]

    async def test_limit_above_cap_rejected(self):
        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports", params={"limit": 51})
        # FastAPI Query(le=50) → 422 Unprocessable Entity, not 400.
        assert resp.status_code == 422

    async def test_does_not_leak_other_users_rows(self):
        """Cross-tenant: user 1 calls list, only sees their own — even though
        user 2 has rows with the same week_start values."""
        await _ensure_user(2)
        await WeeklyReport.upsert(user_id=1, week_start=date(2026, 5, 4), content_md="user 1", model="m")
        await WeeklyReport.upsert(user_id=2, week_start=date(2026, 5, 4), content_md="user 2", model="m")
        await WeeklyReport.upsert(user_id=2, week_start=date(2026, 4, 27), content_md="user 2", model="m")

        async with _build_client(user_id=1) as c:
            resp = await c.get("/api/weekly-reports")
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["week_start"] == "2026-05-04"
        assert "user 1" in body["items"][0]["preview"]


# ---------------------------------------------------------------------------
# GET /api/weekly-reports/{week_start}
# ---------------------------------------------------------------------------


class TestGetOne:
    async def test_returns_full_content(self):
        await WeeklyReport.upsert(
            user_id=1, week_start=date(2026, 5, 4), content_md=SAMPLE_MD, model="claude-sonnet-4-6"
        )
        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports/2026-05-04")
        assert resp.status_code == 200
        body = resp.json()
        assert body["week_start"] == "2026-05-04"
        assert body["content_md"] == SAMPLE_MD
        assert body["model"] == "claude-sonnet-4-6"
        assert body["generated_at"] is not None

    async def test_404_when_missing(self):
        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports/2026-05-04")
        assert resp.status_code == 404

    async def test_404_when_row_belongs_to_other_user(self):
        """Defence-in-depth — leaked URL with someone else's week_start
        surfaces as 404, not 200. The auth-resolved user_id is the gate."""
        await _ensure_user(2)
        await WeeklyReport.upsert(user_id=2, week_start=date(2026, 5, 4), content_md="theirs", model="m")

        async with _build_client(user_id=1) as c:
            resp = await c.get("/api/weekly-reports/2026-05-04")
        assert resp.status_code == 404

    async def test_invalid_iso_date_in_path_422(self):
        """Malformed path param → 422 from FastAPI before hitting our code.
        Pinned so a future regex/string-conversion swap doesn't silently
        weaken input validation."""
        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports/not-a-date")
        assert resp.status_code == 422
