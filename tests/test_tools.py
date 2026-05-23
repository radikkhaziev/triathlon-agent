"""Unit tests for tasks/tools.py — TelegramTool.send_message + 400-failure self-healing.

`TelegramTool.send_photo` / `send_document` were removed during the Halo
cleanup (zero production callers). Tests for those methods (formerly
`TestSendPhoto*` / `TestSendDocument`) were deleted in the same wave; this
file now covers `send_message` and the shared `_post_with_retries` self-heal
behaviour (issue #266 and round-2 cross-tenant guard).
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from data.db.dto import UserDTO
from tasks.tools import TelegramTool

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _make_user(
    *,
    is_silent: bool = False,
    chat_id: str = "123456",
    bot_chat_initialized: bool = True,
) -> UserDTO:
    return UserDTO(id=1, chat_id=chat_id, is_silent=is_silent, bot_chat_initialized=bot_chat_initialized)


def _make_tool(user: UserDTO | None = None) -> TelegramTool:
    return TelegramTool(user=user, bot_token="test-token")


def _ok_response(payload: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = payload or {"ok": True, "result": {"message_id": 1}}
    return resp


# ---------------------------------------------------------------------------
#  Suppress for users without a bot chat — issue #266
# ---------------------------------------------------------------------------


class TestSuppressBotChatNotInitialized:
    """`send_message` skips HTTP and returns None when the user logged in via
    the Login Widget but never opened a bot chat (Telegram would return 400
    chat-not-found and create a Sentry storm)."""

    def test_send_message_skips_when_bot_chat_not_initialized(self):
        tool = _make_tool(_make_user(bot_chat_initialized=False))
        with patch("httpx.post") as mock_post:
            result = tool.send_message("hello")

        assert result is None
        mock_post.assert_not_called()

    def test_send_message_proceeds_when_bot_chat_initialized(self):
        """Sanity guard: True should NOT be the path that suppresses."""
        tool = _make_tool(_make_user(bot_chat_initialized=True))
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_message("hello")

        mock_post.assert_called_once()

    def test_anonymous_user_none_does_not_suppress(self):
        """``user=None`` (e.g. owner-broadcast paths) bypasses both gates."""
        tool = _make_tool(None)
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_message("hello", chat_id="999")

        mock_post.assert_called_once()


# ---------------------------------------------------------------------------
#  Self-healing on Telegram 400 chat-not-found — issue #266 bleed-stop
# ---------------------------------------------------------------------------


def _telegram_400(description: str) -> MagicMock:
    """Build a Telegram-shaped 400 response with a given ``description``."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 400
    resp.json.return_value = {"ok": False, "error_code": 400, "description": description}
    return resp


class TestSendMessage400PermanentFailure:
    """``_post_with_retries``: 400 with a permanent ``description`` clears
    ``bot_chat_initialized`` and returns None instead of raising. Covers the
    case where a user had a chat (initialized=True) but later deleted it —
    without this, the original 400 still escapes to Sentry."""

    def test_returns_none_on_chat_not_found(self):
        tool = _make_tool(_make_user(chat_id="123", bot_chat_initialized=True))
        with (
            patch("httpx.post", return_value=_telegram_400("Bad Request: chat not found")),
            patch("data.db.user.User.set_bot_chat_initialized"),
        ):
            result = tool.send_message("hello")

        assert result is None

    def test_clears_bot_chat_initialized_on_chat_not_found(self):
        tool = _make_tool(_make_user(chat_id="123", bot_chat_initialized=True))
        with (
            patch("httpx.post", return_value=_telegram_400("Bad Request: chat not found")),
            patch("data.db.user.User.set_bot_chat_initialized") as mock_set,
        ):
            tool.send_message("hello")

        mock_set.assert_called_once_with("123", False)

    def test_no_retry_on_permanent_400(self):
        tool = _make_tool(_make_user(chat_id="123", bot_chat_initialized=True))
        with (
            patch("httpx.post", return_value=_telegram_400("Bad Request: chat not found")) as mock_post,
            patch("data.db.user.User.set_bot_chat_initialized"),
        ):
            tool.send_message("hello")

        assert mock_post.call_count == 1

    def test_user_is_deactivated_also_clears_flag(self):
        tool = _make_tool(_make_user(chat_id="123", bot_chat_initialized=True))
        with (
            patch("httpx.post", return_value=_telegram_400("Bad Request: user is deactivated")),
            patch("data.db.user.User.set_bot_chat_initialized") as mock_set,
        ):
            result = tool.send_message("hello")

        assert result is None
        mock_set.assert_called_once_with("123", False)

    def test_peer_id_invalid_also_clears_flag(self):
        tool = _make_tool(_make_user(chat_id="123", bot_chat_initialized=True))
        with (
            patch("httpx.post", return_value=_telegram_400("Bad Request: PEER_ID_INVALID")),
            patch("data.db.user.User.set_bot_chat_initialized") as mock_set,
        ):
            result = tool.send_message("hello")

        assert result is None
        mock_set.assert_called_once_with("123", False)

    def test_unknown_400_still_raises(self):
        """A 400 we don't recognize (e.g. parse_mode bug) is OUR problem and
        must surface to Sentry — don't silently swallow it."""
        tool = _make_tool(_make_user(chat_id="123", bot_chat_initialized=True))
        resp = _telegram_400("Bad Request: message text is empty")
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "HTTP 400",
            request=MagicMock(),
            response=resp,
        )
        with (
            patch("httpx.post", return_value=resp),
            patch("data.db.user.User.set_bot_chat_initialized") as mock_set,
        ):
            with pytest.raises(httpx.HTTPStatusError):
                tool.send_message("hello")

        mock_set.assert_not_called()

    def test_400_with_unparseable_body_still_raises(self):
        """Defensive: if Telegram returns 400 with a non-JSON body, fall through
        to ``raise_for_status`` rather than silently swallowing."""
        tool = _make_tool(_make_user(chat_id="123", bot_chat_initialized=True))
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 400
        resp.json.side_effect = ValueError("not JSON")
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "HTTP 400",
            request=MagicMock(),
            response=resp,
        )
        with (
            patch("httpx.post", return_value=resp),
            patch("data.db.user.User.set_bot_chat_initialized") as mock_set,
        ):
            with pytest.raises(httpx.HTTPStatusError):
                tool.send_message("hello")

        mock_set.assert_not_called()


