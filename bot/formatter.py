"""Telegram message formatting for morning reports and bot commands."""

from data.models import RecoveryScore, Wellness

# ---------------------------------------------------------------------------
# Display mappings
# ---------------------------------------------------------------------------

CATEGORY_DISPLAY = {
    "excellent": ("🟢", "ОТЛИЧНОЕ ВОССТАНОВЛЕНИЕ"),
    "good": ("🟢", "ГОТОВ К НАГРУЗКЕ"),
    "moderate": ("🟡", "УМЕРЕННАЯ НАГРУЗКА"),
    "low": ("🔴", "РЕКОМЕНДОВАН ОТДЫХ"),
}

RECOMMENDATION_TEXT = {
    "zone2_ok": "тренировка Z2 — полный объём",
    "zone1_long": "только аэробная база, Z1-Z2",
    "zone1_short": "лёгкая активность, 30-45 мин",
    "skip": "отдых — не тренироваться",
}

STATUS_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴", "insufficient_data": "⚪"}


def build_report_summary(
    recovery: RecoveryScore | None = None,
    sleep_data: Wellness | None = None,
) -> str:
    """Short summary for the Telegram message that accompanies the Mini App button."""
    lines: list[str] = []

    if recovery:
        emoji, title = CATEGORY_DISPLAY.get(recovery.category, ("⚪", "СТАТУС НЕИЗВЕСТЕН"))
        rec_text = RECOMMENDATION_TEXT.get(recovery.recommendation, recovery.recommendation)
        lines.append(f"{emoji} {title}")
        lines.append(f"Readiness: {recovery.score:.0f}/100")
        lines.append(f"Rec: {rec_text}")
    else:
        lines.append("☀️ Morning Report")

    if sleep_data and sleep_data.sleep_score:
        lines.append(f"Sleep: {sleep_data.sleep_score}/100")

    return "\n".join(lines)
