"""Tests for POST /api/intervals/auth/init — issue #266 bot-chat gate.

Login Widget signups land with ``user.bot_chat_initialized = False`` because
they never opened a chat with the bot. Letting OAuth complete in that state
fans out goal/wellness notifications that 400 with ``chat not found`` —
exactly what spawned issues #266/#267/#268. The gate forces those users
through /start before OAuth can dispatch any actor.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.routers.intervals.oauth import _OAUTH_INIT_MAX, _oauth_init_attempts, intervals_oauth_init

pytestmark = pytest.mark.real_db  # opt out of per-test DB truncate


def _user(
    *,
    user_id: int = 1,
    role: str = "viewer",
    bot_chat_initialized: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        role=role,
        bot_chat_initialized=bot_chat_initialized,
    )


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    """Rate-limit dict is module-level — wipe between tests."""
    _oauth_init_attempts.clear()
    yield
    _oauth_init_attempts.clear()


class TestBotChatGate:
    @pytest.mark.asyncio
    async def test_412_when_bot_chat_not_initialized(self):
        """Widget-only signup: gate must fire before any OAuth state is minted."""
        user = _user(bot_chat_initialized=False)
        with pytest.raises(HTTPException) as exc_info:
            await intervals_oauth_init(user=user)

        assert exc_info.value.status_code == 412
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["error"] == "bot_chat_not_initialized"
        assert "bot_username" in detail

    @pytest.mark.asyncio
    async def test_412_does_not_consume_rate_limit(self):
        """The gate fires before the rate-limit bookkeeping. A user stuck on the
        gate cannot accidentally exhaust their per-window OAuth attempts by
        clicking the dead button repeatedly."""
        user = _user(bot_chat_initialized=False)
        for _ in range(_OAUTH_INIT_MAX + 2):
            with pytest.raises(HTTPException) as exc_info:
                await intervals_oauth_init(user=user)
            assert exc_info.value.status_code == 412

        # No entry recorded in the rate-limit dict — gate short-circuits before it.
        assert user.id not in _oauth_init_attempts

    @pytest.mark.asyncio
    async def test_demo_role_blocked_before_bot_chat_check(self):
        """Demo gate has higher priority than the bot-chat gate."""
        user = _user(role="demo", bot_chat_initialized=False)
        with pytest.raises(HTTPException) as exc_info:
            await intervals_oauth_init(user=user)

        assert exc_info.value.status_code == 403
