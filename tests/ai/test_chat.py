"""Tests for MCP Phase 3: Free-form Telegram chat."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tasks.tools import CHAT_TOOLS, MORNING_TOOLS

# ---------------------------------------------------------------------------
# CHAT_TOOLS
# ---------------------------------------------------------------------------


class TestChatTools:
    def test_chat_tools_extends_morning_tools(self):
        assert len(CHAT_TOOLS) > len(MORNING_TOOLS)
        morning_names = {t["name"] for t in MORNING_TOOLS}
        chat_names = {t["name"] for t in CHAT_TOOLS}
        assert morning_names.issubset(chat_names)

    def test_save_mood_checkin_in_chat_only(self):
        morning_names = {t["name"] for t in MORNING_TOOLS}
        chat_names = {t["name"] for t in CHAT_TOOLS}
        assert "save_mood_checkin" in chat_names
        assert "save_mood_checkin" not in morning_names

    def test_chat_tools_independent(self):
        """Modifying CHAT_TOOLS doesn't affect MORNING_TOOLS."""
        original_len = len(MORNING_TOOLS)
        CHAT_TOOLS.append({"name": "test_tool", "description": "test", "input_schema": {"type": "object"}})
        assert len(MORNING_TOOLS) == original_len
        CHAT_TOOLS.pop()  # cleanup


# ---------------------------------------------------------------------------
# ClaudeAgent.chat()
# ---------------------------------------------------------------------------


def _make_text_response(text):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


class TestClaudeAgentChat:
    @pytest.mark.asyncio
    async def test_simple_chat(self):
        """Direct answer without tools."""
        from bot.agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        text_response = _make_text_response("Z2 — это аэробная зона, 72-82% от LTHR.")
        agent.client.messages.create = AsyncMock(return_value=text_response)

        with (
            patch("bot.agent.MCPClient") as mock_mcp_cls,
            patch("bot.agent.get_system_prompt_chat", new_callable=AsyncMock, return_value="You are a coach."),
        ):
            mock_mcp = MagicMock()
            mock_mcp.list_tools = AsyncMock(return_value=[])
            mock_mcp_cls.return_value = mock_mcp

            result = await agent.chat("Как правильно бегать Z2?")

        assert "Z2" in result
        assert agent.client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_chat_uses_max_tokens_2048(self):
        """Chat uses 2048 max_tokens, not 4096."""
        from bot.agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        text_response = _make_text_response("Ответ")
        agent.client.messages.create = AsyncMock(return_value=text_response)

        with (
            patch("bot.agent.MCPClient") as mock_mcp_cls,
            patch("bot.agent.get_system_prompt_chat", new_callable=AsyncMock, return_value="You are a coach."),
        ):
            mock_mcp = MagicMock()
            mock_mcp.list_tools = AsyncMock(return_value=[])
            mock_mcp_cls.return_value = mock_mcp

            await agent.chat("Вопрос")

        call_kwargs = agent.client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 2048

    @pytest.mark.asyncio
    async def test_chat_empty_response(self):
        """Empty response returns fallback message."""
        from bot.agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        # Response with no text blocks
        response = SimpleNamespace(stop_reason="end_turn", content=[])
        agent.client.messages.create = AsyncMock(return_value=response)

        with (
            patch("bot.agent.MCPClient") as mock_mcp_cls,
            patch("bot.agent.get_system_prompt_chat", new_callable=AsyncMock, return_value="You are a coach."),
        ):
            mock_mcp = MagicMock()
            mock_mcp.list_tools = AsyncMock(return_value=[])
            mock_mcp_cls.return_value = mock_mcp

            result = await agent.chat("Вопрос")
        assert result == "Не удалось обработать запрос."


# ---------------------------------------------------------------------------
# handle_chat_message
# ---------------------------------------------------------------------------


def _mock_user(chat_id: str = "12345"):
    """Return a mock User object for @athlete_required decorator bypass."""
    u = MagicMock()
    u.id = 1
    u.chat_id = chat_id
    u.is_active = True
    u.athlete_id = "i001"
    u.mcp_token = "test_token"
    u.role = "owner"
    return u


