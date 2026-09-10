"""DB-level tests for User.update_sports.

The JSON-column round-trip on ``users.sports`` is the only persistence path
for the SportsPicker gate; covered here against a real test DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from data.db import AthleteSettings, User

_DAY = timedelta(hours=24)


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


class TestIsStale:
    """``AthleteSettings.is_stale`` gates the missed-webhook API fetch in ``actor_user_wellness``."""

    async def _age_rows(self, user_id: int, *, hours: float) -> None:
        # Imported inside — the ``_test_db`` fixture rebinds the session factory.
        from data.db.common import _AsyncSessionLocal

        async with _AsyncSessionLocal() as session:
            await session.execute(
                update(AthleteSettings)
                .where(AthleteSettings.user_id == user_id)
                .values(synced_at=datetime.now(timezone.utc) - timedelta(hours=hours))
            )
            await session.commit()

    @pytest.mark.asyncio
    async def test_no_rows_is_stale(self, _test_db):
        assert await AthleteSettings.is_stale(user_id=1, max_age=_DAY) is True

    @pytest.mark.asyncio
    async def test_fresh_upsert_is_not_stale(self, _test_db):
        await AthleteSettings.upsert(user_id=1, sport="Ride", ftp=250)
        assert await AthleteSettings.is_stale(user_id=1, max_age=_DAY) is False

    @pytest.mark.asyncio
    async def test_old_synced_at_is_stale(self, _test_db):
        await AthleteSettings.upsert(user_id=1, sport="Ride", ftp=250)
        await self._age_rows(1, hours=25)
        assert await AthleteSettings.is_stale(user_id=1, max_age=_DAY) is True

    @pytest.mark.asyncio
    async def test_newest_row_wins(self, _test_db):
        """One fresh sport row is enough — every sync (webhook or API) writes all sports."""
        await AthleteSettings.upsert(user_id=1, sport="Ride", ftp=250)
        await self._age_rows(1, hours=25)
        await AthleteSettings.upsert(user_id=1, sport="Run", lthr=160)
        assert await AthleteSettings.is_stale(user_id=1, max_age=_DAY) is False

    @pytest.mark.asyncio
    async def test_per_user_scoping(self, _test_db):
        from data.db.common import _AsyncSessionLocal

        async with _AsyncSessionLocal() as session:
            session.add(User(id=2, chat_id="test_user_2", role="athlete"))
            await session.commit()
        await AthleteSettings.upsert(user_id=2, sport="Ride", ftp=250)
        assert await AthleteSettings.is_stale(user_id=1, max_age=_DAY) is True
        assert await AthleteSettings.is_stale(user_id=2, max_age=_DAY) is False

    @pytest.mark.asyncio
    async def test_sync_twin_matches(self, _test_db):
        """The only production caller (``actor_user_wellness``) runs the sync
        branch of ``@dual`` — exercise it off-loop via a worker thread."""
        import asyncio

        assert await asyncio.to_thread(AthleteSettings.is_stale, 1, max_age=_DAY) is True
        await AthleteSettings.upsert(user_id=1, sport="Ride", ftp=250)
        assert await asyncio.to_thread(AthleteSettings.is_stale, 1, max_age=_DAY) is False
