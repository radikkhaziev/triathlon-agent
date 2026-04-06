"""Tests for MCP Phase 2: Claude tool-use for morning analysis."""

from tasks.tools import MORNING_TOOLS

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