class TestHandleChatMessage:
    def _make_update(self, user_id: str, text: str):
        user = SimpleNamespace(id=int(user_id))
        chat = AsyncMock()
        message = AsyncMock()
        message.text = text
        message.chat = chat
        message.reply_text = AsyncMock()
        message.reply_to_message = None
        update = SimpleNamespace(
            effective_user=user,
            message=message,
            callback_query=None,
        )
        return update

    @pytest.mark.asyncio
    async def test_owner_gets_response(self):
        from bot.main import handle_chat_message

        update = self._make_update("12345", "Как дела?")
        mock_db_user = _mock_user("12345")

        with (
            patch("bot.decorator.User.get_by_chat_id", new=AsyncMock(return_value=mock_db_user)),
            patch("bot.main.agent") as mock_agent,
        ):
            mock_agent.chat = AsyncMock(return_value="Всё хорошо")
            await handle_chat_message(update, None)

        update.message.reply_text.assert_called()
        first_call = update.message.reply_text.call_args_list[0]
        assert first_call.args[0] == "Всё хорошо"

    @pytest.mark.asyncio
    async def test_non_owner_no_access(self):
        """User not in DB or not active → 'Нет доступа.'"""
        from bot.main import handle_chat_message

        update = self._make_update("99999", "Как дела?")

        with patch("bot.decorator.User.get_by_chat_id", new=AsyncMock(return_value=None)):
            await handle_chat_message(update, None)

        # The decorator replies with "Нет доступа."
        update.message.reply_text.assert_called_once_with("Нет доступа.")

    @pytest.mark.asyncio
    async def test_markdown_fallback(self):
        """If Markdown send fails, falls back to plain text."""
        from bot.main import handle_chat_message

        update = self._make_update("12345", "Вопрос")
        mock_db_user = _mock_user("12345")
        # First reply_text call (Markdown) raises, second (plain) succeeds
        update.message.reply_text = AsyncMock(side_effect=[Exception("Bad Request: can't parse entities"), None])

        with (
            patch("bot.decorator.User.get_by_chat_id", new=AsyncMock(return_value=mock_db_user)),
            patch("bot.main.agent") as mock_agent,
        ):
            mock_agent.chat = AsyncMock(return_value="*ответ*")
            await handle_chat_message(update, None)

        assert update.message.reply_text.call_count == 2
        # Second call — plain text (no parse_mode)
        second_call = update.message.reply_text.call_args_list[1]
        assert "parse_mode" not in second_call.kwargs

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Claude error → user-friendly error message."""
        from bot.main import handle_chat_message

        update = self._make_update("12345", "Вопрос")
        mock_db_user = _mock_user("12345")

        with (
            patch("bot.decorator.User.get_by_chat_id", new=AsyncMock(return_value=mock_db_user)),
            patch("bot.main.agent") as mock_agent,
        ):
            mock_agent.chat = AsyncMock(side_effect=RuntimeError("API down"))
            await handle_chat_message(update, None)

        update.message.reply_text.assert_called_with("Ошибка при обработке. Попробуй ещё раз.")


# ---------------------------------------------------------------------------
# ClaudeAgent.chat() — MCPClient wiring
# ---------------------------------------------------------------------------


class TestClaudeAgentMCPWiring:
    """ClaudeAgent.chat() creates MCPClient with correct token."""

    @pytest.mark.asyncio
    async def test_chat_creates_mcp_client_with_provided_token(self):
        """When mcp_token is provided, MCPClient receives it."""
        from bot.agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        text_resp = _make_text_response("Ответ")
        agent.client.messages.create = AsyncMock(return_value=text_resp)

        mock_mcp = MagicMock()
        mock_mcp.list_tools = AsyncMock(return_value=[])
        mock_mcp.call_tool = AsyncMock()

        with (
            patch("bot.agent.MCPClient", return_value=mock_mcp) as mock_cls,
            patch("bot.agent.get_system_prompt_chat", new_callable=AsyncMock, return_value="You are a coach."),
        ):
            await agent.chat("Вопрос", mcp_token="user_token_123")

        mock_cls.assert_called_once_with(token="user_token_123")

    @pytest.mark.asyncio
    async def test_chat_falls_back_to_default_token(self):
        """When mcp_token is None, MCPClient gets None (uses default)."""
        from bot.agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        text_resp = _make_text_response("Ответ")
        agent.client.messages.create = AsyncMock(return_value=text_resp)

        mock_mcp = MagicMock()
        mock_mcp.list_tools = AsyncMock(return_value=[])

        with (
            patch("bot.agent.MCPClient", return_value=mock_mcp) as mock_cls,
            patch("bot.agent.get_system_prompt_chat", new_callable=AsyncMock, return_value="You are a coach."),
        ):
            await agent.chat("Вопрос", mcp_token=None)

        mock_cls.assert_called_once_with(token=None)

    @pytest.mark.asyncio
    async def test_chat_passes_image_data(self):
        """When image_data is provided, message contains image block."""
        from bot.agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        text_resp = _make_text_response("Вижу скриншот")
        agent.client.messages.create = AsyncMock(return_value=text_resp)

        mock_mcp = MagicMock()
        mock_mcp.list_tools = AsyncMock(return_value=[])

        with (
            patch("bot.agent.MCPClient", return_value=mock_mcp),
            patch("bot.agent.get_system_prompt_chat", new_callable=AsyncMock, return_value="You are a coach."),
        ):
            result = await agent.chat(
                "Что тут?",
                image_data=b"\x89PNG",
                image_media_type="image/png",
            )

        call_kwargs = agent.client.messages.create.call_args.kwargs
        user_msg = call_kwargs["messages"][0]
        # Content is a list of blocks (image + text)
        assert isinstance(user_msg["content"], list)
        types = [b["type"] for b in user_msg["content"]]
        assert "image" in types
        assert "text" in types
        assert "Вижу скриншот" in result
