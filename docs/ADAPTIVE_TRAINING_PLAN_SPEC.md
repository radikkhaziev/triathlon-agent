# Adaptive Training Plan

> Автоматическая адаптация тренировок HumanGo на основе recovery/HRV/training load данных.

**Status:** ✅ All phases done.
- Phase 1 (Write API + AI generation) ✅
- Phase 2 (HumanGo adaptation) ✅
- Phase 3 (training_log + personal patterns + prompt enrichment, ATP-finish 2026-05-07) ✅
- Phase 4 (ramp tests + threshold drift) ✅ — full Run+Bike protocols rebuilt 2026-05-08, см. `docs/RAMP_TEST_BIKE_SPEC.md`

См. CLAUDE.md §«Implementation Status» для свежей сводки.

**Code anchors:**

| Concern | File |
|---|---|
| Intervals.icu write API | `data/intervals/client.py` (`create_event` / `update_event` / `delete_event`) |
| Workout DTOs | `data/intervals/dto.py` (`WorkoutStepDTO` / `PlannedWorkoutDTO.to_intervals_event`) |
| AI workouts table | `data/db/workout.py:AiWorkout` |
| MCP tools (workouts) | `mcp_server/tools/ai_workouts.py` (`suggest_workout` / `remove_ai_workout` / `list_ai_workouts`) |
| Two-phase preview | `bot/main.py:_PREVIEWABLE_TOOLS` |
| Adaptation parser/detector | `data/workout_adapter.py`, `tasks/actors/reports.py:130-158` |
| Adapt callback | `bot/main.py:handle_adapt_callback` |
| Training log | `data/db/training_log.py`, `data/personal_patterns.py:compute_personal_patterns` |
| Prompt enrichment | `bot/prompts.py:_render_personal_patterns`, `_ATHLETE_BLOCK_TEMPLATE.{personal_patterns_block}` |
| Ramp tests | `data/ramp_tests.py`, `mcp_server/tools/ramp_tests.py` (протоколы — `RAMP_TEST_BIKE_SPEC`) |
| Drift detector | `data/db/user.py:detect_threshold_drift`, `_drift_alert_{lthr,pace,ftp}` |
| Drift actor | `tasks/actors/athlets.py:actor_update_zones` |

---

## Концепция

HumanGo генерирует тренировочный план и синхронизирует его в Intervals.icu. Агент анализирует состояние атлета (recovery, HRV, TSB, Ra) и при необходимости создаёт **адаптированную версию** тренировки как **новый event** в Intervals.icu. Оригинальная тренировка HumanGo остаётся в календаре без изменений. На часах Garmin появляются обе тренировки — атлет выбирает какую выполнять.

```
HumanGo → Intervals.icu (оригинал, read-only)
                ↓
         Agent анализирует recovery + HRV + TSB + Ra
                ↓
         Создаёт AI: {name} (adapted) event → Intervals.icu → Garmin sync
                ↓
         Атлет выполняет → Activity → training_log (факт vs план)
```

---

## Фаза 1: Write API + AI generation

**Auth:** per-user dual mode через `IntervalsAsyncClient.for_user(user_id)` — Bearer (OAuth) либо Basic (api_key) в зависимости от `users.intervals_auth_method`. Глобальный `INTERVALS_API_KEY` не используется. См. CLAUDE.md §«Intervals.icu Auth — Dual Mode».

### Naming convention

`PlannedWorkoutDTO.to_intervals_event()` рендерит имя как `"AI: " + name + (" (" + suffix + ")" if suffix else "")`:
- **`AI: {name}`** — Phase 1, AI сгенерировал с нуля (`suffix=None`)
- **`AI: {name} (adapted)`** — Phase 2, AI модифицировал HumanGo (`suffix="adapted"`)

Префикс всегда `AI:` независимо от фазы. Если Claude уже добавил `AI: ` в `name`, повторный префикс снимается.

### External ID strategy

