"""DB-level tests for User.update_sports.

The JSON-column round-trip on ``users.sports`` is the only persistence path
for the SportsPicker gate; covered here against a real test DB.
"""

from __future__ import annotations

import pytest

from data.db import AthleteSettings, User


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
