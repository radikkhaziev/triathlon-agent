"""Tests for `ClaudeAgent.chat()` ChatResult return type and nudge_boundary signal.

We mock the Anthropic client and `ApiUsageDaily.increment` so we can drive
`request_count` directly without hitting the DB or real API.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.agent import ChatResult, ClaudeAgent


class TestChatResultDataclass:

    def test_defaults(self):
        r = ChatResult(text="hello")
        assert r.text == "hello"
        assert r.tool_calls == []
        assert r.nudge_boundary is False
        assert r.request_count == 0

    def test_fields(self):
        r = ChatResult(text="x", tool_calls=[{"name": "foo", "input": {}}], nudge_boundary=True, request_count=5)
        assert r.tool_calls[0]["name"] == "foo"
        assert r.nudge_boundary is True
        assert r.request_count == 5


class TestChatNudgeBoundary:
    """Verify `chat()` populates nudge_boundary from `request_count % N == 0`."""

    @pytest.fixture
    def mock_claude_response(self):
        """Stub an anthropic `Message` with a single text block and no tool_use."""
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hi there")],
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=20,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

    @pytest.mark.asyncio
    async def test_boundary_true_on_fifth_request(self, mock_claude_response, monkeypatch):
        monkeypatch.setattr("config.settings.DONATE_NUDGE_EVERY_N", 5)
        agent = ClaudeAgent()
        agent.client.messages.create = AsyncMock(return_value=mock_claude_response)

        with (
            patch("bot.agent.MCPClient") as MockMCP,
            patch("bot.agent.get_system_prompt_chat", new=AsyncMock(return_value="sys")),
            patch("bot.agent.select_tool_groups", return_value={"core"}),
            patch("bot.agent.filter_tools", return_value=[]),
            patch("bot.agent.ApiUsageDaily.increment", new=AsyncMock(return_value=SimpleNamespace(request_count=5))),
        ):
            MockMCP.return_value.list_tools = AsyncMock(return_value=[])
            result = await agent.chat("hello", user_id=1)

        assert isinstance(result, ChatResult)
        assert result.text == "hi there"
        assert result.nudge_boundary is True
        assert result.request_count == 5

    @pytest.mark.asyncio
    async def test_boundary_false_on_non_fifth(self, mock_claude_response, monkeypatch):
        monkeypatch.setattr("config.settings.DONATE_NUDGE_EVERY_N", 5)
        agent = ClaudeAgent()
        agent.client.messages.create = AsyncMock(return_value=mock_claude_response)

        with (
            patch("bot.agent.MCPClient") as MockMCP,
            patch("bot.agent.get_system_prompt_chat", new=AsyncMock(return_value="sys")),
            patch("bot.agent.select_tool_groups", return_value={"core"}),
            patch("bot.agent.filter_tools", return_value=[]),
            patch("bot.agent.ApiUsageDaily.increment", new=AsyncMock(return_value=SimpleNamespace(request_count=3))),
        ):
            MockMCP.return_value.list_tools = AsyncMock(return_value=[])
            result = await agent.chat("hello", user_id=1)

        assert result.nudge_boundary is False
        assert result.request_count == 3

    @pytest.mark.asyncio
    async def test_increment_failure_defaults_to_false(self, mock_claude_response):
        """If ApiUsageDaily.increment raises, nudge_boundary must be False and text still returned."""
        agent = ClaudeAgent()
        agent.client.messages.create = AsyncMock(return_value=mock_claude_response)

        with (
            patch("bot.agent.MCPClient") as MockMCP,
            patch("bot.agent.get_system_prompt_chat", new=AsyncMock(return_value="sys")),
            patch("bot.agent.select_tool_groups", return_value={"core"}),
            patch("bot.agent.filter_tools", return_value=[]),
            patch("bot.agent.ApiUsageDaily.increment", new=AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            MockMCP.return_value.list_tools = AsyncMock(return_value=[])
            result = await agent.chat("hello", user_id=1)

        assert result.text == "hi there"
        assert result.nudge_boundary is False
        assert result.request_count == 0

    @pytest.mark.asyncio
    async def test_empty_text_fallback(self):
        """If Claude returns no text blocks, chat() returns a fallback."""
        response = SimpleNamespace(
            content=[],
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=5, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0
            ),
        )
        agent = ClaudeAgent()
        agent.client.messages.create = AsyncMock(return_value=response)

        with (
            patch("bot.agent.MCPClient") as MockMCP,
            patch("bot.agent.get_system_prompt_chat", new=AsyncMock(return_value="sys")),
            patch("bot.agent.select_tool_groups", return_value={"core"}),
            patch("bot.agent.filter_tools", return_value=[]),
            patch("bot.agent.ApiUsageDaily.increment", new=AsyncMock(return_value=SimpleNamespace(request_count=1))),
        ):
            MockMCP.return_value.list_tools = AsyncMock(return_value=[])
            result = await agent.chat("hello", user_id=1)

        assert result.text == "Не удалось обработать запрос."
