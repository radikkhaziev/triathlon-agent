"""Tests for the dormant-user wake-up flow in `bot/decorator.py`.

`scheduler_deactivate_inactive_users_job` flips `is_active=False` for users
idle 30+ days; if they later send any message, the `athlete_required` /
`user_required` decorators must reactivate them in-flight rather than
bouncing with "Сначала отправьте /start". This file pins down both the
happy path (dormant athlete → reactivated, handler runs) and the bounce
paths (missing row / no athlete_id) that the wake-up must NOT swallow.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.decorator import athlete_required, user_required


def _update(chat_id: int = 999) -> SimpleNamespace:
    """Minimal `Update` stand-in — message-only path (callback_query=None)."""
    message = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=chat_id),
        callback_query=None,
        message=message,
    )


def _user(*, is_active: bool, athlete_id: str | None = "i001", chat_id: str = "999") -> MagicMock:
    """ORM-shaped `User` stand-in. MagicMock so `set_active_by_chat_id` can
    mutate `user.is_active` and the wrapper can observe the new state."""
    user = MagicMock()
    user.id = 1
    user.chat_id = chat_id
    user.athlete_id = athlete_id
    user.is_active = is_active
    user.language = "ru"
    return user


class TestAthleteRequiredWakeUp:
    @pytest.mark.asyncio
    async def test_dormant_athlete_is_reactivated_and_handler_runs(self):
        """Dormant athlete (`is_active=False`) sends a command → flip to True,
        touch_last_action, handler invoked with the woken user."""
        user = _user(is_active=False, athlete_id="i001")
        handler = AsyncMock(return_value="ran")
        decorated = athlete_required(handler)

        with (
            patch(
                "bot.decorator.User.get_by_chat_id",
                new=AsyncMock(return_value=user),
            ) as get_by_chat_id,
            patch(
                "bot.decorator.User.set_active_by_chat_id",
                new=AsyncMock(),
            ) as set_active,
            patch(
                "bot.decorator.User.touch_last_action",
                new=AsyncMock(),
            ) as touch,
        ):
            result = await decorated(_update(), MagicMock())

        assert result == "ran"
        get_by_chat_id.assert_awaited_once_with("999", include_inactive=True)
        set_active.assert_awaited_once_with(user.chat_id, True)
        touch.assert_awaited_once_with(user.id)
        handler.assert_awaited_once()
        assert user.is_active is True

    @pytest.mark.asyncio
    async def test_active_athlete_skips_reactivation_call(self):
        """Already-active user must NOT trigger the wake-up UPDATE (one less
        DB round-trip on the hot path)."""
        user = _user(is_active=True, athlete_id="i001")
        handler = AsyncMock(return_value="ran")
        decorated = athlete_required(handler)

        with (
            patch("bot.decorator.User.get_by_chat_id", new=AsyncMock(return_value=user)),
            patch("bot.decorator.User.set_active_by_chat_id", new=AsyncMock()) as set_active,
            patch("bot.decorator.User.touch_last_action", new=AsyncMock()),
        ):
            await decorated(_update(), MagicMock())

        set_active.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_athlete_id_still_bounces_after_wake_up_change(self):
        """The wake-up refactor switched `include_inactive=True`, so a viewer
        (no athlete_id, even if dormant) is now visible to the decorator.
        Athlete-only commands must still bounce — wake-up doesn't grant new
        permissions, it just lifts the dormancy gate."""
        user = _user(is_active=True, athlete_id=None)
        handler = AsyncMock()
        decorated = athlete_required(handler)
        update = _update()

        with (
            patch("bot.decorator.User.get_by_chat_id", new=AsyncMock(return_value=user)),
            patch("bot.decorator.User.set_active_by_chat_id", new=AsyncMock()) as set_active,
            patch("bot.decorator.User.touch_last_action", new=AsyncMock()),
        ):
            await decorated(update, MagicMock())

        handler.assert_not_awaited()
        set_active.assert_not_awaited()
        update.message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_user_bounces(self):
        """No row at all → bounce, no wake-up attempt."""
        handler = AsyncMock()
        decorated = athlete_required(handler)
        update = _update()

        with (
            patch("bot.decorator.User.get_by_chat_id", new=AsyncMock(return_value=None)),
            patch("bot.decorator.User.set_active_by_chat_id", new=AsyncMock()) as set_active,
        ):
            await decorated(update, MagicMock())

        handler.assert_not_awaited()
        set_active.assert_not_awaited()


class TestUserRequiredWakeUp:
    @pytest.mark.asyncio
    async def test_dormant_viewer_is_reactivated(self):
        """`user_required` doesn't require `athlete_id`, so a dormant viewer
        (e.g. someone who only ever used /silent or /donate) gets woken too."""
        user = _user(is_active=False, athlete_id=None)
        handler = AsyncMock(return_value="ran")
        decorated = user_required(handler)

        with (
            patch("bot.decorator.User.get_by_chat_id", new=AsyncMock(return_value=user)),
            patch("bot.decorator.User.set_active_by_chat_id", new=AsyncMock()) as set_active,
            patch("bot.decorator.User.touch_last_action", new=AsyncMock()) as touch,
        ):
            result = await decorated(_update(), MagicMock())

        assert result == "ran"
        set_active.assert_awaited_once_with(user.chat_id, True)
        touch.assert_awaited_once_with(user.id)
        assert user.is_active is True

    @pytest.mark.asyncio
    async def test_missing_user_bounces(self):
        handler = AsyncMock()
        decorated = user_required(handler)
        update = _update()

        with (
            patch("bot.decorator.User.get_by_chat_id", new=AsyncMock(return_value=None)),
            patch("bot.decorator.User.set_active_by_chat_id", new=AsyncMock()) as set_active,
        ):
            await decorated(update, MagicMock())

        handler.assert_not_awaited()
        set_active.assert_not_awaited()
        update.message.reply_text.assert_awaited_once()