Формат `tricoach:{date}:{sport}:{slot}` (пример: `tricoach:2026-03-28:ride:morning`). Идентификатор не меняется при адаптации — фазы отличаются только `suffix` в имени. На один (date, sport, slot) живёт ровно один `AI:`-event; повторный push — upsert через `client.update_event(intervals_id, …)`.

Преимущества: отличает наши тренировки от пользовательских/тренерских; делает upsert без локального event_id; `external_id` хранится также для DELETE.

### workout_doc step format

Тренировки отдаются как `workout_doc.steps` — структурированный JSON со step-объектами (`text` / `duration` / `reps` / `steps` (вложенные для repeat-групп) / `hr` / `power` / `pace` / `cadence`). Intervals.icu передаёт их на часы (Garmin/Wahoo) как structured workout с target-зонами.

**Per-sport convention:** Ride → `power` (`%ftp`), Run → `hr` (`%lthr`), Swim → `pace` (`%pace`). Distance-based steps (`distance` в метрах) — альтернатива `duration`, mutually exclusive.

`PlannedWorkoutDTO._check_steps_have_targets` отбивает terminal steps без `hr` / `power` / `pace` — часы не алертят без numeric target. Sport `Other` (yoga/mobility) освобождён от проверки.

**Rationale в `workout_doc.description`, не в top-level event description.** Intervals.icu иначе молча роняет `workout_doc.steps` для Swim (regression замечена 2026-04-30, см. docstring `to_intervals_event`).

### AI generation

Через MCP-tool `suggest_workout`. Claude в чате собирает контекст (recovery, HRV, TSB, scheduled workouts) сам через MCP-tools и формирует `steps` JSON. Системный промпт `bot/prompts.py:_STATIC_PROMPT_CHAT` + per-user `render_athlete_block` несут все правила и зоны.

**Когда вызывается** — только on-demand, не автоматически по cron'у:
- `/workout` команда
- Free-form запрос в чате («предложи тренировку»)
- Inline-кнопка «Адаптировать» в утреннем отчёте (Phase 2)

Авто-генерация по утреннему cron намеренно НЕ реализована — при отсутствии плана пользователь явно просит, либо тренировки нет вовсе. Отдельных флагов `AI_WORKOUT_ENABLED` / `AI_WORKOUT_AUTO_PUSH` нет.

`suggest_workout` следует двухфазному dry-run паттерну: первый вызов с `dry_run=True` рендерит preview + inline-кнопку «Отправить»; `bot/main.py` сохраняет tool_use блок в `pending_workout` и при confirm повторяет его с `dry_run=False` без re-inference. Та же дисциплина для `/workout`. См. `_PREVIEWABLE_TOOLS`.

### Safety

- Тренировки без `external_id` с префиксом `tricoach:` НЕ трогаем — это пользовательские/тренерские
- Расы (`RACE_A/B/C`), заметки (`NOTE`) — никогда не создаём и не удаляем
- Оригинальная тренировка HumanGo не модифицируется и не удаляется
- `AI:` префикс в name — визуальный маркер в календаре
- Rate limit: 1-2 write запроса в день (далеко от Intervals 30 req/s)

---

## Фаза 2: HumanGo adaptation

> Когда тренировка запланирована HumanGo, но состояние атлета не позволяет выполнить её как есть — создаётся `AI: {name} (adapted)` event рядом с оригиналом. Оригинал НЕ модифицируется и НЕ удаляется. На часах Garmin видны обе — атлет выбирает.

### Inputs

| Метрика | Источник | Роль |
|---|---|---|
| Recovery score (0-100) + category | `data/metrics.py` | Основной индикатор |
| HRV status (flatt_esco) | `hrv_analysis` | Recovery boost / brake |
| RHR status | `rhr_analysis` | Дополнительный сигнал |
| TSB | Intervals.icu | Накопленная усталость |
| Ra (Readiness) | DFA a1 pipeline | Свежесть |
| Planned workout | `scheduled_workouts` | Что запланировал HumanGo |
| `workout_doc` | Intervals.icu JSON | Структурированный разбор тренировки |

