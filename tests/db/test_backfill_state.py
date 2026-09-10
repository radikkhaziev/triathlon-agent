"""DB-level tests for ``UserBackfillState.mark_quota_paused`` and its
interaction with ``advance_cursor`` / status guards."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from data.db import UserBackfillState
from data.db.backfill import QUOTA_PAUSED_PREFIX, parse_quota_pause

_OLDEST = date(2025, 9, 10)
_NEWEST = date(2026, 9, 9)


async def _start(user_id: int = 1) -> None:
    await UserBackfillState.start(user_id=user_id, period_days=365, oldest_dt=_OLDEST, newest_dt=_NEWEST)


class TestMarkQuotaPaused:
    @pytest.mark.asyncio
    async def test_stamps_resume_time_on_running_row(self, _test_db):
        await _start()
        resume = datetime.now(timezone.utc) + timedelta(hours=6)

        await UserBackfillState.mark_quota_paused(user_id=1, resume_at=resume)

        row = await UserBackfillState.get(user_id=1)
        assert row.status == "running"
        assert row.last_error.startswith(QUOTA_PAUSED_PREFIX)
        assert parse_quota_pause(row.last_error) == resume

    @pytest.mark.asyncio
    async def test_advance_cursor_clears_pause(self, _test_db):
        await _start()
        await UserBackfillState.mark_quota_paused(user_id=1, resume_at=datetime.now(timezone.utc))

        await UserBackfillState.advance_cursor(user_id=1, cursor_dt=_OLDEST + timedelta(days=30))

        row = await UserBackfillState.get(user_id=1)
        assert row.last_error is None
        assert row.chunks_done == 1

    @pytest.mark.asyncio
    async def test_ignores_non_running_row(self, _test_db):
        await _start()
        await UserBackfillState.mark_failed(user_id=1, error="watchdog_exhausted")

        await UserBackfillState.mark_quota_paused(user_id=1, resume_at=datetime.now(timezone.utc))

        row = await UserBackfillState.get(user_id=1)
        assert row.status == "failed"
        assert row.last_error == "watchdog_exhausted"

    @pytest.mark.asyncio
    async def test_overwrites_watchdog_kick_counter(self, _test_db):
        """A pause is not a stuck chain — the escalation counter restarts."""
        await _start()
        await UserBackfillState.bump_watchdog_kick(user_id=1, kick_number=2)

        await UserBackfillState.mark_quota_paused(user_id=1, resume_at=datetime.now(timezone.utc))

        row = await UserBackfillState.get(user_id=1)
        assert row.last_error.startswith(QUOTA_PAUSED_PREFIX)


class TestAdvanceCursorCas:
    @pytest.mark.asyncio
    async def test_matching_expected_cursor_advances(self, _test_db):
        await _start()
        ok = await UserBackfillState.advance_cursor(
            user_id=1, cursor_dt=_OLDEST + timedelta(days=30), expected_cursor=_OLDEST
        )
        assert ok is True
        row = await UserBackfillState.get(user_id=1)
        assert row.cursor_dt == _OLDEST + timedelta(days=30)
        assert row.chunks_done == 1

    @pytest.mark.asyncio
    async def test_stale_expected_cursor_is_rejected(self, _test_db):
        """Second copy of the chain loses the race: no advance, no double bump."""
        await _start()
        await UserBackfillState.advance_cursor(
            user_id=1, cursor_dt=_OLDEST + timedelta(days=30), expected_cursor=_OLDEST
        )

        ok = await UserBackfillState.advance_cursor(
            user_id=1, cursor_dt=_OLDEST + timedelta(days=30), expected_cursor=_OLDEST
        )

        assert ok is False
        row = await UserBackfillState.get(user_id=1)
        assert row.cursor_dt == _OLDEST + timedelta(days=30)
        assert row.chunks_done == 1

    @pytest.mark.asyncio
    async def test_no_expected_cursor_keeps_unconditional_semantics(self, _test_db):
        await _start()
        ok = await UserBackfillState.advance_cursor(user_id=1, cursor_dt=_OLDEST + timedelta(days=30))
        assert ok is True
