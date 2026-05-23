"""Tests for MCP Phase 3: Free-form Telegram chat."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# ClaudeAgent.chat()
# ---------------------------------------------------------------------------


def _make_text_response(text):
    usage = SimpleNamespace(
        input_tokens=100, output_tokens=50, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)], usage=usage)


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

        with (patch("bot.agent.MCPClient") as mock_mcp_cls,):
            mock_mcp = MagicMock()
            mock_mcp.list_tools = AsyncMock(return_value=[])
            mock_mcp_cls.return_value = mock_mcp

            result = await agent.chat("Как правильно бегать Z2?")

        assert "Z2" in result.text
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

        with (patch("bot.agent.MCPClient") as mock_mcp_cls,):
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
        usage = SimpleNamespace(
            input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        response = SimpleNamespace(stop_reason="end_turn", content=[], usage=usage)
        agent.client.messages.create = AsyncMock(return_value=response)

        with (patch("bot.agent.MCPClient") as mock_mcp_cls,):
            mock_mcp = MagicMock()
            mock_mcp.list_tools = AsyncMock(return_value=[])
            mock_mcp_cls.return_value = mock_mcp

            result = await agent.chat("Вопрос")
        assert result.text == "Не удалось обработать запрос."


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
    u.language = "ru"
    # _is_donor compares last_donation_at against a datetime cutoff — keep
    # the default MagicMock (truthy + uncomparable) from triggering TypeError.
    u.last_donation_at = None
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
        # ``handle_chat_message`` reads ``update.effective_chat.id`` when
        # clearing prior undo buttons. A SimpleNamespace with a chat_id-like
        # int works — tests don't care about the value.
        effective_chat = SimpleNamespace(id=int(user_id))
        update = SimpleNamespace(
            effective_user=user,
            effective_chat=effective_chat,
            message=message,
            callback_query=None,
        )
        return update

    def _make_context(self):
        """Real Telegram passes a ``ContextTypes.DEFAULT_TYPE`` with mutable
        ``user_data`` and a ``job_queue``. Mirror that shape with a
        SimpleNamespace so the chat handler's race-creation /
        undoable-tool branches don't AttributeError on ``None``."""
        return SimpleNamespace(user_data={}, job_queue=None)

    @pytest.mark.asyncio
    async def test_owner_gets_response(self):
        from bot.main import handle_chat_message

        update = self._make_update("12345", "Как дела?")
        mock_db_user = _mock_user("12345")

        with (
            patch("bot.decorator.User.get_by_chat_id", new=AsyncMock(return_value=mock_db_user)),
            patch("bot.main.agent") as mock_agent,
        ):
            from bot.agent import ChatResult

            mock_agent.chat = AsyncMock(return_value=ChatResult(text="Всё хорошо"))
            await handle_chat_message(update, self._make_context())

        update.message.reply_text.assert_called()
        first_call = update.message.reply_text.call_args_list[0]
        assert first_call.args[0] == "Всё хорошо"

    @pytest.mark.asyncio
    async def test_non_owner_no_access(self):
        """User not in DB or not active → 'Нет доступа.'"""
        from bot.main import handle_chat_message

        update = self._make_update("99999", "Как дела?")

        with patch("bot.decorator.User.get_by_chat_id", new=AsyncMock(return_value=None)):
            await handle_chat_message(update, self._make_context())

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
            from bot.agent import ChatResult

            mock_agent.chat = AsyncMock(return_value=ChatResult(text="*ответ*"))
            await handle_chat_message(update, self._make_context())

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
            await handle_chat_message(update, self._make_context())

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

        with (patch("bot.agent.MCPClient", return_value=mock_mcp) as mock_cls,):
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

        with (patch("bot.agent.MCPClient", return_value=mock_mcp) as mock_cls,):
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

        with (patch("bot.agent.MCPClient", return_value=mock_mcp),):
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
        assert "Вижу скриншот" in result.text
