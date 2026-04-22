"""Tests for scheduler_watchdog_bootstrap — stuck-state rescue with escalation.

Behaviour under test:
- No stuck rows → no action
- Stuck row with kick_count < MAX → re-dispatch actor + bump counter
- Stuck row with kick_count >= MAX → mark_failed (no dispatch)
- Stuck row with missing/inactive user → skip
- ``_parse_kick_count`` semantics (sentinels other than watchdog_kick_N → 0)
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.scheduler import _BOOTSTRAP_MAX_WATCHDOG_KICKS, _parse_kick_count, scheduler_watchdog_bootstrap

pytestmark = pytest.mark.real_db  # mocks only; skips DB truncate


def _stuck_state(
    *,
    user_id: int = 1,
    last_error: str | None = None,
    chunks_done: int = 5,
    cursor_dt: date | None = None,
    period_days: int = 365,
) -> SimpleNamespace:
    today = date.today()
    return SimpleNamespace(
        user_id=user_id,
        status="running",
        cursor_dt=cursor_dt or (today - timedelta(days=200)),
        oldest_dt=today - timedelta(days=365),
        newest_dt=today - timedelta(days=1),
        chunks_done=chunks_done,
        last_error=last_error,
        period_days=period_days,
    )


# ---------------------------------------------------------------------------
# _parse_kick_count
# ---------------------------------------------------------------------------


class TestParseKickCount:
    def test_none_zero(self):
        assert _parse_kick_count(None) == 0

    def test_empty_zero(self):
        assert _parse_kick_count("") == 0

    def test_watchdog_kick_parses(self):
        assert _parse_kick_count("watchdog_kick_1") == 1
        assert _parse_kick_count("watchdog_kick_7") == 7

    def test_unrelated_sentinel_zero(self):
        """EMPTY_INTERVALS, OAuth revoked, etc. don't accidentally roll into
        the kick counter — they're fundamentally different states we don't
        want to touch."""
        assert _parse_kick_count("EMPTY_INTERVALS") == 0
        assert _parse_kick_count("OAuth revoked during backfill") == 0

    def test_malformed_counter_zero(self):
        assert _parse_kick_count("watchdog_kick_abc") == 0
        assert _parse_kick_count("watchdog_kick_") == 0


# ---------------------------------------------------------------------------
# scheduler_watchdog_bootstrap — main behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def watchdog_mocks():
    with (
        patch("bot.scheduler.UserBackfillState") as state_cls,
        patch("bot.scheduler.get_session") as session_cm,
        patch("bot.scheduler.actor_bootstrap_step") as actor,
        patch("bot.scheduler._UserAdapter") as adapter,
    ):
        session = MagicMock()
        session.get = AsyncMock(return_value=SimpleNamespace(id=1, is_active=True))
        session_cm.return_value.__aenter__ = AsyncMock(return_value=session)
        session_cm.return_value.__aexit__ = AsyncMock(return_value=None)

        state_cls.list_stuck = AsyncMock(return_value=[])
        state_cls.bump_watchdog_kick = AsyncMock()
        state_cls.mark_failed = AsyncMock()

        adapter.validate_python = MagicMock(return_value=SimpleNamespace(id=1))

        yield SimpleNamespace(state_cls=state_cls, actor=actor, session=session)


class TestWatchdog:
    @pytest.mark.asyncio
    async def test_no_stuck_rows_noop(self, watchdog_mocks):
        watchdog_mocks.state_cls.list_stuck = AsyncMock(return_value=[])
        await scheduler_watchdog_bootstrap()
        watchdog_mocks.actor.send.assert_not_called()
        watchdog_mocks.state_cls.bump_watchdog_kick.assert_not_awaited()
        watchdog_mocks.state_cls.mark_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_first_kick_dispatches_and_bumps_counter(self, watchdog_mocks):
        stuck = _stuck_state(last_error=None)
        watchdog_mocks.state_cls.list_stuck = AsyncMock(return_value=[stuck])
        await scheduler_watchdog_bootstrap()

        watchdog_mocks.state_cls.bump_watchdog_kick.assert_awaited_once_with(
            user_id=stuck.user_id,
            kick_number=1,
        )
        watchdog_mocks.actor.send.assert_called_once()
        watchdog_mocks.state_cls.mark_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_existing_kick_counter_increments(self, watchdog_mocks):
        stuck = _stuck_state(last_error="watchdog_kick_1")
        watchdog_mocks.state_cls.list_stuck = AsyncMock(return_value=[stuck])
        await scheduler_watchdog_bootstrap()

        watchdog_mocks.state_cls.bump_watchdog_kick.assert_awaited_once_with(
            user_id=stuck.user_id,
            kick_number=2,
        )
        watchdog_mocks.actor.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_exhausted_kicks_mark_failed_no_dispatch(self, watchdog_mocks):
        stuck = _stuck_state(last_error=f"watchdog_kick_{_BOOTSTRAP_MAX_WATCHDOG_KICKS}")
        watchdog_mocks.state_cls.list_stuck = AsyncMock(return_value=[stuck])
        await scheduler_watchdog_bootstrap()

        watchdog_mocks.state_cls.mark_failed.assert_awaited_once()
        mark_kwargs = watchdog_mocks.state_cls.mark_failed.await_args.kwargs
        assert mark_kwargs["user_id"] == stuck.user_id
        assert mark_kwargs["error"] == "watchdog_exhausted"

        watchdog_mocks.actor.send.assert_not_called()
        watchdog_mocks.state_cls.bump_watchdog_kick.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_user_skipped(self, watchdog_mocks):
        stuck = _stuck_state(user_id=42)
        watchdog_mocks.state_cls.list_stuck = AsyncMock(return_value=[stuck])
        watchdog_mocks.session.get = AsyncMock(return_value=None)

        await scheduler_watchdog_bootstrap()
        watchdog_mocks.actor.send.assert_not_called()
        watchdog_mocks.state_cls.bump_watchdog_kick.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inactive_user_skipped(self, watchdog_mocks):
        stuck = _stuck_state(user_id=42)
        watchdog_mocks.state_cls.list_stuck = AsyncMock(return_value=[stuck])
        watchdog_mocks.session.get = AsyncMock(return_value=SimpleNamespace(id=42, is_active=False))

        await scheduler_watchdog_bootstrap()
        watchdog_mocks.actor.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_stuck_rows_processed_independently(self, watchdog_mocks):
        stuck_rows = [
            _stuck_state(user_id=1, last_error=None),
            _stuck_state(user_id=2, last_error=f"watchdog_kick_{_BOOTSTRAP_MAX_WATCHDOG_KICKS}"),
        ]
        watchdog_mocks.state_cls.list_stuck = AsyncMock(return_value=stuck_rows)

        # session.get returns a valid user for any lookup
        watchdog_mocks.session.get = AsyncMock(
            side_effect=[
                SimpleNamespace(id=1, is_active=True),
                SimpleNamespace(id=2, is_active=True),
            ]
        )
        await scheduler_watchdog_bootstrap()

        # user 1 → kicked; user 2 → failed
        watchdog_mocks.actor.send.assert_called_once()
        assert watchdog_mocks.state_cls.bump_watchdog_kick.await_count == 1
        watchdog_mocks.state_cls.mark_failed.assert_awaited_once()
