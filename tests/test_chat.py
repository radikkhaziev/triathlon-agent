"""Tests for MCP Phase 3: Free-form Telegram chat."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai.tool_definitions import CHAT_TOOLS, MORNING_TOOLS, TOOL_HANDLERS

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


def _make_tool_use_response(tool_calls):
    blocks = []
    for i, (name, input_data) in enumerate(tool_calls):
        blocks.append(SimpleNamespace(type="tool_use", id=f"call_{i}", name=name, input=input_data))
    return SimpleNamespace(stop_reason="tool_use", content=blocks)


class TestClaudeAgentChat:
    @pytest.mark.asyncio
    async def test_simple_chat(self):
        """Direct answer without tools."""
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        text_response = _make_text_response("Z2 — это аэробная зона, 72-82% от LTHR.")
        agent.client.messages.create = AsyncMock(return_value=text_response)

        result = await agent.chat("Как правильно бегать Z2?")

        assert "Z2" in result
        assert agent.client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_chat_with_tools(self):
        """Chat with tool-use — Claude fetches data first."""
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        tool_response = _make_tool_use_response([("get_training_load", {"date": "2026-03-28"})])
        text_response = _make_text_response("TSB = -5, оптимальная зона.")

        agent.client.messages.create = AsyncMock(side_effect=[tool_response, text_response])

        with patch.dict(TOOL_HANDLERS, {"get_training_load": AsyncMock(return_value={"tsb": -5})}):
            result = await agent.chat("Какой у меня TSB?")

        assert "TSB" in result
        assert agent.client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_chat_uses_max_tokens_2048(self):
        """Chat uses 2048 max_tokens, not 4096."""
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        text_response = _make_text_response("Ответ")
        agent.client.messages.create = AsyncMock(return_value=text_response)

        await agent.chat("Вопрос")

        call_kwargs = agent.client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 2048

    @pytest.mark.asyncio
    async def test_chat_empty_response(self):
        """Empty response returns fallback message."""
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        # Response with no text blocks
        response = SimpleNamespace(stop_reason="end_turn", content=[])
        agent.client.messages.create = AsyncMock(return_value=response)

        result = await agent.chat("Вопрос")
        assert result == "Не удалось обработать запрос."


# ---------------------------------------------------------------------------
# handle_chat_message
# ---------------------------------------------------------------------------


class TestHandleChatMessage:
    def _make_update(self, user_id: str, text: str):
        user = SimpleNamespace(id=int(user_id))
        chat = AsyncMock()
        message = AsyncMock()
        message.text = text
        message.chat = chat
        message.reply_text = AsyncMock()
        update = SimpleNamespace(effective_user=user, message=message)
        return update

    @pytest.mark.asyncio
    async def test_owner_gets_response(self):
        from bot.main import handle_chat_message

        update = self._make_update("12345", "Как дела?")

        with (
            patch("bot.main.settings") as mock_settings,
            patch("ai.claude_agent.ClaudeAgent") as MockAgent,
        ):
            mock_settings.TELEGRAM_CHAT_ID = "12345"
            mock_settings.AI_CHAT_ENABLED = True
            agent_instance = MockAgent.return_value
            agent_instance.chat = AsyncMock(return_value="Всё хорошо")

            await handle_chat_message(update, None)

        update.message.reply_text.assert_called()
        first_call = update.message.reply_text.call_args_list[0]
        assert first_call.args[0] == "Всё хорошо"

    @pytest.mark.asyncio
    async def test_non_owner_ignored(self):
        from bot.main import handle_chat_message

        update = self._make_update("99999", "Как дела?")

        with patch("bot.main.settings") as mock_settings:
            mock_settings.TELEGRAM_CHAT_ID = "12345"
            mock_settings.AI_CHAT_ENABLED = True

            await handle_chat_message(update, None)

        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_disabled(self):
        from bot.main import handle_chat_message

        update = self._make_update("12345", "Как дела?")

        with patch("bot.main.settings") as mock_settings:
            mock_settings.AI_CHAT_ENABLED = False

            await handle_chat_message(update, None)

        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_markdown_fallback(self):
        """If Markdown send fails, falls back to plain text."""
        from bot.main import handle_chat_message

        update = self._make_update("12345", "Вопрос")
        # First reply_text call (Markdown) raises, second (plain) succeeds
        update.message.reply_text = AsyncMock(side_effect=[Exception("Bad Request: can't parse entities"), None])

        with (
            patch("bot.main.settings") as mock_settings,
            patch("ai.claude_agent.ClaudeAgent") as MockAgent,
        ):
            mock_settings.TELEGRAM_CHAT_ID = "12345"
            mock_settings.AI_CHAT_ENABLED = True
            agent_instance = MockAgent.return_value
            agent_instance.chat = AsyncMock(return_value="*ответ*")

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

        with (
            patch("bot.main.settings") as mock_settings,
            patch("ai.claude_agent.ClaudeAgent") as MockAgent,
        ):
            mock_settings.TELEGRAM_CHAT_ID = "12345"
            mock_settings.AI_CHAT_ENABLED = True
            agent_instance = MockAgent.return_value
            agent_instance.chat = AsyncMock(side_effect=RuntimeError("API down"))

            await handle_chat_message(update, None)

        update.message.reply_text.assert_called_with("Ошибка при обработке. Попробуй ещё раз.")


# ---------------------------------------------------------------------------
# handle_save_mood_checkin handler
# ---------------------------------------------------------------------------


class TestHandleSaveMoodCheckin:
    @pytest.mark.asyncio
    async def test_saves_mood(self):
        from ai.tool_definitions import handle_save_mood_checkin

        fake_row = SimpleNamespace(
            id=42,
            timestamp=SimpleNamespace(isoformat=lambda: "2026-03-28T10:00:00+00:00"),
            energy=2,
            mood=3,
            anxiety=4,
            social=3,
            note="устал",
        )
        with patch("ai.tool_definitions.save_mood_checkin", new=AsyncMock(return_value=fake_row)):
            result = await handle_save_mood_checkin(energy=2, mood=3, anxiety=4, social=3, note="устал")

        assert result["id"] == 42
        assert result["energy"] == 2
        assert result["mood"] == 3
        assert result["note"] == "устал"

    @pytest.mark.asyncio
    async def test_validation_error(self):
        from ai.tool_definitions import handle_save_mood_checkin

        with patch(
            "ai.tool_definitions.save_mood_checkin",
            new=AsyncMock(side_effect=ValueError("At least one field required")),
        ):
            result = await handle_save_mood_checkin()

        assert "error" in result
        assert "At least one field" in result["error"]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestChatConfig:
    def test_default_enabled(self):
        from config import Settings

        s = Settings(
            INTERVALS_API_KEY="x",
            INTERVALS_ATHLETE_ID="i1",
            TELEGRAM_BOT_TOKEN="x",
            TELEGRAM_CHAT_ID="1",
            ANTHROPIC_API_KEY="x",
        )
        assert s.AI_CHAT_ENABLED is True

    def test_disabled(self):
        from config import Settings

        s = Settings(
            INTERVALS_API_KEY="x",
            INTERVALS_ATHLETE_ID="i1",
            TELEGRAM_BOT_TOKEN="x",
            TELEGRAM_CHAT_ID="1",
            ANTHROPIC_API_KEY="x",
            AI_CHAT_ENABLED=False,
        )
        assert s.AI_CHAT_ENABLED is False
