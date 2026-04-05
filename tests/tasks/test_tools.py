"""Tests for tasks/tools.py — TelegramTool and MCPTool."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _httpx_response(status_code: int = 200, json_body: dict | None = None) -> MagicMock:
    """Create a mock httpx.Response."""
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status_code
    mock.json.return_value = json_body or {}
    if status_code >= 400:
        mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=mock,
        )
    else:
        mock.raise_for_status.return_value = None
    return mock


# ---------------------------------------------------------------------------
# TelegramTool
# ---------------------------------------------------------------------------


class TestTelegramToolInit:
    """TelegramTool initializes base_url from bot_token."""

    def test_base_url_contains_token(self):
        from tasks.tools import TelegramTool

        tool = TelegramTool(bot_token="test-token-123")
        assert "test-token-123" in tool.base_url
        assert tool.base_url.startswith("https://api.telegram.org/bot")

    def test_base_url_format(self):
        from tasks.tools import TelegramTool

        tool = TelegramTool(bot_token="abc")
        assert tool.base_url == "https://api.telegram.org/botabc"


class TestTelegramToolSendMessage:
    """TelegramTool.send_message sends POST to sendMessage endpoint."""

    def _make_tool(self):
        from tasks.tools import TelegramTool

        return TelegramTool(bot_token="test-token")

    def test_posts_to_send_message_url(self):
        """send_message calls POST /sendMessage."""
        tool = self._make_tool()
        mock_resp = _httpx_response(200, {"ok": True, "result": {"message_id": 1}})

        with patch("tasks.tools.httpx.post", return_value=mock_resp) as mock_post:
            tool.send_message(chat_id=12345, text="Hello")

        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert url.endswith("/sendMessage")

    def test_payload_contains_chat_id_and_text(self):
        """Payload sent to Telegram includes chat_id and text."""
        tool = self._make_tool()
        mock_resp = _httpx_response(200, {"ok": True, "result": {}})

        with patch("tasks.tools.httpx.post", return_value=mock_resp) as mock_post:
            tool.send_message(chat_id=99, text="Test message")

        payload = mock_post.call_args[1]["json"]
        assert payload["chat_id"] == 99
        assert payload["text"] == "Test message"

    def test_no_reply_markup_in_payload_when_none(self):
        """reply_markup key absent from payload when not provided."""
        tool = self._make_tool()
        mock_resp = _httpx_response(200, {"ok": True, "result": {}})

        with patch("tasks.tools.httpx.post", return_value=mock_resp) as mock_post:
            tool.send_message(chat_id=1, text="Hi")

        payload = mock_post.call_args[1]["json"]
        assert "reply_markup" not in payload

    def test_reply_markup_serialized_as_json_string(self):
        """reply_markup is JSON-serialized when provided."""
        tool = self._make_tool()
        mock_resp = _httpx_response(200, {"ok": True, "result": {}})
        keyboard = {"inline_keyboard": [[{"text": "Open", "web_app": {"url": "https://example.com"}}]]}

        with patch("tasks.tools.httpx.post", return_value=mock_resp) as mock_post:
            tool.send_message(chat_id=1, text="Hi", reply_markup=keyboard)

        payload = mock_post.call_args[1]["json"]
        assert "reply_markup" in payload
        # Must be a JSON string, not a dict
        assert isinstance(payload["reply_markup"], str)
        parsed = json.loads(payload["reply_markup"])
        assert parsed == keyboard

    def test_returns_json_response(self):
        """send_message returns the parsed JSON response body."""
        tool = self._make_tool()
        expected = {"ok": True, "result": {"message_id": 42}}
        mock_resp = _httpx_response(200, expected)

        with patch("tasks.tools.httpx.post", return_value=mock_resp):
            result = tool.send_message(chat_id=1, text="Hi")

        assert result == expected

    def test_raises_on_http_error(self):
        """HTTP 4xx/5xx raises httpx.HTTPStatusError."""
        tool = self._make_tool()
        mock_resp = _httpx_response(403)

        with patch("tasks.tools.httpx.post", return_value=mock_resp):
            with pytest.raises(httpx.HTTPStatusError):
                tool.send_message(chat_id=1, text="Hi")

    def test_timeout_is_15_seconds(self):
        """Timeout passed to httpx.post is 15.0."""
        tool = self._make_tool()
        mock_resp = _httpx_response(200, {"ok": True, "result": {}})

        with patch("tasks.tools.httpx.post", return_value=mock_resp) as mock_post:
            tool.send_message(chat_id=1, text="Hi")

        assert mock_post.call_args[1]["timeout"] == 15.0

    def test_string_chat_id_accepted(self):
        """chat_id can be a string (Telegram channel ID like @channelusername)."""
        tool = self._make_tool()
        mock_resp = _httpx_response(200, {"ok": True, "result": {}})

        with patch("tasks.tools.httpx.post", return_value=mock_resp) as mock_post:
            tool.send_message(chat_id="@testchannel", text="Hi")

        payload = mock_post.call_args[1]["json"]
        assert payload["chat_id"] == "@testchannel"


# ---------------------------------------------------------------------------
# MCPTool session init
# ---------------------------------------------------------------------------


class TestMCPToolSessionInit:
    """MCPTool._ensure_session initializes MCP session via JSON-RPC."""

    def _make_tool(self):
        from tasks.tools import MCPTool

        return MCPTool(mcp_url="http://localhost:8000/mcp", token="secret")

    def test_sends_initialize_and_notification(self):
        tool = self._make_tool()
        init_resp = _httpx_response(200, {"result": {"protocolVersion": "2025-03-26"}})
        init_resp.headers = {"mcp-session-id": "sess-123"}
        notif_resp = _httpx_response(200)

        with patch("tasks.tools.httpx.post", side_effect=[init_resp, notif_resp]) as mock_post:
            tool._ensure_session()

        assert tool._session_id == "sess-123"
        assert mock_post.call_count == 2
        init_payload = mock_post.call_args_list[0][1]["json"]
        assert init_payload["method"] == "initialize"
        notif_payload = mock_post.call_args_list[1][1]["json"]
        assert notif_payload["method"] == "notifications/initialized"

    def test_skips_if_already_initialized(self):
        tool = self._make_tool()
        tool._session_id = "existing"

        with patch("tasks.tools.httpx.post") as mock_post:
            tool._ensure_session()

        mock_post.assert_not_called()

    def test_session_id_sent_in_notification(self):
        tool = self._make_tool()
        init_resp = _httpx_response(200, {"result": {}})
        init_resp.headers = {"mcp-session-id": "sess-456"}
        notif_resp = _httpx_response(200)

        with patch("tasks.tools.httpx.post", side_effect=[init_resp, notif_resp]) as mock_post:
            tool._ensure_session()

        notif_headers = mock_post.call_args_list[1][1]["headers"]
        assert notif_headers["Mcp-Session-Id"] == "sess-456"


# ---------------------------------------------------------------------------
# MCPTool._call_mcp
# ---------------------------------------------------------------------------


class TestMCPToolCallMcp:
    """MCPTool._call_mcp parses JSON-RPC MCP responses."""

    def _make_tool(self):
        from tasks.tools import MCPTool

        tool = MCPTool(mcp_url="http://localhost:8000/mcp", token="secret")
        tool._session_id = "test-session"  # skip init, test _call_mcp only
        return tool

    def test_posts_json_rpc_payload(self):
        """_call_mcp POSTs a JSON-RPC 2.0 envelope to /mcp."""
        tool = self._make_tool()
        body = {"result": {"content": [{"type": "text", "text": '{"score": 85}'}]}}
        mock_resp = _httpx_response(200, body)

        with patch("tasks.tools.httpx.post", return_value=mock_resp) as mock_post:
            tool._call_mcp("get_wellness", {"date": "2026-04-03"})

        payload = mock_post.call_args[1]["json"]
        assert payload["jsonrpc"] == "2.0"
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "get_wellness"
        assert payload["params"]["arguments"] == {"date": "2026-04-03"}

    def test_authorization_header_sent(self):
        """Bearer token is included in Authorization header."""
        tool = self._make_tool()
        mock_resp = _httpx_response(200, {"result": {"content": []}})

        with patch("tasks.tools.httpx.post", return_value=mock_resp) as mock_post:
            tool._call_mcp("get_wellness", {})

        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer secret"

    def test_parses_text_content_as_json(self):
        """Text block with valid JSON is parsed and returned as dict."""
        tool = self._make_tool()
        body = {"result": {"content": [{"type": "text", "text": '{"recovery_score": 72, "category": "good"}'}]}}
        mock_resp = _httpx_response(200, body)

        with patch("tasks.tools.httpx.post", return_value=mock_resp):
            result = tool._call_mcp("get_recovery", {})

        assert result == {"recovery_score": 72, "category": "good"}

    def test_returns_text_key_when_json_parse_fails(self):
        """Non-JSON text block returns {"text": raw_text}."""
        tool = self._make_tool()
        body = {"result": {"content": [{"type": "text", "text": "plain text, not JSON"}]}}
        mock_resp = _httpx_response(200, body)

        with patch("tasks.tools.httpx.post", return_value=mock_resp):
            result = tool._call_mcp("some_tool", {})

        assert result == {"text": "plain text, not JSON"}

    def test_skips_non_text_blocks(self):
        """Image or other non-text content blocks are skipped."""
        tool = self._make_tool()
        body = {
            "result": {
                "content": [
                    {"type": "image", "data": "base64..."},
                    {"type": "text", "text": '{"found": true}'},
                ]
            }
        }
        mock_resp = _httpx_response(200, body)

        with patch("tasks.tools.httpx.post", return_value=mock_resp):
            result = tool._call_mcp("some_tool", {})

        assert result == {"found": True}

    def test_returns_empty_dict_when_no_content(self):
        """Empty content array returns {}."""
        tool = self._make_tool()
        body = {"result": {"content": []}}
        mock_resp = _httpx_response(200, body)

        with patch("tasks.tools.httpx.post", return_value=mock_resp):
            result = tool._call_mcp("some_tool", {})

        assert result == {}

    def test_returns_error_dict_when_jsonrpc_error(self):
        """JSON-RPC error field returns {"error": str(error)}."""
        tool = self._make_tool()
        body = {"error": {"code": -32601, "message": "Method not found"}}
        mock_resp = _httpx_response(200, body)

        with patch("tasks.tools.httpx.post", return_value=mock_resp):
            result = tool._call_mcp("unknown_tool", {})

        assert "error" in result

    def test_raises_on_http_error(self):
        """HTTP 5xx from MCP server raises HTTPStatusError."""
        tool = self._make_tool()
        mock_resp = _httpx_response(500)

        with patch("tasks.tools.httpx.post", return_value=mock_resp):
            with pytest.raises(httpx.HTTPStatusError):
                tool._call_mcp("get_wellness", {})

    def test_timeout_is_30_seconds(self):
        """Timeout passed to httpx.post is 30.0."""
        tool = self._make_tool()
        mock_resp = _httpx_response(200, {"result": {"content": []}})

        with patch("tasks.tools.httpx.post", return_value=mock_resp) as mock_post:
            tool._call_mcp("some_tool", {})

        assert mock_post.call_args[1]["timeout"] == 30.0

    def test_uses_first_text_block_only(self):
        """Only the first text block is returned; subsequent ones are ignored."""
        tool = self._make_tool()
        body = {
            "result": {
                "content": [
                    {"type": "text", "text": '{"first": true}'},
                    {"type": "text", "text": '{"second": true}'},
                ]
            }
        }
        mock_resp = _httpx_response(200, body)

        with patch("tasks.tools.httpx.post", return_value=mock_resp):
            result = tool._call_mcp("some_tool", {})

        # Returns first parseable text block
        assert result == {"first": True}


# ---------------------------------------------------------------------------
# MCPTool.generate_morning_report_via_mcp
# ---------------------------------------------------------------------------


class TestMCPToolGenerateMorningReport:
    """MCPTool.generate_morning_report_via_mcp runs Claude API + MCP tool loop."""

    def _make_tool(self):
        from tasks.tools import MCPTool

        tool = MCPTool(mcp_url="http://localhost:8000/mcp", token="secret")
        tool._session_id = "test-session"  # skip init, test report logic only
        return tool

    def _make_text_response(self, text: str) -> MagicMock:
        """Create a mock Anthropic response with a single text block (stop_reason=end_turn)."""
        block = MagicMock()
        block.type = "text"
        block.text = text
        response = MagicMock()
        response.stop_reason = "end_turn"
        response.content = [block]
        return response

    def _make_tool_use_response(self, tool_name: str, tool_id: str, tool_input: dict) -> MagicMock:
        """Create a mock Anthropic response requesting a tool call."""
        block = MagicMock()
        block.type = "tool_use"
        block.name = tool_name
        block.id = tool_id
        block.input = tool_input
        response = MagicMock()
        response.stop_reason = "tool_use"
        response.content = [block]
        return response

    def test_returns_text_when_no_tool_calls(self):
        """Single-turn response (no tool_use) returns the text content."""
        tool = self._make_tool()
        mock_response = self._make_text_response("Morning report: recovery is good.")

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("tasks.tools.anthropic.Anthropic", return_value=mock_client):
            result = tool.generate_morning_report_via_mcp("2026-04-03")

        assert result == "Morning report: recovery is good."

    def test_returns_none_on_empty_text(self):
        """Empty text blocks → returns None."""
        tool = self._make_tool()
        block = MagicMock()
        block.type = "text"
        block.text = ""
        response = MagicMock()
        response.stop_reason = "end_turn"
        response.content = [block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        with patch("tasks.tools.anthropic.Anthropic", return_value=mock_client):
            result = tool.generate_morning_report_via_mcp("2026-04-03")

        assert result is None

    def test_returns_none_on_exception(self):
        """Any exception during generation is caught and None is returned."""
        tool = self._make_tool()

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API failure")

        with patch("tasks.tools.anthropic.Anthropic", return_value=mock_client):
            result = tool.generate_morning_report_via_mcp("2026-04-03")

        assert result is None

    def test_performs_tool_use_loop(self):
        """Tool-use loop: sends tool results back and gets final text response."""
        tool = self._make_tool()

        tool_response = self._make_tool_use_response("get_wellness", "tu_001", {"date": "2026-04-03"})
        final_response = self._make_text_response("Final report text.")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [tool_response, final_response]

        mcp_result = {"recovery_score": 80}

        with (
            patch("tasks.tools.anthropic.Anthropic", return_value=mock_client),
            patch.object(tool, "_call_mcp", return_value=mcp_result) as mock_mcp,
        ):
            result = tool.generate_morning_report_via_mcp("2026-04-03")

        # MCP was called with the tool name from Claude's request
        mock_mcp.assert_called_once_with("get_wellness", {"date": "2026-04-03"})
        # Claude was called twice: initial + with tool results
        assert mock_client.messages.create.call_count == 2
        assert result == "Final report text."

    def test_tool_results_contain_tool_use_id(self):
        """Tool result message includes the matching tool_use_id from Claude's request."""
        tool = self._make_tool()

        tool_response = self._make_tool_use_response("get_recovery", "tu_42", {})
        final_response = self._make_text_response("Done.")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [tool_response, final_response]

        with (
            patch("tasks.tools.anthropic.Anthropic", return_value=mock_client),
            patch.object(tool, "_call_mcp", return_value={}),
        ):
            tool.generate_morning_report_via_mcp("2026-04-03")

        # Second call to Claude has tool_result with correct tool_use_id
        second_call_messages = mock_client.messages.create.call_args_list[1][1]["messages"]
        tool_result_message = second_call_messages[-1]
        assert tool_result_message["role"] == "user"
        content = tool_result_message["content"]
        assert len(content) == 1
        assert content[0]["type"] == "tool_result"
        assert content[0]["tool_use_id"] == "tu_42"

    def test_accepts_date_object(self):
        """date object is converted to ISO string for the prompt."""
        from datetime import date

        tool = self._make_tool()
        mock_response = self._make_text_response("Report.")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("tasks.tools.anthropic.Anthropic", return_value=mock_client):
            result = tool.generate_morning_report_via_mcp(date(2026, 4, 3))

        assert result == "Report."
        # Verify the prompt contains the ISO date string
        call_kwargs = mock_client.messages.create.call_args[1]
        messages = call_kwargs["messages"]
        assert "2026-04-03" in messages[0]["content"]

    def test_max_iterations_prevents_infinite_loop(self):
        """Loop terminates after max_iterations even if Claude keeps requesting tools."""
        tool = self._make_tool()

        # Always returns tool_use, never end_turn
        tool_response = self._make_tool_use_response("get_wellness", "tu_001", {})

        mock_client = MagicMock()
        mock_client.messages.create.return_value = tool_response

        with (
            patch("tasks.tools.anthropic.Anthropic", return_value=mock_client),
            patch.object(tool, "_call_mcp", return_value={}),
        ):
            # Should not raise, should return None or last text (empty in this case)
            tool.generate_morning_report_via_mcp("2026-04-03")

        # Capped at 10 iterations
        assert mock_client.messages.create.call_count == 10

    def test_uses_morning_tools_in_request(self):
        """Claude API call includes MORNING_TOOLS."""
        from tasks.tools import MORNING_TOOLS

        tool = self._make_tool()
        mock_response = self._make_text_response("Report.")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("tasks.tools.anthropic.Anthropic", return_value=mock_client):
            tool.generate_morning_report_via_mcp("2026-04-03")

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["tools"] == MORNING_TOOLS
