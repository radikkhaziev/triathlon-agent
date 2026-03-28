# Adaptive Training Plan

> Автоматическая адаптация тренировок HumanGo на основе recovery/HRV/training load данных.

---

## Концепция

HumanGo генерирует тренировочный план и синхронизирует его в Intervals.icu. Агент анализирует состояние атлета (recovery, HRV, TSB, Ra) и при необходимости создаёт **адаптированную версию** тренировки как **новый event** в Intervals.icu. Оригинальная тренировка HumanGo остаётся в календаре без изменений. На часах Garmin появляются обе тренировки — атлет выбирает какую выполнять.

```
HumanGo → Intervals.icu (оригинал, read-only)
                ↓
         Agent анализирует recovery + HRV + TSB + Ra
                ↓
         Создаёт ADAPTED: event → Intervals.icu → Garmin sync
                ↓
         Атлет выполняет → Activity → adaptation_log (факт vs план)
```

---

## Фаза 1: Intervals.icu Write API

### API Endpoints

| Операция | Method | Endpoint |
|---|---|---|
| Создать event | POST | `/athlete/{id}/events` |
| Обновить event | PUT | `/athlete/{id}/events/{eventId}` |
| Удалить event | DELETE | `/athlete/{id}/events/{eventId}` |

Auth: HTTP Basic (`API_KEY` : `INTERVALS_API_KEY`), как в текущем клиенте.

### Event Object (POST/PUT)

```json
{
  "category": "WORKOUT",
  "type": "Ride",
  "name": "AI: Z2 Endurance + Tempo (generated)",
  "start_date_local": "2026-03-28T00:00:00",
  "moving_time": 3600,
  "external_id": "tricoach:2026-03-28:ride:morning",
  "workout_doc": {
    "steps": [
      {"text": "Warm-up", "duration": 600, "power": {"units": "%ftp", "value": 60}, "cadence": {"units": "rpm", "value": 90}},
      {"text": "Z2 Base", "duration": 1800, "power": {"units": "%ftp", "value": 75}},
      {"text": "Tempo", "reps": 3, "steps": [
        {"duration": 300, "power": {"units": "%ftp", "value": 88}},
        {"duration": 180, "power": {"units": "%ftp", "value": 60}}
      ]},
      {"text": "Cool-down", "duration": 600, "power": {"units": "%ftp", "value": 55}}
    ]
  }
}
```

Тренировки создаются через `workout_doc` JSON — структурированные шаги с целевыми значениями. Intervals.icu передаёт их на часы (Garmin/Wahoo) как structured workout с target-зонами.

### Naming convention

- **`AI: {name} (generated)`** — AI сгенерировал с нуля (Фаза 1, нет плана на день)
- **`AI: {name} (adapted)`** — AI модифицировал существующую тренировку HumanGo (Фаза 2)

### workout_doc step format

Каждый шаг — JSON-объект:

| Поле | Тип | Описание |
|---|---|---|
| `text` | string | Название шага ("Warm-up", "Tempo", "Cool-down") |
| `duration` | int | Длительность в секундах (600 = 10 мин) |
| `reps` | int | Количество повторов (для интервалов) |
| `steps` | array | Вложенные шаги (работа + отдых в repeat-группе) |
| `hr` | object | Целевой пульс: `{"units": "%lthr", "value": 75}` |
| `power` | object | Целевая мощность: `{"units": "%ftp", "value": 80}` |
| `pace` | object | Целевой темп: `{"units": "%pace", "value": 90}` |
| `cadence` | object | Каденс: `{"units": "rpm", "value": 90}` |

**Правило:** Ride → `power` (%ftp), Run → `hr` (%lthr), Swim → `pace` (%pace).

**Пример Run с интервалами:**

```json
[
  {"text": "Warm-up", "duration": 600, "hr": {"units": "%lthr", "value": 65}},
  {"text": "Z2 Base", "duration": 900, "hr": {"units": "%lthr", "value": 75}},
  {"text": "Tempo", "reps": 3, "steps": [
    {"duration": 300, "hr": {"units": "%lthr", "value": 88}},
    {"duration": 120, "hr": {"units": "%lthr", "value": 65}}
  ]},
  {"text": "Cool-down", "duration": 600, "hr": {"units": "%lthr", "value": 60}}
]
```

### Новые методы в IntervalsClient

