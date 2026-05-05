"""Tests for bot/main.py:start — focuses on the i18n contract.

``/start`` is the only handler not wrapped in ``@user_required`` /
``@athlete_required``, so it's the only one that has to call
``set_language`` itself. Forgetting that call (regression: existing
en-locale users got Russian welcome text on every ``/start``) is the bug
this test exists to prevent.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_update(chat_id: int, *, full_name: str = "Test User", username: str | None = "tester"):
    """Build a minimal ``Update`` stand-in with the fields ``start()`` reads."""
    tg_user = SimpleNamespace(id=chat_id, username=username, full_name=full_name)
    message = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(effective_user=tg_user, message=message)


def _make_user(*, language: str, athlete_id: str | None = None):
    """Stand-in for the ``User`` ORM row that ``start()`` operates on."""
    return SimpleNamespace(
        id=1,
        chat_id="999",
        language=language,
        athlete_id=athlete_id,
        is_active=True,
        bot_chat_initialized=True,
    )


class TestStartLanguageContract:
    @pytest.mark.asyncio
    async def test_existing_en_user_gets_set_language_called(self):
        """Existing user with ``language='en'`` must end up with the chat
        contextvar set to ``'en'`` — otherwise the welcome text falls back
        to the contextvar default ('ru')."""
        from bot.main import start

        update = _make_update(chat_id=999)
        user = _make_user(language="en", athlete_id="i123")

        with (
            patch("bot.main.User.get_or_create_from_telegram", new=AsyncMock(return_value=user)),
            patch("bot.main._set_lang") as mock_set_lang,
        ):
            await start(update, MagicMock())

        mock_set_lang.assert_called_once_with("en")

    @pytest.mark.asyncio
    async def test_missing_language_falls_back_to_ru(self):
        """A null ``user.language`` should pin contextvar to 'ru' explicitly,
        not leave it at whatever leaked from a previous task — contextvars
        in PTB tasks inherit from the parent dispatcher context."""
        from bot.main import start

        update = _make_update(chat_id=999)
        user = _make_user(language=None, athlete_id="i123")

        with (
            patch("bot.main.User.get_or_create_from_telegram", new=AsyncMock(return_value=user)),
            patch("bot.main._set_lang") as mock_set_lang,
        ):
            await start(update, MagicMock())

        mock_set_lang.assert_called_once_with("ru")

    @pytest.mark.asyncio
    async def test_set_language_runs_before_first_translation_call(self):
        """Order matters: ``_set_lang`` must run before the first ``_(...)``,
        otherwise ``reply_text`` ships the wrong locale even if the call is
        made eventually. Verify by recording ordering of the two patched
        callables on a single side-effect log."""
        from bot.main import start

        update = _make_update(chat_id=999)
        user = _make_user(language="en", athlete_id=None)  # new-user branch

        order: list[str] = []

        def _record_set_lang(lang):
            order.append(f"set_lang:{lang}")

        async def _record_reply_text(*args, **kwargs):
            order.append("reply_text")

        update.message.reply_text = _record_reply_text

        with (
            patch("bot.main.User.get_or_create_from_telegram", new=AsyncMock(return_value=user)),
            patch("bot.main._set_lang", side_effect=_record_set_lang),
        ):
            await start(update, MagicMock())

        assert order[0] == "set_lang:en"
        assert "reply_text" in order
        assert order.index("set_lang:en") < order.index("reply_text")