### Правила адаптации

Правила определяют **максимально допустимую зону** и **коррекцию длительности**. Начальные значения статические; после накопления данных в training_log (Phase 3) — персонализируются через `compute_personal_patterns`.

```
Recovery excellent (>85) + TSB > 0 + HRV green:
  → Без адаптации.

Recovery good (70-85) + HRV green:
  → Без адаптации.

Recovery good (70-85) + HRV yellow:
  → Max zone: текущая или Z3 (что ниже)
  → Длительность: -10%

Recovery moderate (40-70):
  → Max zone: Z2
  → Длительность: -15%
  → Интервалы → Steady Z2

Recovery low (<40) OR HRV red:
  → Max zone: Z1-Z2
  → Длительность: -25% или max 45 min
  → Интервалы → лёгкая аэробная работа

TSB < -30 (zone `risk`, high risk):
  → Max zone: Z2 cap (независимо от recovery)
  → Длительность: -20%

Ra < -5% (3+ дней подряд):
  → Дополнительное снижение
  → Флаг в description
```

`docs/knowledge/hrv.md` ссылается сюда как на canonical-источник этой матрицы (см. §«Decision Logic» там).

### Алгоритм — LLM-driven через две стадии

**Stage 1 — детектор (морнинг-cron)** в `tasks/actors/reports.py:130-158`. Парсит `workout_doc` (приоритет) или текстовый `description` (regex fallback). Через `compute_constraints(recovery, hrv_status, tsb)` → `max_zone` → `needs_adaptation(steps, max_zone)`. Если требуется — инжектит warning в morning summary + inline-кнопку «Адаптировать: {Workout name}». Детектор НЕ строит адаптированную тренировку — лишь сигналит решение атлету.

**Stage 2 — Claude-генератор (по клику)** в `bot/main.py:handle_adapt_callback`. Передаёт промпт «адаптируй workout id={X}, оцени recovery, предложи через `suggest_workout(dry_run=True)`». Claude собирает контекст через MCP, формирует `steps` с понижёнными зонами, зовёт `suggest_workout`. Confirm-кнопка делает push без re-inference (`_PREVIEWABLE_TOOLS` паттерн), `suffix="adapted"`.

`compute_constraints` / `needs_adaptation` / `parse_humango_description` — helper'ы в `data/workout_adapter.py`, используются только в детекторе. Функция `adapt_workout()` в том же файле (детерминистический pipeline `parse → check → clamp → DTO`) реализована, но в проде НЕ вызывается — legacy-fallback, покрыт только тестами.

**Утренний cron не пушит адаптации сам.** Двухступенчатый opt-in: cron сигналит «оригинал не подходит» → атлет тапает кнопку → Claude формирует preview → confirm → push. Если атлет проигнорировал — оригинал HumanGo остаётся единственным event'ом на день.

---

## Фаза 3: Training Log + персональные паттерны

> Ключевая фаза для персонализации. Вместо статических правил «recovery < 40 → отдых» — конкретные паттерны конкретного атлета.

Каждая тренировка (оригинал HumanGo, AI-сгенерированная или адаптация) фиксируется в `training_log`. После выполнения добавляется факт + outcome.

### Жизненный цикл записи

```
06:00     утренний cron → создаёт запись: pre_* контекст (recovery, HRV, RHR, TSB, Ra,
          sleep, sport), source ('humango' | 'ai' | 'adapted' | 'none'),
          original_/adapted_ поля. compliance, actual_*, post_* = NULL.

18:00     sync_activities_job → находит activity за дату+sport →
          заполняет actual_*, определяет compliance.

06:00+1   следующий день → заполняет post_* (recovery, HRV, RHR, sleep, Ra),
          вычисляет recovery_delta = post_recovery - pre_recovery.
```