```python
# data/intervals_client.py

async def create_event(self, event: dict) -> dict:
    """POST /athlete/{id}/events → created event with server-generated ID."""
    resp = await self._request(
        "POST",
        f"/athlete/{self._athlete_id}/events",
        json=event,
    )
    return resp.json()

async def update_event(self, event_id: int, event: dict) -> dict:
    """PUT /athlete/{id}/events/{event_id} → updated event."""
    resp = await self._request(
        "PUT",
        f"/athlete/{self._athlete_id}/events/{event_id}",
        json=event,
    )
    return resp.json()

async def delete_event(self, event_id: int) -> None:
    """DELETE /athlete/{id}/events/{event_id}."""
    await self._request(
        "DELETE",
        f"/athlete/{self._athlete_id}/events/{event_id}",
    )
```

### External ID стратегия

Формат: `tricoach:{date}:{sport}:{slot}`

- `tricoach:2026-03-28:ride:morning`
- `tricoach:2026-03-28:run:evening`
- `tricoach:2026-03-29:swim:morning`

`external_id` позволяет:
- Отличать наши тренировки от пользовательских/тренерских
- Делать upsert при повторной генерации (обновить, а не дублировать)
- Не хранить Intervals.icu event ID локально (хотя мы его сохраняем для DELETE)

### Pydantic модели

```python
# data/models.py

class WorkoutStep(BaseModel):
    """Один шаг структурированной тренировки."""
    text: str = ""               # "Warm-up", "Tempo"
    duration: int = 0            # секунды
    reps: int | None = None      # повторы (3 для 3x intervals)
    hr: dict | None = None       # {"units": "%lthr", "value": 75}
    power: dict | None = None    # {"units": "%ftp", "value": 80}
    pace: dict | None = None     # {"units": "%pace", "value": 90}
    cadence: dict | None = None  # {"units": "rpm", "value": 90}
    steps: list[WorkoutStep] | None = None  # вложенные шаги (repeat group)

class PlannedWorkout(BaseModel):
    """AI-generated workout to push to Intervals.icu."""
    sport: str                   # "Ride" | "Run" | "Swim"
    name: str                    # "Z2 Endurance + 3x5m Tempo"
    steps: list[WorkoutStep]     # структурированные шаги
    duration_minutes: int        # 60
    target_tss: int | None       # 65
    rationale: str               # Почему именно эта тренировка
    target_date: date
    slot: str = "morning"        # morning | evening
    suffix: str = "generated"    # "generated" | "adapted"

    @property
    def external_id(self) -> str:
        return f"tricoach:{self.target_date}:{self.sport.lower()}:{self.slot}"

    def to_intervals_event(self) -> dict:
        return {
            "category": "WORKOUT",
            "type": self.sport,
            "name": f"AI: {self.name} ({self.suffix})",
            "start_date_local": f"{self.target_date}T00:00:00",
            "moving_time": self.duration_minutes * 60,
            "external_id": self.external_id,
            "workout_doc": {"steps": [s.model_dump(exclude_none=True) for s in self.steps]},
        }
```

### Новая таблица: `ai_workouts`

```sql
CREATE TABLE ai_workouts (
    id              SERIAL PRIMARY KEY,
    date            VARCHAR(10) NOT NULL,
    sport           VARCHAR(30) NOT NULL,
    slot            VARCHAR(10) NOT NULL DEFAULT 'morning',
    external_id     VARCHAR(100) NOT NULL UNIQUE,
    intervals_id    INTEGER,
    name            VARCHAR(200) NOT NULL,
    description     TEXT,
    duration_minutes INTEGER,
    target_tss      INTEGER,
    rationale       TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

Статусы: `active` | `completed` | `cancelled` | `replaced`.

### AI: генерация тренировок

Новый промпт генерирует структурированный JSON с workout description в Intervals.icu syntax.

```python
# ai/prompts.py — WORKOUT_GENERATION_PROMPT

## Входные данные (подставляются в промпт)
- Recovery score/category, HRV delta, RHR, sleep
- CTL/ATL/TSB, per-sport CTL vs targets, ramp rate
- Вчерашняя тренировка + DFA (Ra, Da)
- Атлет: age, LTHR, FTP, CSS, goal

