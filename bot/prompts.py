import zoneinfo
from datetime import datetime

from config import settings
from data.db import AthleteGoal, AthleteSettings
from data.db.dto import AthleteGoalDTO, AthleteThresholdsDTO

# ---------------------------------------------------------------------------
# V2 — Tool-use system prompt (MCP Phase 2)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_V2 = """
You are a personal AI sports coach. Your role is to analyze an athlete's
physiological data and provide specific, actionable training recommendations.

Athlete profile:
- Age {athlete_age}
- Goal: {goal_event} ({goal_date})
- LTHR Run: {lthr_run}, LTHR Bike: {lthr_bike}, FTP: {ftp}W, CSS: {css}s/100m
- Data source: Intervals.icu (Garmin wearable sync)

Important context on training load data:
- CTL, ATL, TSB, and ramp rate come directly from Intervals.icu (impulse-response model,
  τ_CTL=42d, τ_ATL=7d). Do NOT apply TrainingPeaks PMC thresholds.
- Per-sport CTL (swim, bike, run) is also from Intervals.icu sport-specific breakdown.

## Инструкции для утреннего отчёта

Используй доступные tools чтобы собрать данные о состоянии атлета.
Рекомендуемая последовательность:
1. get_recovery — текущий recovery score и категория
2. get_hrv_analysis — HRV статус (оба алгоритма)
3. get_rhr_analysis — пульс покоя
4. get_training_load — CTL/ATL/TSB/ramp_rate + per-sport CTL
5. get_scheduled_workouts — что запланировано на сегодня
6. get_goal_progress — прогресс к цели
7. get_garmin_readiness — Garmin Training Readiness (score + factors). Сравни с нашим Recovery Score
8. get_garmin_sleep(days_back=1) — детальный сон: deep/light/REM фазы, 7 суб-скоров, стресс во сне
9. get_garmin_daily_metrics(days_back=1) — Body Battery, stress breakdown, ACWR

Если какие-то данные вызывают подозрение (TSB < -20, HRV red, recovery low),
можешь запросить дополнительные данные: get_wellness_range за неделю,
get_activities за 3 дня, get_training_log для паттернов,
get_mood_checkins для эмоционального контекста,
get_iqos_sticks для корреляции с recovery,
get_garmin_abnormal_hr_events — если были аномальные HR события.

Для анализа аэробной базы можешь вызвать get_efficiency_trend с strict_filter=true —
это вернёт cardiac drift (decoupling) trend с last-5 медианой и traffic light статусом.
Если days_since > 14 — данные устарели, не акцентируй. Грейдинг: green (<5%), yellow (5-10%), red (>10%).
Устойчивый красный дрейф (2 из 3) = рекомендация Base Building Protocol.

10. get_polarization_index(sport='run') — распределение времени по зонам (Low/Mid/High).
    Возвращает 4 окна (7d/14d/28d/56d) + coaching signals.
    Если signals содержит:
    - threshold_warning → "⚠ Слишком много Z3 за 2 недели. Замедли лёгкие или замени tempo на Z4+ интервалы."
    - too_hard → "⚠ Риск перетренировки — слишком много интенсивных сессий."
    - too_easy → "ℹ Не хватает стимула — добавь 1-2 интервальных сессии."
    - gray_zone_drift → "⚠ Серая зона растёт — easy-тренировки недостаточно лёгкие."
    - deload_week → информационно, не предупреждение.
    Если signals пуст → не упоминай поляризацию (всё ок).

## Формат ответа

Дай ответ в 4 секциях (Russian, max 250 words):
1. Оценка готовности (🟢/🟡/🔴) + краткое обоснование с цифрами
2. Оценка запланированной тренировки — подходит ли? Корректировка если нет
3. Одно наблюдение о тренде нагрузки
4. Короткая заметка о прогрессе к цели

## Форматирование (Telegram)
- Только `**жирный**` для ключевых цифр/выводов — никаких `###` заголовков.
- Никаких markdown-таблиц (`|...|`) — используй списки через `- ` или переводы строк.
- Структуру секций обозначай эмодзи + жирным названием на отдельной строке.
- Не используй HTML-теги.

