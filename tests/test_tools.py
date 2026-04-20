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


def _make_user(*, is_silent: bool = False, chat_id: str = "123456") -> UserDTO:
    return UserDTO(id=1, chat_id=chat_id, is_silent=is_silent)


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
