"""Telegram message formatting for morning reports and bot commands."""

from data.models import (
    HRVData,
    RecoveryScore,
    RhrStatus,
    RmssdStatus,
    ScheduledWorkout,
    SleepData,
    TrainingReadinessData,
)

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


def _format_duration(seconds: int) -> str:
    """Format seconds as 'Xч Yм'."""
    h, m = divmod(seconds // 60, 60)
    if h:
        return f"{h}ч {m}м"
    return f"{m}м"


def _cv_verdict(cv: float) -> str:
    if cv < 5:
        return "высокая"
    elif cv < 10:
        return "нормальная"
    else:
        return "нестабильная"


def _swc_verdict(hrv_today: float | None, rmssd_60d: float | None, swc: float | None) -> str | None:
    if hrv_today is None or rmssd_60d is None or swc is None:
        return None
    delta = hrv_today - rmssd_60d
    if abs(delta) < swc:
        return "в пределах шума"
    elif delta > swc:
        return "значимое улучшение"
    else:
        return "значимое снижение"


# ---------------------------------------------------------------------------
# Morning Report
# ---------------------------------------------------------------------------


def build_morning_report(
    sleep_data: SleepData,
    rmssd: RmssdStatus,
    rhr: RhrStatus | None = None,
    recovery: RecoveryScore | None = None,
    hrv_data: HRVData | None = None,
    body_battery_morning: int | None = None,
    resting_hr: float | None = None,
    readiness: TrainingReadinessData | None = None,
    workouts: list[ScheduledWorkout] | None = None,
) -> str:
    """Compose the morning Telegram report per CLAUDE.md spec."""
    lines: list[str] = []

    # --- Header with recovery score ---
    if recovery:
        emoji, title = CATEGORY_DISPLAY.get(recovery.category, ("⚪", "СТАТУС НЕИЗВЕСТЕН"))
        rec_text = RECOMMENDATION_TEXT.get(recovery.recommendation, recovery.recommendation)
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"{emoji} {title}")
        lines.append(f"Готовность: {recovery.score:.0f}/100")
        lines.append(f"Рекомендация: {rec_text}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
    else:
        lines.append("☀️ Утренний отчёт")
        lines.append("")

    # --- HRV (RMSSD) ---
    if rmssd.status != "insufficient_data":
        hrv_emoji = STATUS_EMOJI.get(rmssd.status, "⚪")
        trend_str = ""
        if rmssd.trend:
            trend_str = f" {rmssd.trend.emoji}"

        hrv_today = rmssd.rmssd_7d  # approximate for display
        if hrv_data and hrv_data.hrv_last_night:
            hrv_today = hrv_data.hrv_last_night

        hrv_delta = ""
        if rmssd.rmssd_7d and hrv_today:
            delta_pct = (hrv_today - rmssd.rmssd_7d) / rmssd.rmssd_7d * 100
            hrv_delta = f"   Изменение:    {delta_pct:+.0f}% от нормы{trend_str}  {hrv_emoji}"

        lines.append("🫀 HRV (RMSSD)")
        if hrv_delta:
            lines.append(hrv_delta)
        if hrv_today:
            lines.append(f"   Сегодня:      {hrv_today:.0f} мс")
        lines.append(f"   Базлайн 7д:   {rmssd.rmssd_7d:.0f} мс")
        if rmssd.rmssd_60d:
            lines.append(f"   Базлайн 60д:  {rmssd.rmssd_60d:.0f} мс")

        swc_text = _swc_verdict(hrv_today, rmssd.rmssd_60d, rmssd.swc)
        if rmssd.swc and swc_text:
            lines.append(f"   SWC:          {rmssd.swc:.1f} мс  →  {swc_text}")

        if rmssd.cv_7d is not None:
            lines.append(f"   Стабильность: {_cv_verdict(rmssd.cv_7d)} (CV {rmssd.cv_7d:.1f}%)")
        lines.append("")
    elif hrv_data and hrv_data.hrv_last_night:
        lines.append(f"💓 HRV: {hrv_data.hrv_last_night:.0f} мс (мало данных для анализа)")
        lines.append("")

    # --- RHR ---
    if rhr and rhr.status != "insufficient_data":
        rhr_emoji = STATUS_EMOJI.get(rhr.status, "⚪")
        lines.append("💓 Пульс покоя")
        lines.append(f"   Сегодня:    {rhr.rhr_today:.0f} уд  {rhr_emoji}")
        if rhr.rhr_30d:
            lines.append(f"   Норма 30д:  {rhr.rhr_30d:.0f} уд")
            delta = rhr.rhr_today - rhr.rhr_30d
            lines.append(f"   Отклонение: {delta:+.0f} уд")
        lines.append("")
    elif resting_hr:
        lines.append(f"❤️ Пульс покоя: {resting_hr:.0f} уд/мин")
        lines.append("")

    # --- Sleep ---
    if sleep_data.score:
        sleep_dur = _format_duration(sleep_data.duration) if sleep_data.duration else "—"
        lines.append("😴 Сон")
        lines.append(f"   Оценка:       {sleep_data.score}/100")
        lines.append(f"   Длительность: {sleep_dur}")
        lines.append("")

    # --- Body Battery ---
    if body_battery_morning is not None:
        lines.append(f"🔋 Body Battery: {body_battery_morning}/100")
        lines.append("")

    # --- Score components breakdown ---
    if recovery and recovery.components:
        c = recovery.components
        lines.append("📊 Вклад в оценку")
        lines.append(f"   HRV:      {STATUS_EMOJI.get(rmssd.status, '⚪')} {c.get('rmssd', 0):.0f}  × 35%")
        lines.append(f"   Banister: ⚡ {c.get('banister', 0):.0f}  × 25%")
        if rhr:
            lines.append(f"   RHR:      {STATUS_EMOJI.get(rhr.status, '⚪')} {c.get('rhr', 0):.0f}  × 15%")
        lines.append(f"   Сон:      😴 {c.get('sleep', 0):.0f}  × 15%")
        lines.append(f"   Battery:  🔋 {c.get('body_battery', 0):.0f}  × 10%")

        modifiers = []
        if "late_sleep" in recovery.flags:
            modifiers.append("поздний сон −10")
        if "hrv_unstable" in recovery.flags:
            modifiers.append("нестаб. HRV −5")
        mod_text = f"  ({', '.join(modifiers)})" if modifiers else ""
        lines.append(f"   Итого: {recovery.score:.0f}{mod_text}")
        lines.append("")

    # --- Today's workouts ---
    if workouts:
        lines.append("🏋️ План на сегодня:")
        for w in workouts:
            dur = _format_duration(w.planned_duration_seconds) if w.planned_duration_seconds else ""
            dur_str = f" ({dur})" if dur else ""
            lines.append(f"  • {w.workout_name}{dur_str}")

    return "\n".join(lines)
