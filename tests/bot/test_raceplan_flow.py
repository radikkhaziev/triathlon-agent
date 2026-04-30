"""Tests for /raceplan flow in bot/main.py (END-64).

Mirrors tests/bot/test_race_flow.py — the two flows share the
``_extract_pending_preview`` / ``_apply_push_flag`` plumbing, so what changes
here is the tool name (``generate_race_plan``), the stash key
(``pending_raceplan``), the callback names (``raceplan_push`` / ``raceplan_cancel``),
and the success-path render (Markdown body + PNG card).

Covers:
- ``_PREVIEWABLE_TOOLS`` registry includes ``generate_race_plan``.
- ``_RACEPLAN_TOOLS`` does not overlap workout / race sets.
- ``_extract_pending_preview`` filtered by ``_RACEPLAN_TOOLS``.
- ``_apply_push_flag`` flips ``dry_run`` for ``generate_race_plan``.
- ``_find_tool_result`` returns the latest matching call result.
- ``raceplan_command``: dry-run path, error-from-tool path, no-tool-call path.
- ``raceplan_push``: pops draft, calls MCP with ``dry_run=False``, renders body
  + PNG card, double-tap is idempotent, MCP error is surfaced.
- ``raceplan_cancel``: clears stash, replies cancellation.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.main import (
    _PREVIEWABLE_TOOLS,
    _RACE_TOOLS,
    _RACEPLAN_TOOLS,
    _WORKOUT_TOOLS,
    _apply_push_flag,
    _extract_pending_preview,
    _find_tool_result,
    raceplan_cancel,
    raceplan_command,
    raceplan_push,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _tool_call(name: str, inp: dict, result: dict | None = None) -> dict:
    return {"name": name, "input": inp, "result": result}


def _update_with_callback(user_id: int = 1):
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.chat = MagicMock()
    query.message.chat.send_action = AsyncMock()
    query.message.reply_text = AsyncMock()
    query.message.reply_photo = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=str(user_id))
    return update, query


def _update_with_message():
    update = MagicMock()
    update.message = MagicMock()
    update.message.chat = MagicMock()
    update.message.chat.send_action = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.message.reply_photo = AsyncMock()
    return update


def _make_context(pending_raceplan: dict | None = None):
    ctx = MagicMock()
    ctx.user_data = {}
    if pending_raceplan is not None:
        ctx.user_data["pending_raceplan"] = pending_raceplan
    return ctx


def _user(id: int = 1, mcp_token: str = "tok"):
    return SimpleNamespace(id=id, mcp_token=mcp_token, is_active=True, athlete_id="i001", language="ru")


def _sample_plan_payload(name: str = "Drina Trail") -> dict:
    """Realistic ``generate_race_plan`` MCP result for renderer/handler tests."""
    return {
        "id": None,
        "dry_run": True,
        "preliminary": False,
        "model_version": "v0",
        "payload": {
            "preliminary": False,
            "race": {"id": 7, "name": name, "date": "2026-06-15", "days_to_race": 12, "discipline": "Run"},
            "plan": {
                "headline": "Patient first half, finish strong.",
                "warmup": "20 min easy + 4 strides.",
                "legs": [
                    {
                        "leg": "first half",
                        "distance": "10 km",
                        "pacing": {"low": "5:30/km", "target": "5:15/km", "cap": "5:00/km"},
                        "hr_ceiling_bpm": 162,
                    }
                ],
                "fueling": {"carbs_g_per_hour": 70, "notes": "Gel every 25 min."},
                "transitions": [],
                "contingencies": [
                    {"scenario": "heat", "plan": "Slow target 10s/km."},
                    {"scenario": "cramp", "plan": "Walk 2 min, salt cap."},
                    {"scenario": "off-pace", "plan": "Hold cap not target."},
                ],
            },
            "generated_at": "2026-04-30T20:00:00+00:00",
            "model_version": "v0",
        },
    }


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


class TestPreviewableToolsWiring:
    def test_generate_race_plan_registered(self):
        assert "generate_race_plan" in _PREVIEWABLE_TOOLS

    def test_raceplan_tools_disjoint_from_workout_and_race(self):
        assert _RACEPLAN_TOOLS.isdisjoint(_WORKOUT_TOOLS)
        assert _RACEPLAN_TOOLS.isdisjoint(_RACE_TOOLS)


# ---------------------------------------------------------------------------
# _extract_pending_preview filtered by _RACEPLAN_TOOLS
# ---------------------------------------------------------------------------


class TestExtractPendingPreview:
    def test_filters_to_generate_race_plan_only(self):
        calls = [
            _tool_call("suggest_race", {"dry_run": True, "name": "Drina"}),
            _tool_call("generate_race_plan", {"dry_run": True, "goal_id": 7}),
        ]
        pending = _extract_pending_preview(calls, _RACEPLAN_TOOLS)
        assert pending is not None
        assert pending["name"] == "generate_race_plan"
        assert pending["input"]["goal_id"] == 7

    def test_skips_non_preview_calls(self):
        calls = [_tool_call("generate_race_plan", {"dry_run": False, "goal_id": 7})]
        assert _extract_pending_preview(calls, _RACEPLAN_TOOLS) is None

    def test_returns_deep_copy(self):
        inp = {"dry_run": True, "goal_id": 7, "nested": {"x": 1}}
        calls = [_tool_call("generate_race_plan", inp)]
        pending = _extract_pending_preview(calls, _RACEPLAN_TOOLS)
        pending["input"]["nested"]["x"] = 99
        assert inp["nested"]["x"] == 1

    def test_picks_latest_when_multiple(self):
        calls = [
            _tool_call("generate_race_plan", {"dry_run": True, "goal_id": 1}),
            _tool_call("generate_race_plan", {"dry_run": True, "goal_id": 2}),
        ]
        pending = _extract_pending_preview(calls, _RACEPLAN_TOOLS)
        assert pending["input"]["goal_id"] == 2


# ---------------------------------------------------------------------------
# _apply_push_flag for generate_race_plan
# ---------------------------------------------------------------------------


class TestApplyPushFlag:
    def test_flips_dry_run_for_generate_race_plan(self):
        pending = {"name": "generate_race_plan", "input": {"dry_run": True, "goal_id": 7}}
        _apply_push_flag(pending)
        assert pending["input"]["dry_run"] is False
        assert pending["input"]["goal_id"] == 7


# ---------------------------------------------------------------------------
# _find_tool_result
# ---------------------------------------------------------------------------


class TestFindToolResult:
    def test_returns_latest_match(self):
        calls = [
            _tool_call("generate_race_plan", {"dry_run": True}, result={"id": 1}),
            _tool_call("save_fact", {}, result={"fact_id": 99}),
            _tool_call("generate_race_plan", {"dry_run": True}, result={"id": 2}),
        ]
        assert _find_tool_result(calls, "generate_race_plan") == {"id": 2}

    def test_returns_none_when_missing(self):
        calls = [_tool_call("save_fact", {}, result={"fact_id": 99})]
        assert _find_tool_result(calls, "generate_race_plan") is None

    def test_skips_non_dict_result(self):
        calls = [_tool_call("generate_race_plan", {}, result="oops not a dict")]
        assert _find_tool_result(calls, "generate_race_plan") is None


# ---------------------------------------------------------------------------
# raceplan_command — dry-run preview path
# ---------------------------------------------------------------------------


class TestRaceplanCommand:
    @pytest.mark.asyncio
    async def test_renders_preview_with_buttons_on_success(self, monkeypatch):
        update = _update_with_message()
        ctx = _make_context()

        sample = _sample_plan_payload()
        chat_result = SimpleNamespace(
            text="ok",
            tool_calls=[_tool_call("generate_race_plan", {"dry_run": True, "goal_id": 7}, result=sample)],
        )
        monkeypatch.setattr("bot.main.agent.chat", AsyncMock(return_value=chat_result))

        await raceplan_command.__wrapped__(update, ctx, user=_user())

        # Draft stashed for confirm callback
        assert ctx.user_data["pending_raceplan"]["name"] == "generate_race_plan"
        assert ctx.user_data["pending_raceplan"]["input"]["dry_run"] is True
        # Reply sent with markdown body containing the headline
        update.message.reply_text.assert_awaited()
        body = update.message.reply_text.await_args.args[0]
        assert "Drina Trail" in body
        assert "Patient first half" in body
        # Inline keyboard attached
        kwargs = update.message.reply_text.await_args.kwargs
        assert kwargs.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_no_tool_call_surfaces_agent_text(self, monkeypatch):
        update = _update_with_message()
        ctx = _make_context()

        chat_result = SimpleNamespace(text="No RACE_A goal — set one first.", tool_calls=[])
        monkeypatch.setattr("bot.main.agent.chat", AsyncMock(return_value=chat_result))

        await raceplan_command.__wrapped__(update, ctx, user=_user())

        assert ctx.user_data["pending_raceplan"] is None
        body = update.message.reply_text.await_args.args[0]
        assert "RACE_A" in body
        # No keyboard when there's nothing to confirm
        kwargs = update.message.reply_text.await_args.kwargs
        assert kwargs.get("reply_markup") is None

    @pytest.mark.asyncio
    async def test_tool_returned_error_surfaces_message_and_drops_draft(self, monkeypatch):
        update = _update_with_message()
        ctx = _make_context()

        # Tool was called with dry_run=True but returned an error (e.g. <6 activities).
        # The pending preview is still extracted from the call list — handler must
        # then drop it once it sees the error in the result.
        err = {"error": "Only 3 activities in the last 6 weeks — sync Intervals.icu and try again."}
        chat_result = SimpleNamespace(
            text="ok",
            tool_calls=[_tool_call("generate_race_plan", {"dry_run": True}, result=err)],
        )
        monkeypatch.setattr("bot.main.agent.chat", AsyncMock(return_value=chat_result))

        await raceplan_command.__wrapped__(update, ctx, user=_user())

        assert ctx.user_data.get("pending_raceplan") is None
        body = update.message.reply_text.await_args.args[0]
        assert "Only 3 activities" in body

    @pytest.mark.asyncio
    async def test_agent_chat_exception_surfaces_friendly_error(self, monkeypatch):
        update = _update_with_message()
        ctx = _make_context()

        monkeypatch.setattr("bot.main.agent.chat", AsyncMock(side_effect=RuntimeError("network")))

        await raceplan_command.__wrapped__(update, ctx, user=_user())

        body = update.message.reply_text.await_args.args[0]
        assert "план" in body.lower() or "plan" in body.lower()


# ---------------------------------------------------------------------------
# raceplan_push — confirm callback
# ---------------------------------------------------------------------------


class TestRaceplanPush:
    @pytest.mark.asyncio
    async def test_pushes_with_dry_run_false_renders_body_and_card(self, monkeypatch):
        pending = {"name": "generate_race_plan", "input": {"dry_run": True, "goal_id": 7}}
        update, query = _update_with_callback()
        ctx = _make_context(pending_raceplan=pending)

        sample = _sample_plan_payload()
        sample["dry_run"] = False
        sample["id"] = 42
        call_tool = AsyncMock(return_value=sample)
        client_instance = MagicMock()
        client_instance.call_tool = call_tool
        monkeypatch.setattr("bot.main.MCPClient", MagicMock(return_value=client_instance))

        with patch("bot.raceplan_render.render_race_plan_card", return_value=b"PNGFAKE") as render_card:
            await raceplan_push.__wrapped__(update, ctx, user=_user())

        # Draft consumed
        assert "pending_raceplan" not in ctx.user_data
        # MCP called with dry_run=False
        call_tool.assert_awaited_once()
        tool_name, tool_input = call_tool.await_args.args
        assert tool_name == "generate_race_plan"
        assert tool_input["dry_run"] is False
        assert tool_input["goal_id"] == 7
        # Markdown body sent
        query.message.reply_text.assert_awaited()
        body = query.message.reply_text.await_args.args[0]
        assert "Drina Trail" in body
        # PNG card sent as photo
        render_card.assert_called_once()
        query.message.reply_photo.assert_awaited_once()
        kwargs = query.message.reply_photo.await_args.kwargs
        assert kwargs["photo"] == b"PNGFAKE"
        assert "Drina Trail" in kwargs["caption"]

    @pytest.mark.asyncio
    async def test_no_pending_draft_prompts_retry(self):
        update, query = _update_with_callback()
        ctx = _make_context(pending_raceplan=None)

        await raceplan_push.__wrapped__(update, ctx, user=_user())

        query.message.reply_text.assert_awaited_once()
        body = query.message.reply_text.await_args.args[0]
        assert "raceplan" in body.lower() or "план" in body.lower()
        query.message.reply_photo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mcp_error_surfaced_to_user(self, monkeypatch):
        pending = {"name": "generate_race_plan", "input": {"dry_run": True, "goal_id": 7}}
        update, query = _update_with_callback()
        ctx = _make_context(pending_raceplan=pending)

        client_instance = MagicMock()
        client_instance.call_tool = AsyncMock(return_value={"error": "DB down"})
        monkeypatch.setattr("bot.main.MCPClient", MagicMock(return_value=client_instance))

        await raceplan_push.__wrapped__(update, ctx, user=_user())

        body = query.message.reply_text.await_args.args[0]
        assert "DB down" in body
        # Card not rendered on error
        query.message.reply_photo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mcp_exception_returns_generic_error(self, monkeypatch):
        pending = {"name": "generate_race_plan", "input": {"dry_run": True, "goal_id": 7}}
        update, query = _update_with_callback()
        ctx = _make_context(pending_raceplan=pending)

        client_instance = MagicMock()
        client_instance.call_tool = AsyncMock(side_effect=RuntimeError("network down"))
        monkeypatch.setattr("bot.main.MCPClient", MagicMock(return_value=client_instance))

        await raceplan_push.__wrapped__(update, ctx, user=_user())

        body = query.message.reply_text.await_args.args[0]
        assert "Ошибка" in body or "Error" in body
        query.message.reply_photo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_double_tap_second_call_finds_no_draft(self, monkeypatch):
        pending = {"name": "generate_race_plan", "input": {"dry_run": True, "goal_id": 7}}
        update, query = _update_with_callback()
        ctx = _make_context(pending_raceplan=pending)

        client_instance = MagicMock()
        client_instance.call_tool = AsyncMock(return_value=_sample_plan_payload())
        monkeypatch.setattr("bot.main.MCPClient", MagicMock(return_value=client_instance))

        with patch("bot.raceplan_render.render_race_plan_card", return_value=b"PNGFAKE"):
            await raceplan_push.__wrapped__(update, ctx, user=_user())
            assert client_instance.call_tool.await_count == 1

            update2, _ = _update_with_callback()
            await raceplan_push.__wrapped__(update2, ctx, user=_user())
            # Still just one MCP call total; second tap finds no draft.
            assert client_instance.call_tool.await_count == 1

    @pytest.mark.asyncio
    async def test_card_render_failure_does_not_eat_text_response(self, monkeypatch):
        # A broken renderer must NOT prevent the user from seeing the saved
        # plan body. The card is a bonus; the plan is the deliverable.
        pending = {"name": "generate_race_plan", "input": {"dry_run": True, "goal_id": 7}}
        update, query = _update_with_callback()
        ctx = _make_context(pending_raceplan=pending)

        client_instance = MagicMock()
        client_instance.call_tool = AsyncMock(return_value=_sample_plan_payload())
        monkeypatch.setattr("bot.main.MCPClient", MagicMock(return_value=client_instance))

        with patch("bot.raceplan_render.render_race_plan_card", side_effect=RuntimeError("font missing")):
            await raceplan_push.__wrapped__(update, ctx, user=_user())

        # Body sent
        query.message.reply_text.assert_awaited()
        # Card not sent (render exploded) but we did NOT raise
        query.message.reply_photo.assert_not_awaited()


# ---------------------------------------------------------------------------
# raceplan_cancel
# ---------------------------------------------------------------------------


class TestRaceplanCancel:
    @pytest.mark.asyncio
    async def test_clears_pending_raceplan_and_replies(self):
        pending = {"name": "generate_race_plan", "input": {"dry_run": True, "goal_id": 7}}
        update, query = _update_with_callback()
        ctx = _make_context(pending_raceplan=pending)

        await raceplan_cancel.__wrapped__(update, ctx, user=_user())

        assert "pending_raceplan" not in ctx.user_data
        query.message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_safe_when_no_pending(self):
        update, query = _update_with_callback()
        ctx = _make_context(pending_raceplan=None)

        await raceplan_cancel.__wrapped__(update, ctx, user=_user())
        query.message.reply_text.assert_awaited_once()
