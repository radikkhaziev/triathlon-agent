"""Tests for the scheduled workouts page feature:
- ScheduledWorkoutRow.save_bulk() sets last_synced_at
- ScheduledWorkoutRow.get_range() returns workouts + max last_synced_at
- GET /api/scheduled-workouts returns 7-day week structure
- POST /api/jobs/sync-workouts requires auth
- Helper functions (_format_duration, week calculation)
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from data.database import ScheduledWorkoutRow
from data.models import ScheduledWorkout

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workout(
    *,
    id: int = 1001,
    dt: date = date(2026, 3, 25),
    name: str = "CYCLING:Endurance Z2",
    category: str = "WORKOUT",
    type: str = "Ride",
    description: str | None = "Warmup 10min\n3x10min @FTP\nCooldown",
    moving_time: int | None = 5400,
    distance: float | None = 45.0,
) -> ScheduledWorkout:
    return ScheduledWorkout(
        id=id,
        start_date_local=dt,
        name=name,
        category=category,
        type=type,
        description=description,
        moving_time=moving_time,
        distance=distance,
    )


# ---------------------------------------------------------------------------
# save_scheduled_workouts — last_synced_at
# ---------------------------------------------------------------------------


class TestSaveScheduledWorkoutsLastSyncedAt:
    async def test_sets_last_synced_at_on_insert(self):
        before = datetime.now(timezone.utc)
        await ScheduledWorkoutRow.save_bulk([_make_workout(id=2001)])
        after = datetime.now(timezone.utc)

        rows = await ScheduledWorkoutRow.get_for_date(date(2026, 3, 25))
        assert len(rows) == 1
        assert rows[0].last_synced_at is not None
        # Timestamp should be between before and after
        raw_ts = rows[0].last_synced_at
        ts = raw_ts.replace(tzinfo=timezone.utc) if raw_ts.tzinfo is None else raw_ts
        assert before <= ts <= after

    async def test_updates_last_synced_at_on_upsert(self):
        await ScheduledWorkoutRow.save_bulk([_make_workout(id=2002, name="Old name")])
        rows = await ScheduledWorkoutRow.get_for_date(date(2026, 3, 25))
        first_sync = rows[0].last_synced_at

        # Upsert same ID with new name
        await ScheduledWorkoutRow.save_bulk([_make_workout(id=2002, name="New name")])
        rows = await ScheduledWorkoutRow.get_for_date(date(2026, 3, 25))
        assert rows[0].name == "New name"
        assert rows[0].last_synced_at >= first_sync

    async def test_all_rows_get_last_synced_at(self):
        workouts = [
            _make_workout(id=2010, dt=date(2026, 3, 25)),
            _make_workout(id=2011, dt=date(2026, 3, 26)),
            _make_workout(id=2012, dt=date(2026, 3, 27)),
        ]
        await ScheduledWorkoutRow.save_bulk(workouts)

        for dt_offset in range(3):
            dt = date(2026, 3, 25) + timedelta(days=dt_offset)
            rows = await ScheduledWorkoutRow.get_for_date(dt)
            for row in rows:
                assert row.last_synced_at is not None


# ---------------------------------------------------------------------------
# get_scheduled_workouts_range
# ---------------------------------------------------------------------------


class TestGetScheduledWorkoutsRange:
    async def test_returns_workouts_in_range(self):
        workouts = [
            _make_workout(id=3001, dt=date(2026, 3, 23)),
            _make_workout(id=3002, dt=date(2026, 3, 25)),
            _make_workout(id=3003, dt=date(2026, 3, 29)),
            _make_workout(id=3004, dt=date(2026, 3, 30)),  # outside range
        ]
        await ScheduledWorkoutRow.save_bulk(workouts)

        rows, _ = await ScheduledWorkoutRow.get_range(date(2026, 3, 23), date(2026, 3, 29))
        ids = {r.id for r in rows}
        assert ids == {3001, 3002, 3003}
        assert 3004 not in ids

    async def test_returns_last_synced_at(self):
        await ScheduledWorkoutRow.save_bulk([_make_workout(id=3010)])

        _, last_synced = await ScheduledWorkoutRow.get_range(date(2026, 3, 20), date(2026, 3, 30))
        assert last_synced is not None

    async def test_empty_range_returns_none_sync(self):
        rows, last_synced = await ScheduledWorkoutRow.get_range(date(2099, 1, 1), date(2099, 1, 7))
        assert rows == []
        assert last_synced is None

    async def test_ordered_by_date(self):
        workouts = [
            _make_workout(id=3020, dt=date(2026, 3, 27)),
            _make_workout(id=3021, dt=date(2026, 3, 23)),
            _make_workout(id=3022, dt=date(2026, 3, 25)),
        ]
        await ScheduledWorkoutRow.save_bulk(workouts)

        rows, _ = await ScheduledWorkoutRow.get_range(date(2026, 3, 23), date(2026, 3, 29))
        dates = [r.start_date_local for r in rows]
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# API — GET /api/scheduled-workouts
# ---------------------------------------------------------------------------


class TestScheduledWorkoutsEndpoint:
    @pytest.fixture(autouse=True)
    def _bypass_auth(self):
        with patch("api.routes._require_viewer", return_value="owner"):
            yield

    @pytest.fixture
    def client(self):
        """AsyncClient for the FastAPI app, bypassing static file mount issues."""
        from fastapi import FastAPI

        from api.routes import router

        test_app = FastAPI()
        test_app.include_router(router)
        return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")

    async def test_returns_7_days(self, client):
        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["days"]) == 7

    async def test_days_are_monday_to_sunday(self, client):
        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=0")
        data = resp.json()
        weekdays = [d["weekday"] for d in data["days"]]
        assert weekdays == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    async def test_week_offset_navigation(self, client):
        async with client as c:
            resp0 = await c.get("/api/scheduled-workouts?week_offset=0")
            resp1 = await c.get("/api/scheduled-workouts?week_offset=1")
        d0 = resp0.json()
        d1 = resp1.json()
        # Next week starts 7 days after current week
        start0 = date.fromisoformat(d0["week_start"])
        start1 = date.fromisoformat(d1["week_start"])
        assert start1 - start0 == timedelta(days=7)

    async def test_workouts_appear_on_correct_day(self, client):
        # Wednesday 2026-03-25
        await ScheduledWorkoutRow.save_bulk([_make_workout(id=5001, dt=date(2026, 3, 25))])

        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=0")
        data = resp.json()

        # Find the day with our workout
        wed = next(d for d in data["days"] if d["date"] == "2026-03-25")
        assert len(wed["workouts"]) >= 1
        assert wed["workouts"][0]["id"] == 5001
        assert wed["workouts"][0]["type"] == "Ride"
        assert wed["workouts"][0]["name"] == "CYCLING:Endurance Z2"

    async def test_empty_day_has_empty_workouts(self, client):
        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=10")
        data = resp.json()
        # Far future — no workouts
        for day in data["days"]:
            assert day["workouts"] == []

    async def test_duration_formatted(self, client):
        await ScheduledWorkoutRow.save_bulk([_make_workout(id=5010, moving_time=5400)])

        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=0")
        data = resp.json()

        wed = next(d for d in data["days"] if d["date"] == "2026-03-25")
        w = wed["workouts"][0]
        assert w["duration"] == "1h 30m"
        assert w["duration_secs"] == 5400

    async def test_last_synced_at_in_response(self, client):
        await ScheduledWorkoutRow.save_bulk([_make_workout(id=5020)])

        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=0")
        data = resp.json()
        assert data["last_synced_at"] is not None

    async def test_today_field_from_server(self, client):
        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=0")
        data = resp.json()
        assert "today" in data
        # Should be a valid date string
        date.fromisoformat(data["today"])

    async def test_includes_description(self, client):
        desc = "Warmup 10min\n3x10min @FTP\nCooldown"
        await ScheduledWorkoutRow.save_bulk([_make_workout(id=5030, description=desc)])

        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=0")
        data = resp.json()

        wed = next(d for d in data["days"] if d["date"] == "2026-03-25")
        assert wed["workouts"][0]["description"] == desc


# ---------------------------------------------------------------------------
# API — POST /api/jobs/sync-workouts
# ---------------------------------------------------------------------------


class TestSyncWorkoutsEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI

        from api.routes import router

        test_app = FastAPI()
        test_app.include_router(router)
        return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")

    async def test_requires_auth(self, client):
        """POST without authorization should fail (when bot token is set)."""
        with patch("api.routes.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_TOKEN.get_secret_value.return_value = "test-token"
            mock_settings.TIMEZONE = "Europe/Belgrade"
            async with client as c:
                resp = await c.post("/api/jobs/sync-workouts")
            assert resp.status_code == 401

    async def test_runs_sync_job(self, client):
        """POST with valid auth should run the job and return result."""
        with (
            patch("api.routes.settings") as mock_settings,
            patch("api.routes._require_owner"),
            patch("api.routes.scheduled_workouts_job", new_callable=AsyncMock) as mock_job,
        ):
            mock_settings.TIMEZONE = "Europe/Belgrade"
            # Pre-populate a workout so last_synced_at exists
            await ScheduledWorkoutRow.save_bulk([_make_workout(id=6001)])

            async with client as c:
                resp = await c.post("/api/jobs/sync-workouts")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["last_synced_at"] is not None
            assert "synced_count" in data
            mock_job.assert_awaited_once()


# ---------------------------------------------------------------------------
# _format_duration helper
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_hours_and_minutes(self):
        from data.utils import format_duration

        assert format_duration(5400) == "1h 30m"

    def test_exact_hour(self):
        from data.utils import format_duration

        assert format_duration(3600) == "1h"

    def test_minutes_only(self):
        from data.utils import format_duration

        assert format_duration(2700) == "45m"

    def test_none(self):
        from data.utils import format_duration

        assert format_duration(None) is None

    def test_zero(self):
        from data.utils import format_duration

        assert format_duration(0) == "0m"

    def test_two_hours_fifteen(self):
        from data.utils import format_duration

        assert format_duration(8100) == "2h 15m"
