"""Tests for bot/tool_filter.py — tool group selection and filtering."""

from bot.tool_filter import TOOL_GROUPS, filter_tools, select_tool_groups


class TestSelectToolGroups:
    def test_core_always_included(self):
        groups = select_tool_groups("привет")
        assert "core" in groups
        assert "tracking" in groups

    def test_plain_message_only_core_and_tracking(self):
        groups = select_tool_groups("как дела?")
        assert groups == {"core", "tracking"}

    def test_garmin_sleep(self):
        assert "garmin" in select_tool_groups("как мой сон?")

    def test_garmin_readiness(self):
        assert "garmin" in select_tool_groups("garmin readiness")

    def test_garmin_body_battery(self):
        assert "garmin" in select_tool_groups("body battery низкий")

    def test_garmin_vo2(self):
        assert "garmin" in select_tool_groups("покажи vo2max")

    def test_workout_keywords(self):
        groups = select_tool_groups("создай тренировку на велосипед")
        assert "workouts" in groups

    def test_workout_plan(self):
        assert "workouts" in select_tool_groups("план на завтра")

    def test_workout_exercise(self):
        assert "workouts" in select_tool_groups("упражнения для ягодиц")

    def test_analysis_trend(self):
        assert "analysis" in select_tool_groups("покажи тренд эффективности")

    def test_analysis_zone(self):
        assert "analysis" in select_tool_groups("какие у меня зоны?")

    def test_analysis_weight(self):
        assert "analysis" in select_tool_groups("как мой вес?")

    def test_analysis_dfa(self):
        assert "analysis" in select_tool_groups("dfa alpha1 results")

    def test_analysis_threshold(self):
        assert "analysis" in select_tool_groups("мои пороги устарели?")

    def test_admin_issue(self):
        assert "admin" in select_tool_groups("создай issue про баг")

    def test_admin_github(self):
        assert "admin" in select_tool_groups("github issues")

    def test_admin_ramp(self):
        assert "admin" in select_tool_groups("нужен ramp тест")

    def test_tracking_mood(self):
        assert "tracking" in select_tool_groups("как мой mood?")

    def test_tracking_iqos(self):
        assert "tracking" in select_tool_groups("сколько стиков сегодня?")

    def test_multiple_groups(self):
        groups = select_tool_groups("покажи сон и тренд дрифта")
        assert "garmin" in groups
        assert "analysis" in groups

    def test_case_insensitive(self):
        assert "garmin" in select_tool_groups("Покажи GARMIN данные")

    def test_empty_message(self):
        groups = select_tool_groups("")
        assert groups == {"core", "tracking"}


class TestFilterTools:
    def test_filters_to_core_only(self):
        all_tools = [{"name": "get_wellness"}, {"name": "get_garmin_sleep"}, {"name": "suggest_workout"}]
        filtered = filter_tools(all_tools, {"core"})
        assert len(filtered) == 1
        assert filtered[0]["name"] == "get_wellness"

    def test_multiple_groups(self):
        all_tools = [
            {"name": "get_wellness"},
            {"name": "get_garmin_sleep"},
            {"name": "suggest_workout"},
            {"name": "save_mood_checkin_tool"},
        ]
        filtered = filter_tools(all_tools, {"core", "garmin"})
        names = {t["name"] for t in filtered}
        assert "get_wellness" in names
        assert "get_garmin_sleep" in names
        assert "suggest_workout" not in names

    def test_empty_groups(self):
        all_tools = [{"name": "get_wellness"}]
        assert filter_tools(all_tools, set()) == []

    def test_unknown_group_ignored(self):
        all_tools = [{"name": "get_wellness"}]
        filtered = filter_tools(all_tools, {"nonexistent"})
        assert filtered == []


class TestToolGroupIntegrity:
    def test_all_tools_in_exactly_one_group(self):
        """No tool should appear in multiple groups."""
        seen: dict[str, str] = {}
        for group, tools in TOOL_GROUPS.items():
            for tool in tools:
                assert tool not in seen, f"{tool} in both {seen[tool]} and {group}"
                seen[tool] = group

    def test_total_tool_count(self):
        """All groups combined should cover expected tool count."""
        all_names: set[str] = set()
        for tools in TOOL_GROUPS.values():
            all_names.update(tools)
        # 45 tools total (7 core + 6 garmin + 10 workouts + 3 tracking + 14 analysis + 5 admin)
        assert len(all_names) == 45

    def test_core_has_essential_tools(self):
        core = set(TOOL_GROUPS["core"])
        assert "get_wellness" in core
        assert "get_recovery" in core
        assert "get_hrv_analysis" in core
        assert "get_scheduled_workouts" in core
