"""Tests for ``Race.get_recent_for_user`` — race-plan context enrichment.

Spec RACE_PLAN_SPEC §4: race history is the single highest-ROI predictor of
next-race pacing. Recency window ``≥ today − 18 months`` with cold-start
fallback (drop the recency filter when filtered list is empty). Sport-type
filter narrows to ``Activity.type`` for mono-sport goals; tri-family goals
skip the activity-type filter (multi-sport activities don't reduce cleanly).
"""

from datetime import date, timedelta

import pytest  # noqa: F401  — pytest-asyncio collects via marker auto-discovery

from data.db import Activity, Race, get_session
from data.intervals.dto import ActivityDTO


async def _seed_activity(*, id: str, dt: date, type: str = "Run") -> None:
    await Activity.save_bulk(
        1,
        activities=[
            ActivityDTO(
                id=id,
                start_date_local=dt,
                type=type,
                icu_training_load=100.0,
                moving_time=3600,
                average_hr=150.0,
            )
        ],
    )


async def _seed_race(
    *,
    activity_id: str,
    name: str,
    distance_m: float | None = 21097.0,
    finish_time_sec: int | None = 5400,
    rpe: int | None = 8,
    user_id: int = 1,
) -> None:
    async with get_session() as session:
        session.add(
            Race(
                user_id=user_id,
                activity_id=activity_id,
                name=name,
                race_type="A",
                distance_m=distance_m,
                finish_time_sec=finish_time_sec,
                rpe=rpe,
            )
        )
        await session.commit()


class TestGetRecentForUser:
    async def test_returns_empty_for_new_user(self):
        rows = await Race.get_recent_for_user(1, sport_type="run", limit=5)
        assert rows == []

    async def test_returns_recent_with_activity_date_and_type(self):
        ref = date(2026, 3, 15)
        await _seed_activity(id="r1", dt=ref - timedelta(days=10), type="Run")
        await _seed_race(activity_id="r1", name="Belgrade Half")

        rows = await Race.get_recent_for_user(1, sport_type="run", limit=5)
        assert len(rows) == 1
        race, activity_date, activity_type = rows[0]
        assert race.name == "Belgrade Half"
        assert activity_date == (ref - timedelta(days=10)).isoformat()
        assert activity_type == "Run"

    async def test_recency_filter_excludes_old_races(self):
        ref = date(2026, 5, 9)
        await _seed_activity(id="r_old", dt=ref - timedelta(days=600), type="Run")
        await _seed_race(activity_id="r_old", name="Old Race 2024")
        await _seed_activity(id="r_new", dt=ref - timedelta(days=30), type="Run")
        await _seed_race(activity_id="r_new", name="Recent Race 2026")

        eighteen_mo_ago = ref - timedelta(days=18 * 30)
        rows = await Race.get_recent_for_user(1, sport_type="run", since=eighteen_mo_ago, limit=5)
        assert len(rows) == 1
        assert rows[0][0].name == "Recent Race 2026"

    async def test_sport_filter_run_only(self):
        ref = date(2026, 3, 15)
        await _seed_activity(id="run1", dt=ref - timedelta(days=5), type="Run")
        await _seed_race(activity_id="run1", name="Half Marathon")
        await _seed_activity(id="ride1", dt=ref - timedelta(days=10), type="Ride")
        await _seed_race(activity_id="ride1", name="Gran Fondo")

        rows = await Race.get_recent_for_user(1, sport_type="run", limit=5)
        names = [r[0].name for r in rows]
        assert names == ["Half Marathon"]

    async def test_triathlon_skips_activity_type_filter(self):
        """Tri/duathlon/aquathlon goals match any Activity.type — tri races
        may be logged as multi-sport or per-leg activities."""
        ref = date(2026, 3, 15)
        await _seed_activity(id="run_tri", dt=ref - timedelta(days=5), type="Run")
        await _seed_race(activity_id="run_tri", name="Drina 70.3")
        await _seed_activity(id="ride_tri", dt=ref - timedelta(days=15), type="Ride")
        await _seed_race(activity_id="ride_tri", name="Gran Fondo")

        rows = await Race.get_recent_for_user(1, sport_type="triathlon", limit=5)
        # Both rows returned — tri filter doesn't narrow Activity.type.
        assert len(rows) == 2

    async def test_limit_caps_result_count(self):
        ref = date(2026, 3, 15)
        for i in range(8):
            aid = f"r{i}"
            await _seed_activity(id=aid, dt=ref - timedelta(days=i * 2), type="Run")
            await _seed_race(activity_id=aid, name=f"Race {i}")

        rows = await Race.get_recent_for_user(1, sport_type="run", limit=3)
        assert len(rows) == 3

    async def test_orders_newest_first(self):
        ref = date(2026, 3, 15)
        await _seed_activity(id="a1", dt=ref - timedelta(days=30), type="Run")
        await _seed_race(activity_id="a1", name="Older")
        await _seed_activity(id="a2", dt=ref - timedelta(days=5), type="Run")
        await _seed_race(activity_id="a2", name="Newer")

        rows = await Race.get_recent_for_user(1, sport_type="run", limit=5)
        # Sort: Activity.start_date_local DESC.
        assert [r[0].name for r in rows] == ["Newer", "Older"]

    async def test_user_scope(self):
        ref = date(2026, 3, 15)
        # Other-user activity + race
        from data.db.user import User

        async with get_session() as session:
            session.add(User(id=2, chat_id="other", role="athlete"))
            await session.commit()
        await Activity.save_bulk(
            2,
            activities=[
                ActivityDTO(
                    id="other_run",
                    start_date_local=ref - timedelta(days=5),
                    type="Run",
                    icu_training_load=80.0,
                    moving_time=3600,
                    average_hr=145.0,
                )
            ],
        )
        await _seed_race(activity_id="other_run", name="Foreign Race", user_id=2)

        rows = await Race.get_recent_for_user(1, sport_type="run", limit=5)
        assert rows == []  # nothing for user 1