## Правила выбора нагрузки
- Recovery excellent + TSB > 0 → можно интенсив (Z4-Z5)
- Recovery good → Z2-Z3, до 90 мин
- Recovery moderate / sleep < 50 → Z1-Z2, 45-60 мин
- Recovery low / red HRV → отдых или Z1 до 30 мин
- TSB < -25 → максимум Z1-Z2
- HRV delta < -15% → максимум Z1-Z2
- Ramp rate > 7 → снизить объём
- Приоритет спорта: тот, где CTL отстаёт от цели больше всего

## Формат ответа: строго JSON
{
  "sport": "Ride|Run|Swim",
  "name": "краткое название",
  "description": "Intervals.icu workout syntax",
  "duration_minutes": число,
  "target_tss": число или null,
  "rationale": "1-2 предложения"
}
```

**Когда вызывается:**
- После утренней рекомендации, если `recovery_category != "low"` и AI не рекомендовал полный отдых
- Только если на сегодня нет тренировки ИЛИ AI считает запланированную неуместной

**Модель:** `claude-sonnet-4-6`, max_tokens 512.

### MCP Tools

```python
@mcp.tool()
async def suggest_workout(
    sport: str,              # "Ride" | "Run" | "Swim"
    name: str,
    description: str,        # Intervals.icu workout syntax
    duration_minutes: int,
    target_tss: int | None = None,
    rationale: str = "",
    target_date: str | None = None,
) -> str:
    """Push AI workout to Intervals.icu calendar. Workout appears
    on athlete's devices (Garmin/Wahoo) via Intervals.icu sync.
    Use Intervals.icu workout syntax: 75%, Z2, 4x (8m 90% / 2m 60%)."""

@mcp.tool()
async def remove_ai_workout(
    target_date: str,
    sport: str | None = None,
) -> str:
    """Remove AI workout from Intervals.icu. Only removes AI: workouts."""

@mcp.tool()
async def list_ai_workouts(days_ahead: int = 7) -> str:
    """List upcoming AI-generated workouts."""
```

### Конфиг

```env
AI_WORKOUT_ENABLED=true        # Включить генерацию (default: false)
AI_WORKOUT_AUTO_PUSH=false     # Автопуш в Intervals.icu (default: false)
```

### Безопасность

- Тренировки без `external_id` с префиксом `tricoach:` **не трогаем** — это пользовательские/тренерские
- Расы (`RACE_A/B/C`), заметки (`NOTE`) — никогда не создаём и не удаляем
- Оригинальная тренировка HumanGo **не модифицируется и не удаляется**
- `AI:` префикс в name — визуальный маркер в календаре
- Rate limit: 1-2 write запроса в день, далеко от лимита Intervals.icu (30 req/s)

### Порядок реализации Фазы 1

| # | Задача | Файлы |
|---|---|---|
| 1 | Write-методы в IntervalsClient | `data/intervals_client.py` |
| 2 | PlannedWorkout модель | `data/models.py` |
| 3 | Таблица `ai_workouts` + CRUD | `data/database.py`, Alembic миграция |
| 4 | Промпт генерации | `ai/prompts.py` |
| 5 | `generate_workout()` в ClaudeAgent | `ai/claude_agent.py` |
| 6 | MCP tools: suggest, remove, list | `mcp_server/tools/` |
| 7 | Интеграция в утренний cron | `bot/scheduler.py` |
| 8 | Конфиг + env vars | `config.py`, `.env.example` |

### Критерии готовности Фазы 1

- [ ] `IntervalsClient.create_event()` / `update_event()` / `delete_event()` работают
- [ ] AI генерирует валидный Intervals.icu workout syntax
- [ ] MCP tool `suggest_workout` создаёт тренировку в Intervals.icu
- [ ] MCP tool `remove_ai_workout` удаляет только AI-тренировки
- [ ] Тренировки видны в Intervals.icu с маркером `AI:`
- [ ] Тренировки синхронизируются на часы через Intervals.icu
- [ ] `ai_workouts` таблица ведёт аудит всех операций
- [ ] `external_id` предотвращает дубликаты
- [ ] Конфиг `AI_WORKOUT_ENABLED` / `AI_WORKOUT_AUTO_PUSH` работает
- [ ] Нет влияния на пользовательские/тренерские тренировки

---

## Фаза 2: Адаптация тренировок HumanGo

> Когда тренировка запланирована тренером (HumanGo), но состояние атлета не позволяет выполнить её как есть — создаётся `ADAPTED:` версия рядом с оригиналом.

### Два сценария

| Сценарий | Фаза | Префикс | Когда |
|---|---|---|---|
| Нет плана → AI генерирует с нуля | Фаза 1 | `AI:` | Нет тренировки на день |
| Есть план → адаптация под состояние | Фаза 2 | `ADAPTED:` | Тренировка HumanGo неуместна |

Оригинальная тренировка HumanGo **не модифицируется и не удаляется**. Адаптация создаётся как отдельный event. На часах Garmin видны обе — атлет выбирает.

### Входные данные

| Метрика | Источник | Роль |
|---|---|---|
| Recovery score (0-100) | `data/metrics.py` | Основной индикатор |
| Recovery category | excellent/good/moderate/low | Категоризация |
| HRV status | flatt_esco + ai_endurance | Оба алгоритма |
| RHR status | green/yellow/red | Дополнительный сигнал |
| TSB | Intervals.icu | Накопленная усталость |
| Ra (Readiness) | DFA a1 pipeline | Свежесть |
| Planned workout | `scheduled_workouts` | Что запланировал HumanGo |
| `workout_doc` | Intervals.icu JSON | Структурированный разбор тренировки (парсится server-side) |

### Правила адаптации

Правила определяют **максимально допустимую зону** и **коррекцию длительности**. Начальные значения — статические. После накопления данных в Adaptation Log (Фаза 3) — персонализируются.

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

TSB < -25 (overtraining risk):
  → Max zone: Z2 cap (независимо от recovery)
  → Длительность: -20%

Ra < -5% (3+ дней подряд):
  → Дополнительное снижение
  → Флаг в description
```