Schema `training_log` (~30 полей: pre / actual / post + compliance + `race_id` FK). FK на `scheduled_workouts` намеренно НЕТ — soft reference (sync делает stale deletion, FK сломал бы логи).

### Compliance detection

`_detect_compliance(log, activity)` сравнивает фактическую активность с вариантами (sport + duration ±20% + intensity / TSS). Возвращает `followed_adapted` / `followed_original` / `followed_ai` / `modified` (best match score < 0.5) / `skipped` (activity is None).

### Pattern types (методология)

Накопленные данные (≥30 записей) позволяют извлечь:

1. **Recovery Response Model** — при каком `pre_recovery` + типе нагрузки → какой `recovery_delta`. Пример: `pre=65, Z3 intervals → -20`; `pre=65, Z2 steady → +5`; `pre=65, skipped → +15`. Для атлета: при recovery 60-70 Z2 steady оптимален, Z3 вредит.
2. **Personal Adaptation Thresholds** — при каком `pre_recovery` атлет реально справляется с Z3+ нагрузкой (могут быть мягче дефолтных правил). Пример: дефолт «recovery<70 → max Z2», факт «при 55-65 Z3 + recovery_delta>0» → персональный порог 50.
3. **HRV Sensitivity** — насколько HRV-yellow предсказывает плохой `recovery_delta` после нагрузки. Пример: HRV yellow + Z3 → -12 в среднем, HRV green + Z3 → +2 → HRV yellow надёжный сигнал для этого атлета.
4. **DFA Readiness Patterns** — `Ra < -5%` подряд + любая нагрузка vs отдых (для конкретного атлета может потребовать обязательного отдыха).

Полная теория этих паттернов — кандидат на extraction в `docs/knowledge/personal-patterns.md` (Mode 4 spec-curator); пока живёт здесь.

### Prompt enrichment

Персональные паттерны добавляются в контекст Claude **двумя путями, без cron + персистенс**:

1. **Weekly report** — Claude вызывает `get_personal_patterns` MCP-tool сам (прописан как шаг 2 в `SYSTEM_PROMPT_WEEKLY` + whitelist `WEEKLY_TOOL_NAMES`). При ≥30 complete-записях возвращает dict, Claude использует в секциях «Восстановление» / «Наблюдение».
2. **Chat (`render_athlete_block`)** — прямой вызов `compute_personal_patterns(user_id, days_back=90)` при сборке athlete-блока. Слот `{personal_patterns_block}` в `_ATHLETE_BLOCK_TEMPLATE`. Если функция возвращает `None` (entries_complete < 30) — слот пустой.

`compute_personal_patterns` — чистая функция в `data/personal_patterns.py`, агрегирует ≤365 строк за миллисекунды. Звать on-demand дешевле, чем поддерживать таблицу + еженедельный actor + invalidation. Если профилирование когда-нибудь покажет горячий путь — добавить кэш (Redis / таблица `personal_patterns` с `computed_at`); до этого — преждевременная оптимизация.

---

## Фаза 4: Ramp-тесты + threshold drift

> Проактивные ramp-тесты для обновления HRVT1/HRVT2 порогов. **Полные протоколы Run + Bike — `docs/RAMP_TEST_BIKE_SPEC.md` (источник истины, rebuild 2026-05-08).** Здесь — триггеры детектора + drift detection logic.

### Триггеры (ежедневный cron)

`tasks/utils.py:RampTrainingSuggestion.is_test_needed`. Все условия ОБЯЗАТЕЛЬНЫ:

- Есть валидные данные wellness (CTL и ATL не None)
- TSB > **−10** (deep fatigue искажает DFA a1)
- Recovery score >= **70** (low recovery шумит HRV-сигнал, линейный фит коллапсирует)
- Нет ramp-теста в `AiWorkout.get_upcoming(days_ahead=14)`
- Для `sport`: `ThresholdFreshnessDTO.status == "no_data"` ИЛИ `days_since > 30`
- Cron проверяет оба спорта (`sports=["Run", "Ride"]`); первый stale/no_data выигрывает

