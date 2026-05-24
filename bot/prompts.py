import asyncio
import logging

from data.db import AthleteGoal, AthleteSettings
from data.db.dto import AthleteGoalDTO, AthleteThresholdsDTO
from data.personal_patterns import MIN_COMPLETE_ENTRIES, compute_personal_patterns
from data.sport_map import RAMP_PRIORITY
from tasks.dto import local_today

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# V2 — Tool-use system prompt (MCP Phase 2)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_V2 = """
You are a personal AI sports coach. Your role is to analyze an athlete's
physiological data and provide specific, actionable training recommendations.

Today's date: {today}

Athlete profile:
- Age {athlete_age}
- Sports: {sports}
{goals_block}
- LTHR Run: {lthr_run}, LTHR Bike: {lthr_bike}, FTP: {ftp}W, CSS: {css}s/100m
- Data source: Intervals.icu (Garmin wearable sync)

Important context on training load data:
- CTL, ATL, TSB, and ramp rate come directly from Intervals.icu (impulse-response model,
  τ_CTL=42d, τ_ATL=7d). Do NOT apply TrainingPeaks PMC thresholds.
- Per-sport CTL (swim, bike, run) is also from Intervals.icu sport-specific breakdown.

## Инструкции для утреннего отчёта

Используй доступные tools чтобы собрать данные о состоянии атлета.
Рекомендуемая последовательность:
1. get_recovery(date='{today}') — текущий recovery score и категория
2. get_hrv_analysis(date='{today}') — HRV статус (Flatt & Esco baseline)
3. get_rhr_analysis(date='{today}') — пульс покоя
4. get_training_load(date='{today}') — CTL/ATL/TSB/ramp_rate + per-sport CTL
5. get_scheduled_workouts(target_date='{today}') — что запланировано на сегодня
6. get_goal_progress() — прогресс к цели
7. get_personal_patterns(days_back=90) — персональные паттерны восстановления и compliance.
   Если status = insufficient_data → пропусти, не упоминай. Иначе используй для секции
   "наблюдение о тренде" — возвращает средний recovery_delta по pre-категории / max-зоне /
   HRV-статусу, распределение compliance и сравнение skipped vs trained. Это индивидуальные
   реакции, которых нет в общих метриках. Не выдумывай поля типа "recovery hours" — их там нет.
8. get_polarization_index(sport='{primary_sport}') — распределение времени по зонам (Low/Mid/High).
   Возвращает 4 окна (7d/14d/28d/56d) + coaching signals.
   Если signals содержит:
   - threshold_warning → "⚠ Слишком много Z3 за 2 недели. Замедли лёгкие или замени tempo на Z4+ интервалы."
   - too_hard → "⚠ Риск перетренировки — слишком много интенсивных сессий."
   - too_easy → "ℹ Не хватает стимула — добавь 1-2 интервальных сессии."
   - gray_zone_drift → "⚠ Серая зона растёт — easy-тренировки недостаточно лёгкие."
   - deload_week → информационно, не предупреждение.
   Если signals пуст → не упоминай поляризацию (всё ок).

Если какие-то данные вызывают подозрение (TSB в зоне `risk` < -30, HRV red, recovery low),
можешь запросить дополнительные данные: get_wellness_range за неделю,
get_activities за 3 дня, get_training_log для паттернов,
get_mood_checkins для эмоционального контекста,
get_iqos_sticks для корреляции с recovery,
get_weight_trend(days_back=30) — если HRV/recovery низкие, потеря веса часто коррелирует.

Для анализа аэробной базы можешь вызвать get_efficiency_trend с strict_filter=true —
это вернёт cardiac drift (decoupling) trend с last-5 медианой и traffic light статусом.
Если days_since > 14 — данные устарели, не акцентируй. Грейдинг: green (<5%), yellow (5-10%), red (>10%).
Устойчивый красный дрейф (2 из 3) = рекомендация Base Building Protocol.

