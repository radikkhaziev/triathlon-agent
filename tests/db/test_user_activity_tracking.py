"""Tests for ``User.touch_last_action`` + ``User.deactivate_stale``.

Stale-user deactivation is the morning-report token-spend guard:
``scheduler_deactivate_inactive_users_job`` runs once a day and flips
``is_active=False`` for accounts whose ``last_action_at`` has fallen behind
the threshold (default 30 days). Touch is called from every Telegram
handler (``user_required`` / ``athlete_required``) and every authenticated
webapp request (``get_current_user``).
"""

from datetime import datetime, timedelta, timezone

from data.db import User
from data.db.common import get_session


async def _seed_user(*, user_id: int, last_action_at: datetime | None, is_active: bool = True) -> None:
    async with get_session() as session:
        existing = await session.get(User, user_id)
        if existing is None:
            session.add(
                User(
                    id=user_id,
                    chat_id=f"chat_{user_id}",
                    role="athlete",
                    is_active=is_active,
                    last_action_at=last_action_at,
                )
            )
        else:
            existing.is_active = is_active
            existing.last_action_at = last_action_at
        await session.commit()


async def _get(user_id: int) -> User:
    async with get_session() as session:
        return await session.get(User, user_id)


class TestTouchLastAction:
    async def test_bumps_to_now(self, _test_db):
        """``last_action_at`` after touch is within seconds of ``datetime.now``."""
        now = datetime.now(timezone.utc)
        await _seed_user(user_id=1, last_action_at=now - timedelta(days=10))

        await User.touch_last_action(1)

        u = await _get(1)
        assert u.last_action_at is not None
        # Should be very fresh — within 10 seconds of now.
        assert (datetime.now(timezone.utc) - u.last_action_at) < timedelta(seconds=10)

    async def test_overwrites_null(self, _test_db):
        """A row with ``last_action_at=NULL`` (post-migration edge) gets a value."""
        await _seed_user(user_id=1, last_action_at=None)

        await User.touch_last_action(1)

        u = await _get(1)
        assert u.last_action_at is not None


class TestDeactivateStale:
    async def test_stale_user_deactivated(self, _test_db):
        """31 days idle → deactivated and id returned."""
        stale_dt = datetime.now(timezone.utc) - timedelta(days=31)
        await _seed_user(user_id=1, last_action_at=stale_dt, is_active=True)

        ids = await User.deactivate_stale(threshold_days=30)

        assert 1 in ids
        u = await _get(1)
        assert u.is_active is False

    async def test_fresh_user_kept_active(self, _test_db):
        """29 days idle → still active (under threshold)."""
        fresh_dt = datetime.now(timezone.utc) - timedelta(days=29)
        await _seed_user(user_id=1, last_action_at=fresh_dt, is_active=True)

        ids = await User.deactivate_stale(threshold_days=30)

        assert 1 not in ids
        u = await _get(1)
        assert u.is_active is True

    async def test_already_inactive_skipped(self, _test_db):
        """Already-deactivated users don't get touched (idempotent run)."""
        stale_dt = datetime.now(timezone.utc) - timedelta(days=60)
        await _seed_user(user_id=1, last_action_at=stale_dt, is_active=False)

        ids = await User.deactivate_stale(threshold_days=30)

        assert 1 not in ids
        u = await _get(1)
        assert u.is_active is False

    async def test_custom_threshold(self, _test_db):
        """``threshold_days`` is a knob — a 7-day threshold catches 10-day-idle users."""
        idle_dt = datetime.now(timezone.utc) - timedelta(days=10)
        await _seed_user(user_id=1, last_action_at=idle_dt, is_active=True)

        ids = await User.deactivate_stale(threshold_days=7)

        assert 1 in ids

    async def test_null_last_action_treated_as_stale(self, _test_db):
        """A row that never recorded an interaction is stale by definition.
        The ``NULL < timestamp`` quirk in SQL (always falsy) is handled by an
        explicit ``IS NULL`` branch — without it, never-touched rows would
        be invisible to the deactivation cron forever.
        """
        await _seed_user(user_id=1, last_action_at=None, is_active=True)

        ids = await User.deactivate_stale(threshold_days=30)

        assert 1 in ids
        u = await _get(1)
        assert u.is_active is False