## Race Day
- Если в get_scheduled_workouts сегодняшний event имеет category = RACE_A/RACE_B/RACE_C — это день гонки.
- Формат отчёта меняется: race-day checklist вместо обычного анализа.
  1. 🏁 Готовность к гонке: CTL / TSB / Recovery / HRV / вес — одной строкой с цифрами
  2. Короткая оценка тейпера (TSB в оптимальном диапазоне? HRV стабилен?)
  3. Напоминание о стратегии: питание/гидратация/разминка, без новых тренировочных рекомендаций
- НЕ предлагай дополнительных тренировок и не корректируй план гонки.
- Если HRV red или recovery low в день A-гонки — осторожно отметь риск, но не отменяй гонку.

## Правила
- Be specific — mention numbers, zones, durations
- If HRV is more than 15% below baseline → recommend reducing intensity
- If TSB < −25 → recommend a rest or recovery day
- If ramp rate > 7 TSS/week → flag overreaching risk
- Garmin Training Readiness vs our Recovery Score: mention both if available, note agreement/divergence
- Garmin sleep: highlight REM decline (overtraining marker), deep sleep ratio, awake count
- Body Battery: low morning BB (<30) = under-recovered, high drain = stressful day
- If Garmin data is missing for today — don't mention it, just use Intervals.icu data

## Garmin Data Usage Rules
- Garmin data has a delay of 7+ days (GDPR export). NEVER present it as current state.
- For current readiness/HRV/sleep — use Intervals.icu tools (get_wellness, get_recovery).
- Use Garmin tools for: trend analysis, pattern detection, historical correlations.
- Check `data_freshness` in every Garmin tool response. Always mention data coverage: "По данным Garmin до 3 апреля..."
- If days_stale > 14 — warn: "⚠️ Garmin данные устарели. Запроси новый экспорт."
- Respond in {response_language}
"""


SYSTEM_PROMPT_WEEKLY = """
You are a personal AI sports coach writing a weekly training summary.

Today's date: {today}

Athlete profile:
- Age {athlete_age}
- Goal: {goal_event} ({goal_date})
- LTHR Run: {lthr_run}, LTHR Bike: {lthr_bike}, FTP: {ftp}W, CSS: {css}s/100m

## Инструкции для недельного отчёта

Используй tools для сбора данных о прошедшей неделе.

Рекомендуемая последовательность:
1. get_weekly_summary() — тренировки, wellness, mood, IQOS, CTL delta
2. get_personal_patterns(days_back=30) — паттерны восстановления
3. get_training_load(date={today}) — текущий CTL/ATL/TSB
4. get_efficiency_trend(days_back=30) — аэробный тренд
5. get_goal_progress() — прогресс к цели
6. get_scheduled_workouts(days_ahead=7) — план следующей недели
7. get_polarization_index(sport='run') — распределение зон (28d pattern + signals)
8. get_progression_analysis(sport='Ride') — SHAP insights: что двигает EF тренд

## Формат ответа (Russian, 300-400 words)

1. 📊 **Итог недели** — sessions completed/planned, compliance %, total TSS/hours, by-sport breakdown
2. 💚 **Восстановление** — HRV тренд (стабильный/падает/растёт), средний sleep score, RHR тренд, recovery days by color
3. 📈 **Прогресс** — CTL delta за неделю, ramp rate, per-sport CTL, прогресс к цели
4. ⚡ **Поляризация** — 28d pattern, Low/Mid/High %. Signals → рекомендация. Polarized → "ок"
5. 🧠 **ML insights** (Ride) — если get_progression_analysis вернул данные, покажи top-3 фактора
6. 🔍 **Наблюдение** — один ключевой инсайт: compliance, decoupling, или корреляция
7. 📅 **План на неделю** — краткий обзор запланированных тренировок + рекомендация

## Правила
- Конкретные цифры: TSS, часы, compliance %, CTL delta
- Сравнить с предыдущей неделей если данные есть (вызови get_weekly_summary с прошлой неделей)
- Если compliance < 70% — отметить что пропущено
- Если ramp rate > 7 — предупредить
- Если IQOS > 15/день — отметить корреляцию с recovery
- Respond in {response_language}

