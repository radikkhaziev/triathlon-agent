"""Telegram message formatting for morning reports and bot commands."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from data.models import RecoveryScore, Wellness

if TYPE_CHECKING:
    from data.database import ActivityHrvRow, ActivityRow, WellnessRow

# Russian month names for date formatting
_MONTHS_RU = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

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


def format_duration(seconds: int | None) -> str:
    """Format seconds as 'Xh Ym' or 'Ym'."""
    if not seconds:
        return "—"
    h, remainder = divmod(seconds, 3600)
    m = remainder // 60
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def sport_emoji(activity_type: str | None) -> str:
    """Return sport emoji based on activity type."""
    if not activity_type:
        return "🏋️"
    t = activity_type.lower()
    if "ride" in t or "bike" in t or "cycling" in t:
        return "🚴"
    if "run" in t:
        return "🏃"
    if "swim" in t:
        return "🏊"
    return "🏋️"


def build_post_activity_message(activity: ActivityRow, hrv: ActivityHrvRow) -> str:
    """Build short post-activity DFA notification (3-4 lines max)."""
    emoji = sport_emoji(activity.type)
    dur = format_duration(activity.moving_time)
    tss = f" | TSS {activity.icu_training_load:.0f}" if activity.icu_training_load else ""

    lines: list[str] = [f"{emoji} {activity.type or '?'} {dur}{tss}"]

    # DFA a1 line
    if hrv.dfa_a1_warmup is not None or hrv.dfa_a1_mean is not None:
        parts = []
        if hrv.dfa_a1_warmup is not None:
            parts.append(f"{hrv.dfa_a1_warmup:.2f} (warmup)")
        if hrv.dfa_a1_mean is not None:
            parts.append(f"{hrv.dfa_a1_mean:.2f} (avg)")
        lines.append(f"DFA a1: {' → '.join(parts)}")

    # Ra line
    if hrv.ra_pct is not None:
        ra_emoji = "✅" if hrv.ra_pct > -5 else "⚠️"
        lines.append(f"Ra: {hrv.ra_pct:+.1f}% {ra_emoji}")

    # HRVT1 line
    if hrv.hrvt1_hr is not None:
        hrvt1 = f"HRVT1: {hrv.hrvt1_hr:.0f} bpm"
        if hrv.hrvt1_power is not None:
            hrvt1 += f" / {hrv.hrvt1_power:.0f}W"
        if hrv.hrvt1_pace is not None:
            hrvt1 += f" / {hrv.hrvt1_pace}"
        lines.append(hrvt1)

    # Da line (only if ≥40 min)
    if hrv.da_pct is not None and activity.moving_time and activity.moving_time >= 2400:
        lines.append(f"Da: {hrv.da_pct:+.1f}%")

    return "\n".join(lines)


def _format_workout_short(w) -> str:
    """Format a ScheduledWorkoutRow as short string: 'Плавание 21м'."""
    sport_names = {
        "Swim": "Плавание",
        "Ride": "Вело",
        "VirtualRide": "Вело",
        "Run": "Бег",
        "WeightTraining": "Силовая",
    }
    sport = sport_names.get(w.type or "", w.type or "Тренировка")

    name_part = ""
    if w.name:
        # Strip sport prefix like "CYCLING:" or "SWIMMING:"
        parts = w.name.split(":", 1)
        name_part = parts[1].strip() if len(parts) > 1 else parts[0].strip()

    dur = format_duration(w.moving_time)

    if name_part:
        return f"{sport} {name_part} {dur}"
    return f"{sport} {dur}"


def build_evening_message(
    row: WellnessRow | None,
    activities: list[ActivityRow],
    hrv_analyses: list[ActivityHrvRow],
    tomorrow_workouts: list | None = None,
) -> str:
    """Build evening report message."""
    today = date.today()
    date_str = f"{today.day} {_MONTHS_RU.get(today.month, '')}"

    lines: list[str] = [f"📊 Итог дня — {date_str}", ""]

    # Activities section
    if activities:
        total_tss = sum(a.icu_training_load or 0 for a in activities)
        lines.append(f"Тренировки: {len(activities)} | TSS: {total_tss:.0f}")
        for a in activities:
            emoji = sport_emoji(a.type)
            dur = format_duration(a.moving_time)
            tss = f" (TSS {a.icu_training_load:.0f})" if a.icu_training_load else ""
            lines.append(f"  {emoji} {a.type or '?'} {dur}{tss}")
    else:
        lines.append("🏋️ День отдыха")

    lines.append("")

    # Recovery section
    if row:
        if row.recovery_score is not None:
            emoji, title = CATEGORY_DISPLAY.get(row.recovery_category or "", ("⚪", "—"))
            lines.append(f"Recovery: {row.recovery_score:.0f}/100 ({title.lower()})")

        # ESS / Banister
        ess_banister_parts = []
        if row.ess_today is not None:
            ess_banister_parts.append(f"ESS: {row.ess_today:.1f}")
        if row.banister_recovery is not None:
            ess_banister_parts.append(f"Banister: {row.banister_recovery:.0f}%")
        if ess_banister_parts:
            lines.append(" | ".join(ess_banister_parts))

        # HRV
        if row.hrv is not None:
            hrv_emoji = STATUS_EMOJI.get(row.readiness_level or "", "⚪")
            lines.append(f"HRV: {hrv_emoji} {row.hrv:.1f} мс")

        # RHR
        if row.resting_hr is not None:
            lines.append(f"RHR: {row.resting_hr} уд/мин")

    # DFA Ra section
    processed = [h for h in hrv_analyses if h.processing_status == "processed" and h.ra_pct is not None]
    if processed:
        ra_parts = []
        for h in processed:
            sport = h.activity_type.lower() if h.activity_type else "?"
            ra_parts.append(f"Ra {h.ra_pct:+.1f}% ({sport})")
        lines.append(f"DFA: {' | '.join(ra_parts)}")

    # Tomorrow's plan
    if tomorrow_workouts:
        workout_strs = [_format_workout_short(w) for w in tomorrow_workouts if w.category == "WORKOUT"]
        if workout_strs:
            lines.append("")
            lines.append(f"📋 Завтра: {', '.join(workout_strs)}")
    elif tomorrow_workouts is not None:
        lines.append("")
        lines.append("📋 Завтра: отдых")

    return "\n".join(lines)


def build_morning_message(row: WellnessRow) -> str:
    """Build full morning report text from a WellnessRow (summary + AI recommendation)."""
    recovery = None
    if row.recovery_score is not None:
        recovery = RecoveryScore(
            score=row.recovery_score,
            category=row.recovery_category or "moderate",
            recommendation=row.recovery_recommendation or "zone1_long",
        )

    wellness = Wellness(sleep_score=row.sleep_score, sleep_secs=row.sleep_secs)
    summary = build_report_summary(recovery=recovery, sleep_data=wellness)

    if row.ai_recommendation:
        summary += f"\n\n{row.ai_recommendation}"

    return summary
