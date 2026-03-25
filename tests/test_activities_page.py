"""Tests for the activities page feature:
- save_activities() sets last_synced_at
- get_activities_range() returns activities + max last_synced_at
- GET /api/activities-week returns 7-day week structure
- POST /api/jobs/sync-activities requires auth
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from data.database import get_activities_for_date, get_activities_range, save_activities
from data.models import Activity


def _make_activity(
    *,
    id: str = "i1001",
    dt: date = date(2026, 3, 25),
    type: str = "Ride",
    load: float | None = 85.0,
    moving_time: int = 5400,
    average_hr: float | None = 142.0,
) -> Activity:
    return Activity(
        id=id,
        start_date_local=dt,
        type=type,
        icu_training_load=load,
        moving_time=moving_time,
        average_hr=average_hr,
    )


# ---------------------------------------------------------------------------
# save_activities — last_synced_at
# ---------------------------------------------------------------------------


class TestSaveActivitiesLastSyncedAt:
    async def test_sets_last_synced_at_on_insert(self):
        before = datetime.now(timezone.utc)
        await save_activities([_make_activity(id="a2001")])
        after = datetime.now(timezone.utc)

        rows = await get_activities_for_date(date(2026, 3, 25))
        assert len(rows) == 1
        assert rows[0].last_synced_at is not None
        ts = rows[0].last_synced_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        assert before <= ts <= after

    async def test_updates_last_synced_at_on_upsert(self):
        await save_activities([_make_activity(id="a2002", load=70.0)])
        rows = await get_activities_for_date(date(2026, 3, 25))
        first_sync = rows[0].last_synced_at

        await save_activities([_make_activity(id="a2002", load=90.0)])
        rows = await get_activities_for_date(date(2026, 3, 25))
        assert rows[0].icu_training_load == 90.0
        assert rows[0].last_synced_at >= first_sync

    async def test_all_rows_get_last_synced_at(self):
        activities = [
            _make_activity(id="a2010", dt=date(2026, 3, 25)),
            _make_activity(id="a2011", dt=date(2026, 3, 26)),
            _make_activity(id="a2012", dt=date(2026, 3, 27)),
        ]
        await save_activities(activities)

        for dt_offset in range(3):
            dt = date(2026, 3, 25) + timedelta(days=dt_offset)
            rows = await get_activities_for_date(dt)
            for row in rows:
                assert row.last_synced_at is not None


# ---------------------------------------------------------------------------
# get_activities_range
# ---------------------------------------------------------------------------


class TestGetActivitiesRange:
    async def test_returns_activities_in_range(self):
        activities = [
            _make_activity(id="a3001", dt=date(2026, 3, 23)),
            _make_activity(id="a3002", dt=date(2026, 3, 25)),
            _make_activity(id="a3003", dt=date(2026, 3, 29)),
            _make_activity(id="a3004", dt=date(2026, 3, 30)),
        ]
        await save_activities(activities)

        rows, _ = await get_activities_range(date(2026, 3, 23), date(2026, 3, 29))
        ids = {r.id for r in rows}
        assert ids == {"a3001", "a3002", "a3003"}
        assert "a3004" not in ids

    async def test_returns_last_synced_at(self):
        await save_activities([_make_activity(id="a3010")])
        _, last_synced = await get_activities_range(date(2026, 3, 20), date(2026, 3, 30))
        assert last_synced is not None

    async def test_empty_range(self):
        rows, last_synced = await get_activities_range(date(2099, 1, 1), date(2099, 1, 7))
        assert rows == []
        assert last_synced is None

    async def test_ordered_by_date_and_id(self):
        activities = [
            _make_activity(id="a3022", dt=date(2026, 3, 25)),
            _make_activity(id="a3020", dt=date(2026, 3, 27)),
            _make_activity(id="a3021", dt=date(2026, 3, 23)),
        ]
        await save_activities(activities)

        rows, _ = await get_activities_range(date(2026, 3, 23), date(2026, 3, 29))
        dates = [r.start_date_local for r in rows]
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# API — GET /api/activities-week
# ---------------------------------------------------------------------------


class TestActivitiesWeekEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI

        from api.routes import router

        test_app = FastAPI()
        test_app.include_router(router)
        return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")

    async def test_returns_7_days(self, client):
        async with client as c:
            resp = await c.get("/api/activities-week?week_offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["days"]) == 7

    async def test_days_are_monday_to_sunday(self, client):
        async with client as c:
            resp = await c.get("/api/activities-week?week_offset=0")
        data = resp.json()
        weekdays = [d["weekday"] for d in data["days"]]
        assert weekdays == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    async def test_week_offset_navigation(self, client):
        async with client as c:
            resp0 = await c.get("/api/activities-week?week_offset=0")
            resp_minus1 = await c.get("/api/activities-week?week_offset=-1")
        d0 = resp0.json()
        dm1 = resp_minus1.json()
        start0 = date.fromisoformat(d0["week_start"])
        startm1 = date.fromisoformat(dm1["week_start"])
        assert start0 - startm1 == timedelta(days=7)

    async def test_activities_appear_on_correct_day(self, client):
        await save_activities([_make_activity(id="a5001", dt=date(2026, 3, 25), type="Run")])

        async with client as c:
            resp = await c.get("/api/activities-week?week_offset=0")
        data = resp.json()

        wed = next(d for d in data["days"] if d["date"] == "2026-03-25")
        assert len(wed["activities"]) >= 1
        a = wed["activities"][0]
        assert a["id"] == "a5001"
        assert a["type"] == "Run"
        assert a["duration"] == "1h 30m"
        assert a["icu_training_load"] == 85.0
        assert a["average_hr"] == 142

    async def test_empty_day_has_empty_activities(self, client):
        async with client as c:
            resp = await c.get("/api/activities-week?week_offset=10")
        data = resp.json()
        for day in data["days"]:
            assert day["activities"] == []

    async def test_today_field_from_server(self, client):
        async with client as c:
            resp = await c.get("/api/activities-week?week_offset=0")
        data = resp.json()
        assert "today" in data
        date.fromisoformat(data["today"])

    async def test_last_synced_at_in_response(self, client):
        await save_activities([_make_activity(id="a5020")])

        async with client as c:
            resp = await c.get("/api/activities-week?week_offset=0")
        data = resp.json()
        assert data["last_synced_at"] is not None


# ---------------------------------------------------------------------------
# API — POST /api/jobs/sync-activities
# ---------------------------------------------------------------------------


class TestSyncActivitiesEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI

        from api.routes import router

        test_app = FastAPI()
        test_app.include_router(router)
        return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")

    async def test_requires_auth(self, client):
        with patch("api.routes.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_TOKEN.get_secret_value.return_value = "test-token"
            mock_settings.TIMEZONE = "Europe/Belgrade"
            async with client as c:
                resp = await c.post("/api/jobs/sync-activities")
            assert resp.status_code == 401

    async def test_runs_sync_job(self, client):
        with (
            patch("api.routes.settings") as mock_settings,
            patch("api.routes._verify_request"),
            patch("api.routes.sync_activities_job", new_callable=AsyncMock) as mock_job,
        ):
            mock_settings.TIMEZONE = "Europe/Belgrade"
            await save_activities([_make_activity(id="a6001")])

            async with client as c:
                resp = await c.post("/api/jobs/sync-activities")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["last_synced_at"] is not None
            assert "synced_count" in data
            mock_job.assert_awaited_once()

    async def test_returns_502_on_failure(self, client):
        with (
            patch("api.routes._verify_request"),
            patch("api.routes.sync_activities_job", new_callable=AsyncMock, side_effect=Exception("API down")),
        ):
            async with client as c:
                resp = await c.post("/api/jobs/sync-activities")
            assert resp.status_code == 502
