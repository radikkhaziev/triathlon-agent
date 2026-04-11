"""Tool filtering — select relevant MCP tools based on message content."""

TOOL_GROUPS: dict[str, list[str]] = {
    "core": [
        "get_wellness",
        "get_recovery",
        "get_hrv_analysis",
        "get_rhr_analysis",
        "get_training_load",
        "get_scheduled_workouts",
        "get_activities",
    ],
    "garmin": [
        "get_garmin_sleep",
        "get_garmin_readiness",
        "get_garmin_daily_metrics",
        "get_garmin_race_predictions",
        "get_garmin_vo2max_trend",
        "get_garmin_abnormal_hr_events",
    ],
    "workouts": [
        "suggest_workout",
        "remove_ai_workout",
        "list_ai_workouts",
        "compose_workout",
        "create_exercise_card",
        "update_exercise_card",
        "list_exercise_cards",
        "get_animation_guidelines",
        "list_workout_cards",
        "remove_workout_card",
    ],
    "tracking": [
        "save_mood_checkin_tool",
        "get_mood_checkins_tool",
        "get_iqos_sticks",
    ],
    "analysis": [
        "get_activity_details",
        "get_activity_hrv",
        "get_efficiency_trend",
        "get_training_log",
        "get_personal_patterns",
        "get_goal_progress",
        "get_thresholds_history",
        "get_readiness_history",
        "get_threshold_freshness",
        "get_wellness_range",
        "get_zones",
        "get_weight_trend",
        "get_workout_compliance",
    ],
    "admin": [
        "create_github_issue",
        "get_github_issues",
        "get_api_usage",
        "create_ramp_test_tool",
    ],
}

ALWAYS_INCLUDE = {"core", "tracking"}

KEYWORD_TO_GROUPS: dict[str, list[str]] = {
    # Garmin
    "garmin": ["garmin"],
    "sleep": ["garmin"],
    "сон": ["garmin"],
    "body battery": ["garmin"],
    "readiness": ["garmin"],
    "vo2max": ["garmin"],
    "vo2": ["garmin"],
    "race prediction": ["garmin"],
    "прогноз": ["garmin"],
    # Workouts
    "тренировк": ["workouts"],
    "workout": ["workouts"],
    "зарядк": ["workouts"],
    "exercise": ["workouts"],
    "упражнен": ["workouts"],
    "suggest": ["workouts"],
    "plan": ["workouts"],
    "план": ["workouts"],
    "создай тренировку": ["workouts"],
    "анимац": ["workouts"],
    "карточк": ["workouts"],
    # Analysis
    "детал": ["analysis"],
    "detail": ["analysis"],
    "тренд": ["analysis"],
    "trend": ["analysis"],
    "прогресс": ["analysis"],
    "progress": ["analysis"],
    "goal": ["analysis"],
    "цел": ["analysis"],
    "эффективн": ["analysis"],
    "efficiency": ["analysis"],
    "drift": ["analysis"],
    "decoupling": ["analysis"],
    "зон": ["analysis"],
    "zone": ["analysis"],
    "порог": ["analysis"],
    "threshold": ["analysis"],
    "вес": ["analysis"],
    "weight": ["analysis"],
    "compliance": ["analysis"],
    "выполнен": ["analysis"],
    "dfa": ["analysis"],
    "паттерн": ["analysis"],
    "log": ["analysis"],
    "лог": ["analysis"],
    # Tracking
    "настроен": ["tracking"],
    "mood": ["tracking"],
    "стик": ["tracking"],
    "iqos": ["tracking"],
    "курен": ["tracking"],
    # Admin
    "issue": ["admin"],
    "баг": ["admin"],
    "bug": ["admin"],
    "github": ["admin"],
    "фича": ["admin"],
    "feature": ["admin"],
    "задач": ["admin"],
    "ramp": ["admin"],
    "тест": ["admin"],
    "usage": ["admin"],
    "токен": ["admin"],
}


def select_tool_groups(user_message: str) -> set[str]:
    """Determine which tool groups are needed based on message content."""
    groups = set(ALWAYS_INCLUDE)
    msg_lower = user_message.lower()

    for keyword, keyword_groups in KEYWORD_TO_GROUPS.items():
        if keyword in msg_lower:
            groups.update(keyword_groups)

    return groups


def filter_tools(all_tools: list[dict], groups: set[str]) -> list[dict]:
    """Filter tool list to only include tools from selected groups."""
    needed_names: set[str] = set()
    for group in groups:
        needed_names.update(TOOL_GROUPS.get(group, []))

    return [t for t in all_tools if t["name"] in needed_names]
