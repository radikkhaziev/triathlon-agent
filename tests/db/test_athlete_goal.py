"""DB-level tests for ``AthleteGoal.upsert_from_intervals``.

The load-bearing invariant in this file: **on the update branch, an existing
row's `sport_type` MUST NOT be overwritten by the supplied value**. The user
may have fixed the sport via Settings (#323 Strand B); a re-sync from
Intervals must not stomp the user-edit. See ``athlete.py`` docstring of
``upsert_from_intervals`` for the trade-off rationale.
"""

from __future__ import annotations

from datetime import date

import pytest

from data.db import AthleteGoal


class TestUpsertFromIntervalsSportType:
    @pytest.mark.asyncio
    async def test_insert_uses_supplied_sport_type(self, _test_db):
        """First write of a new event_id stores `sport_type` as-supplied."""
        goal = await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="Spring 10K",
            event_date=date(2026, 9, 1),
            intervals_event_id=42,
            sport_type="run",
        )
        assert goal.sport_type == "run"
        assert goal.event_name == "Spring 10K"

    @pytest.mark.asyncio
    async def test_update_does_not_stomp_sport_type(self, _test_db):
        """Re-sync of the same event_id with a different `sport_type` keeps
        the original. Mirrors the «user fixed via Settings» case."""
        # Initial insert as "run"
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="Spring 10K",
            event_date=date(2026, 9, 1),
            intervals_event_id=42,
            sport_type="run",
        )
        # Re-sync as "fitness" (e.g. Intervals re-fetch where event.type → Other)
        goal = await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="Spring 10K Renamed",
            event_date=date(2026, 9, 1),
            intervals_event_id=42,
            sport_type="fitness",
        )
        # event_name was updated by re-sync, but sport_type retained
        assert goal.event_name == "Spring 10K Renamed"
        assert goal.sport_type == "run"

    @pytest.mark.asyncio
    async def test_update_no_stomp_even_when_supplied_matches(self, _test_db):
        """Re-sync with the SAME sport_type is a no-op on that field — verifies
        we don't accidentally branch on equality and stomp anyway."""
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="Tri 70.3",
            event_date=date(2026, 9, 1),
            intervals_event_id=43,
            sport_type="triathlon",
        )
        goal = await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="Tri 70.3",
            event_date=date(2026, 9, 1),
            intervals_event_id=43,
            sport_type="triathlon",
        )
        assert goal.sport_type == "triathlon"


class TestGetGoalsForPrompt:
    """`AthleteGoal.get_goals_for_prompt` returns 0/1/2 DTOs (#323 Strand D)."""

    @pytest.mark.asyncio
    async def test_no_goals_returns_empty(self, _test_db):
        out = await AthleteGoal.get_goals_for_prompt(user_id=1, today=date(2026, 5, 9))
        assert out == []

    @pytest.mark.asyncio
    async def test_only_past_goals_returns_empty(self, _test_db):
        """Past races filtered out — Claude doesn't need them in current state."""
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="Spring 10K",
            event_date=date(2026, 1, 15),
            intervals_event_id=1,
            sport_type="run",
        )
        out = await AthleteGoal.get_goals_for_prompt(user_id=1, today=date(2026, 5, 9))
        assert out == []

    @pytest.mark.asyncio
    async def test_single_race_a_returns_one(self, _test_db):
        """One RACE_A in future, no other goals → list[RACE_A]."""
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="Ironman 70.3",
            event_date=date(2026, 9, 15),
            intervals_event_id=10,
            sport_type="triathlon",
        )
        out = await AthleteGoal.get_goals_for_prompt(user_id=1, today=date(2026, 5, 9))
        assert len(out) == 1
        assert out[0].event_name == "Ironman 70.3"
        assert out[0].sport_type == "triathlon"

    @pytest.mark.asyncio
    async def test_only_race_b_returns_one(self, _test_db):
        """No RACE_A; nearest is a B → list with just B."""
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_B",
            event_name="Tune-up 10K",
            event_date=date(2026, 6, 1),
            intervals_event_id=20,
            sport_type="run",
        )
        out = await AthleteGoal.get_goals_for_prompt(user_id=1, today=date(2026, 5, 9))
        assert len(out) == 1
        assert out[0].event_name == "Tune-up 10K"

    @pytest.mark.asyncio
    async def test_race_a_is_nearest_returns_one(self, _test_db):
        """RACE_A is also the nearest race → no duplicate, just one entry."""
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="Marathon",
            event_date=date(2026, 6, 1),
            intervals_event_id=30,
            sport_type="run",
        )
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_C",
            event_name="Far-future test",
            event_date=date(2026, 12, 1),
            intervals_event_id=31,
            sport_type="run",
        )
        out = await AthleteGoal.get_goals_for_prompt(user_id=1, today=date(2026, 5, 9))
        # Only RACE_A — RACE_A IS the nearest, the C is later
        assert len(out) == 1
        assert out[0].event_name == "Marathon"

    @pytest.mark.asyncio
    async def test_race_a_with_nearer_b_returns_two(self, _test_db):
        """RACE_A is the season anchor but a B is closer → list[RACE_A, B]."""
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="Ironman 70.3",
            event_date=date(2026, 9, 15),
            intervals_event_id=40,
            sport_type="triathlon",
        )
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_B",
            event_name="Olympic Distance",
            event_date=date(2026, 6, 1),
            intervals_event_id=41,
            sport_type="triathlon",
        )
        out = await AthleteGoal.get_goals_for_prompt(user_id=1, today=date(2026, 5, 9))
        assert len(out) == 2
        # RACE_A always first
        assert out[0].event_name == "Ironman 70.3"
        # Nearest second
        assert out[1].event_name == "Olympic Distance"

    @pytest.mark.asyncio
    async def test_per_user_scoping(self, _test_db):
        """user_id=1 goals must not leak to user_id=2's prompt fetch."""
        from data.db import User
        from data.db.common import _AsyncSessionLocal

        async with _AsyncSessionLocal() as session:
            session.add(User(id=2, chat_id="test_user_2", role="athlete"))
            await session.commit()

        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="User1 Race",
            event_date=date(2026, 9, 15),
            intervals_event_id=50,
            sport_type="triathlon",
        )

        out_user1 = await AthleteGoal.get_goals_for_prompt(user_id=1, today=date(2026, 5, 9))
        out_user2 = await AthleteGoal.get_goals_for_prompt(user_id=2, today=date(2026, 5, 9))
        assert len(out_user1) == 1
        assert out_user2 == []