### Разбор структуры тренировки

Приоритет источников для парсинга:
1. **`workout_doc` JSON** — Intervals.icu автоматически парсит `description` при создании event и сохраняет структурированную версию. Доступен через GET API. Содержит шаги с типами, длительностью и target-значениями. **Предпочтительный источник** — не нужно парсить текст самостоятельно.
2. **`description` текст** — fallback если `workout_doc` пустой. Regex-парсер для основных паттернов HumanGo.

```python
def parse_workout(workout: ScheduledWorkout) -> list[WorkoutStep]:
    """Извлекает структурированные шаги из тренировки.

    Приоритет: workout_doc (JSON) > description (text regex).
    """
    if workout.workout_doc:
        return parse_workout_doc(workout.workout_doc)
    return parse_description_text(workout.description)
```

### Алгоритм адаптации

```python
def adapt_workout(original: ScheduledWorkout, recovery: RecoveryScore,
                  hrv_status: str, tsb: float, ra: float | None) -> PlannedWorkout | None:
    """Создаёт адаптированную тренировку или None если адаптация не нужна."""

    # 1. Определить ограничения
    max_zone, duration_factor = compute_constraints(recovery, hrv_status, tsb, ra)

    # 2. Разобрать оригинал
    steps = parse_workout(original)

    # 3. Проверить нужна ли адаптация
    if not needs_adaptation(steps, max_zone, duration_factor):
        return None

    # 4. Clamp шаги: снизить зоны, укоротить длительность
    adapted_steps = [clamp_step(s, max_zone, duration_factor) for s in steps]

    # 5. Сериализовать обратно в Intervals.icu workout syntax
    description = steps_to_description(adapted_steps)

    # 6. Собрать PlannedWorkout
    return PlannedWorkout(
        sport=original.type,
        name=f"Adapted: {original.name}",  # → "ADAPTED: Adapted: ..."
        description=description,
        duration_minutes=...,
        rationale=f"Recovery {recovery.score} ({recovery.category}), HRV {hrv_status}, TSB {tsb}",
        date=original.start_date_local,
    )
```

### External ID для адаптаций

Формат: `tricoach-adapted:{date}:{sport}`

Отличается от Фазы 1 (`tricoach:{date}:{sport}:{slot}`) — два типа не конфликтуют.

### Интеграция в утренний cron

```
scheduler → sync → analysis → Claude recommendation
    → if planned workout exists:
        → adapt_workout() → create ADAPTED: event (if needed)
    → else:
        → generate_workout() → create AI: event (Phase 1)
    → Telegram report
```

Telegram report дополняется:

```
🔄 Адаптация тренировки:
Оригинал: Tempo Run 40min (Z3-Z4)
ADAPTED: Z2 Run 35min — recovery 58%, HRV жёлтый
→ Создана в Intervals.icu, появится на часах
```

Или:

```
✅ Тренировка без изменений:
Tempo Run 40min — recovery 82%, HRV зелёный
→ Выполняй как запланировано
```

---

## Фаза 3: Adaptation Log + обучение

> Ключевая фаза для персонализации. Вместо статических правил "recovery < 40 → отдых" — **твои конкретные паттерны**.

### Что записываем

Каждая тренировка (оригинал, адаптация или AI-сгенерированная) фиксируется в лог. После выполнения — добавляется факт и outcome.

### Таблица `training_log`

```sql
CREATE TABLE training_log (
    id              SERIAL PRIMARY KEY,
    date            VARCHAR(10) NOT NULL,       -- "2026-03-28"
    sport           VARCHAR(30),

    -- Что было запланировано
    source          VARCHAR(20) NOT NULL,       -- "humango" | "ai" | "adapted" | "none"
    original_name   TEXT,
    original_description TEXT,
    original_duration_sec INTEGER,

    -- Адаптация (если была)
    adapted_name    TEXT,
    adapted_description TEXT,
    adapted_duration_sec INTEGER,
    adaptation_reason TEXT,                      -- "Recovery 58 (moderate), HRV yellow, TSB -18"

    -- Контекст ДО тренировки
    pre_recovery_score    FLOAT,
    pre_recovery_category VARCHAR(20),
    pre_hrv_status        VARCHAR(20),
    pre_hrv_delta_pct     FLOAT,
    pre_rhr_today         FLOAT,
    pre_rhr_status        VARCHAR(20),
    pre_tsb               FLOAT,
    pre_ctl               FLOAT,
    pre_atl               FLOAT,
    pre_ra_pct            FLOAT,
    pre_sleep_score       FLOAT,

    -- Факт (заполняется после тренировки)
    actual_activity_id    VARCHAR(50),
    actual_sport          VARCHAR(30),
    actual_duration_sec   INTEGER,
    actual_avg_hr         FLOAT,
    actual_tss            FLOAT,
    actual_max_zone_time  VARCHAR(10),           -- "Z2" | "Z3" | "Z4" — реально достигнутая макс зона
    compliance            VARCHAR(20),           -- "followed_adapted" | "followed_original" | "followed_ai" | "skipped" | "modified"

    -- Outcome: состояние ПОСЛЕ тренировки (на следующий день)
    post_recovery_score   FLOAT,
    post_hrv_delta_pct    FLOAT,
    post_rhr_today        FLOAT,
    post_sleep_score      FLOAT,
    post_ra_pct           FLOAT,
    recovery_delta        FLOAT,                 -- post_recovery - pre_recovery (положительный = хорошо восстановился)

    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_training_log_date ON training_log(date);
CREATE INDEX idx_training_log_source ON training_log(source);
```

**Без FK на `scheduled_workouts`** — soft reference. Таблица `scheduled_workouts` делает stale deletion при sync, FK сломал бы логи.

### Жизненный цикл записи

```
06:00  daily_metrics_job → создаёт запись: pre_* контекст, source, original, adapted
       (compliance, actual, post = NULL)

18:00  sync_activities_job → находит activity за дату+спорт
       → заполняет actual_*, определяет compliance

06:00+1  daily_metrics_job следующего дня
       → заполняет post_* (recovery, HRV, RHR, sleep, Ra следующего дня)
       → вычисляет recovery_delta
```

### Compliance detection

Сравниваем фактическую активность с вариантами:

```python
def detect_compliance(log: TrainingLogRow, activity: Activity) -> str:
    """Определяет какой вариант выбрал атлет."""

    if activity is None:
        return "skipped"

    # Сравнение по: sport type + duration (±20%) + intensity (avg HR / TSS)
    adapted_match = match_score(log.adapted_*, activity)
    original_match = match_score(log.original_*, activity)
    ai_match = match_score(log.ai_*, activity) if log.source == "ai" else 0

    best = max(adapted_match, original_match, ai_match)

    if best < 0.5:  # порог совпадения
        return "modified"
    if best == adapted_match:
        return "followed_adapted"
    if best == original_match:
        return "followed_original"
    return "followed_ai"
```

### Паттерны для обучения