**Phase-aware cadence** (`RampTrainingSuggestion`): peak/taper (≤14d to nearest race) suppress, base (≤56d) 8w cadence, build (>56d) 6w cadence, no goal — 30d default. Multi-goal aware (nearest race wins, не RACE_A first).

Push идёт сразу по клику кнопки — без двухфазного preview. Протокол детерминистический (Ride фиксированный, Run параметризованный по `threshold_pace`), free-form input нет, нечего в превью смотреть. MCP-tool `create_ramp_test_tool` даёт on-demand путь.

### Threshold drift detection

`User.detect_threshold_drift` сравнивает **последний валидный** ramp-замер (LIMIT 1, ORDER BY `start_date_local DESC`) с текущими `AthleteSettings`. Три метрики, общий гейт:

| Metric | Sport(s) | Source | Compared to | Pushed to (Intervals API) |
|---|---|---|---|---|
| `LTHR` | Ride + Run | `ActivityHrv.hrvt2_hr` | `AthleteSettings.lthr` | `update_sport_settings(sport, {"lthr": bpm})` |
| `THRESHOLD_PACE` | Run only | `parse_pace_to_sec(ActivityHrv.hrvt2_pace)` | `AthleteSettings.threshold_pace` | `update_sport_settings("Run", {"threshold_pace": m_per_s})` |
| `FTP` | Ride only | `ActivityHrv.hrvt2_power` | `AthleteSettings.ftp` | `update_sport_settings("Ride", {"ftp": watts})` |

Каждая триггерит alert когда выполнены **оба** условия:

- `|drift|` превышает абсолютный гейт: `DRIFT_LTHR_BPM=3`, `DRIFT_PACE_SEC_PER_KM=5`, `DRIFT_FTP_WATTS=5` (заменили старый 5%-relative)
- `R²` 3-tier:
  - `R² ≥ DRIFT_R2_HIGH=0.85` → авто-fire `actor_update_zones` (без кнопки)
  - `0.70 ≤ R² < 0.85` → button показывается
  - `R² < 0.70` → soft-hint «низкое R² — повтори ramp test»

Helper-сигнатура: `_drift_alert_{lthr,pace,ftp}(sport, hrvt2_value, r_squared, config) → DriftAlertDTO | None` в `data/db/user.py`.

### HRVT2 → Intervals semantics — критично

Intervals.icu's `lthr` / `ftp` / `threshold_pace` поля все **семантически = anaerobic threshold = HRVT2** (LTHR = pow at LT2 = pace at LT2 = HRVT2-эквивалент по Coggan/Friel), НЕ HRVT1.

До 2026-05-08 актёр пушил HRVT1 туда — съезжало все Intervals-зоны на ~13% вниз (Z4 SubThreshold = 95-99% LTHR при `lthr=HRVT1` ≈ Z2 по реальной нагрузке). Фикс: `actor_update_zones` теперь пушит HRVT2-варианты во все три поля. Существующие пользователи получают большой drift на первом новом ramp — intentional (значения у них стояли неправильные). FTP-метрика добавлена 2026-05-08 в рамках issue #313 — раньше автоматического push'а не было (только ручной `mcp_server/tools/update_zones.py`).

### Decisions log

