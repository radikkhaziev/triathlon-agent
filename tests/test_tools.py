"""Unit tests for tasks/tools.py — TelegramTool.send_photo."""

import json
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


def _error_response(status_code: int) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=MagicMock(),
        response=resp,
    )
    return resp


# ---------------------------------------------------------------------------
#  send_photo — happy path
# ---------------------------------------------------------------------------


class TestSendPhotoHappyPath:
    """send_photo: successful multipart POST to /sendPhoto."""

    def test_posts_to_send_photo_endpoint(self):
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES")

        mock_post.assert_called_once()
        url = mock_post.call_args.args[0]
        assert url.endswith("/sendPhoto")

    def test_includes_bot_token_in_url(self):
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES")

        url = mock_post.call_args.args[0]
        assert "test-token" in url

    def test_sends_photo_bytes_as_multipart(self):
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES")

        files = mock_post.call_args.kwargs["files"]
        assert "photo" in files
        name, content, mime = files["photo"]
        assert content == b"PNG_BYTES"
        assert mime == "image/png"

    def test_chat_id_in_data_field(self):
        tool = _make_tool(_make_user(chat_id="999"))
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES")

        data = mock_post.call_args.kwargs["data"]
        assert data["chat_id"] == "999"

    def test_caption_included_when_provided(self):
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES", caption="Great run!")

        data = mock_post.call_args.kwargs["data"]
        assert data["caption"] == "Great run!"

    def test_no_caption_key_when_empty(self):
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES", caption="")

        data = mock_post.call_args.kwargs["data"]
        assert "caption" not in data

    def test_reply_markup_json_encoded(self):
        markup = {"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]}
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES", reply_markup=markup)

        data = mock_post.call_args.kwargs["data"]
        assert json.loads(data["reply_markup"]) == markup

    def test_no_reply_markup_key_when_none(self):
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES", reply_markup=None)

        data = mock_post.call_args.kwargs["data"]
        assert "reply_markup" not in data

    def test_returns_response_json(self):
        payload = {"ok": True, "result": {"message_id": 42}}
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response(payload)):
            result = tool.send_photo(b"PNG_BYTES")

        assert result == payload

    def test_explicit_chat_id_overrides_user_chat_id(self):
        tool = _make_tool(_make_user(chat_id="111"))
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES", chat_id="999")

        data = mock_post.call_args.kwargs["data"]
        assert data["chat_id"] == "999"

    def test_no_user_uses_explicit_chat_id(self):
        tool = _make_tool(user=None)
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES", chat_id="777")

        data = mock_post.call_args.kwargs["data"]
        assert data["chat_id"] == "777"

    def test_timeout_is_30_seconds(self):
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES")

        assert mock_post.call_args.kwargs["timeout"] == 30.0


# ---------------------------------------------------------------------------
#  send_photo — silent user
# ---------------------------------------------------------------------------


class TestSendPhotoSilentUser:
    """send_photo: skips HTTP call and returns None when user.is_silent."""

    def test_returns_none_for_silent_user(self):
        tool = _make_tool(_make_user(is_silent=True))
        with patch("httpx.post") as mock_post:
            result = tool.send_photo(b"PNG_BYTES")

        assert result is None
        mock_post.assert_not_called()

    def test_non_silent_user_does_send(self):
        tool = _make_tool(_make_user(is_silent=False))
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_photo(b"PNG_BYTES")

        mock_post.assert_called_once()


# ---------------------------------------------------------------------------
#  Suppress for users without a bot chat — issue #266
# ---------------------------------------------------------------------------


class TestSuppressBotChatNotInitialized:
    """All three send methods skip HTTP and return None when the user logged
    in via the Login Widget but never opened a bot chat (Telegram would
    return 400 chat-not-found and create a Sentry storm)."""

    def test_send_message_skips_when_bot_chat_not_initialized(self):
        tool = _make_tool(_make_user(bot_chat_initialized=False))
        with patch("httpx.post") as mock_post:
            result = tool.send_message("hello")

        assert result is None
        mock_post.assert_not_called()

    def test_send_photo_skips_when_bot_chat_not_initialized(self):
        tool = _make_tool(_make_user(bot_chat_initialized=False))
        with patch("httpx.post") as mock_post:
            result = tool.send_photo(b"PNG_BYTES")

        assert result is None
        mock_post.assert_not_called()

    def test_send_document_skips_when_bot_chat_not_initialized(self):
        tool = _make_tool(_make_user(bot_chat_initialized=False))
        with patch("httpx.post") as mock_post:
            result = tool.send_document(b"PDF_BYTES", filename="report.pdf")

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
#  send_photo — missing chat_id
# ---------------------------------------------------------------------------