В секции "прогресс к цели" вместо общих фраз используй predict_ctl(target_ctl=...) —
он считает ETA до целевого CTL по текущему ramp rate и возвращает конкретную дату
("при текущем темпе достигнешь 75 CTL к 12 июня"). Цель и текущий CTL уже видны
из get_goal_progress + get_training_load.

## Формат ответа

Дай ответ в 4 секциях (max 250 words):
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
- If TSB < −30 (zone `risk`) → recommend a rest or recovery day
- If ramp rate > 7 TSS/week → flag overreaching risk
- Respond in {response_language}
"""


SYSTEM_PROMPT_WEEKLY = """
You are a personal AI sports coach writing a weekly training summary.

Today's date: {today}

Athlete profile:
- Age {athlete_age}
- Sports: {sports}
{goals_block}
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
7. get_polarization_index(sport='{primary_sport}') — распределение зон (28d pattern + signals)
8. **Race-day прогноз (опционально):** если у атлета есть RACE_A goal и до неё 30-200 дней —
   вызови get_race_projection(mode="race_day"). Дистанции **строго из категории гонки** (sport_type
   на goal). Для триатлона: Sprint 750/20000/5000, Olympic 1500/40000/10000, 70.3 1900/90000/21100,
   IM 3800/180000/42200 — передавай race_distance_{{swim,ride,run}}_m. Для бега (sport_type=run):
   5K=5000, 10K=10000, Half=21100, Marathon=42195 — передавай **только** race_distance_run_m.
   Для ride/swim single-sport — аналогично только соответствующая дистанция. Если в названии гонки
   («Drina Trail», «Belgrade Marathon») дистанция не угадывается явно — пропусти шаг, не выдумывай.
   Получив envelope, добавь ОДНУ строку в секцию 📈 Прогресс:
   «🏁 Race-day прогноз ({{event_date}}): Swim {{swim}} · Bike {{bike}} · Run {{run}} → ~{{total}} (±{{ci_minutes}} мин)»
   (для single-sport — только соответствующий сплит без точек-разделителей).
   Если available=False (cold-start: model_not_trained / no_fitness_projection / распределение
   ещё нестабильно) — **молча пропусти**, не упоминай. Не раздувай — одна строка максимум.{progression_step}

## Формат ответа (300-400 words)

