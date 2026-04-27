"""Tests for handle_ramp_callback in bot/main.py.

Issue #277: ``plan_ramp`` calls @dual ORM methods that detect a running event
loop and return coroutines. Inside the bot handler there *is* a loop, so a
direct ``ramp.plan_ramp(...)`` returns a coroutine instead of a string. The
fix wraps the call in ``asyncio.to_thread`` so @dual dispatches to the sync
branch.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.main import handle_ramp_callback


def _update(sport: str = "Run"):
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.data = f"ramp_test:{sport}"
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    return update, query


def _user():
    return SimpleNamespace(
        id=1,
        chat_id="111",
        username="tester",
        athlete_id="i001",
        is_active=True,
        language="ru",
    )


def _dual_like_plan_ramp(*, sport: str):
    """Mimic @dual dispatch: coroutine if a loop is running here, string otherwise.

    This is the actual class of bug from #277 — ``plan_ramp`` calls @dual ORM
    methods, so its return value depends on whether the caller is on a thread
    with a running event loop. The fix uses ``asyncio.to_thread`` to push the
    call onto a worker thread (no loop) so @dual takes the sync branch.
    """
    try:
        asyncio.get_running_loop()

        async def _coro():
            return f"async-{sport}"

        return _coro()
    except RuntimeError:
        return f"sync-{sport}"


class TestHandleRampCallback:
    @pytest.mark.asyncio
    async def test_dispatches_through_to_thread_so_dual_takes_sync_branch(self):
        """Pre-fix: direct ``ramp.plan_ramp(...)`` ran on the async thread, so @dual
        returned a coroutine and ``f"⚡ {coro}"`` produced ``<coroutine object ...>``.
        Post-fix: ``asyncio.to_thread`` moves the call to a worker thread (no loop)
        and @dual dispatches to the sync branch, returning a real string.
        """
        update, query = _update(sport="Run")
        ctx = MagicMock()

        ramp_instance = MagicMock()
        ramp_instance.plan_ramp = MagicMock(side_effect=_dual_like_plan_ramp)

        with patch("bot.main.RampTrainingSuggestion", return_value=ramp_instance):
            await handle_ramp_callback.__wrapped__(update, ctx, user=_user())

        ramp_instance.plan_ramp.assert_called_once_with(sport="Run")
        query.message.reply_text.assert_awaited_once()
        body = query.message.reply_text.await_args.args[0]
        assert body == "⚡ sync-Run"
        assert "coroutine" not in body

    @pytest.mark.asyncio
    async def test_default_sport_when_callback_data_missing_colon(self):
        update, query = _update(sport="Run")
        update.callback_query.data = "ramp_test"  # no colon → fallback to Run
        ctx = MagicMock()

        ramp_instance = MagicMock()
        ramp_instance.plan_ramp = MagicMock(return_value="ok")

        with patch("bot.main.RampTrainingSuggestion", return_value=ramp_instance):
            await handle_ramp_callback.__wrapped__(update, ctx, user=_user())

        ramp_instance.plan_ramp.assert_called_once_with(sport="Run")