- **Latest, не avg.** Тест по определению — измерение текущего состояния. 3-sample rolling avg сглаживал прогресс: после успешного теста, который улучшил пороги на 8%, средний с двумя старыми замерами всё ещё показывал «3-4%» — слабо для гейта 5%. R²-гейт защищает от шумных одиночных тестов лучше, чем «два теста подряд».
- **HRVT linear fit только по WORK-сегментам.** `detect_hrv_thresholds(work_segments=...)` исключает WU/CD/recovery; на реальных данных R² поднимается с 0.33 до 0.72+.
- **Ramp test push без двухфазного preview.** Протокол детерминистический, free-form input нет.
- **`event.target = "PACE"` критичен для Run ramp.** Без этого Intervals defaults в AUTO → для Run = HR, и Garmin молча выкидывает pace cells из step view (verified live, 2026-05-07). `PlannedWorkoutDTO.to_intervals_event()` выставляет автоматически через `has_pace_steps` детектор.
- **Slope-sign sanity check.** `data/hrv_activity.py:414-423` отбивает не-отрицательный наклон в α1↔HR регрессии — broken sensor / BLE fragmentation. См. `docs/knowledge/dfa-alpha1.md` §«Detection Strategy» step 3.
- **Per-threshold confidence (`n_local × R²`).** `data/hrv_activity.py:349-354` — band ±0.15 around α1 crossing × global R². HIGH = `n_local≥5 ∧ R²≥0.85`, MEDIUM = `n_local≥3 ∧ R²≥0.70`. См. `dfa-alpha1.md` §«Confidence Levels».

### Backfill HRVT2-полей

`hrvt2_pace` (миграция `v2c3d4e5f6a7`) и `hrvt2_power` (миграция `w3d4e5f6a7b8`, 2026-05-08) добавлены постфактум. Старые `activity_hrv` строки имеют их NULL → drift detector их не учитывает. Чтобы переподнять последний ramp без полного reprocessing'а:

```
python -m cli reprocess-ramp-test <user_id> <activity_id> [--push]
```

Команда тянет `dfa_timeseries` + `work_segments` из БД, прогоняет `detect_hrv_thresholds`, патчит **только** HRVT2-производные поля (`hrvt2_pace` для Run, `hrvt2_power` для Ride; другие поля не трогает, чтобы не возникало случайных перерасчётов из-за float rounding). С `--push` сразу дёргает `actor_update_zones`. Push блокируется если activity не последний валидный ramp для своего спорта (защита от пуша по устаревшему тесту).

---

## Утреннее Telegram-сообщение

Telegram — компактный summary, детали в webapp. Формат:

```
Recovery 72 (good), HRV 🟢
🏃 Tempo Run 40min
TSB: -35 🔴 (high risk)

🔔 ПОРОГИ — РАССМОТРИ ОБНОВЛЕНИЕ
━━━━━━━━━━━━━━━━━━━━━
HRVT2 172 bpm
Текущий LTHR: 153 bpm (+12.4%)
→ Обнови в настройках

[Кнопка: Открыть отчёт]
```

Блоки:

1. **Recovery + HRV** — всегда. Score, category, HRV emoji (🟢/🟡/🔴).
2. **Тренировка на сегодня** — если есть. Название + ` (adapted)` если адаптирована.
3. **TSB** — только если < −30 (🔴 high risk). Зоны `optimal`/`gray`/`fresh`/`transition` (TSB ≥ −30) — без отдельной строки в утреннем сообщении.
4. **Threshold drift** — только если обнаружен сдвиг. Яркий блок с разделителем + inline-кнопка `Обновить зоны` (видна при медиум R²-tier).
5. **Кнопка** «Открыть отчёт» — InlineKeyboardButton с web_app URL.

AI-рекомендация **не дублируется** в Telegram — только в webapp.

Ramp-test post-activity flow: `_actor_send_activity_notification` детектит ramp-test через `_is_ramp_test_activity` (matches `ScheduledWorkout` или `AiWorkout` fallback на same date+sport) и роутит на `build_ramp_test_message` (`tasks/formatter.py`) вместо generic. На failure `diagnose_hrv_thresholds` возвращает структурированную причину (`too_few_points` / `a1_range_low` / `a1_range_high` / `positive_slope` / `noisy_fit` / `out_of_range`), formatter показывает её + actionable advice (`_ramp_failure_advice`).
