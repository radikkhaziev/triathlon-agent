"""Tests for MCP Phase 2: Claude tool-use for morning analysis."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai.tool_definitions import MORNING_TOOLS, TOOL_HANDLERS

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


class TestToolDefinitions:
    def test_all_tools_have_required_fields(self):
        for tool in MORNING_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_tool_count(self):
        assert len(MORNING_TOOLS) == 15

    def test_all_tools_have_handlers(self):
        """Every tool in MORNING_TOOLS and CHAT_TOOLS has a handler."""
        from ai.tool_definitions import CHAT_TOOLS

        all_tool_names = {t["name"] for t in CHAT_TOOLS}
        handler_names = set(TOOL_HANDLERS.keys())
        assert all_tool_names.issubset(handler_names)

    def test_core_tools_present(self):
        names = {t["name"] for t in MORNING_TOOLS}
        core = {
            "get_recovery",
            "get_hrv_analysis",
            "get_rhr_analysis",
            "get_training_load",
            "get_scheduled_workouts",
            "get_goal_progress",
            "get_activity_hrv",
        }
        assert core.issubset(names)

    def test_optional_tools_present(self):
        names = {t["name"] for t in MORNING_TOOLS}
        optional = {
            "get_wellness_range",
            "get_activities",
            "get_training_log",
            "get_threshold_freshness",
            "get_readiness_history",
            "get_mood_checkins",
            "get_iqos_sticks",
        }
        assert optional.issubset(names)


# ---------------------------------------------------------------------------
# Handler tests (with mocked DB)
# ---------------------------------------------------------------------------


def _fake_wellness(**overrides):
    defaults = dict(
        id="2026-03-28",
        ctl=45.0,
        atl=50.0,
        ramp_rate=3.5,
        ctl_load=None,
        atl_load=None,
        sport_info=[
            {"type": "Swim", "ctl": 10},
            {"type": "Ride", "ctl": 20},
            {"type": "Run", "ctl": 15},
        ],
        weight=75.0,
        resting_hr=52,
        hrv=55.0,
        sleep_secs=27000,
        sleep_score=78,
        sleep_quality=3,
        body_fat=None,
        vo2max=None,
        steps=8000,
        ess_today=30.0,
        banister_recovery=72.0,
        recovery_score=75.0,
        recovery_category="good",
        recovery_recommendation="zone2_ok",
        readiness_score=70.0,
        readiness_level="good",
        ai_recommendation=None,
        ai_recommendation_gemini=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_hrv_row(**overrides):
    defaults = dict(
        algorithm="flatt_esco",
        status="green",
        rmssd_7d=55.0,
        rmssd_sd_7d=5.0,
        rmssd_60d=52.0,
        lower_bound=47.0,
        upper_bound=57.5,
        cv_7d=9.1,
        swc=2.5,
        days_available=60,
        trend_direction="stable",
        trend_slope=0.1,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_rhr_row(**overrides):
    defaults = dict(
        status="green",
        rhr_today=52,
        rhr_7d=53.0,
        rhr_sd_7d=2.0,
        rhr_30d=54.0,
        rhr_sd_30d=2.5,
        rhr_60d=55.0,
        lower_bound=52.5,
        upper_bound=55.5,
        cv_7d=3.8,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestHandleGetRecovery:
    @pytest.mark.asyncio
    async def test_returns_recovery_data(self):
        row = _fake_wellness()
        with patch("ai.tool_definitions.get_session") as mock_session:
            session_mock = AsyncMock()
            session_mock.get = AsyncMock(return_value=row)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await TOOL_HANDLERS["get_recovery"](date="2026-03-28")

        assert result["score"] == 75.0
        assert result["category"] == "good"
        assert result["recommendation"] == "zone2_ok"
        assert result["sleep_score"] == 78

    @pytest.mark.asyncio
    async def test_no_data(self):
        with patch("ai.tool_definitions.get_session") as mock_session:
            session_mock = AsyncMock()
            session_mock.get = AsyncMock(return_value=None)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await TOOL_HANDLERS["get_recovery"](date="2026-03-28")

        assert "error" in result


class TestHandleGetHrvAnalysis:
    @pytest.mark.asyncio
    async def test_specific_algorithm(self):
        row = _fake_hrv_row()
        with patch("ai.tool_definitions.get_session") as mock_session:
            session_mock = AsyncMock()
            session_mock.get = AsyncMock(return_value=row)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await TOOL_HANDLERS["get_hrv_analysis"](date="2026-03-28", algorithm="flatt_esco")

        assert result["status"] == "green"
        assert result["rmssd_7d"] == 55.0

    @pytest.mark.asyncio
    async def test_both_algorithms(self):
        flatt = _fake_hrv_row(algorithm="flatt_esco")
        aie = _fake_hrv_row(algorithm="ai_endurance", status="yellow")

        with patch("ai.tool_definitions.get_session") as mock_session:
            session_mock = AsyncMock()

            async def mock_get(model, key):
                if key == ("2026-03-28", "flatt_esco"):
                    return flatt
                if key == ("2026-03-28", "ai_endurance"):
                    return aie
                return None

            session_mock.get = mock_get
            mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await TOOL_HANDLERS["get_hrv_analysis"](date="2026-03-28")

        assert "flatt_esco" in result
        assert "ai_endurance" in result
        assert result["flatt_esco"]["status"] == "green"
        assert result["ai_endurance"]["status"] == "yellow"


class TestHandleGetTrainingLoad:
    @pytest.mark.asyncio
    async def test_computes_tsb(self):
        row = _fake_wellness(ctl=45.0, atl=50.0)
        with patch("ai.tool_definitions.get_session") as mock_session:
            session_mock = AsyncMock()
            session_mock.get = AsyncMock(return_value=row)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await TOOL_HANDLERS["get_training_load"](date="2026-03-28")

        assert result["tsb"] == -5.0
        assert result["interpretation"]["tsb_zone"] == "optimal"
        assert result["sport_ctl"]["swim"] == 10
        assert result["sport_ctl"]["bike"] == 20
        assert result["sport_ctl"]["run"] == 15


# ---------------------------------------------------------------------------
# _execute_tool dispatch
# ---------------------------------------------------------------------------


class TestExecuteTool:
    @pytest.mark.asyncio
    async def test_dispatches_to_handler(self):
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        # Mock a handler
        with patch.dict(TOOL_HANDLERS, {"get_recovery": AsyncMock(return_value={"score": 80})}):
            result = await agent._execute_tool("get_recovery", {"date": "2026-03-28"})
        assert result["score"] == 80

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        result = await agent._execute_tool("unknown_tool", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_handler_exception(self):
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        with patch.dict(TOOL_HANDLERS, {"get_recovery": AsyncMock(side_effect=RuntimeError("DB down"))}):
            result = await agent._execute_tool("get_recovery", {"date": "2026-03-28"})
        assert "error" in result
        assert "DB down" in result["error"]


# ---------------------------------------------------------------------------
# Tool-use loop
# ---------------------------------------------------------------------------


def _make_tool_use_response(tool_calls):
    """Create a fake Anthropic response with tool_use blocks."""
    blocks = []
    for i, (name, input_data) in enumerate(tool_calls):
        blocks.append(SimpleNamespace(type="tool_use", id=f"call_{i}", name=name, input=input_data))
    return SimpleNamespace(stop_reason="tool_use", content=blocks)


def _make_text_response(text):
    """Create a fake Anthropic response with a text block."""
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


class TestToolUseLoop:
    @pytest.mark.asyncio
    async def test_simple_loop(self):
        """Claude calls one tool, then returns text."""
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        # First call: Claude wants get_recovery
        tool_response = _make_tool_use_response([("get_recovery", {"date": "2026-03-28"})])
        text_response = _make_text_response("Готовность: зелёная")

        agent.client.messages.create = AsyncMock(side_effect=[tool_response, text_response])

        with patch.dict(TOOL_HANDLERS, {"get_recovery": AsyncMock(return_value={"score": 80})}):
            result = await agent.get_morning_recommendation_v2(date(2026, 3, 28))

        assert "Готовность" in result
        assert agent.client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_max_iterations_safety(self):
        """Loop terminates after max_iterations even if Claude keeps calling tools."""
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        # Always return tool_use
        tool_response = _make_tool_use_response([("get_recovery", {"date": "2026-03-28"})])
        agent.client.messages.create = AsyncMock(return_value=tool_response)

        with patch.dict(TOOL_HANDLERS, {"get_recovery": AsyncMock(return_value={"score": 80})}):
            result = await agent.get_morning_recommendation_v2(date(2026, 3, 28))

        # 1 initial + 10 iterations = 11 calls
        assert agent.client.messages.create.call_count == 11
        # No text block → fallback message
        assert "Не удалось" in result

    @pytest.mark.asyncio
    async def test_multi_tool_calls(self):
        """Claude calls multiple tools in one turn."""
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent.model = "claude-sonnet-4-6"
        agent.client = MagicMock()

        tool_response = _make_tool_use_response(
            [
                ("get_recovery", {"date": "2026-03-28"}),
                ("get_hrv_analysis", {"date": "2026-03-28"}),
            ]
        )
        text_response = _make_text_response("Отчёт готов")

        agent.client.messages.create = AsyncMock(side_effect=[tool_response, text_response])

        with patch.dict(
            TOOL_HANDLERS,
            {
                "get_recovery": AsyncMock(return_value={"score": 80}),
                "get_hrv_analysis": AsyncMock(return_value={"status": "green"}),
            },
        ):
            result = await agent.get_morning_recommendation_v2(date(2026, 3, 28))

        assert result == "Отчёт готов"


# ---------------------------------------------------------------------------
# Config toggle
# ---------------------------------------------------------------------------


class TestConfigToggle:
    def test_config_default(self):
        from config import Settings

        s = Settings(
            INTERVALS_API_KEY="x",
            INTERVALS_ATHLETE_ID="i1",
            TELEGRAM_BOT_TOKEN="x",
            TELEGRAM_CHAT_ID="1",
            ANTHROPIC_API_KEY="x",
        )
        assert s.AI_USE_TOOL_USE is True

    def test_config_disabled(self):
        from config import Settings

        s = Settings(
            INTERVALS_API_KEY="x",
            INTERVALS_ATHLETE_ID="i1",
            TELEGRAM_BOT_TOKEN="x",
            TELEGRAM_CHAT_ID="1",
            ANTHROPIC_API_KEY="x",
            AI_USE_TOOL_USE=False,
        )
        assert s.AI_USE_TOOL_USE is False