Накопленные данные (30+ записей) позволяют извлечь **персональные паттерны**:

#### 1. Recovery Response Model

Вопрос: при каком `pre_recovery` + каком типе нагрузки → какой `recovery_delta`?

```
pre_recovery=65, actual_tss=80 (Z3 intervals) → recovery_delta = -20 (плохо)
pre_recovery=65, actual_tss=45 (Z2 steady)    → recovery_delta = +5  (хорошо)
pre_recovery=65, skipped                       → recovery_delta = +15 (отдых помог)
```

**Вывод:** для этого атлета при recovery 60-70 → Z2 steady оптимален, Z3 вредит.

#### 2. Personal Adaptation Thresholds

Вопрос: при каком `pre_recovery` атлет реально справляется с Z3+ нагрузкой?

```
Стандартное правило: recovery < 70 → max Z2
Факт: атлет при recovery 55-65 регулярно выполняет Z3 и recovery_delta > 0
→ Персональный порог: recovery < 50 → max Z2 (вместо 70)
```

#### 3. HRV Sensitivity

Вопрос: насколько HRV предсказывает recovery response?

```
HRV yellow + Z3 → recovery_delta среднее: -12
HRV green + Z3  → recovery_delta среднее: +2
→ HRV yellow — надёжный сигнал для этого атлета, снижать зоны
```

#### 4. DFA Readiness Patterns

```
Ra < -5% (3 дня) + любая нагрузка → recovery_delta среднее: -15
Ra < -5% (3 дня) + отдых → recovery_delta среднее: +10
→ Ra < -5% устойчивый — обязательный отдых для этого атлета
```

### Prompt enrichment

Персональные паттерны добавляются в контекст Claude:

```python
PERSONAL_PATTERNS_SECTION = """
## Персональные паттерны атлета (из training_log, {N} записей)

Recovery response:
- При recovery 55-70: Z2 steady → следующий день +5 recovery. Z3+ → -12 recovery.
- При recovery < 50: любая нагрузка → -15 recovery. Только отдых помогает.

Compliance:
- При recovery 55-65 атлет в 80% случаев игнорирует адаптацию и выполняет оригинал.
  Результат: recovery_delta -8 в среднем. Рекомендация не снижать до Z2 при recovery > 55.

HRV sensitivity:
- HRV yellow + Z3 → -12 recovery (высокая чувствительность).
  Снижать зоны при HRV yellow — работает для этого атлета.

DFA:
- Ra < -5% три дня подряд → обязательный отдых, иначе -15 recovery.
"""
```

### MCP tools

```python
@mcp.tool()
async def get_training_log(target_date: str = "", days_back: int = 14) -> str:
    """Get training log with pre/post context and compliance."""

@mcp.tool()
async def get_personal_patterns() -> str:
    """Compute personal recovery/compliance patterns from training_log.
    Requires 30+ entries for meaningful patterns."""
```

### Периодический анализ

Еженедельный job (воскресенье 20:00) пересчитывает паттерны:

```python
async def compute_personal_patterns() -> dict:
    """Анализирует training_log за последние 90 дней.

    Returns dict с персональными порогами и паттернами
    для использования в промптах и правилах адаптации.
    """
```

Результат сохраняется в JSON-поле в wellness или отдельную таблицу `personal_patterns` (key-value).

---

## Фаза 4: Ramp-тесты

> Проактивные ramp-тесты для обновления HRVT1/HRVT2 порогов.

### Триггеры

Агент проверяет ежедневно (в утреннем cron):

- Последний валидный HRVT1/HRVT2 старше **21 дня**
- Recovery score >= 70 (good или excellent)
- TSB > -10 (не в глубокой усталости)
- Нет ключевой тренировки завтра (race, hard intervals)
- Нет ramp-теста за последние 14 дней

### Протокол: велосипед

```
Warm-up
10m 60%

Step 1
5m 65%

Step 2
5m 73%

Step 3
5m 80%

Step 4
5m 88%

Step 5
5m 95%

Step 6
5m 103%

Cool-down
10m 55%
```

Total: 50 min. Ступеньки по 5 минут — минимум для стабилизации DFA a1. Шаги ~8% FTP.

### Протокол: бег

```
Warm-up
10m 65% Pace

Step 1
5m 70% Pace

Step 2
5m 78% Pace

Step 3
5m 85% Pace

Step 4
5m 92% Pace

Step 5
5m 100% Pace

Cool-down
10m 60% Pace
```

