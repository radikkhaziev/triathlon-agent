"""Tests for GET /api/auth/me — bot-chat fields exposure (issue #266).

The frontend's banner (App.tsx) and Settings/OnboardingPrompt CTAs read
``bot_chat_initialized`` + ``bot_username`` from this endpoint. If the
contract drifts, the entire UX layer of the #266 fix silently breaks (no
backend gate change, but the user never sees the /start prompt).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routers.auth import auth_me

pytestmark = pytest.mark.real_db  # opt out of per-test DB truncate


def _user(
    *,
    user_id: int = 1,
    role: str = "athlete",
    athlete_id: str | None = "i001",
    bot_chat_initialized: bool = True,
    intervals_auth_method: str = "oauth",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        role=role,
        athlete_id=athlete_id,
        bot_chat_initialized=bot_chat_initialized,
        intervals_auth_method=intervals_auth_method,
        intervals_oauth_scope=None,
        language="ru",
    )


@pytest.fixture
def _stub_athlete_lookups():
    """``auth_me`` calls AthleteSettings.get_thresholds + AthleteGoal.get_goal_dto.
    Tests don't care about either — return empty stubs so the body builds."""
    thresholds = SimpleNamespace(
        age=None,
        lthr_run=None,
        lthr_bike=None,
        ftp=None,
        css=None,
        threshold_pace_run=None,
    )
    with (
        patch("api.routers.auth.AthleteSettings.get_thresholds", new=AsyncMock(return_value=thresholds)),
        patch("api.routers.auth.AthleteGoal.get_goal_dto", new=AsyncMock(return_value=None)),
    ):
        yield


class TestBotChatFieldsExposed:
    @pytest.mark.asyncio
    async def test_bot_chat_initialized_true_propagates(self, _stub_athlete_lookups):
        result = await auth_me(user=_user(bot_chat_initialized=True))
        assert result["bot_chat_initialized"] is True

    @pytest.mark.asyncio
    async def test_bot_chat_initialized_false_propagates(self, _stub_athlete_lookups):
        """The False case is the entire point — frontend banner depends on it."""
        result = await auth_me(user=_user(bot_chat_initialized=False))
        assert result["bot_chat_initialized"] is False

    @pytest.mark.asyncio
    async def test_bot_username_exposed_from_settings(self, _stub_athlete_lookups):
        with patch("api.routers.auth.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_USERNAME = "endurai_test_bot"
            result = await auth_me(user=_user())
        assert result["bot_username"] == "endurai_test_bot"

    @pytest.mark.asyncio
    async def test_demo_role_pins_bot_chat_initialized_true(self, _stub_athlete_lookups):
        """Demo browses owner data read-only; no Telegram I/O ever happens, so
        a False value would surface a meaningless /start CTA. Pin to True."""
        result = await auth_me(user=_user(role="demo", bot_chat_initialized=False))
        assert result["bot_chat_initialized"] is True