class TestSendMessage400CrossTenantGuard:
    """Self-healing must NOT update the flag when the failing chat_id doesn't
    belong to the bound ``self.user``. Otherwise a typo'd chat_id in a
    broadcast or an owner-broadcast targeting another user would silently
    clear an innocent victim's ``bot_chat_initialized``. Issue #266 round-2.
    """

    def test_skips_mutation_when_no_user_bound(self):
        """``TelegramTool()`` without ``user`` (broadcast path): swallow the
        400 and return None, but do NOT touch any DB row."""
        tool = _make_tool(None)
        with (
            patch("httpx.post", return_value=_telegram_400("Bad Request: chat not found")),
            patch("data.db.user.User.set_bot_chat_initialized") as mock_set,
        ):
            result = tool.send_message("hello", chat_id="999")

        assert result is None
        mock_set.assert_not_called()

    def test_skips_mutation_when_explicit_chat_id_does_not_match_user(self):
        """``send_message(text, chat_id=other)`` overrides ``self.user.chat_id``.
        The 400 says ``other`` is unreachable, but the bound user might be
        fine — don't touch their flag."""
        tool = _make_tool(_make_user(chat_id="123", bot_chat_initialized=True))
        with (
            patch("httpx.post", return_value=_telegram_400("Bad Request: chat not found")),
            patch("data.db.user.User.set_bot_chat_initialized") as mock_set,
        ):
            result = tool.send_message("hello", chat_id="999_other_user")

        assert result is None
        mock_set.assert_not_called()

    def test_clears_when_chat_id_matches_bound_user(self):
        """Sanity counter-test: when the failing chat_id is exactly
        ``self.user.chat_id``, the flag IS cleared."""
        tool = _make_tool(_make_user(chat_id="123", bot_chat_initialized=True))
        with (
            patch("httpx.post", return_value=_telegram_400("Bad Request: chat not found")),
            patch("data.db.user.User.set_bot_chat_initialized") as mock_set,
        ):
            tool.send_message("hello", chat_id="123")

        mock_set.assert_called_once_with("123", False)


class TestPermanent400AllowlistIsClassConstant:
    """Regression guard: ``_TG_400_PERMANENT_SUBSTRINGS`` must stay a
    ``ClassVar`` so it isn't promoted to a per-instance ``@dataclass`` field
    (which would expose it via ``__init__`` kwargs, ``repr``, ``__eq__``).
    """

    def test_not_in_dataclass_fields(self):
        from dataclasses import fields

        from tasks.tools import TelegramTool

        names = {f.name for f in fields(TelegramTool)}
        assert "_TG_400_PERMANENT_SUBSTRINGS" not in names

    def test_excludes_403_substring(self):
        """``"bot was blocked by the user"`` is a 403, not a 400 — Telegram
        never returns it with status 400, and the 403 branch handles it
        with different semantics (``is_active=False``). Keeping it in the
        400-allowlist would be misleading dead code."""
        from tasks.tools import TelegramTool

        for marker in TelegramTool._TG_400_PERMANENT_SUBSTRINGS:
            assert "blocked" not in marker