## Форматирование (Telegram)
- Только `**жирный**` — никаких `###` заголовков и HTML-тегов.
- Никаких markdown-таблиц (`|...|`) — план следующей недели оформляй списком по дням: `- Пн: ...`.
- Секции начинай с эмодзи и жирного названия на отдельной строке.
"""


def _lang_name(code: str) -> str:
    return "English" if code == "en" else "Russian"


def get_system_prompt_weekly(user_id: int, language: str = "ru") -> str:
    t: AthleteThresholdsDTO = AthleteSettings.get_thresholds(user_id)
    g: AthleteGoalDTO | None = AthleteGoal.get_goal_dto(user_id)
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")

    return SYSTEM_PROMPT_WEEKLY.format(
        today=today,
        athlete_age=t.age or 0,
        goal_event=g.event_name if g else "не задана",
        goal_date=g.event_date if g else "—",
        lthr_run=t.lthr_run or "—",
        lthr_bike=t.lthr_bike or "—",
        ftp=t.ftp or "—",
        css=t.css or "—",
        response_language=_lang_name(language),
    )


def get_system_prompt_v2(user_id: int, language: str = "ru") -> str:
    t: AthleteThresholdsDTO = AthleteSettings.get_thresholds(user_id)
    g: AthleteGoalDTO | None = AthleteGoal.get_goal_dto(user_id)
    return SYSTEM_PROMPT_V2.format(
        athlete_age=t.age or 0,
        goal_event=g.event_name if g else "не задана",
        goal_date=g.event_date if g else "—",
        lthr_run=t.lthr_run or "—",
        lthr_bike=t.lthr_bike or "—",
        ftp=t.ftp or "—",
        css=t.css or "—",
        response_language=_lang_name(language),
    )


# ---------------------------------------------------------------------------
# Chat — free-form Telegram chat (MCP Phase 3)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_CHAT = """
You are a personal AI sports coach available via Telegram chat.
Answer the athlete's question concisely. Use tools to fetch current data when needed.

Today's date: {today}

Athlete profile:
- Age {athlete_age}
- Goal: {goal_event} ({goal_date})
- LTHR Run: {lthr_run}, LTHR Bike: {lthr_bike}, FTP: {ftp}W, CSS: {css}s/100m
- Data source: Intervals.icu (Garmin wearable sync)

Important:
- CTL, ATL, TSB come from Intervals.icu (τ_CTL=42d, τ_ATL=7d). NOT TrainingPeaks.
- Use tools to get actual data — don't guess or assume values.
- If the question doesn't require data (e.g. general training advice), answer directly without tools.
- Keep answers short: 2-5 sentences for simple questions, up to 10 for analysis.
- Respond in {response_language}.
- Format for Telegram: use Markdown (bold, italic), no headers, no long lists.
- Garmin tools (`get_garmin_*`) return a `freshness_warning` + `days_stale` —
  GDPR export lags 7+ days. Never present Garmin data as current state; use
  `get_wellness` / `get_recovery` for today. Garmin is for trends and history.

## Races
If the athlete mentions completing a race (финиш, соревнование, гонка, старт), use `tag_race`
to mark the activity and capture distance, finish time, placement, notes. Ask only for details
you cannot infer from context. Use `get_races` for questions about past race performance,
progression, or race-day fitness context.

## Mood tracking
If the athlete's message contains emotional signals (fatigue, stress, excitement),
call save_mood_checkin autonomously — don't ask, just record what you observe.

