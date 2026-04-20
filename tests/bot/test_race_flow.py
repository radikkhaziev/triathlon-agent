"""Tests for race-creation flow in bot/main.py.

Covers:
- _extract_pending_preview with tool_filter (race vs workout)
- _apply_push_flag flips dry_run for suggest_race
- race_push handler: pops pending_race, calls MCP with dry_run=False, handles result/error
- race_cancel handler: clears pending_race and confirms
- Double-tap of race_push: second one finds no draft and prompts retry
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.main import (
    _PREVIEWABLE_TOOLS,
    _RACE_TOOLS,
    _WORKOUT_TOOLS,
    _apply_push_flag,
    _extract_pending_preview,
    race_cancel,
    race_command,
    race_push,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_call(name: str, inp: dict) -> dict:
    return {"name": name, "input": inp}


def _update_with_callback(user_id: int = 1):
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.chat = MagicMock()
    query.message.chat.send_action = AsyncMock()
    query.message.reply_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=str(user_id))
    return update, query


def _make_context(pending_race: dict | None = None):
    ctx = MagicMock()
    ctx.user_data = {}
    if pending_race is not None:
        ctx.user_data["pending_race"] = pending_race
    return ctx


def _user(id: int = 1, mcp_token: str = "tok"):
    return SimpleNamespace(id=id, mcp_token=mcp_token, is_active=True, athlete_id="i001", language="ru")


# ---------------------------------------------------------------------------
# _PREVIEWABLE_TOOLS wiring
# ---------------------------------------------------------------------------


class TestPreviewableToolsWiring:
    def test_suggest_race_registered(self):
        assert "suggest_race" in _PREVIEWABLE_TOOLS

    def test_race_tool_set_does_not_overlap_with_workout_set(self):
        assert _RACE_TOOLS.isdisjoint(_WORKOUT_TOOLS)


# ---------------------------------------------------------------------------
# _extract_pending_preview
# ---------------------------------------------------------------------------


class TestExtractPendingPreview:
    def test_extracts_race_only_when_filter_is_race(self):
        calls = [
            _tool_call("suggest_workout", {"dry_run": True, "sport": "Run"}),
            _tool_call("suggest_race", {"dry_run": True, "name": "Drina", "category": "RACE_A"}),
        ]
        pending = _extract_pending_preview(calls, _RACE_TOOLS)
        assert pending is not None
        assert pending["name"] == "suggest_race"
        assert pending["input"]["name"] == "Drina"

    def test_skips_non_preview_race_calls(self):
        calls = [_tool_call("suggest_race", {"dry_run": False, "name": "Drina"})]
        assert _extract_pending_preview(calls, _RACE_TOOLS) is None

    def test_returns_deep_copy(self):
        inp = {"dry_run": True, "name": "Drina", "category": "RACE_A", "nested": {"x": 1}}
        calls = [_tool_call("suggest_race", inp)]
        pending = _extract_pending_preview(calls, _RACE_TOOLS)
        pending["input"]["nested"]["x"] = 99
        assert inp["nested"]["x"] == 1  # original untouched

    def test_picks_latest_when_multiple(self):
        calls = [
            _tool_call("suggest_race", {"dry_run": True, "name": "First"}),
            _tool_call("suggest_race", {"dry_run": True, "name": "Second"}),
        ]
        pending = _extract_pending_preview(calls, _RACE_TOOLS)
        assert pending["input"]["name"] == "Second"

    def test_filter_excludes_other_tools(self):
        calls = [_tool_call("suggest_workout", {"dry_run": True})]
        assert _extract_pending_preview(calls, _RACE_TOOLS) is None


# ---------------------------------------------------------------------------
# _apply_push_flag
# ---------------------------------------------------------------------------


class TestApplyPushFlag:
    def test_flips_dry_run_for_suggest_race(self):
        pending = {"name": "suggest_race", "input": {"dry_run": True, "name": "X"}}
        _apply_push_flag(pending)
        assert pending["input"]["dry_run"] is False

    def test_unknown_tool_raises(self):
        pending = {"name": "unknown_tool", "input": {}}
        with pytest.raises(KeyError):
            _apply_push_flag(pending)


# ---------------------------------------------------------------------------
# race_push
# ---------------------------------------------------------------------------


class TestRacePush:
    @pytest.mark.asyncio
    async def test_pushes_with_dry_run_false_and_returns_text(self, monkeypatch):
        pending = {
            "name": "suggest_race",
            "input": {"dry_run": True, "name": "Drina Trail", "category": "RACE_A", "dt": "2026-05-03"},
        }
        update, query = _update_with_callback()
        ctx = _make_context(pending_race=pending)

        call_tool = AsyncMock(return_value={"text": "✅ RACE_A created"})
        client_instance = MagicMock()
        client_instance.call_tool = call_tool
        monkeypatch.setattr("bot.main.MCPClient", MagicMock(return_value=client_instance))

        await race_push.__wrapped__(update, ctx, user=_user())

        # Draft consumed
        assert "pending_race" not in ctx.user_data
        # MCP called with dry_run=False
        call_tool.assert_awaited_once()
        tool_name, tool_input = call_tool.await_args.args
        assert tool_name == "suggest_race"
        assert tool_input["dry_run"] is False
        assert tool_input["name"] == "Drina Trail"
        # Reply delivered
        query.reply_text_called = True  # type: ignore[attr-defined]
        query.message.reply_text.assert_awaited_once()
        body = query.message.reply_text.await_args.args[0]
        assert "✅ RACE_A created" in body

    @pytest.mark.asyncio
    async def test_no_pending_draft_prompts_retry(self):
        update, query = _update_with_callback()
        ctx = _make_context(pending_race=None)

        await race_push.__wrapped__(update, ctx, user=_user())

        query.message.reply_text.assert_awaited_once()
        body = query.message.reply_text.await_args.args[0]
        assert "черновик" in body.lower()

    @pytest.mark.asyncio
    async def test_mcp_error_surfaced_to_user(self, monkeypatch):
        pending = {
            "name": "suggest_race",
            "input": {"dry_run": True, "name": "X", "category": "RACE_A", "dt": "2026-05-03"},
        }
        update, query = _update_with_callback()
        ctx = _make_context(pending_race=pending)

        client_instance = MagicMock()
        client_instance.call_tool = AsyncMock(return_value={"error": "401 Unauthorized"})
        monkeypatch.setattr("bot.main.MCPClient", MagicMock(return_value=client_instance))

        await race_push.__wrapped__(update, ctx, user=_user())

        body = query.message.reply_text.await_args.args[0]
        assert "401" in body or "Unauthorized" in body

    @pytest.mark.asyncio
    async def test_mcp_exception_returns_generic_error(self, monkeypatch):
        pending = {
            "name": "suggest_race",
            "input": {"dry_run": True, "name": "X", "category": "RACE_A", "dt": "2026-05-03"},
        }
        update, query = _update_with_callback()
        ctx = _make_context(pending_race=pending)

        client_instance = MagicMock()
        client_instance.call_tool = AsyncMock(side_effect=RuntimeError("network down"))
        monkeypatch.setattr("bot.main.MCPClient", MagicMock(return_value=client_instance))

        await race_push.__wrapped__(update, ctx, user=_user())

        body = query.message.reply_text.await_args.args[0]
        assert "Ошибка" in body

    @pytest.mark.asyncio
    async def test_double_tap_second_call_finds_no_draft(self, monkeypatch):
        pending = {
            "name": "suggest_race",
            "input": {"dry_run": True, "name": "X", "category": "RACE_A", "dt": "2026-05-03"},
        }
        update, query = _update_with_callback()
        ctx = _make_context(pending_race=pending)

        client_instance = MagicMock()
        client_instance.call_tool = AsyncMock(return_value={"text": "ok"})
        monkeypatch.setattr("bot.main.MCPClient", MagicMock(return_value=client_instance))

        # First tap — should push
        await race_push.__wrapped__(update, ctx, user=_user())
        assert client_instance.call_tool.await_count == 1

        # Second tap on the same ctx — pending already popped
        update2, _ = _update_with_callback()
        await race_push.__wrapped__(update2, ctx, user=_user())
        # Still just one MCP call total
        assert client_instance.call_tool.await_count == 1


# ---------------------------------------------------------------------------
# race_cancel
# ---------------------------------------------------------------------------


class TestRaceCancel:
    @pytest.mark.asyncio
    async def test_clears_pending_race_and_replies(self):
        pending = {"name": "suggest_race", "input": {"dry_run": True, "name": "X"}}
        update, query = _update_with_callback()
        ctx = _make_context(pending_race=pending)

        await race_cancel.__wrapped__(update, ctx, user=_user())

        assert "pending_race" not in ctx.user_data
        query.message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_safe_when_no_pending(self):
        update, query = _update_with_callback()
        ctx = _make_context(pending_race=None)

        await race_cancel.__wrapped__(update, ctx, user=_user())
        query.message.reply_text.assert_awaited_once()


# ---------------------------------------------------------------------------
# /race command
# ---------------------------------------------------------------------------


class TestRaceCommand:
    @pytest.mark.asyncio
    async def test_sends_priming_message(self):
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        ctx = MagicMock()

        await race_command.__wrapped__(update, ctx, user=_user())

        update.message.reply_text.assert_awaited_once()
        body = update.message.reply_text.await_args.args[0]
        assert "RACE" in body or "race" in body.lower()
        # Priming message should mention delete intent too — covers both flows.
        assert "удали" in body.lower() or "delete" in body.lower()
