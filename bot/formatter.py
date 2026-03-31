"""Telegram message formatting — re-exports from tasks.formatter + bot-specific helpers."""

from __future__ import annotations

from datetime import date

from data.intervals.dto import RecoveryScoreDTO, WellnessDTO

# Re-export shared constants, helpers, and report builders from tasks.formatter
from tasks.formatter import (  # noqa: F401
    CATEGORY_DISPLAY,
    RECOMMENDATION_TEXT,
    STATUS_EMOJI,
    build_evening_message,
    build_morning_message,
    build_post_activity_message,
    format_duration,
    sport_emoji,
)


def build_report_summary(
    recovery: RecoveryScoreDTO | None = None,
    sleep_data: WellnessDTO | None = None,
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


def build_workout_pushed_message(
    sport: str,
    name: str,
    duration_minutes: int,
    target_tss: int | None,
    suffix: str,
    intervals_id: int | None,
    athlete_id: str,
    target_date: date | None = None,
) -> str:
    """Build Telegram notification for AI workout pushed to Intervals.icu."""
    emoji = sport_emoji(sport)
    suffix_label = "adapted" if suffix == "adapted" else "generated"

    date_str = target_date.strftime("%d.%m") if target_date else ""
    header = f"🏋️ AI тренировка добавлена на {date_str}" if date_str else "🏋️ AI тренировка добавлена"

    tss_part = f", ~{target_tss} TSS" if target_tss else ""
    detail = f"{emoji} {name} ({suffix_label})\n{duration_minutes} мин{tss_part}"

    lines = [header, "", detail]

    if intervals_id and athlete_id:
        link = f"https://intervals.icu/athlete/{athlete_id}/calendar"
        lines.append(f"\nОткрыть в Intervals.icu → {link}")

    return "\n".join(lines)