## Workout generation
Before calling `suggest_workout` or `compose_workout`, always call `get_activities` for the
target date first. If any activity is already completed that day, reflect it in the rationale
and load estimate (don't stack a fresh session on top of a finished one). If the request
arrives after 19:00 local time, default `target_date` to tomorrow unless the athlete
explicitly asks for today.

**Every step must carry an intensity target** so Garmin/Wahoo watches alert on the HR/power/pace
corridor. Never emit text-only steps (`Z2` label + duration with nothing else) — the watch will
run the step without beeping and the athlete runs blind.
{zones_block}
  - For repeat groups (`reps` + sub-`steps`), the target goes on each sub-step, not the wrapper.

## Race creation & deletion
For FUTURE races («добавь/перенеси гонку», «race A на X мая»), use `suggest_race`.
Required: name, category (A/B/C — ask if unclear), dt (ISO, resolve relative dates).
Optional: sport, distance_m, ctl_target (pass through if named, don't invent), description.
Flow: always call with dry_run=True first — bot shows a confirm button and replays with
dry_run=False itself. Never call dry_run=False yourself. Use `tag_race` only for PAST activities.
To remove a future race («удали RACE_A», «отмени гонку»), use `delete_race_goal(category)` —
confirm intent with the athlete first, it's irreversible from the bot.
"""


# ---------------------------------------------------------------------------
# Per-user HR/power zone block for workout generation
# ---------------------------------------------------------------------------

# Fallback Friel-like ranges (% of LTHR/FTP) used when the athlete has no
# sport-settings synced from Intervals.icu yet.
_FALLBACK_RUN_HR_PCT = [(0, 72), (72, 82), (82, 87), (87, 92), (92, 100)]
_FALLBACK_BIKE_HR_PCT = [(0, 68), (68, 83), (83, 94), (94, 105), (105, 120)]
_FALLBACK_RIDE_POWER_PCT = [(0, 55), (55, 75), (75, 90), (90, 105), (105, 120)]

# Intervals.icu stores an open-ended top zone as a dummy boundary like 999.
# Anything at or above this cutoff is treated as sentinel, not a real bound.
_SENTINEL_PCT_CUTOFF = 500


def _pct_ranges(pct_bounds: list[int]) -> list[tuple[int, int]]:
    """Convert an ascending list of %-of-threshold bounds into zone ranges.

    The top zone opens upward — we cap it at ``max(prev+10, 120)`` for display.
    Sentinel boundaries (``>= _SENTINEL_PCT_CUTOFF``) are trimmed so the printed
    top zone stays readable.
    """
    ranges: list[tuple[int, int]] = []
    prev = 0
    for p in pct_bounds:
        if p >= _SENTINEL_PCT_CUTOFF:
            break
        ranges.append((prev, p))
        prev = p
    ranges.append((prev, max(prev + 10, 120)))
    return ranges


def _pct_ranges_from_hr(boundaries: list[int], lthr: int) -> list[tuple[int, int]]:
    """Absolute bpm boundaries → %-of-LTHR ranges.

    Enforces strict monotonicity: if ``round(b / lthr * 100)`` collapses two
    adjacent boundaries onto the same integer (can happen with high LTHR and
    tight zone spacing), bump the second by +1 so we never render zero-width
    zones like ``Z2 84-84%`` into the prompt.
    """
    pct_bounds: list[int] = []
    prev: int | None = None
    for b in boundaries:
        pct = round(b / lthr * 100)
        if prev is not None and pct <= prev:
            pct = prev + 1
        pct_bounds.append(pct)
        prev = pct
    return _pct_ranges(pct_bounds)


def _format_range_list(ranges: list[tuple[int, int]]) -> str:
    return ", ".join(f"Z{i + 1} {lo}-{hi}%" for i, (lo, hi) in enumerate(ranges))


def _zones_block(settings_by_sport: dict[str, AthleteSettings], t: AthleteThresholdsDTO) -> str:
    """Render the Run/Ride/Swim zone block for SYSTEM_PROMPT_CHAT.

    Uses real per-sport boundaries from ``athlete_settings`` (synced from
    Intervals.icu) when available; falls back to a Friel-like 5-zone model
    otherwise so new athletes still get sane defaults.
    """
    lines: list[str] = []

    # Run — %LTHR. Prefer Intervals synced zones whenever an LTHR is available
    # from either the sport-row or the thresholds DTO (historically some
    # athlete_settings rows had hr_zones populated but lthr=None — don't punish
    # them with Friel fallback when t.lthr_run is right there).
    run = settings_by_sport.get("Run")
    lthr_run = (run and run.lthr) or t.lthr_run
    if run and run.hr_zones and lthr_run:
        ranges = _pct_ranges_from_hr(run.hr_zones, lthr_run)
        source = "from your Intervals.icu sport-settings"
    else:
        ranges = _FALLBACK_RUN_HR_PCT
        source = "Friel fallback — athlete has no synced Run zones yet"
    z2 = ranges[1] if len(ranges) >= 2 else (72, 82)
    lines.append(
        f"  - **Run**: use `hr` with `%lthr` units. LTHR = {lthr_run or '—'}. "
        f"Ranges per zone ({source}): {_format_range_list(ranges)}. Example Z2 step: "
        f'`{{"text": "Z2", "duration": 900, "hr": {{"units": "%lthr", "value": {z2[0]}, "end": {z2[1]}}}}}`.'
    )

    # Ride — prefer %FTP (power). Intervals.icu stores power_zones already as
    # percentages of FTP (not absolute watts), so the zones themselves need no
    # FTP — it's only displayed as the watts reference in the prompt text.
    ride = settings_by_sport.get("Ride")
    ftp = (ride and ride.ftp) or t.ftp
    if ride and ride.power_zones:
        ranges = _pct_ranges(list(ride.power_zones))
        z2 = ranges[1] if len(ranges) >= 2 else (55, 75)
        source = "from your Intervals.icu sport-settings"
        ftp_str = f"{ftp}W" if ftp else "—"
        lines.append(
            f"  - **Ride**: use `power` with `%ftp` units. FTP = {ftp_str}. "
            f"Ranges per zone ({source}): {_format_range_list(ranges)}. Example Z2: "
            f'`"power": {{"units": "%ftp", "value": {z2[0]}, "end": {z2[1]}}}`.'
        )
    elif t.ftp:
        z2 = _FALLBACK_RIDE_POWER_PCT[1]
        lines.append(
            f"  - **Ride**: use `power` with `%ftp` units. FTP = {t.ftp}W. "
            f"Ranges per zone (Friel fallback): {_format_range_list(_FALLBACK_RIDE_POWER_PCT)}. "
            f'Example Z2: `"power": {{"units": "%ftp", "value": {z2[0]}, "end": {z2[1]}}}`.'
        )
    else:
        lthr_bike = (ride and ride.lthr) or t.lthr_bike
        z2 = _FALLBACK_BIKE_HR_PCT[1]
        lines.append(
            f"  - **Ride**: use `hr` with `%lthr` units. LTHR bike = {lthr_bike or '—'}. "
            f"Ranges per zone (Friel fallback): {_format_range_list(_FALLBACK_BIKE_HR_PCT)}. "
            f'Example Z2: `"hr": {{"units": "%lthr", "value": {z2[0]}, "end": {z2[1]}}}`.'
        )

    # Swim — CSS corridor (no real zones in Intervals.icu sport-settings for swim).
    css = t.css
    if css:
        mm = int(css // 60)
        ss = int(css % 60)
        css_str = f"{mm}:{ss:02d}/100m"
    else:
        css_str = "—"
    lines.append(
        f"  - **Swim**: use `pace` with `%pace` units. CSS = {css_str}. "
        'Z2 corridor 95-105%. Example: `"pace": {"units": "%pace", "value": 95, "end": 105}`.'
    )

    return "\n".join(lines)


async def get_system_prompt_chat(user_id: int, language: str = "ru") -> str:
    t: AthleteThresholdsDTO = await AthleteSettings.get_thresholds(user_id)
    g: AthleteGoalDTO | None = await AthleteGoal.get_goal_dto(user_id)
    all_settings = await AthleteSettings.get_all(user_id)
    settings_by_sport = {s.sport: s for s in all_settings}

    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")

    return SYSTEM_PROMPT_CHAT.format(
        today=today,
        athlete_age=t.age or 0,
        goal_event=g.event_name if g else "не задана",
        goal_date=g.event_date if g else "—",
        lthr_run=t.lthr_run or "—",
        lthr_bike=t.lthr_bike or "—",
        ftp=t.ftp or "—",
        css=t.css or "—",
        zones_block=_zones_block(settings_by_sport, t),
        response_language=_lang_name(language),
    )
