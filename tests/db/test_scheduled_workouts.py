"""Tests for the scheduled workouts page feature:
- ScheduledWorkout.save_bulk() sets last_synced_at
- ScheduledWorkout.get_range() returns workouts + max last_synced_at
- GET /api/scheduled-workouts returns 7-day week structure
- POST /api/jobs/sync-workouts requires auth
- Helper functions (_format_duration, week calculation)
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from data.db import ScheduledWorkout
from data.intervals.dto import ScheduledWorkoutDTO

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(offset: int = 0) -> int:
    # Keep IDs in PostgreSQL integer range while minimizing cross-run collisions.
    return 1_000_000_000 + (uuid4().int % 900_000_000) + offset


def _make_workout(
    *,
    id: int = 0,
    dt: date = date(2026, 3, 25),
    name: str = "CYCLING:Endurance Z2",
    category: str = "WORKOUT",
    type: str = "Ride",
    description: str | None = "Warmup 10min\n3x10min @FTP\nCooldown",
    moving_time: int | None = 5400,
    distance: float | None = 45.0,
) -> ScheduledWorkoutDTO:
    if id == 0:
        id = _uid()
    return ScheduledWorkoutDTO(
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
        workout_id = _uid(1)
        before = datetime.now(timezone.utc)
        ScheduledWorkout.save_bulk(1, [_make_workout(id=workout_id)])
        after = datetime.now(timezone.utc)

        rows = await ScheduledWorkout.get_for_date(1, date(2026, 3, 25))
        target = next((r for r in rows if r.id == workout_id), None)
        assert target is not None
        assert target.last_synced_at is not None
        # Timestamp should be between before and after
        raw_ts = target.last_synced_at
        ts = raw_ts.replace(tzinfo=timezone.utc) if raw_ts.tzinfo is None else raw_ts
        assert before <= ts <= after

    async def test_updates_last_synced_at_on_upsert(self):
        workout_id = _uid(2)
        ScheduledWorkout.save_bulk(1, [_make_workout(id=workout_id, name="Old name")])
        rows = await ScheduledWorkout.get_for_date(1, date(2026, 3, 25))
        first_sync = rows[0].last_synced_at

        # Upsert same ID with new name
        ScheduledWorkout.save_bulk(1, [_make_workout(id=workout_id, name="New name")])
        rows = await ScheduledWorkout.get_for_date(1, date(2026, 3, 25))
        target = next((r for r in rows if r.id == workout_id), None)
        assert target is not None
        assert target.name == "New name"
        assert target.last_synced_at >= first_sync

    async def test_all_rows_get_last_synced_at(self):
        base = _uid(10)
        workouts = [
            _make_workout(id=base, dt=date(2026, 3, 25)),
            _make_workout(id=base + 1, dt=date(2026, 3, 26)),
            _make_workout(id=base + 2, dt=date(2026, 3, 27)),
        ]
        ScheduledWorkout.save_bulk(1, workouts)

        for dt_offset in range(3):
            dt = date(2026, 3, 25) + timedelta(days=dt_offset)
            rows = await ScheduledWorkout.get_for_date(1, dt)
            for row in rows:
                assert row.last_synced_at is not None

    async def test_empty_sync_deletes_all_rows_in_range(self):
        base_id = int(datetime.now(timezone.utc).timestamp() * 1000000) % 1000000000

        ScheduledWorkout.save_bulk(
            1,
            [
                _make_workout(id=base_id, dt=date(2026, 3, 25)),
                _make_workout(id=base_id + 1, dt=date(2026, 3, 26)),
            ],
        )

        ScheduledWorkout.save_bulk(1, [], oldest=date(2026, 3, 25), newest=date(2026, 3, 26))

        rows, _ = await ScheduledWorkout.get_range(1, date(2026, 3, 25), date(2026, 3, 26))
        assert rows == []


# ---------------------------------------------------------------------------
# get_scheduled_workouts_range
# ---------------------------------------------------------------------------


class TestGetScheduledWorkoutsRange:
    async def test_returns_workouts_in_range(self):
        base = _uid(20)
        workouts = [
            _make_workout(id=base, dt=date(2026, 3, 23)),
            _make_workout(id=base + 1, dt=date(2026, 3, 25)),
            _make_workout(id=base + 2, dt=date(2026, 3, 29)),
            _make_workout(id=base + 3, dt=date(2026, 3, 30)),  # outside range
        ]
        ScheduledWorkout.save_bulk(1, workouts)

        rows, _ = await ScheduledWorkout.get_range(1, date(2026, 3, 23), date(2026, 3, 29))
        ids = {r.id for r in rows}
        assert {base, base + 1, base + 2}.issubset(ids)
        assert (base + 3) not in ids

    async def test_returns_last_synced_at(self):
        ScheduledWorkout.save_bulk(1, [_make_workout(id=_uid(30))])

        _, last_synced = await ScheduledWorkout.get_range(1, date(2026, 3, 20), date(2026, 3, 30))
        assert last_synced is not None

    async def test_empty_range_returns_none_sync(self):
        rows, last_synced = await ScheduledWorkout.get_range(1, date(2099, 1, 1), date(2099, 1, 7))
        assert rows == []
        assert last_synced is None

    async def test_ordered_by_date(self):
        base = _uid(40)
        workouts = [
            _make_workout(id=base, dt=date(2026, 3, 27)),
            _make_workout(id=base + 1, dt=date(2026, 3, 23)),
            _make_workout(id=base + 2, dt=date(2026, 3, 25)),
        ]
        ScheduledWorkout.save_bulk(1, workouts)

        rows, _ = await ScheduledWorkout.get_range(1, date(2026, 3, 23), date(2026, 3, 29))
        dates = [r.start_date_local for r in rows]
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# API — GET /api/scheduled-workouts
# ---------------------------------------------------------------------------


class TestScheduledWorkoutsEndpoint:
    @pytest.fixture
    def client(self):
        """AsyncClient for the FastAPI app, bypassing static file mount issues."""
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
        workout_id = _uid(50)
        # Use a date in the current week (today = 2026-04-04, Monday = 2026-03-30)
        from datetime import date as _date

        today = _date.today()
        monday = today - timedelta(days=today.weekday())
        wed = monday + timedelta(days=2)  # Wednesday of current week
        ScheduledWorkout.save_bulk(1, [_make_workout(id=workout_id, dt=wed)])

        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=0")
        data = resp.json()

        # Find the day with our workout
        wed_day = next(d for d in data["days"] if d["date"] == str(wed))
        assert len(wed_day["workouts"]) >= 1
        target = next((w for w in wed_day["workouts"] if w["id"] == workout_id), None)
        assert target is not None
        assert target["type"] == "Ride"
        assert target["name"] == "CYCLING:Endurance Z2"

    async def test_empty_day_has_empty_workouts(self, client):
        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=10")
        data = resp.json()
        # Far future — no workouts
        for day in data["days"]:
            assert day["workouts"] == []

    async def test_duration_formatted(self, client):
        from datetime import date as _date

        today = _date.today()
        monday = today - timedelta(days=today.weekday())
        wed = monday + timedelta(days=2)

        workout_id = _uid(60)
        ScheduledWorkout.save_bulk(1, [_make_workout(id=workout_id, dt=wed, moving_time=5400)])

        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=0")
        data = resp.json()

        wed_day = next(d for d in data["days"] if d["date"] == str(wed))
        w = next((it for it in wed_day["workouts"] if it["id"] == workout_id), None)
        assert w is not None
        assert w["duration"] == "1h 30m"
        assert w["duration_secs"] == 5400

    async def test_last_synced_at_in_response(self, client):
        ScheduledWorkout.save_bulk(1, [_make_workout(id=_uid(70))])

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
        from datetime import date as _date

        today = _date.today()
        monday = today - timedelta(days=today.weekday())
        wed = monday + timedelta(days=2)

        desc = "Warmup 10min\n3x10min @FTP\nCooldown"
        workout_id = _uid(80)
        ScheduledWorkout.save_bulk(1, [_make_workout(id=workout_id, dt=wed, description=desc)])

        async with client as c:
            resp = await c.get("/api/scheduled-workouts?week_offset=0")
        data = resp.json()

        wed_day = next(d for d in data["days"] if d["date"] == str(wed))
        target = next((w for w in wed_day["workouts"] if w["id"] == workout_id), None)
        assert target is not None
        assert target["description"] == desc


# ---------------------------------------------------------------------------
# API — POST /api/jobs/sync-workouts
# ---------------------------------------------------------------------------


class TestSyncWorkoutsEndpoint:
    @pytest.fixture
    def client(self):
        from unittest.mock import MagicMock

        from fastapi import FastAPI

        from api.deps import require_athlete
        from api.routes import router

        test_app = FastAPI()
        test_app.include_router(router)

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.chat_id = "111"
        mock_user.username = "test"
        mock_user.athlete_id = "i001"
        mock_user.api_key = "key1"
        mock_user.role = "owner"
        mock_user.is_active = True
        test_app.dependency_overrides[require_athlete] = lambda: mock_user
        return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")

    async def test_requires_auth(self, client):
        """POST without authorization should fail (when bot token is set)."""
        from fastapi import FastAPI

        from api.routes import router

        app_no_override = FastAPI()
        app_no_override.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=app_no_override), base_url="http://test") as c:
            resp = await c.post("/api/jobs/sync-workouts")
        assert resp.status_code == 401

    async def test_dispatches_dramatiq_actor(self, client):
        """POST with valid auth dispatches dramatiq actor and returns 202."""
        with patch("api.routers.jobs.actor_user_scheduled_workouts") as mock_actor:
            async with client as c:
                resp = await c.post("/api/jobs/sync-workouts")

            assert resp.status_code == 202
            data = resp.json()
            assert data["status"] == "accepted"
            assert data["job"] == "sync-workouts"
            mock_actor.send.assert_called_once()


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
