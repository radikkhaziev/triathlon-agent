"""Tests for the activities page feature:
- Activity.save_bulk() sets last_synced_at
- Activity.get_range() returns activities + max last_synced_at
- GET /api/activities-week returns 7-day week structure
- POST /api/jobs/sync-activities requires auth
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from data.db import Activity
from data.intervals.dto import ActivityDTO


def _make_activity(
    *,
    id: str = "i1001",
    dt: date = date(2026, 3, 25),
    type: str = "Ride",
    load: float | None = 85.0,
    moving_time: int = 5400,
    average_hr: float | None = 142.0,
) -> ActivityDTO:
    return ActivityDTO(
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
        await Activity.save_bulk(1, activities=[_make_activity(id="a2001")])
        after = datetime.now(timezone.utc)

        rows = await Activity.get_for_date(1, date(2026, 3, 25))
        assert len(rows) == 1
        assert rows[0].last_synced_at is not None
        ts = rows[0].last_synced_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        assert before <= ts <= after

    async def test_updates_last_synced_at_on_upsert(self):
        await Activity.save_bulk(1, activities=[_make_activity(id="a2002", load=70.0)])
        rows = await Activity.get_for_date(1, date(2026, 3, 25))
        first_sync = rows[0].last_synced_at

        await Activity.save_bulk(1, activities=[_make_activity(id="a2002", load=90.0)])
        rows = await Activity.get_for_date(1, date(2026, 3, 25))
        assert rows[0].icu_training_load == 90.0
        assert rows[0].last_synced_at >= first_sync

    async def test_all_rows_get_last_synced_at(self):
        activities = [
            _make_activity(id="a2010", dt=date(2026, 3, 25)),
            _make_activity(id="a2011", dt=date(2026, 3, 26)),
            _make_activity(id="a2012", dt=date(2026, 3, 27)),
        ]
        await Activity.save_bulk(1, activities=activities)

        for dt_offset in range(3):
            dt = date(2026, 3, 25) + timedelta(days=dt_offset)
            rows = await Activity.get_for_date(1, dt)
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
        await Activity.save_bulk(1, activities=activities)

        rows, _ = await Activity.get_range(1, date(2026, 3, 23), date(2026, 3, 29))
        ids = {r.id for r in rows}
        assert ids == {"a3001", "a3002", "a3003"}
        assert "a3004" not in ids

    async def test_returns_last_synced_at(self):
        await Activity.save_bulk(1, activities=[_make_activity(id="a3010")])
        _, last_synced = await Activity.get_range(1, date(2026, 3, 20), date(2026, 3, 30))
        assert last_synced is not None

    async def test_empty_range(self):
        rows, last_synced = await Activity.get_range(1, date(2099, 1, 1), date(2099, 1, 7))
        assert rows == []
        assert last_synced is None

    async def test_ordered_by_date_and_id(self):
        activities = [
            _make_activity(id="a3022", dt=date(2026, 3, 25)),
            _make_activity(id="a3020", dt=date(2026, 3, 27)),
            _make_activity(id="a3021", dt=date(2026, 3, 23)),
        ]
        await Activity.save_bulk(1, activities=activities)

        rows, _ = await Activity.get_range(1, date(2026, 3, 23), date(2026, 3, 29))
        dates = [r.start_date_local for r in rows]
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# API — GET /api/activities-week
# ---------------------------------------------------------------------------


class TestActivitiesWeekEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI

        from api.deps import require_viewer
        from api.routes import router

        test_app = FastAPI()
        test_app.include_router(router)
        from unittest.mock import MagicMock

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.role = "owner"
        mock_user.is_active = True
        test_app.dependency_overrides[require_viewer] = lambda: mock_user
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
        today = date.today()
        today_str = today.isoformat()
        await Activity.save_bulk(1, activities=[_make_activity(id="a5001", dt=today, type="Run")])

        async with client as c:
            resp = await c.get("/api/activities-week?week_offset=0")
        data = resp.json()

        day_entry = next(d for d in data["days"] if d["date"] == today_str)
        assert len(day_entry["activities"]) >= 1
        a = day_entry["activities"][0]
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
        await Activity.save_bulk(1, activities=[_make_activity(id="a5020")])

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

        from api.deps import require_athlete
        from api.routes import router

        test_app = FastAPI()
        test_app.include_router(router)

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.role = "owner"
        mock_user.is_active = True
        mock_user.athlete_id = "i001"
        mock_user.chat_id = "111"
        mock_user.mcp_token = "test_token"
        mock_user.username = "tester"
        mock_user.api_key = "key1"
        mock_user.display_name = None
        test_app.dependency_overrides[require_athlete] = lambda: mock_user
        return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")

    async def test_requires_auth(self, client):
        from fastapi import FastAPI

        from api.routes import router

        app_no_override = FastAPI()
        app_no_override.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=app_no_override), base_url="http://test") as c:
            resp = await c.post("/api/jobs/sync-activities")
        assert resp.status_code == 401

    async def test_runs_sync_job(self, client):
        with (patch("api.routers.jobs.actor_fetch_user_activities") as mock_actor,):
            mock_actor.send = MagicMock()
            await Activity.save_bulk(1, activities=[_make_activity(id="a6001")])

            async with client as c:
                resp = await c.post("/api/jobs/sync-activities")

            assert resp.status_code == 202
            data = resp.json()
            assert data["status"] == "accepted"
            assert data["job"] == "sync-activities"
            mock_actor.send.assert_called_once()

    async def test_raises_on_dispatch_failure(self, client):
        with (patch("api.routers.jobs.actor_fetch_user_activities") as mock_actor,):
            mock_actor.send = MagicMock(side_effect=Exception("Redis down"))
            with pytest.raises(Exception, match="Redis down"):
                async with client as c:
                    await c.post("/api/jobs/sync-activities")
