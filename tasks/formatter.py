"""Message formatting for dramatiq actors — morning, evening, post-activity reports."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bot.i18n import _, get_language
from tasks.dto import local_today

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from data.db import Activity, ActivityHrv, Race, Wellness

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


# Keep static refs for code that doesn't need i18n (e.g. MCP tools) — Russian default
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

_CATEGORY_DISPLAY_EN = {
    "excellent": ("🟢", "EXCELLENT RECOVERY"),
    "good": ("🟢", "READY TO TRAIN"),
    "moderate": ("🟡", "MODERATE LOAD"),
    "low": ("🔴", "REST RECOMMENDED"),
}

_RECOMMENDATION_TEXT_EN = {
    "zone2_ok": "Z2 training — full volume",
    "zone1_long": "aerobic base only, Z1-Z2",
    "zone1_short": "light activity, 30-45 min",
    "skip": "rest day — no training",
}


def get_category_display(category: str, language: str = "ru") -> tuple[str, str]:
    table = _CATEGORY_DISPLAY_EN if language == "en" else CATEGORY_DISPLAY
    return table.get(category, ("⚪", "UNKNOWN" if language == "en" else "СТАТУС НЕИЗВЕСТЕН"))


def get_recommendation_text(key: str, language: str = "ru") -> str:
    table = _RECOMMENDATION_TEXT_EN if language == "en" else RECOMMENDATION_TEXT
    return table.get(key, key)


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


def _format_pace(sec_per_km: float | None) -> str | None:
    if not sec_per_km or sec_per_km <= 0:
        return None
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


def _format_hms(seconds: int | None) -> str | None:
    if not seconds:
        return None
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_post_activity_message(
    activity: Activity,
    hrv: ActivityHrv,
    race: Race | None = None,
) -> str:
    """Build short post-activity notification. Race-specific format when `race` is given."""
    if race is not None:
        return _build_post_race_message(activity, race)

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


def _ramp_failure_text(reason: dict) -> str:
    """Localize a diagnose_hrv_thresholds code dict into a user message."""
    code = reason.get("code")
    if code == "too_few_points":
        return _("слишком мало валидных точек ({count} < 20)").format(count=reason.get("count", 0))
    if code == "a1_range_high":
        return _("DFA a1 не достиг лёгкой зоны (max {max_a1} < 0.9)").format(max_a1=reason.get("max_a1"))
    if code == "a1_range_low":
        return _("последняя ступень слишком лёгкая — DFA a1 не пересёк порог (min {min_a1} > 0.80)").format(
            min_a1=reason.get("min_a1")
        )
    if code == "positive_slope":
        return _("DFA a1 не падает с ростом HR (slope={slope})").format(slope=reason.get("slope"))
    if code == "noisy_fit":
        return _("линейный фит слишком шумный (R²={r_squared} < 0.5)").format(r_squared=reason.get("r_squared"))
    if code == "out_of_range":
        return _("интерполяция вне физиологического диапазона (HRVT1={hrvt1}, HRVT2={hrvt2})").format(
            hrvt1=reason.get("hrvt1"), hrvt2=reason.get("hrvt2")
        )
    return _("неизвестная причина")


def _ramp_failure_advice(reason: dict) -> str:
    """Actionable next-step guidance for a diagnose_hrv_thresholds code.

    The diagnostic code tells *what went wrong*; this tells *what to do about it*.
    Empty string for unknown codes (no false confidence).
    """
    code = reason.get("code")
    if code == "too_few_points":
        return _(
            "Тренировка короткая — нужна work-фаза 30+ минут. " "Проверь, что ramp-protocol сгенерирован полностью."
        )
    if code == "a1_range_high":
        return _(
            "На вершине HR не поднялся достаточно. Добавь финальный шаг с "
            "более жёстким темпом или беги последний шаг до отказа."
        )
    if code == "a1_range_low":
        return _("Финальный шаг слишком лёгкий — DFA a1 не дошёл до порога. " "Бери выше темп на последних 2-3 шагах.")
    if code == "positive_slope":
        return _(
            "HR-данные подозрительные (DFA растёт вместе с HR). "
            "Проверь chest strap — оптический датчик не подходит для DFA."
        )
    if code == "noisy_fit":
        return _(
            "Слишком много шума в данных. Возможные причины: outdoor против "
            "ветра/холмов, нестабильный темп. Попробуй на тредмилле."
        )
    if code == "out_of_range":
        return _(
            "Threshold в Intervals.icu сильно расходится с реальностью. "
            "Обнови LTHR или threshold pace вручную, потом перетестируй."
        )
    # Unknown code — surfaces no advice line (caller skips on empty string),
    # but log so a new diagnose code added in `data/hrv_activity.py` doesn't
    # silently ship a UX regression. Caught in monitoring before users complain.
    logger.warning("ramp failure advice: unknown diagnose code %r — add to _ramp_failure_advice", code)
    return ""


def _drift_button_status(
    measured: float, config: float, sample_count: int, r2: float | None
) -> tuple[bool, str | None]:
    """Decide whether to surface the «Update zones» button + which hint to render.

    Returns ``(button_visible, hint_text)``. Hint is the recommendation line
    («рекомендуем обновить» / «bootstrap» / «нужен ещё один ramp»); ``None``
    when there's nothing useful to say (drift below threshold).

    **DUPLICATION WARNING:** the gating thresholds here MUST stay in sync with
    ``data/db/user.py:_drift_alert_lthr`` and ``_drift_alert_pace``. The button
    fires `actor_update_zones`, which re-reads `User.detect_threshold_drift` —
    if this UI gate is more permissive than the backend gate, the button shows
    but the actor finds nothing to push (no-op + confused user). If less
    permissive, real drift goes uncommunicated. Keep the thresholds in lockstep
    or extract a shared helper. Direct unit tests below cover all branches.
    """
    pct = (measured - config) / config * 100
    if sample_count >= 2 and abs(pct) > 5:
        return True, _("Рекомендуем обновить зоны")
    if sample_count == 1 and r2 is not None and r2 > 0.85 and abs(pct) > 10:
        return True, _("Bootstrap-обновление: высокое R² и >10% drift")
    if abs(pct) > 5:
        return False, _("для обновления зон нужен ещё один ramp test")
    return False, None


def build_ramp_test_message(
    activity: Activity,
    hrv: ActivityHrv,
    config_lthr: int | None,
    failure_reason: dict | None = None,
    hrvt1_sample_count: int = 0,
    *,
    config_threshold_pace: float | None = None,
    hrvt1_pace_sec: int | None = None,
) -> tuple[str, bool]:
    """Build ramp-test-specific notification. Returns (message, show_update_zones_button).

    Button surfaces when a new HRVT1 was detected and either:
      - ≥2 samples agree on >5% drift (standard path), or
      - bootstrap: single sample with R² > 0.85 and >10% drift — covers the
        first ramp test after config setup, where waiting for sample #2 means
        another month on stale settings. Mirrors `User.detect_threshold_drift`.

    Both LTHR (HR) and Run threshold pace are evaluated independently — drift
    on either dimension lights up the button so `actor_update_zones` can push
    whichever moved.
    """
    sport = activity.type or "?"
    lines: list[str] = [f"⚡ {_('Ramp Test')} ({sport}) — {_('результат')}"]
    show_button = False

    if hrv.hrvt1_hr is not None:
        hrvt1 = f"HRVT1: {hrv.hrvt1_hr:.0f} bpm"
        if hrv.hrvt1_power:
            hrvt1 += f" / {hrv.hrvt1_power:.0f}W"
        if hrv.hrvt1_pace:
            hrvt1 += f" / {hrv.hrvt1_pace}"
        lines.append(hrvt1)
        if hrv.hrvt2_hr:
            lines.append(f"HRVT2: {hrv.hrvt2_hr:.0f} bpm")

        meta_bits = []
        if hrv.threshold_r_squared is not None:
            meta_bits.append(f"R²={hrv.threshold_r_squared:.2f}")
        if hrv.threshold_confidence:
            meta_bits.append(hrv.threshold_confidence)
        if meta_bits:
            lines.append(f"({', '.join(meta_bits)})")

        r2 = hrv.threshold_r_squared
        soft_hints: list[str] = []  # «нужен ещё один» — collected if no drift fires

        if config_lthr:
            lthr_pct = (hrv.hrvt1_hr - config_lthr) / config_lthr * 100
            lines.append(f"{_('текущий LTHR')}: {config_lthr} bpm ({lthr_pct:+.1f}%)")
            visible, hint = _drift_button_status(hrv.hrvt1_hr, config_lthr, hrvt1_sample_count, r2)
            if visible:
                show_button = True
                lines.append(f"💡 {hint}")
            elif hint:
                soft_hints.append(hint)

        if config_threshold_pace and hrvt1_pace_sec:
            cfg_pace = int(round(config_threshold_pace))
            pace_pct = (hrvt1_pace_sec - cfg_pace) / cfg_pace * 100
            lines.append(f"{_('текущий threshold pace')}: {cfg_pace} s/km ({pace_pct:+.1f}%)")
            visible, hint = _drift_button_status(hrvt1_pace_sec, cfg_pace, hrvt1_sample_count, r2)
            if visible and not show_button:
                show_button = True
                lines.append(f"💡 {hint}")
            elif visible:
                # Button already on for LTHR — just note pace drift too
                lines.append(f"💡 {hint} ({_('threshold pace')})")
            elif hint:
                soft_hints.append(hint)

        # Show one soft hint only when no drift fired anywhere — avoids
        # «recommend update» + «need another test» showing together.
        if not show_button and soft_hints:
            lines.append(f"ℹ️ {soft_hints[0]}")
    else:
        lines.append(f"⚠️ {_('детекция HRVT не удалась')}")
        if failure_reason:
            lines.append(f"{_('причина')}: {_ramp_failure_text(failure_reason)}")
            advice = _ramp_failure_advice(failure_reason)
            if advice:
                lines.append(f"💡 {advice}")

    return "\n".join(lines), show_button


# ---------------------------------------------------------------------------
# RPE inline keyboard (Borg CR-10, see docs/RPE_SPEC.md)
# ---------------------------------------------------------------------------

# Anchor labels: emoji only on 1, 3, 5, 7, 10 — keeps mobile rows readable.
_RPE_BUTTON_LABELS: dict[int, str] = {
    1: "1 😴",
    2: "2",
    3: "3 😌",
    4: "4",
    5: "5 💪",
    6: "6",
    7: "7 🔥",
    8: "8",
    9: "9",
    10: "10 🤯",
}

# Public mapping for places that render the value back to the user (e.g. the
# "RPE: 7 🔥" suffix appended to the message after a successful tap).
RPE_EMOJI_BY_VALUE: dict[int, str] = {
    1: "😴",
    2: "",
    3: "😌",
    4: "",
    5: "💪",
    6: "",
    7: "🔥",
    8: "",
    9: "",
    10: "🤯",
}


def rpe_label_with_emoji(value: int) -> str:
    """Format ``"7 🔥"`` for in-message rendering. Bare number when no anchor emoji."""
    emoji = RPE_EMOJI_BY_VALUE.get(value, "")
    return f"{value} {emoji}".strip()


def build_rpe_keyboard(activity_id: str) -> dict:
    """Two-row inline keyboard for Borg CR-10 RPE rating, raw Telegram Bot API format.

    Returns a dict matching the ``inline_keyboard`` markup schema so it can
    be passed directly to :meth:`tasks.tools.TelegramTool.send_message`.

    Callback data: ``rpe:{activity_id}:{value}`` — handled by
    :func:`bot.main.handle_rpe_callback`. Single-shot semantics: handler
    edits the message to remove this keyboard after the first successful tap.
    """

    def _btn(value: int) -> dict:
        return {"text": _RPE_BUTTON_LABELS[value], "callback_data": f"rpe:{activity_id}:{value}"}

    return {
        "inline_keyboard": [
            [_btn(v) for v in (1, 2, 3, 4, 5)],
            [_btn(v) for v in (6, 7, 8, 9, 10)],
        ]
    }


def _build_post_race_message(activity: Activity, race: Race) -> str:
    """Race finish notification with distance, time, pace, fitness context."""
    sport = sport_emoji(activity.type)
    name = race.name or (activity.type or _("Гонка"))
    header = f"🏁 {sport} {_('Гонка завершена')}: {name}"
    if race.race_type:
        header += f" ({race.race_type})"

    lines: list[str] = [header]

    finish = _format_hms(race.finish_time_sec) or _format_hms(activity.moving_time)
    goal = _format_hms(race.goal_time_sec)
    dist_km = round(race.distance_m / 1000, 2) if race.distance_m else None

    time_parts: list[str] = []
    if finish:
        time_parts.append(f"⏱ {finish}" + (f" ({_('цель')}: {goal})" if goal else ""))
    if dist_km is not None:
        time_parts.append(f"📏 {dist_km} km")
    pace = _format_pace(race.avg_pace_sec_km)
    if pace:
        time_parts.append(f"⚡ {pace}")
    if time_parts:
        lines.append(" | ".join(time_parts))

    hr_parts: list[str] = []
    if activity.average_hr:
        hr_parts.append(f"💓 avg {activity.average_hr:.0f}")
    if activity.icu_training_load:
        hr_parts.append(f"TSS {activity.icu_training_load:.0f}")
    if hr_parts:
        lines.append(" | ".join(hr_parts))

    ctx_parts: list[str] = []
    if race.race_day_ctl is not None:
        ctx_parts.append(f"CTL {race.race_day_ctl:.0f}")
    if race.race_day_tsb is not None:
        ctx_parts.append(f"TSB {race.race_day_tsb:+.0f}")
    if race.race_day_recovery_score is not None:
        ctx_parts.append(f"Recovery {race.race_day_recovery_score:.0f}")
    if ctx_parts:
        lines.append("📊 " + " | ".join(ctx_parts))

    if race.placement:
        place = f"{race.placement}"
        if race.placement_total:
            place += f"/{race.placement_total}"
        lines.append(f"🏆 {_('Место')}: {place}")

    lines.append("")
    lines.append(_("Заполни детали (RPE, погода, заметки) — запомню для анализа."))

    return "\n".join(lines)


def _format_workout_short(w) -> str:
    """Format a ScheduledWorkout as short string."""
    sport_names = {
        "Swim": _("Плавание"),
        "Ride": _("Вело"),
        "Run": _("Бег"),
        "Other": _("Другое"),
    }
    sport = sport_names.get(w.type or "", w.type or _("Тренировка"))
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
    today = local_today()
    date_str = f"{today.day} {_get_months().get(today.month, '')}"

    lines: list[str] = [f"📊 {_('Итог дня')} — {date_str}", ""]

    if activities:
        total_tss = sum(a.icu_training_load or 0 for a in activities)
        lines.append(f"{_('Тренировки')}: {len(activities)} | TSS: {total_tss:.0f}")
        for a in activities:
            emoji = sport_emoji(a.type)
            dur = format_duration(a.moving_time)
            tss = f" (TSS {a.icu_training_load:.0f})" if a.icu_training_load else ""
            lines.append(f"  {emoji} {a.type or '?'} {dur}{tss}")
    else:
        lines.append(f"🏋️ {_('День отдыха')}")

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
            lines.append(f"HRV: {hrv_emoji} {row.hrv:.1f} {_('мс')}")

        if row.resting_hr is not None:
            lines.append(f"RHR: {row.resting_hr} {_('уд/мин')}")

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
            lines.append(f"📋 {_('Завтра')}: {', '.join(workout_strs)}")
    elif tomorrow_workouts is not None:
        lines.append("")
        lines.append(f"📋 {_('Завтра')}: {_('отдых')}")

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
        lines.append(f"🔔 {_('ПОРОГИ — РАССМОТРИ ОБНОВЛЕНИЕ')}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        for alert in threshold_drift["alerts"]:
            lines.append(f"{_('HRVT1 стабильно')} {alert['measured_avg']} bpm ({alert['tests_count']} {_('теста')})")
            lines.append(f"{_('Текущий LTHR')}: {alert['config_value']} bpm ({alert['diff_pct']:+.1f}%)")
            lines.append(f"→ {_('Обнови LTHR в настройках')}")

    return "\n".join(lines)


def build_onboarding_hey_message() -> str:
    """Post-onboarding nudge for athletes who finished bootstrap but haven't
    sent a single chat message in 24-48h (issue #258). i18n via the active
    contextvar — caller must ``set_language(user.language)`` first.

    The body deliberately spells out the chat mental model (stateless per
    message + Reply continues + long-term facts) since it's non-obvious and
    affects how the athlete writes their first message.
    """
    return _(
        "👋 Привет! Готов помочь с тренировками и восстановлением.\n\n"
        "⚙️ Как со мной работать:\n"
        "• каждое сообщение — отдельный диалог (контекст не тянется)\n"
        "• Reply на моё сообщение — продолжает разговор\n"
        "• важные факты (травмы, график) я запоминаю\n\n"
        "Пробуй!"
    )
