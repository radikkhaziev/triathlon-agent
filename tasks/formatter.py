"""Message formatting for dramatiq actors — morning, evening, post-activity reports."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from bot.i18n import _

if TYPE_CHECKING:
    from data.db import Activity, ActivityHrv, Wellness

# ---------------------------------------------------------------------------
# Shared constants (language-aware via _())
# ---------------------------------------------------------------------------


def _category_display() -> dict:
    return {
        "excellent": ("🟢", _("ОТЛИЧНОЕ ВОССТАНОВЛЕНИЕ")),
        "good": ("🟢", _("ГОТОВ К НАГРУЗКЕ")),
        "moderate": ("🟡", _("УМЕРЕННАЯ НАГРУЗКА")),
        "low": ("🔴", _("РЕКОМЕНДОВАН ОТДЫХ")),
    }


def _recommendation_text() -> dict:
    return {
        "zone2_ok": _("тренировка Z2 — полный объём"),
        "zone1_long": _("только аэробная база, Z1-Z2"),
        "zone1_short": _("лёгкая активность, 30-45 мин"),
        "skip": _("отдых — не тренироваться"),
    }


# Keep static refs for code that doesn't need i18n (e.g. MCP tools)
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


_MONTHS = {
    "ru": {
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
    },
    "en": {
        1: "Jan",
        2: "Feb",
        3: "Mar",
        4: "Apr",
        5: "May",
        6: "Jun",
        7: "Jul",
        8: "Aug",
        9: "Sep",
        10: "Oct",
        11: "Nov",
        12: "Dec",
    },
}


def _get_months() -> dict:
    from bot.i18n import get_language

    return _MONTHS.get(get_language(), _MONTHS["ru"])


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
    """Return sport emoji based on canonical activity type."""
    if not activity_type:
        return "🏋️"
    _EMOJI = {"Ride": "🚴", "Run": "🏃", "Swim": "🏊"}
    return _EMOJI.get(activity_type, "🏋️")


def build_post_activity_message(activity: Activity, hrv: ActivityHrv) -> str:
    """Build short post-activity DFA notification (3-4 lines max)."""
    emoji = sport_emoji(activity.type)
    dur = format_duration(activity.moving_time)
    tss = f" | TSS {activity.icu_training_load:.0f}" if activity.icu_training_load else ""

    lines: list[str] = [f"{emoji} {activity.type or '?'} {dur}{tss}"]

    if hrv.dfa_a1_warmup is not None or hrv.dfa_a1_mean is not None:
        parts = []
        if hrv.dfa_a1_warmup is not None:
            parts.append(f"{hrv.dfa_a1_warmup:.2f} (warmup)")
        if hrv.dfa_a1_mean is not None:
            parts.append(f"{hrv.dfa_a1_mean:.2f} (avg)")
        lines.append(f"DFA a1: {' → '.join(parts)}")

    if hrv.ra_pct is not None:
        ra_emoji = "✅" if hrv.ra_pct > -5 else "⚠️"
        lines.append(f"Ra: {hrv.ra_pct:+.1f}% {ra_emoji}")

    if hrv.hrvt1_hr is not None:
        hrvt1 = f"HRVT1: {hrv.hrvt1_hr:.0f} bpm"
        if hrv.hrvt1_power is not None:
            hrvt1 += f" / {hrv.hrvt1_power:.0f}W"
        if hrv.hrvt1_pace is not None:
            hrvt1 += f" / {hrv.hrvt1_pace}"
        lines.append(hrvt1)

    if hrv.da_pct is not None and activity.moving_time and activity.moving_time >= 2400:
        lines.append(f"Da: {hrv.da_pct:+.1f}%")

    return "\n".join(lines)


def _format_workout_short(w) -> str:
    """Format a ScheduledWorkout as short string."""
    sport_names = {
        "Swim": "Плавание",
        "Ride": "Вело",
        "Run": "Бег",
        "Other": "Другое",
    }
    sport = sport_names.get(w.type or "", w.type or "Тренировка")
    name_part = ""
    if w.name:
        parts = w.name.split(":", 1)
        name_part = parts[1].strip() if len(parts) > 1 else parts[0].strip()
    dur = format_duration(w.moving_time)
    return f"{sport} {name_part} {dur}" if name_part else f"{sport} {dur}"


def build_evening_message(
    row: Wellness | None,
    activities: list[Activity],
    hrv_analyses: list[ActivityHrv],
    tomorrow_workouts: list | None = None,
) -> str:
    """Build evening report message."""
    today = date.today()
    date_str = f"{today.day} {_get_months().get(today.month, '')}"

    lines: list[str] = [f"📊 Итог дня — {date_str}", ""]

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

    if row:
        if row.recovery_score is not None:
            emoji, title = _category_display().get(row.recovery_category or "", ("⚪", "—"))
            lines.append(f"Recovery: {row.recovery_score:.0f}/100 ({title.lower()})")

        ess_banister_parts = []
        if row.ess_today is not None:
            ess_banister_parts.append(f"ESS: {row.ess_today:.1f}")
        if row.banister_recovery is not None:
            ess_banister_parts.append(f"Banister: {row.banister_recovery:.0f}%")
        if ess_banister_parts:
            lines.append(" | ".join(ess_banister_parts))

        if row.hrv is not None:
            hrv_emoji = STATUS_EMOJI.get(row.readiness_level or "", "⚪")
            lines.append(f"HRV: {hrv_emoji} {row.hrv:.1f} мс")

        if row.resting_hr is not None:
            lines.append(f"RHR: {row.resting_hr} уд/мин")

    processed = [h for h in hrv_analyses if h.processing_status == "processed" and h.ra_pct is not None]
    if processed:
        ra_parts = []
        for h in processed:
            sport = h.activity_type.lower() if h.activity_type else "?"
            ra_parts.append(f"Ra {h.ra_pct:+.1f}% ({sport})")
        lines.append(f"DFA: {' | '.join(ra_parts)}")

    if tomorrow_workouts:
        workout_strs = [_format_workout_short(w) for w in tomorrow_workouts if w.category == "WORKOUT"]
        if workout_strs:
            lines.append("")
            lines.append(f"📋 Завтра: {', '.join(workout_strs)}")
    elif tomorrow_workouts is not None:
        lines.append("")
        lines.append("📋 Завтра: отдых")

    return "\n".join(lines)


def build_morning_message(
    row: Wellness,
    threshold_drift: dict | None = None,
) -> str:
    """Build compact morning Telegram message."""
    lines = []

    score = row.recovery_score or 0
    cat = row.recovery_category or "moderate"
    cat_display = _category_display().get(cat, ("", cat))[1]
    hrv_emoji = STATUS_EMOJI.get(row.readiness_level or "", "⚪")
    lines.append(f"Recovery {score:.0f} ({cat_display}), HRV {hrv_emoji}")

    tsb = (row.ctl - row.atl) if row.ctl and row.atl else None
    if tsb is not None and tsb < -25:
        lines.append(f"TSB: {tsb:+.0f} 🔴 (overtraining risk)")
    elif tsb is not None and tsb < -10:
        lines.append(f"TSB: {tsb:+.0f} ⚠️ (productive overreach)")

    if threshold_drift and threshold_drift.get("alerts"):
        lines.append("")
        lines.append("🔔 ПОРОГИ — РАССМОТРИ ОБНОВЛЕНИЕ")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        for alert in threshold_drift["alerts"]:
            lines.append(f"HRVT1 стабильно {alert['measured_avg']} bpm ({alert['tests_count']} теста)")
            lines.append(f"Текущий LTHR: {alert['config_value']} bpm ({alert['diff_pct']:+.1f}%)")
            lines.append("→ Обнови LTHR в настройках")

    return "\n".join(lines)