Total: 50 min. `% Pace` — относительно CSS/threshold pace в Intervals.icu.

### Создание event

```python
def create_ramp_test(sport: str, target_date: date) -> PlannedWorkout:
    return PlannedWorkout(
        sport=sport,
        name=f"Ramp Test ({sport})",   # → "AI: Ramp Test (Ride)"
        description=RAMP_PROTOCOL[sport],
        duration_minutes=50,
        target_tss=None,
        rationale=f"HRVT1/HRVT2 устарели ({days_since} дней). Chest strap обязателен для DFA.",
        date=target_date,
    )
```

`description` включает комментарий:
```
# Ramp test для определения HRVT1/HRVT2
# Chest strap обязателен (оптический датчик не подходит для DFA)
# Каждая ступенька 5 мин — держать ровный темп
```

### Обновление порогов

После ramp-теста DFA a1 pipeline (`data/hrv_activity.py`) автоматически:
1. Обрабатывает FIT → RR интервалы
2. Рассчитывает DFA a1 по окнам
3. Детектирует HRVT1 (a1=0.75) и HRVT2 (a1=0.50)
4. Сохраняет в `activity_hrv`

Если новые пороги отличаются от текущих (>5%), утренний отчёт следующего дня включает:
```
📊 Обновлены пороги:
HRVT1: 148 → 152 bpm (+2.7%)
HRVT2: 168 → 170 bpm (+1.2%)
```

### MCP tools

```python
@mcp.tool()
async def get_threshold_freshness(sport: str = "") -> str:
    """Check how fresh HRVT1/HRVT2 thresholds are. Returns days since last valid test."""

@mcp.tool()
async def create_ramp_test(sport: str, target_date: str) -> str:
    """Create a ramp test workout in Intervals.icu. Only Ride and Run supported."""
```

---

## План реализации

### Этап 1: Фаза 1 — Write API + AI генерация (2-3 дня)
- [ ] `create_event()` / `update_event()` / `delete_event()` в IntervalsClient
- [ ] `PlannedWorkout` модель + `to_intervals_event()`
- [ ] Таблица `ai_workouts` + Alembic миграция + CRUD
- [ ] Промпт `WORKOUT_GENERATION_PROMPT` + `generate_workout()` в ClaudeAgent
- [ ] MCP tools: `suggest_workout`, `remove_ai_workout`, `list_ai_workouts`
- [ ] Интеграция в утренний cron (если `AI_WORKOUT_ENABLED`)
- [ ] Конфиг: `AI_WORKOUT_ENABLED`, `AI_WORKOUT_AUTO_PUSH`
- [ ] Тест: event в Intervals.icu → синхронизация на часы

### Этап 2: Фаза 2 — Парсер + адаптация (3-4 дня)
- [ ] Парсер `workout_doc` JSON → `WorkoutStep[]`
- [ ] Fallback парсер `description` текст → `WorkoutStep[]`
- [ ] `compute_constraints()` — правила max_zone + duration_factor
- [ ] `needs_adaptation()` + `clamp_step()` + `steps_to_description()`
- [ ] `adapt_workout()` — полный pipeline
- [ ] Утренний cron: создание `ADAPTED:` event
- [ ] Telegram report: секция адаптации
- [ ] Тесты на реальных тренировках HumanGo

### Этап 3: Фаза 3 — Training Log + обучение (2-3 дня)
- [ ] Таблица `training_log` + Alembic миграция + ORM
- [ ] Запись pre-контекста в утреннем cron
- [ ] Заполнение actual-данных при sync activities
- [ ] Заполнение post-данных на следующий день
- [ ] Compliance detection
- [ ] MCP tools: `get_training_log`, `get_personal_patterns`
- [ ] `compute_personal_patterns()` — еженедельный анализ
- [ ] Prompt enrichment: персональные паттерны в контексте Claude

### Этап 4: Фаза 4 — Ramp-тесты (1-2 дня)
- [ ] Ramp протоколы (Ride + Run) в Intervals.icu workout syntax
- [ ] Проверка свежести порогов в утреннем cron
- [ ] MCP tools: `get_threshold_freshness`, `create_ramp_test`
- [ ] Telegram уведомление о предложении ramp-теста