class TestSendPhotoMissingChatId:
    """send_photo: raises ValueError when no chat_id can be resolved."""

    def test_raises_value_error_without_user_and_chat_id(self):
        tool = _make_tool(user=None)
        with pytest.raises(ValueError, match="chat_id required"):
            tool.send_photo(b"PNG_BYTES")


# ---------------------------------------------------------------------------
#  send_photo — 403 handling
# ---------------------------------------------------------------------------


class TestSendPhoto403:
    """send_photo: 403 marks the user inactive and returns None."""

    def test_returns_none_on_403(self):
        tool = _make_tool(_make_user(chat_id="123"))
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 403

        with (
            patch("httpx.post", return_value=resp),
            patch("data.db.user.User.set_active_by_chat_id"),
        ):
            result = tool.send_photo(b"PNG_BYTES")

        assert result is None

    def test_calls_set_active_false_on_403(self):
        tool = _make_tool(_make_user(chat_id="123"))
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 403

        with (
            patch("httpx.post", return_value=resp),
            patch("data.db.user.User.set_active_by_chat_id") as mock_set_active,
        ):
            tool.send_photo(b"PNG_BYTES")

        mock_set_active.assert_called_once_with("123", False)

    def test_no_retry_on_403(self):
        """403 should be handled immediately — no retries."""
        tool = _make_tool(_make_user(chat_id="123"))
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 403

        with (
            patch("httpx.post", return_value=resp) as mock_post,
            patch("data.db.user.User.set_active_by_chat_id"),
        ):
            tool.send_photo(b"PNG_BYTES")

        assert mock_post.call_count == 1


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


# ---------------------------------------------------------------------------
#  send_photo — network retry
# ---------------------------------------------------------------------------


class TestSendPhotoRetry:
    """send_photo: retries up to 3 times on transient network errors."""

    def test_retries_on_timeout_then_succeeds(self):
        tool = _make_tool(_make_user())
        side_effects = [
            httpx.TimeoutException("timeout"),
            _ok_response({"ok": True}),
        ]
        with patch("httpx.post", side_effect=side_effects) as mock_post:
            result = tool.send_photo(b"PNG_BYTES")

        assert mock_post.call_count == 2
        assert result == {"ok": True}

    def test_raises_after_all_retries_exhausted(self):
        tool = _make_tool(_make_user())
        with patch("httpx.post", side_effect=httpx.ConnectError("unreachable")):
            with pytest.raises(httpx.ConnectError):
                tool.send_photo(b"PNG_BYTES")


# ---------------------------------------------------------------------------
#  send_document
# ---------------------------------------------------------------------------


class TestSendDocument:
    """send_document: posts to /sendDocument preserving filename, mime and caption."""

    def test_posts_to_send_document_endpoint(self):
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_document(b"PNG_BYTES", filename="card.png", mime_type="image/png")

        assert mock_post.call_args.args[0].endswith("/sendDocument")

    def test_multipart_document_key_and_mime_preserved(self):
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_document(b"PNG_BYTES", filename="run-card.png", mime_type="image/png")

        files = mock_post.call_args.kwargs["files"]
        assert "document" in files
        name, content, mime = files["document"]
        assert name == "run-card.png"
        assert content == b"PNG_BYTES"
        assert mime == "image/png"

    def test_default_mime_is_octet_stream(self):
        tool = _make_tool(_make_user())
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_document(b"BYTES", filename="file.bin")

        _, _, mime = mock_post.call_args.kwargs["files"]["document"]
        assert mime == "application/octet-stream"

    def test_caption_and_reply_markup_passthrough(self):
        tool = _make_tool(_make_user())
        markup = {"inline_keyboard": [[{"text": "ok", "callback_data": "ok"}]]}
        with patch("httpx.post", return_value=_ok_response()) as mock_post:
            tool.send_document(b"B", filename="f.png", caption="card", reply_markup=markup)

        data = mock_post.call_args.kwargs["data"]
        assert data["caption"] == "card"
        assert json.loads(data["reply_markup"]) == markup

    def test_silent_user_skipped(self):
        tool = _make_tool(_make_user(is_silent=True))
        with patch("httpx.post") as mock_post:
            result = tool.send_document(b"B", filename="f.png")

        assert result is None
        mock_post.assert_not_called()

    def test_403_marks_user_inactive(self):
        tool = _make_tool(_make_user())
        with (
            patch("httpx.post", return_value=_error_response(403)) as mock_post,
            patch("tasks.tools.User.set_active_by_chat_id") as mock_set_inactive,
        ):
            result = tool.send_document(b"B", filename="f.png")

        assert result is None
        mock_post.assert_called_once()
        mock_set_inactive.assert_called_once()

    def test_missing_chat_id_raises(self):
        tool = _make_tool(None)
        with pytest.raises(ValueError):
            tool.send_document(b"B", filename="f.png")