Первая строка — заголовок недели в формате `# ` (markdown H1): 3-6 слов, цепляющих
суть недели («# Брик-блок, подводка к гонке», «# Восстановительная неделя»). Без
цифр и без точки в конце. Затем пустая строка и основной отчёт по секциям:

1. 📊 **Итог недели** — sessions completed/planned, compliance %, total TSS/hours, by-sport breakdown ({sports})
2. 💚 **Восстановление** — HRV тренд (стабильный/падает/растёт), средний sleep score, RHR тренд, recovery days by color
3. 📈 **Прогресс** — CTL delta за неделю, ramp rate, per-sport CTL, прогресс к цели
4. ⚡ **Поляризация** — 28d pattern, Low/Mid/High %. Signals → рекомендация. Polarized → "ок"
{format_sections_tail}

## Правила
- Конкретные цифры: TSS, часы, compliance %, CTL delta
- Сравнить с предыдущей неделей если данные есть (вызови get_weekly_summary с прошлой неделей)
- Если compliance < 70% — отметить что пропущено
- Если ramp rate > 7 — предупредить
- Если IQOS > 15/день — отметить корреляцию с recovery
- Respond in {response_language}

## Форматирование (Telegram)
- Внутри секций только `**жирный**` — никаких `###` подзаголовков и HTML-тегов.
- Единственное исключение — обязательная строка-заголовок `# ` в самом начале отчёта.
- Никаких markdown-таблиц (`|...|`) — план следующей недели оформляй списком по дням: `- Пн: ...`.
- Секции начинай с эмодзи и жирного названия на отдельной строке.
"""


def _lang_name(code: str) -> str:
    return "English" if code == "en" else "Russian"


# Map lowercase enum to capitalized prompt-display name. Sourced separately
# from data.sport_map.LOWER_TO_INTERVALS because that one targets the
# Intervals.icu API casing (which happens to match) — keeping the prompt
# rendering decoupled lets us drift display copy (e.g. "Bike" instead of
# "Ride") without rippling into the API mapping.
_SPORT_DISPLAY = {"swim": "Swim", "ride": "Ride", "run": "Run"}


def _format_sports(sports: list[str] | None) -> str:
    """Render ``user.sports`` for the prompt's profile block.

    NULL (athlete hasn't passed through SportsPicker) → ``"all"`` so the
    rendered line stays grammatical without leaking the gate's null-state
    into Claude's context. Empty list is treated identically — the server
    enforces ≥1 so it shouldn't happen, but ``[]`` and ``None`` MUST agree
    with ``_zones_block`` (which also collapses both to "render all
    sections"). Diverging defensive paths once produced "Sports: all" + zero
    zone sections, see USER_SPORTS_SPEC code-review H1 (2026-05-08).
    """
    if not sports:
        return "all"
    return ", ".join(_SPORT_DISPLAY.get(s, s) for s in sports)


def _primary_sport(sports: list[str] | None) -> str:
    """Pick the lowercase enum that should fill hardcoded ``sport=...`` slots
    in the morning/weekly prompt examples (Phase 3 of USER_SPORTS_SPEC §11.3).

    Uses ``RAMP_PRIORITY`` ordering so a triathlete's primary stays Run
    (legacy expectation), and a runner-only / cyclist-only / swimmer-only
    athlete gets their own discipline. NULL/empty ``user.sports`` (gate
    not yet passed) falls back to ``"run"`` — matches the prompt's
    pre-Phase-3 behaviour so the rollout window is regression-free.
    """
    if not sports:
        return "run"
    for capitalized in RAMP_PRIORITY:
        lower = capitalized.lower()
        if lower in sports:
            return lower
    return "run"


def _render_goals_block(goals: list[AthleteGoalDTO]) -> str:
    """Render the Goals: section for the system prompt (#323 Strand D).

    Three shapes depending on what `AthleteGoal.get_goals_for_prompt` returned:

    * **0 goals** — single-line «Goal: не задана».
    * **1 goal** — single-line, just the event + date + sport_type.
    * **2 goals** — multi-line block with a focus-hint so Claude treats
      RACE_A as the strategic anchor and the nearest race only as
      tactical context (typical case: a B/C tune-up before the season A).

    Caller substitutes the result into the templates' `{goals_block}` slot.
    """
    if not goals:
        return "- Goal: не задана"
    if len(goals) == 1:
        g = goals[0]
        return f"- Goal: {g.event_name} ({g.event_date}, {g.sport_type})"
    a, n = goals[0], goals[1]
    return (
        "- Goals (focus on RACE_A; mention nearest only if directly relevant to today):\n"
        f"  - RACE_A: {a.event_name} ({a.event_date}, {a.sport_type})\n"
        f"  - Nearest: {n.event_name} ({n.event_date}, {n.sport_type})"
    )


def _show_ride_progression(sports: list[str] | None) -> bool:
    """Whether to render the weekly Ride-progression hint.

    ``get_progression_analysis`` is currently Ride-only (see CLAUDE.md
    «Ride only» note + §11.4 of USER_SPORTS_SPEC). Hide the hint for
    athletes who don't ride — keeps the prompt focused and avoids
    encouraging Claude to call the tool with no useful data. NULL/empty
    falls through to True (legacy behaviour during gate rollout) — must
    match ``_format_sports`` / ``_zones_block`` empty-list contract;
    diverging defensive paths once produced "Sports: all" + zero zone
    sections (USER_SPORTS_SPEC code-review H1, 2026-05-08).
    """
    if not sports:
        return True
    return "ride" in sports


def get_system_prompt_weekly(user_id: int, language: str = "ru") -> str:
    t: AthleteThresholdsDTO = AthleteSettings.get_thresholds(user_id)
    today_date = local_today()
    goals = AthleteGoal.get_goals_for_prompt(user_id, today_date)

    # Phase 3: Ride-only blocks render conditionally so a runner-only or
    # cyclist-only athlete doesn't see (Ride)-tagged guidance for sports
    # they don't train. The format-section tail is rebuilt per branch so
    # numbering stays consecutive in the user-facing report — Claude
    # tends to renumber visible output and a "4 → 6" jump confuses
    # readers in Telegram.
    show_ride = _show_ride_progression(t.sports)
    progression_step = (
        "\n8. get_progression_analysis(sport='Ride') — SHAP insights: что двигает EF тренд" if show_ride else ""
    )
    if show_ride:
        format_sections_tail = (
            "5. 🧠 **ML insights** (Ride) — если get_progression_analysis вернул данные, покажи top-3 фактора\n"
            "6. 🔍 **Наблюдение** — один ключевой инсайт: compliance, decoupling, или корреляция\n"
            "7. 📅 **План на неделю** — краткий обзор запланированных тренировок + рекомендация"
        )
    else:
        format_sections_tail = (
            "5. 🔍 **Наблюдение** — один ключевой инсайт: compliance, decoupling, или корреляция\n"
            "6. 📅 **План на неделю** — краткий обзор запланированных тренировок + рекомендация"
        )

    return SYSTEM_PROMPT_WEEKLY.format(
        today=today_date.isoformat(),
        athlete_age=t.age or 0,
        sports=_format_sports(t.sports),
        primary_sport=_primary_sport(t.sports),
        progression_step=progression_step,
        format_sections_tail=format_sections_tail,
        goals_block=_render_goals_block(goals),
        lthr_run=t.lthr_run or "—",
        lthr_bike=t.lthr_bike or "—",
        ftp=t.ftp or "—",
        css=t.css or "—",
        response_language=_lang_name(language),
    )


def get_system_prompt_v2(user_id: int, language: str = "ru") -> str:
    t: AthleteThresholdsDTO = AthleteSettings.get_thresholds(user_id)
    today_date = local_today()
    goals = AthleteGoal.get_goals_for_prompt(user_id, today_date)
    return SYSTEM_PROMPT_V2.format(
        today=today_date.isoformat(),
        athlete_age=t.age or 0,
        sports=_format_sports(t.sports),
        primary_sport=_primary_sport(t.sports),
        goals_block=_render_goals_block(goals),
        lthr_run=t.lthr_run or "—",
        lthr_bike=t.lthr_bike or "—",
        ftp=t.ftp or "—",
        css=t.css or "—",
        response_language=_lang_name(language),
    )


# ---------------------------------------------------------------------------
# Chat — free-form Telegram chat (MCP Phase 3)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Chat prompt — two-segment cache layout (USER_CONTEXT_SPEC §5 & §6):
#
#   _STATIC_PROMPT_CHAT         ← cache segment #1, invariant across users/days
#   _ATHLETE_BLOCK_TEMPLATE     ← cache segment #2, per-user + per-day tail
#                                 (today, profile, zones, facts, language)
#
# Any save_fact / goal update invalidates only the tail. Any per-user chat
# request still hits the static cache. The split lives here; the two
# cache_control markers that actually drive Anthropic's prefix-hash caching
# live in `bot/agent.py:_run_tool_use_loop`.
# ---------------------------------------------------------------------------

_STATIC_PROMPT_CHAT = """
You are a personal AI sports coach available via Telegram chat.
Answer the athlete's question concisely. Use tools to fetch current data when needed.

Important:
- CTL, ATL, TSB come from Intervals.icu (τ_CTL=42d, τ_ATL=7d). NOT TrainingPeaks.
- Use tools to get actual data — don't guess or assume values.
- If the question doesn't require data (e.g. general training advice), answer directly without tools.
- Keep answers short: 2-5 sentences for simple questions, up to 10 for analysis.
- Format for Telegram: use Markdown (bold, italic), no headers, no long lists.
- No markdown tables (`|...|`) — Telegram doesn't render them. Use `- ` bullet lists or line-by-line instead.
- Garmin tools (`get_garmin_*`) return a `freshness_warning` + `days_stale` —
  GDPR export lags 7+ days. Never present Garmin data as current state; use
  `get_wellness` / `get_recovery` for today. Garmin is for trends and history.

## Races
When the athlete mentions completing a race (any language — finish, competition, race, start),
use `tag_race` to mark the activity and capture distance, finish time, placement, notes. Ask
only for details you cannot infer from context. Use `get_races` for questions about past race
performance, progression, or race-day fitness context.

## Mood tracking
If the athlete's message contains emotional signals (fatigue, stress, excitement),
call save_mood_checkin_tool autonomously — don't ask, just record what you observe.

## Long-term memory
Use `save_fact` when the athlete reveals a LASTING trait (injury, schedule, family,
preference, equipment, travel, job, health) — something still relevant in 2+ weeks.
Do not use it for transient moods — those go to `save_mood_checkin_tool`.
Call `list_facts` before saving if the same topic may already be recorded;
prefer `deactivate_fact` on a stale fact over adding a near-duplicate.

## Workout generation
Before calling `suggest_workout` or `compose_workout`, always call `get_activities` for the
target date first. If any activity is already completed that day, reflect it in the rationale
and load estimate (don't stack a fresh session on top of a finished one). If the request
arrives after 19:00 local time, default `target_date` to tomorrow unless the athlete
explicitly asks for today.

**Every step must carry an intensity target** so Garmin/Wahoo watches alert on the HR/power/pace
corridor. Never emit text-only steps (`Z2` label + duration with nothing else) — the watch will
run the step without beeping and the athlete runs blind.
  - For repeat groups (`reps` + sub-`steps`), the target goes on each sub-step, not the wrapper.

## Race creation & deletion
For FUTURE races (add/reschedule a race, "race A on May X", etc.), use `suggest_race`.
Required: name, category (A/B/C — ask if unclear), dt (ISO, resolve relative dates).
Optional: sport, distance_m, ctl_target (pass through if named, don't invent), description.
Flow: always call with dry_run=True first — bot shows a confirm button and replays with
dry_run=False itself. Never call dry_run=False yourself. Use `tag_race` only for PAST activities.
To remove a future race ("delete RACE_A", "cancel race"), use `delete_race_goal(category)` —
confirm intent with the athlete first, it's irreversible from the bot.

## Race projection
When the athlete asks for a race forecast ("how will I do", "if I raced today", any
language), call `get_race_projection` with `mode="today"` for current-form check-ins
or `mode="race_day"` for upcoming A-race forecast. Communicate CI ranges honestly,
surface `warnings`, and don't fake numbers when `available=False` (model not yet
trained or below quality floor — say so and point to next Sunday's retrain).
""".strip()


_ATHLETE_BLOCK_TEMPLATE = """\
Today's date: {today}

Athlete profile:
- Age {athlete_age}
- Sports: {sports}
{goals_block}
- LTHR Run: {lthr_run}, LTHR Bike: {lthr_bike}, FTP: {ftp}W, CSS: {css}s/100m
- Data source: Intervals.icu (Garmin wearable sync)

## Zones
{zones_block}
{facts_block}{personal_patterns_block}
Respond in {response_language}."""


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


def _zones_block(
    settings_by_sport: dict[str, AthleteSettings],
    t: AthleteThresholdsDTO,
    sports: list[str] | None = None,
) -> str:
    """Render the Run/Ride/Swim zone block for the chat athlete-block template.

    Uses real per-sport boundaries from ``athlete_settings`` (synced from
    Intervals.icu) when available; falls back to a Friel-like 5-zone model
    otherwise so new athletes still get sane defaults.

    ``sports`` is the lowercase enum list from ``User.sports`` (subset of
    ``{"swim","ride","run"}``). When provided, only those sport sections are
    rendered — a runner-only athlete sees just the Run block, no irrelevant
    Ride/Swim noise. Sections are emitted in fixed order **Run → Ride →
    Swim** regardless of the input list ordering — input is membership-only.
    ``None`` (gate not yet passed) renders all three to avoid silent
    regression during the rollout window; the SportsPicker gate ensures
    this branch only fires for migrating users. Empty list is normalised
    to ``None`` so this function and ``_format_sports`` agree on the
    "render all" semantics — diverging paths once produced "Sports: all"
    + zero zones (USER_SPORTS_SPEC code-review H1, 2026-05-08).

    Unknown enum values (e.g. a future ``"fitness"`` if the schema is
    ever widened) are filtered out before the membership check. If
    nothing recognized remains, falls back to "render all" — same
    safety as the empty-list branch. ``_format_sports`` happily passes
    unknowns through to the prompt copy, so without this filter
    ``sports=["fitness"]`` would print "Sports: fitness" alongside
    zero zone sections, mirroring the H1 contradiction. Keep the two
    paths in lockstep when widening the enum.
    """
    if sports is not None:
        sports = [s for s in sports if s in {"run", "ride", "swim"}] or None
    lines: list[str] = []
    show_run = sports is None or "run" in sports
    show_ride = sports is None or "ride" in sports
    show_swim = sports is None or "swim" in sports

    # Run — %LTHR. Prefer Intervals synced zones whenever an LTHR is available
    # from either the sport-row or the thresholds DTO (historically some
    # athlete_settings rows had hr_zones populated but lthr=None — don't punish
    # them with Friel fallback when t.lthr_run is right there).
    if show_run:
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
            f'`{{"text": "Z2", "duration": 900, "hr": {{"units": "%lthr", "start": {z2[0]}, "end": {z2[1]}}}}}`.'
        )

    # Ride — prefer %FTP (power). Intervals.icu stores power_zones already as
    # percentages of FTP (not absolute watts), so the zones themselves need no
    # FTP — it's only displayed as the watts reference in the prompt text.
    if show_ride:
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
                f'`"power": {{"units": "%ftp", "start": {z2[0]}, "end": {z2[1]}}}`.'
            )
        elif t.ftp:
            z2 = _FALLBACK_RIDE_POWER_PCT[1]
            lines.append(
                f"  - **Ride**: use `power` with `%ftp` units. FTP = {t.ftp}W. "
                f"Ranges per zone (Friel fallback): {_format_range_list(_FALLBACK_RIDE_POWER_PCT)}. "
                f'Example Z2: `"power": {{"units": "%ftp", "start": {z2[0]}, "end": {z2[1]}}}`.'
            )
        else:
            lthr_bike = (ride and ride.lthr) or t.lthr_bike
            z2 = _FALLBACK_BIKE_HR_PCT[1]
            lines.append(
                f"  - **Ride**: use `hr` with `%lthr` units. LTHR bike = {lthr_bike or '—'}. "
                f"Ranges per zone (Friel fallback): {_format_range_list(_FALLBACK_BIKE_HR_PCT)}. "
                f'Example Z2: `"hr": {{"units": "%lthr", "start": {z2[0]}, "end": {z2[1]}}}`.'
            )

    # Swim — CSS corridor (no real zones in Intervals.icu sport-settings for swim).
    if show_swim:
        css = t.css
        if css:
            mm = int(css // 60)
            ss = int(css % 60)
            css_str = f"{mm}:{ss:02d}/100m"
        else:
            css_str = "—"
        lines.append(
            f"  - **Swim**: use `pace` with `%pace` units. CSS = {css_str}. "
            'Z2 corridor 95-105%. Example: `"pace": {"units": "%pace", "start": 95, "end": 105}`.'
        )

    return "\n".join(lines)


def get_static_system_prompt() -> str:
    """Return the invariant chat prompt — the first of two cache segments.

    Constant across users, days, and fact changes; lives in the long-lived
    Anthropic prefix cache. See USER_CONTEXT_SPEC §5 & §6 for the split
    rationale. The second segment comes from ``render_athlete_block``.
    """
    return _STATIC_PROMPT_CHAT


def _facts_block(facts: list, language: str) -> str:
    """Render the active-facts section for the athlete block.

    Empty string when the athlete has zero active facts — no negative-prompt
    "you don't know anything yet" line (wastes tokens, invites hallucination).

    Heading picks Russian for anything that isn't ``"en"``, mirroring
    ``_lang_name`` so a user on ``"sr"`` / future locales gets a consistent
    block — heading and ``Respond in …`` directive won't contradict each other.
    """
    if not facts:
        return ""
    heading = "## What I remember about you" if language == "en" else "## Что я помню о тебе"
    lines = [heading]
    lines.extend(f"- [{f.topic}] {f.fact}" for f in facts)
    return "\n".join(lines)


async def _safe_compute_personal_patterns(user_id: int) -> dict:
    """Patterns are nice-to-have — never let an aggregation error break the prompt.

    A transient DB hiccup or aggregation bug should drop the patterns block,
    not blow up the whole `render_athlete_block` call (which also serves
    facts, zones, and the goal — all required).
    """
    try:
        return await compute_personal_patterns(user_id=user_id)
    except Exception:
        logger.exception("compute_personal_patterns failed for user %d", user_id)
        return {"entries_total": 0, "entries_complete": 0}


def _render_personal_patterns(patterns: dict, language: str) -> str:
    """Format the compute_personal_patterns dict into a compact prompt block.

    Returns ``""`` below ``MIN_COMPLETE_ENTRIES`` so callers can stack the
    result unconditionally without a separate threshold check. Sections
    render only when the underlying bucket has data — a quiet sport (no
    Run rows in the matrix) doesn't surface empty slots.
    """
    n = patterns.get("entries_complete", 0)
    if n < MIN_COMPLETE_ENTRIES:
        return ""

    en = language == "en"
    total = patterns["entries_total"]
    heading = (
        f"## Personal patterns (training_log, {n}/{total} complete entries)"
        if en
        else f"## Персональные паттерны (training_log, {n}/{total} записей)"
    )
    lines = [heading]

    by_cat = patterns.get("recovery_response_by_category") or {}
    if by_cat:
        lines.append(
            "Recovery response by pre-category (avg recovery_delta):"
            if en
            else "Восстановление по pre-категории (avg recovery_delta):"
        )
        for cat in ("excellent", "good", "moderate", "low"):
            row = by_cat.get(cat)
            if row:
                avg = row["avg_delta"]
                lo, hi = row["min_delta"], row["max_delta"]
                lines.append(f"- {cat} (n={row['count']}): {avg:+.1f} [{lo:+.1f}..{hi:+.1f}]")

    hrv = patterns.get("hrv_sensitivity") or {}
    if hrv:
        lines.append("HRV sensitivity (avg recovery_delta):" if en else "Чувствительность HRV (avg recovery_delta):")
        for status in ("green", "yellow", "red", "unknown"):
            row = hrv.get(status)
            if row:
                lines.append(f"- {status} (n={row['count']}): {row['avg_delta']:+.1f}")

    matrix = patterns.get("recovery_intensity_matrix") or {}
    if matrix:
        lines.append(
            "Recovery × max-zone (avg recovery_delta):" if en else "Восстановление × макс-зона (avg recovery_delta):"
        )
        for cat in ("excellent", "good", "moderate", "low"):
            zones = matrix.get(cat) or {}
            if not zones:
                continue
            cells = ", ".join(f"{zone} {row['avg_delta']:+.1f} (n={row['count']})" for zone, row in zones.items())
            lines.append(f"- {cat}: {cells}")

    compliance = patterns.get("compliance_rates") or {}
    if compliance:
        lines.append("Compliance distribution:" if en else "Распределение compliance:")
        for kind, row in sorted(compliance.items(), key=lambda kv: -kv[1]["pct"]):
            lines.append(f"- {kind}: {row['pct']}% (n={row['count']})")

    skipped = patterns.get("skipped_avg_delta")
    trained = patterns.get("trained_avg_delta")
    if skipped is not None and trained is not None:
        if en:
            lines.append(f"Rest vs training: skipped avg {skipped:+.1f}, trained avg {trained:+.1f}")
        else:
            lines.append(f"Отдых vs тренировка: skipped avg {skipped:+.1f}, trained avg {trained:+.1f}")

    return "\n".join(lines)


async def render_athlete_block(
    user_id: int,
    language: str = "ru",
    *,
    include_facts: bool = True,
) -> str:
    """Build the per-user tail: today + thresholds + zones + facts + language.

    This is the second of two cache segments — it invalidates whenever the
    athlete's profile, goal, zones, or facts change. Morning/evening report
    actors can reuse it with ``include_facts=False`` when a fact-aware tail
    is not wanted (facts are conversational context, not analysis input).
    """
    from data.db import UserFact  # local import — avoids circular on boot

    # Fan out the per-user fetches in parallel so the chat hot path doesn't
    # serialize 4-5 round trips. They're independent reads.
    #
    # `return_exceptions=True` so a transient DB hiccup on any one fetch
    # degrades that block (renders empty) rather than killing the whole
    # chat reply (and cancelling the other in-flight queries).
    today_date = local_today()
    coros = [
        AthleteSettings.get_thresholds(user_id),
        AthleteGoal.get_goals_for_prompt(user_id, today_date),
        AthleteSettings.get_all(user_id),
        _safe_compute_personal_patterns(user_id),
    ]
    if include_facts:
        coros.append(UserFact.list_active(user_id=user_id))
    fetched = await asyncio.gather(*coros, return_exceptions=True)

    def _fallback(value, default, label):
        if isinstance(value, BaseException):
            logger.exception("render_athlete_block: %s fetch failed", label, exc_info=value)
            return default
        return value

    t = _fallback(fetched[0], AthleteThresholdsDTO(), "thresholds")
    goals = _fallback(fetched[1], [], "goals")
    all_settings = _fallback(fetched[2], [], "settings_by_sport")
    patterns = _fallback(fetched[3], {"entries_total": 0, "entries_complete": 0}, "patterns")
    facts = _fallback(fetched[4], [], "facts") if include_facts else []

    settings_by_sport = {s.sport: s for s in all_settings}

    facts_section = ""
    if include_facts:
        block = _facts_block(facts, language)
        facts_section = f"\n{block}\n" if block else ""

    patterns_block = _render_personal_patterns(patterns, language)
    patterns_section = f"\n{patterns_block}\n" if patterns_block else ""

    return _ATHLETE_BLOCK_TEMPLATE.format(
        today=today_date.isoformat(),
        athlete_age=t.age or 0,
        sports=_format_sports(t.sports),
        goals_block=_render_goals_block(goals),
        lthr_run=t.lthr_run or "—",
        lthr_bike=t.lthr_bike or "—",
        ftp=t.ftp or "—",
        css=t.css or "—",
        zones_block=_zones_block(settings_by_sport, t, sports=t.sports),
        facts_block=facts_section,
        personal_patterns_block=patterns_section,
        response_language=_lang_name(language),
    )