class TestGetGoalsForSettings:
    """`AthleteGoal.get_goals_for_settings` returns ALL active future goals
    (#323 Strand C). Distinct from `get_goals_for_prompt` which caps at 2."""

    @pytest.mark.asyncio
    async def test_no_goals_returns_empty(self, _test_db):
        out = await AthleteGoal.get_goals_for_settings(user_id=1, today=date(2026, 5, 9))
        assert out == []

    @pytest.mark.asyncio
    async def test_returns_all_active_future_goals(self, _test_db):
        """All RACE_A/B/C present (no max=2 cap, unlike the prompt helper)."""
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="A Race",
            event_date=date(2026, 9, 15),
            intervals_event_id=10,
            sport_type="triathlon",
        )
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_B",
            event_name="B Race",
            event_date=date(2026, 6, 1),
            intervals_event_id=11,
            sport_type="run",
        )
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_C",
            event_name="C Race",
            event_date=date(2026, 7, 15),
            intervals_event_id=12,
            sport_type="ride",
        )
        out = await AthleteGoal.get_goals_for_settings(user_id=1, today=date(2026, 5, 9))
        assert len(out) == 3

    @pytest.mark.asyncio
    async def test_sorted_by_event_date_asc(self, _test_db):
        """Nearest race first — Settings list shows the immediate target on top."""
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="Far",
            event_date=date(2026, 12, 1),
            intervals_event_id=20,
            sport_type="triathlon",
        )
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_B",
            event_name="Near",
            event_date=date(2026, 6, 1),
            intervals_event_id=21,
            sport_type="run",
        )
        out = await AthleteGoal.get_goals_for_settings(user_id=1, today=date(2026, 5, 9))
        assert [g.event_name for g in out] == ["Near", "Far"]

    @pytest.mark.asyncio
    async def test_past_goals_filtered_out(self, _test_db):
        """Past races excluded — the Settings list view only edits future races."""
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="Old",
            event_date=date(2026, 1, 15),
            intervals_event_id=30,
            sport_type="run",
        )
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_B",
            event_name="Future",
            event_date=date(2026, 9, 1),
            intervals_event_id=31,
            sport_type="run",
        )
        out = await AthleteGoal.get_goals_for_settings(user_id=1, today=date(2026, 5, 9))
        assert [g.event_name for g in out] == ["Future"]

    @pytest.mark.asyncio
    async def test_dto_carries_category(self, _test_db):
        """DTO must expose `category` so the Settings list view can show
        the RACE_A/B/C badge on each card."""
        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_B",
            event_name="Olympic",
            event_date=date(2026, 6, 1),
            intervals_event_id=40,
            sport_type="triathlon",
        )
        out = await AthleteGoal.get_goals_for_settings(user_id=1, today=date(2026, 5, 9))
        assert len(out) == 1
        assert out[0].category == "RACE_B"

    @pytest.mark.asyncio
    async def test_per_user_scoping(self, _test_db):
        from data.db import User
        from data.db.common import _AsyncSessionLocal

        async with _AsyncSessionLocal() as session:
            session.add(User(id=2, chat_id="test_user_2", role="athlete"))
            await session.commit()

        await AthleteGoal.upsert_from_intervals(
            user_id=1,
            category="RACE_A",
            event_name="User1",
            event_date=date(2026, 9, 1),
            intervals_event_id=50,
            sport_type="run",
        )
        out1 = await AthleteGoal.get_goals_for_settings(user_id=1, today=date(2026, 5, 9))
        out2 = await AthleteGoal.get_goals_for_settings(user_id=2, today=date(2026, 5, 9))
        assert len(out1) == 1
        assert out2 == []
