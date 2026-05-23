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
from data.db import Activity, ActivityDetail, User, WeeklyReport, Wellness, get_session


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


async def _seed_activity(
    *,
    aid: str,
    dt: date,
    sport: str,
    moving_time: int,
    tss: float,
    distance_m: float | None = None,
) -> None:
    """Insert an Activity row (+ optional ActivityDetail for distance) for user 1."""
    async with get_session() as session:
        session.add(
            Activity(
                id=aid,
                user_id=1,
                start_date_local=dt.isoformat(),
                type=sport,
                moving_time=moving_time,
                icu_training_load=tss,
            )
        )
        if distance_m is not None:
            session.add(ActivityDetail(activity_id=aid, distance=distance_m))
        await session.commit()


async def _seed_wellness(*, dt: date, ctl: float, atl: float, ramp_rate: float | None = None) -> None:
    """Insert a Wellness row for user 1 — only the CTL/ATL/ramp fields the
    Recap enrichment reads."""
    async with get_session() as session:
        session.add(Wellness(user_id=1, date=dt.isoformat(), ctl=ctl, atl=atl, ramp_rate=ramp_rate))
        await session.commit()


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
        assert body["content_md"] == SAMPLE_MD

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


# ---------------------------------------------------------------------------
# List enrichment — headline + per-week training volume / load
# ---------------------------------------------------------------------------


class TestListEnrichment:
    """Each list card carries the AI ``headline`` plus that week's training
    volume and CTL/ramp/TSB bookends — folded in when the Recap tab became
    weekly-report-driven (replacing the retired /api/weekly-recap)."""

    async def test_headline_extracted_from_h1(self):
        md = "# Threshold week, all on plan\n\n📊 **Итог недели**\n\nВыполнено 5 из 5."
        await WeeklyReport.upsert(user_id=1, week_start=date(2026, 5, 4), content_md=md, model="m")
        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports")
        item = resp.json()["items"][0]
        assert item["headline"] == "Threshold week, all on plan"

    async def test_headline_null_for_legacy_report(self):
        """Reports written before the headline prompt have no leading H1 —
        ``headline`` is null and the card falls back to ``preview``."""
        await WeeklyReport.upsert(user_id=1, week_start=date(2026, 5, 4), content_md=SAMPLE_MD, model="m")
        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports")
        item = resp.json()["items"][0]
        assert item["headline"] is None
        assert "12 из 20" in item["preview"]

    async def test_week_with_no_training_has_empty_volume(self):
        await WeeklyReport.upsert(user_id=1, week_start=date(2026, 5, 4), content_md=SAMPLE_MD, model="m")
        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports")
        item = resp.json()["items"][0]
        assert item["by_sport"] == {}
        assert item["ctl_start"] is None
        assert item["ctl_end"] is None
        assert item["ctl_delta"] is None
        assert item["ramp"] is None
        assert item["tsb_end"] is None

    async def test_volume_and_load_aggregated_for_week(self):
        # Report week 2026-05-04 (Mon) … 2026-05-10 (Sun).
        await WeeklyReport.upsert(user_id=1, week_start=date(2026, 5, 4), content_md=SAMPLE_MD, model="m")
        # Two sessions inside the week + one the following Monday (must NOT count).
        await _seed_activity(
            aid="w_ride", dt=date(2026, 5, 6), sport="Ride", moving_time=3600, tss=80.0, distance_m=30_000.0
        )
        await _seed_activity(
            aid="w_run", dt=date(2026, 5, 8), sport="Run", moving_time=1800, tss=40.0, distance_m=8_000.0
        )
        await _seed_activity(aid="next_ride", dt=date(2026, 5, 11), sport="Ride", moving_time=9999, tss=999.0)
        # CTL bookends: the day before Monday (entering) and the Sunday (exiting).
        await _seed_wellness(dt=date(2026, 5, 3), ctl=65.0, atl=50.0, ramp_rate=2.0)
        await _seed_wellness(dt=date(2026, 5, 10), ctl=70.0, atl=58.0, ramp_rate=4.5)

        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports")
        item = resp.json()["items"][0]

        assert item["by_sport"] == {
            "cycling": {"duration_sec": 3600, "distance_m": 30000.0, "tss": 80.0},
            "running": {"duration_sec": 1800, "distance_m": 8000.0, "tss": 40.0},
        }
        assert item["ctl_start"] == 65.0
        assert item["ctl_end"] == 70.0
        assert item["ctl_delta"] == 5.0
        assert item["ramp"] == 4.5  # read off the week-end wellness row
        assert item["tsb_end"] == 12.0  # 70 − 58

    async def test_sunday_activity_lands_in_its_week(self):
        """The week-end Sunday is the inclusive upper bookend — an activity on
        that Sunday buckets into that week, not the next."""
        await WeeklyReport.upsert(user_id=1, week_start=date(2026, 5, 4), content_md=SAMPLE_MD, model="m")
        # 2026-05-10 is the Sunday closing the 2026-05-04 (Mon) week.
        await _seed_activity(aid="sun_swim", dt=date(2026, 5, 10), sport="Swim", moving_time=2400, tss=55.0)
        async with _build_client() as c:
            resp = await c.get("/api/weekly-reports")
        item = resp.json()["items"][0]
        assert item["week_start"] == "2026-05-04"
        assert "swimming" in item["by_sport"]
        assert item["by_sport"]["swimming"]["tss"] == 55.0
