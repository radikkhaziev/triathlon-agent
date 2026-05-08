"""DB-level tests for AthleteSettings.get_thresholds and User.update_sports.

Mocked tests in ``tests/api/test_auth_me.py`` exercise the API response shape
but skip the row-iteration filter inside ``get_thresholds`` and the JSON
column round-trip on ``users.sports``. These are the load-bearing pieces:
- the filter decides which Intervals.icu sport-types prefill the SportsPicker
- the round-trip is the only persistence path for the gate
"""

from __future__ import annotations

import pytest

from data.db import AthleteSettings, User


class TestGetThresholdsAvailableSports:
    """``available_sports`` reflects only sport-rows in the lowercase enum."""

    @pytest.mark.asyncio
    async def test_triathlete_returns_all_three(self, _test_db):
        await AthleteSettings.upsert(user_id=1, sport="Run", lthr=170)
        await AthleteSettings.upsert(user_id=1, sport="Ride", lthr=150, ftp=240)
        await AthleteSettings.upsert(user_id=1, sport="Swim", threshold_pace=90.0)

        t = await AthleteSettings.get_thresholds(user_id=1)
        assert t.available_sports == ["ride", "run", "swim"]

    @pytest.mark.asyncio
    async def test_run_only_athlete(self, _test_db):
        await AthleteSettings.upsert(user_id=1, sport="Run", lthr=170)

        t = await AthleteSettings.get_thresholds(user_id=1)
        assert t.available_sports == ["run"]

    @pytest.mark.asyncio
    async def test_filters_unmapped_intervals_disciplines(self, _test_db):
        """Intervals.icu returns rows for sport types we don't model
        (``Yoga``, ``Workout``, ``WeightTraining``). They must NOT bleed into
        ``available_sports`` — the SportsPicker enum is restricted to
        {swim, ride, run}, so an unmapped row shouldn't pre-tick anything."""
        await AthleteSettings.upsert(user_id=1, sport="Run", lthr=170)
        await AthleteSettings.upsert(user_id=1, sport="Yoga")
        await AthleteSettings.upsert(user_id=1, sport="Workout")
        await AthleteSettings.upsert(user_id=1, sport="WeightTraining")

        t = await AthleteSettings.get_thresholds(user_id=1)
        assert t.available_sports == ["run"]

    @pytest.mark.asyncio
    async def test_no_settings_returns_empty(self, _test_db):
        """New athlete with no sport-rows synced yet — picker opens unchecked."""
        t = await AthleteSettings.get_thresholds(user_id=1)
        assert t.available_sports == []

    @pytest.mark.asyncio
    async def test_canonical_alphabetical_order(self, _test_db):
        """Order mustn't depend on the SQL row-iteration order — frontend
        compares with ``Array.includes`` so any order works at runtime, but a
        canonical alphabetical sort keeps response payloads diff-stable."""
        # Insert in non-alphabetical order
        await AthleteSettings.upsert(user_id=1, sport="Swim", threshold_pace=90.0)
        await AthleteSettings.upsert(user_id=1, sport="Run", lthr=170)
        await AthleteSettings.upsert(user_id=1, sport="Ride", lthr=150, ftp=240)

        t = await AthleteSettings.get_thresholds(user_id=1)
        assert t.available_sports == ["ride", "run", "swim"]


class TestUserUpdateSports:
    """``User.update_sports`` is the only writer for the SportsPicker gate."""

    @pytest.mark.asyncio
    async def test_round_trip_persists_list(self, _test_db):
        """JSON column stores and returns the same Python list."""
        await User.update_sports(user_id=1, sports=["run", "ride"])

        t = await AthleteSettings.get_thresholds(user_id=1)
        assert t.sports == ["run", "ride"]

    @pytest.mark.asyncio
    async def test_round_trip_canonical_input_unchanged(self, _test_db):
        """Already-canonical list (sorted, no dupes) round-trips bit-for-bit."""
        await User.update_sports(user_id=1, sports=["ride", "run", "swim"])

        t = await AthleteSettings.get_thresholds(user_id=1)
        assert t.sports == ["ride", "run", "swim"]

    @pytest.mark.asyncio
    async def test_overwrites_previous_value(self, _test_db):
        """Each PUT is full-replace, not partial merge."""
        await User.update_sports(user_id=1, sports=["swim", "ride", "run"])
        await User.update_sports(user_id=1, sports=["run"])

        t = await AthleteSettings.get_thresholds(user_id=1)
        assert t.sports == ["run"]

    @pytest.mark.asyncio
    async def test_per_user_scoping(self, _test_db):
        """Update on user_id=1 must not bleed into user_id=2."""
        from data.db.common import _AsyncSessionLocal

        async with _AsyncSessionLocal() as session:
            session.add(User(id=2, chat_id="test_user_2", role="athlete"))
            await session.commit()

        await User.update_sports(user_id=1, sports=["run"])
        await User.update_sports(user_id=2, sports=["swim"])

        t1 = await AthleteSettings.get_thresholds(user_id=1)
        t2 = await AthleteSettings.get_thresholds(user_id=2)
        assert t1.sports == ["run"]
        assert t2.sports == ["swim"]
