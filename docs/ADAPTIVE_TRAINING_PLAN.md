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
         Создаёт AI: {name} (adapted) event → Intervals.icu → Garmin sync
                ↓
         Атлет выполняет → Activity → adaptation_log (факт vs план)
```

---

## Фаза 1: Intervals.icu Write API

### API Endpoints

| Операция       | Method | Endpoint                         |
| -------------- | ------ | -------------------------------- |
| Создать event  | POST   | `/athlete/{id}/events`           |
| Обновить event | PUT    | `/athlete/{id}/events/{eventId}` |
| Удалить event  | DELETE | `/athlete/{id}/events/{eventId}` |

Auth: per-user dual mode через `IntervalsAsyncClient.for_user(user_id)` — Bearer (OAuth access_token) либо Basic (api_key) в зависимости от `users.intervals_auth_method`. Глобальный `INTERVALS_API_KEY` в этом пайплайне не используется. См. CLAUDE.md §«Intervals.icu Auth — Dual Mode».

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
      {
        "text": "Warm-up",
        "duration": 600,
        "power": { "units": "%ftp", "value": 60 },
        "cadence": { "units": "rpm", "value": 90 }
      },
      {
        "text": "Z2 Base",
        "duration": 1800,
        "power": { "units": "%ftp", "value": 75 }
      },
      {
        "text": "Tempo",
        "reps": 3,
        "steps": [
          { "duration": 300, "power": { "units": "%ftp", "value": 88 } },
          { "duration": 180, "power": { "units": "%ftp", "value": 60 } }
        ]
      },
      {
        "text": "Cool-down",
        "duration": 600,
        "power": { "units": "%ftp", "value": 55 }
      }
    ]
  }
}
```

Тренировки создаются через `workout_doc` JSON — структурированные шаги с целевыми значениями. Intervals.icu передаёт их на часы (Garmin/Wahoo) как structured workout с target-зонами.

### Naming convention

Имя тренировки рендерится в `PlannedWorkoutDTO.to_intervals_event()` (`data/intervals/dto.py:420`):
`"AI: " + name + (" (" + suffix + ")" if suffix else "")`.

- **`AI: {name}`** — Фаза 1, AI сгенерировал с нуля (`suffix=None`, default)
- **`AI: {name} (adapted)`** — Фаза 2, AI модифицировал существующую тренировку HumanGo (`suffix="adapted"`)

Префикс всегда `AI:`, независимо от фазы. Если Claude уже добавил `AI: ` в `name`, повторный префикс снимается.

### workout_doc step format

Каждый шаг — JSON-объект:

| Поле       | Тип    | Описание                                           |
| ---------- | ------ | -------------------------------------------------- |
| `text`     | string | Название шага ("Warm-up", "Tempo", "Cool-down")    |
| `duration` | int    | Длительность в секундах (600 = 10 мин)             |
| `reps`     | int    | Количество повторов (для интервалов)               |
| `steps`    | array  | Вложенные шаги (работа + отдых в repeat-группе)    |
| `hr`       | object | Целевой пульс: `{"units": "%lthr", "value": 75}`   |
| `power`    | object | Целевая мощность: `{"units": "%ftp", "value": 80}` |
| `pace`     | object | Целевой темп: `{"units": "%pace", "value": 90}`    |
| `cadence`  | object | Каденс: `{"units": "rpm", "value": 90}`            |

**Правило:** Ride → `power` (%ftp), Run → `hr` (%lthr), Swim → `pace` (%pace).

**Пример Run с интервалами:**

```json
[
  {
    "text": "Warm-up",
    "duration": 600,
    "hr": { "units": "%lthr", "value": 65 }
  },
  {
    "text": "Z2 Base",
    "duration": 900,
    "hr": { "units": "%lthr", "value": 75 }
  },
  {
    "text": "Tempo",
    "reps": 3,
    "steps": [
      { "duration": 300, "hr": { "units": "%lthr", "value": 88 } },
      { "duration": 120, "hr": { "units": "%lthr", "value": 65 } }
    ]
  },
  {
    "text": "Cool-down",
    "duration": 600,
    "hr": { "units": "%lthr", "value": 60 }
  }
]
```

### Новые методы в IntervalsClient

```python
# data/intervals/client.py

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
# data/intervals/dto.py

class WorkoutStepDTO(BaseModel):
    """Один шаг структурированной тренировки."""
    text: str = ""               # "Warm-up", "Tempo"
    duration: int = 0            # секунды (mutually exclusive with distance)
    distance: float | None = None  # метры (100, 200, 1000). Для Swim/Run интервалов
    reps: int | None = None      # повторы (3 для 3x intervals)
    hr: dict | None = None       # {"units": "%lthr", "value": 75, "end": 82}
    power: dict | None = None    # {"units": "%ftp", "value": 80}
    pace: dict | None = None     # {"units": "%pace", "value": 90}
    cadence: dict | None = None  # {"units": "rpm", "value": 90}
    steps: list[WorkoutStepDTO] | None = None  # вложенные шаги (repeat group)
    # Validation: terminal step must have duration OR distance, repeat groups exempt;
    # каждый terminal step обязан нести интенсивностный target (hr/power/pace) — иначе
    # часы не пиликают (см. PlannedWorkoutDTO._check_steps_have_targets).

class PlannedWorkoutDTO(BaseModel):
    """AI-generated workout to push to Intervals.icu."""
    sport: str                       # "Ride" | "Run" | "Swim" | "Other"
    name: str                        # "Z2 Endurance + 3x5m Tempo"
    steps: list[WorkoutStepDTO]      # структурированные шаги
    duration_minutes: int            # 60
    target_tss: int | None = None    # 65
    rationale: str = ""              # Почему именно эта тренировка
    target_date: date
    slot: str = "morning"            # morning | evening
    suffix: str | None = None        # None для Phase 1, "adapted" для Phase 2

    @property
    def external_id(self) -> str:
        return f"tricoach:{self.target_date}:{self.sport.lower()}:{self.slot}"

    def to_intervals_event(self) -> EventExDTO:
        clean_name = self.name[4:] if self.name.startswith("AI: ") else self.name
        workout_doc = {"steps": [s.model_dump(exclude_none=True) for s in self.steps]}
        if self.rationale:
            workout_doc["description"] = self.rationale
        return EventExDTO(
            category="WORKOUT",
            type=self.sport,
            name=f"AI: {clean_name}" + (f" ({self.suffix})" if self.suffix else ""),
            start_date_local=f"{self.target_date}T00:00:00",
            moving_time=self.duration_minutes * 60,
            external_id=self.external_id,
            workout_doc=workout_doc,
            target=("PACE" if self.has_distance_steps and self.sport in ("Swim", "Run") else None),
        )
```

> **Конвенция проекта:** все DTO именуются с суффиксом `*DTO`. Старые названия `WorkoutStep` / `PlannedWorkout` в коде не используются. Rationale кладётся в `workout_doc.description`, а не в top-level `description` event'а — Intervals.icu иначе молча роняет `workout_doc.steps` для Swim (regression замечена ~2026-04-30, см. docstring `to_intervals_event`).

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

Генерация идёт через MCP-tool `suggest_workout` (`mcp_server/tools/ai_workouts.py:84`). Claude в чате собирает контекст (recovery, HRV, TSB, scheduled workouts) сам через другие MCP-tools и формирует структурированные `steps` JSON. Системный промпт `bot/prompts.py:_STATIC_PROMPT_CHAT` + per-user `render_athlete_block` несут все правила и зоны.

```python
# Входные данные, которые Claude собирает через MCP перед вызовом suggest_workout
- Recovery score/category, HRV delta, RHR, sleep    (get_recovery, get_hrv_analysis, get_rhr_analysis)
- CTL/ATL/TSB, per-sport CTL vs targets, ramp rate  (get_training_load)
- Вчерашняя тренировка + DFA (Ra, Da)               (get_activities, get_hrv_analysis)
- Атлет: age, LTHR, FTP, CSS, goal                  (system prompt athlete block)
- Уже выполненные сегодня активности                (warning внутри suggest_workout)

# Правила выбора нагрузки (zoned-out for Claude в SYSTEM_PROMPT_CHAT)
- Recovery excellent + TSB > 0 → можно интенсив (Z4-Z5)
- Recovery good → Z2-Z3, до 90 мин
- Recovery moderate / sleep < 50 → Z1-Z2, 45-60 мин
- Recovery low / red HRV → отдых или Z1 до 30 мин
- TSB < -25 → максимум Z1-Z2
- HRV delta < -15% → максимум Z1-Z2
- Ramp rate > 7 → снизить объём
- Приоритет спорта: тот, где CTL отстаёт от цели больше всего
```

Workout-syntax `description` text **больше не используется** — Claude формирует структурированные `steps: list[dict]` напрямую (см. сигнатуру `suggest_workout` ниже). Plain-text `description` оставлен только в `workout_doc.description` под rationale.

**Когда вызывается:**

- **Только on-demand**, не автоматически по утреннему cron. Триггеры:
  - `/workout` команда в чате
  - Free-form запрос в чате («предложи тренировку», «давай интервалы»)
  - Inline-кнопка «Адаптировать» в утреннем отчёте (Фаза 2, см. ниже)
- Авто-генерация Фазы 1 (cron видит «нет тренировки» → создаёт сам) **намеренно не реализована** — при отсутствии плана пользователь явно просит, либо тренировки нет вовсе.

**Модель:** `claude-sonnet-4-6` через `bot.agent.ClaudeAgent.chat()`, обычный chat-loop с MCP-tools (не отдельный constrained-генератор).

### MCP Tools

```python
@mcp.tool()
async def suggest_workout(
    sport: str,                  # "Ride" | "Run" | "Swim" | "Other"
    name: str,
    steps: list[dict],           # структурированные WorkoutStepDTO в JSON
    duration_minutes: int,
    target_tss: int | None = None,
    rationale: str = "",
    target_date: str = "",
    dry_run: bool = False,       # preview-режим: показать confirm-кнопку, не пушить
) -> str:
    """Generate AI workout and push to athlete's Intervals.icu calendar.
    Syncs to Garmin/Wahoo. Каждый terminal step обязан нести hr/power/pace —
    text-only steps валидатор отбивает (часы не алертят без target).
    Sport=Other (yoga/mobility) освобождён от проверки intensity-targets."""

@mcp.tool()
async def remove_ai_workout(
    target_date: str,
    sport: str = "",
) -> str:
    """Remove AI workout from Intervals.icu. Only removes AI: workouts."""

@mcp.tool()
async def list_ai_workouts(days_ahead: int = 7) -> dict:
    """List upcoming AI-generated workouts."""
```

`suggest_workout` следует двухфазному dry-run паттерну: первый вызов с `dry_run=True` рендерит preview + inline-кнопку «Отправить», `bot/main.py` сохраняет tool_use блок в `pending_workout` и при confirm повторяет его с `dry_run=False` без re-inference. См. `bot/main.py:_PREVIEWABLE_TOOLS`.

### Конфиг

Отдельных флагов `AI_WORKOUT_ENABLED` / `AI_WORKOUT_AUTO_PUSH` нет — `suggest_workout` MCP-tool доступен всем активным юзерам безусловно, а «автопуш» заменён двухфазным `dry_run`-паттерном (preview → confirm-кнопка).

### Безопасность

- Тренировки без `external_id` с префиксом `tricoach:` **не трогаем** — это пользовательские/тренерские
- Расы (`RACE_A/B/C`), заметки (`NOTE`) — никогда не создаём и не удаляем
- Оригинальная тренировка HumanGo **не модифицируется и не удаляется**
- `AI:` префикс в name — визуальный маркер в календаре
- Rate limit: 1-2 write запроса в день, далеко от лимита Intervals.icu (30 req/s)

### Порядок реализации Фазы 1

| #   | Задача                                            | Файлы                                                |
| --- | ------------------------------------------------- | ---------------------------------------------------- |
| 1   | Write-методы в IntervalsClient                    | `data/intervals/client.py` (`create_event` / `update_event` / `delete_event`) |
| 2   | `WorkoutStepDTO` + `PlannedWorkoutDTO`            | `data/intervals/dto.py`                              |
| 3   | Таблица `ai_workouts` + CRUD                      | `data/db/workout.py` (`AiWorkout`), Alembic миграция |
| 4   | Промпт чата с workout-генерацией                  | `bot/prompts.py` (`SYSTEM_PROMPT_CHAT` / `_zones_block`) |
| 5   | MCP tools: `suggest_workout` / `remove_ai_workout` / `list_ai_workouts` | `mcp_server/tools/ai_workouts.py`     |
| 6   | Two-phase dry-run preview (workout_push / cancel) | `bot/main.py` (`_PREVIEWABLE_TOOLS`)                 |

### Критерии готовности Фазы 1

- [x] `IntervalsClient.create_event()` / `update_event()` / `delete_event()` работают
- [x] AI формирует валидный `workout_doc.steps` JSON (text-syntax `description` deprecated)
- [x] MCP tool `suggest_workout` создаёт тренировку в Intervals.icu (с `dry_run` preview-режимом)
- [x] MCP tool `remove_ai_workout` удаляет только AI-тренировки
- [x] Тренировки видны в Intervals.icu с маркером `AI:`
- [x] Тренировки синхронизируются на часы через Intervals.icu
- [x] `ai_workouts` таблица ведёт аудит всех операций
- [x] `external_id` предотвращает дубликаты (upsert на (user, date, sport, slot))
- [x] Нет влияния на пользовательские/тренерские тренировки

---

## Фаза 2: Адаптация тренировок HumanGo

> Когда тренировка запланирована тренером (HumanGo), но состояние атлета не позволяет выполнить её как есть — создаётся `AI: ... (adapted)` версия рядом с оригиналом.

### Два сценария

| Сценарий                            | Фаза   | Имя в Intervals.icu          | Когда                        |
| ----------------------------------- | ------ | ---------------------------- | ---------------------------- |
| Нет плана → AI генерирует с нуля    | Фаза 1 | `AI: {name}`                 | Нет тренировки на день       |
| Есть план → адаптация под состояние | Фаза 2 | `AI: Adapted: {name} (adapted)` | Тренировка HumanGo неуместна |

Оригинальная тренировка HumanGo **не модифицируется и не удаляется**. Адаптация создаётся как отдельный event. На часах Garmin видны обе — атлет выбирает.

### Входные данные

| Метрика                | Источник                    | Роль                                                       |
| ---------------------- | --------------------------- | ---------------------------------------------------------- |
| Recovery score (0-100) | `data/metrics.py`           | Основной индикатор                                         |
| Recovery category      | excellent/good/moderate/low | Категоризация                                              |
| HRV status             | flatt_esco                  | Flatt & Esco baseline (issue #307 retired AIEndurance)     |
| RHR status             | green/yellow/red            | Дополнительный сигнал                                      |
| TSB                    | Intervals.icu               | Накопленная усталость                                      |
| Ra (Readiness)         | DFA a1 pipeline             | Свежесть                                                   |
| Planned workout        | `scheduled_workouts`        | Что запланировал HumanGo                                   |
| `workout_doc`          | Intervals.icu JSON          | Структурированный разбор тренировки (парсится server-side) |

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

LLM-driven через две стадии: детерминистический детектор → Claude-генератор. Никакого автоматического mechanical-clamping в проде — детектор только сигналит «адаптация нужна», саму адаптацию формирует Claude через `suggest_workout`.

**Стадия 1 — детектор (морнинг-cron)** в `tasks/actors/reports.py:130-158`:

```python
for w in scheduled_workouts:
    steps = parse_humango_description(w.description)
    if not steps:
        continue
    max_zone, _ = compute_constraints(recovery, hrv_status, tsb)
    if needs_adaptation(steps, max_zone, ftp, lthr):
        # инжектит warning в morning summary + inline-кнопку "Адаптировать"
        keyboard.append({"text": f"Адаптировать: {w.name}", "callback_data": f"adapt:{w.id}"})
```

Детектор не строит адаптированную тренировку — он лишь определяет, что **оригинал** требует адаптации, и предлагает атлету решение.

**Стадия 2 — Claude-генератор (по клику кнопки)** в `bot/main.py:handle_adapt_callback`:

```python
prompt = f"Тренировка (id={workout_id}) требует адаптации... " \
         f"оцени recovery через get_recovery, " \
         f"и предложи адаптированную версию через suggest_workout с dry_run=True."
result = await agent.chat(prompt, mcp_token=user.mcp_token, user_id=user.id)
# → preview + Confirm/Cancel кнопки → push с suffix="adapted"
```

Claude собирает контекст через MCP (`get_scheduled_workouts`, `get_recovery`, `get_hrv_analysis`, `get_training_load`), формирует `steps` с понижёнными зонами/длительностью и зовёт `suggest_workout(dry_run=True)`. Confirm-кнопка делает push без re-inference (см. `_PREVIEWABLE_TOOLS` паттерн).

**Помощники из `data/workout_adapter.py`** (`compute_constraints` / `needs_adaptation` / `parse_humango_description`) — используются только в детекторе. Функция `adapt_workout()` в том же файле существует, но в проде не вызывается (legacy-API, остался для возможного fallback'а; сейчас покрыт только тестами).

### External ID для адаптаций

Единый формат для обеих фаз: `tricoach:{date}:{sport}:{slot}` (см. `PlannedWorkoutDTO.external_id` в `data/intervals/dto.py:378`). Идентификатор не меняется при адаптации — отличает фазы только `suffix` в имени. Конфликт устранять не нужно: на один (date, sport, slot) живёт ровно один `AI:`-event, повторный push делает upsert через `client.update_event(intervals_id, …)`.

### Интеграция в утренний cron

```
scheduler → sync → analysis → Claude morning recommendation
    → for each scheduled workout:
        if needs_adaptation(steps, max_zone): → инжектит warning + "Адаптировать"-кнопку
    → Telegram report (warning + inline-кнопки)
        ↓ (по клику атлетом)
    → handle_adapt_callback → Claude (suggest_workout dry_run=True)
        ↓ (по клику Confirm)
    → push в Intervals.icu с suffix="adapted"
```

**Утренний cron не пушит ивенты сам.** Адаптация — двухступенчатый opt-in: cron сигналит «оригинал не подходит», атлет тапает кнопку → Claude формирует preview → confirm → push. Если атлет проигнорировал кнопку, оригинал HumanGo остаётся единственным event'ом на день.

В morning summary (см. `tasks/actors/reports.py:148-158`) при `needs_adaptation==True` дополнительно появляется:

```
⚠️ {Workout name} требует адаптации (recovery moderate, max Z2)

[Адаптировать: {Workout name}]
```

Если адаптация не нужна — секция отсутствует (никакого «✅ Тренировка без изменений» не рисуется, чтобы не шуметь в утреннем отчёте).

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
06:00  sync_wellness_job → создаёт запись: pre_* контекст, source, original, adapted
       (compliance, actual, post = NULL)

18:00  sync_activities_job → находит activity за дату+спорт
       → заполняет actual_*, определяет compliance

06:00+1  sync_wellness_job следующего дня
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

Персональные паттерны добавляются в контекст Claude **двумя путями**, ни один из них **не требует cron + персистенс**:

1. **Weekly report** — Claude вызывает `get_personal_patterns` MCP-tool сам. Тула уже прописана как шаг 2 в `SYSTEM_PROMPT_WEEKLY` (`bot/prompts.py:121`) и в whitelist'е `WEEKLY_TOOL_NAMES` (`tasks/tools.py:788`). Когда у юзера ≥30 complete-записей — Claude получает dict и использует его в секциях «Восстановление» / «Наблюдение». Изменения **не требуются**.
2. **Chat (`render_athlete_block`)** — прямой вызов compute-функции при сборке athlete-блока. Шаблон блока:

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

Блок живёт внутри dynamic cache-сегмента в `render_athlete_block` (рядом с `{zones_block}` / `{facts_block}`). Если `compute_personal_patterns` вернул `None` (entries_complete < 30) — слот рендерится пустой строкой.

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

```python
def compute_personal_patterns(user_id: int) -> dict | None:
    """Анализирует training_log за последние 90 дней.

    Returns dict с персональными порогами/паттернами или None если
    entries_complete < 30 (insufficient data — нечего инжектить в промпт).
    """
```

**Без cron, без персистенса.** Compute-функция чистая, агрегирует ≤365 строк `training_log` за миллисекунды — звать on-demand дешевле, чем поддерживать таблицу + еженедельный actor + invalidation logic. MCP-tool `get_personal_patterns` рефакторится в тонкий wrapper над `compute_personal_patterns`. Если профилирование когда-нибудь покажет горячий путь — добавить кэш (Redis или таблица `personal_patterns` с `computed_at`); до этого — преждевременная оптимизация.

---

## Фаза 4: Ramp-тесты

> Проактивные ramp-тесты для обновления HRVT1/HRVT2 порогов.

### Триггеры

Агент проверяет ежедневно в утреннем cron (`tasks/utils.py:RampTrainingSuggestion.is_test_needed`). Триггеры (все обязательны):

- Есть валидные данные wellness (CTL и ATL не `None`)
- TSB > **-10** (deep fatigue искажает DFA a1)
- Recovery score >= **70** (low recovery шумит HRV-сигнал, линейный фит коллапсирует)
- Нет ramp-теста в `AiWorkout.get_upcoming(days_ahead=14)` (не дублируем уже запланированный)
- Для проверяемого `sport`: `ThresholdFreshnessDTO.status == "no_data"` ИЛИ `days_since > 30` (не 21 — порог в `is_test_needed` шире)
- Cron проверяет оба спорта по умолчанию: `sports=["Run", "Ride"]`. Первый stale/no_data выигрывает.

Дополнительно (advisory, в коде не enforced): не предлагать ramp в день перед ключевой тренировкой (race / hard intervals). Атлет видит inline-кнопку и может игнорировать — детектор просто сигналит, push идёт только по клику.

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

**Pace-driven**, относительно threshold pace атлета (`AthleteSettings.threshold_pace`). Pace — управляющая переменная (athlete контролирует на тредмилле), HR/DFA — наблюдаемые. Отвязывает протокол от точности LTHR и убирает HR-drift confound.

Лестница: **10 work-шагов × 3 мин**, шаг 5% threshold pace, от **85% до 130%**.

| # | %pace | Pace @ threshold 4:55/km | Speed |
|---|---|---|---|
| WU | — | (HR 70% LTHR) | 10 min |
| 1 | 85% | ~5:47/km | ~10.4 км/ч |
| 2 | 90% | ~5:28/km | ~11.0 км/ч |
| 3 | 95% | ~5:11/km | ~11.6 км/ч |
| 4 | 100% | ~4:55/km (≈ LT2) | ~12.2 км/ч |
| 5 | 105% | ~4:41/km | ~12.8 км/ч |
| 6 | 110% | ~4:28/km | ~13.4 км/ч |
| 7 | 115% | ~4:17/km | ~14.0 км/ч |
| 8 | 120% | ~4:06/km | ~14.6 км/ч |
| 9 | 125% | ~3:56/km | ~15.3 км/ч |
| 10 | 130% | ~3:47/km (≈ 3K race) | ~15.9 км/ч |
| CD | — | (HR 70% LTHR) | 10 min |

Total: **50 min** (10 WU + 30 work + 10 CD). Step duration 3 min — стандарт для DFA a1 stabilization (Rogers 2020).

**Units:** `pace.units = "%pace"` в каждом work-шаге. Intervals.icu сам конвертирует в абсолютный pace по `AthleteSettings.threshold_pace`, Garmin рендерит абсолютный pace target на каждом шаге.

**Критично — `event.target = "PACE"` на верхнем уровне.** Без этого Intervals.icu defaults в `AUTO` → для Run = HR, и Garmin **молча выкидывает** pace cells из step view (verified live, pre-flight 2026-05-07). Автоматически выставляется в `PlannedWorkoutDTO.to_intervals_event()` через `has_pace_steps` детектор.

**Финальный шаг ≈ 130% threshold pace** заведомо выше LT2 — DFA a1 уходит ниже 0.5, ground truth для HRVT2 без шумной экстраполяции. Если атлет не вытянет последние шаги — fit берёт точки до bail-out.

**Treadmill или строго ровная трасса обязательны** — outdoor pace-targeting нерабочий из-за рельефа/ветра. Это явно прописано в `rationale` тренировки.

**WU/CD только по HR (70% LTHR)**, без pace target. На часах при `event.target=PACE` они показываются без таргета — атлет бежит «по ощущению», что и нужно для разминки/заминки.

**Fallback:** если `AthleteSettings.threshold_pace` не настроен → лестница строится в %pace без абсолютной привязки, но Intervals.icu/Garmin не сможет корректно конвертировать → в rationale добавляется warning «Threshold pace not set in Intervals.icu — calibrate by setting your Run threshold there first».

### Создание event

`data/ramp_tests.py:create_ramp_test`:

```python
def create_ramp_test(
    sport: str,
    target_date: date,
    days_since: int = 0,
    threshold_pace: float | None = None,    # Run only, sec/km
) -> PlannedWorkoutDTO:
    if sport == "Ride":
        steps = list(RAMP_STEPS_RIDE)        # фиксированный 8-шаговый протокол
    elif sport == "Run":
        steps = build_ramp_steps_run(threshold_pace)  # параметризованный 12-шаговый
    else:
        raise ValueError(f"Ramp test not supported for {sport}. Only Ride and Run.")

    total_min = sum(s.duration for s in steps) // 60
    rationale = (
        f"HRVT1/HRVT2 thresholds are {days_since} days old. "
        "Chest strap required (optical sensor not suitable for DFA). "
        "Hold steady effort for each step."
    )
    if sport == "Run":
        rationale += " Treadmill or perfectly flat course required ..."
        if not threshold_pace:
            rationale += " Threshold pace not set — used a default; calibrate ..."

    return PlannedWorkoutDTO(sport=sport, name=f"Ramp Test ({sport})",
                              steps=steps, duration_minutes=total_min,
                              rationale=rationale, target_date=target_date)
```

`build_ramp_steps_run(threshold_pace_sec_per_km)` собирает Run-протокол: WU @ 70% LTHR + 10 work-шагов с pace-таргетами в `s/km`, округлёнными к 0.5 km/h grid + CD @ 70% LTHR.

Rationale кладётся в `workout_doc.description` через `to_intervals_event()`. Plain-text `description` с комментариями типа `# Ramp test для определения HRVT1/HRVT2` больше не используется — instructions для атлета идут как rationale на английском (Intervals.icu UI).

### Flow создания ramp-теста

В отличие от обычных `suggest_workout`-тренировок, ramp test пушится **без двухфазного preview**:

```
detector (RampTrainingSuggestion.is_test_needed)
  → инжектит ⚡ warning + inline-кнопку "Создать Ramp Test (sport)" в morning summary
  ↓ (по клику)
ramp_test:{sport} callback (bot/main.py)
  → RampTrainingSuggestion.plan_ramp(sport, dt)
  → create_ramp_test(...)
  → actor_push_workout.send(...)
  → push в Intervals.icu сразу
```

Протокол детерминистический (фиксированный для Ride, параметризованный по `threshold_pace` для Run, без free-form input), нечего в превью смотреть. MCP-tool `create_ramp_test_tool` (`mcp_server/tools/ramp_tests.py`) даёт on-demand путь без cron'а — тоже без preview; для Run он сам тянет `AthleteSettings.threshold_pace` перед сборкой шагов.

### Обновление порогов

После ramp-теста DFA a1 pipeline (`data/hrv_activity.py`) автоматически:

1. Обрабатывает FIT → RR интервалы
2. Рассчитывает DFA a1 по окнам
3. Детектирует HRVT1 (a1=0.75) и HRVT2 (a1=0.50) — **только по WORK-сегментам** из `activity_details.intervals` (исключая WU/CD/recovery), чтобы шум от лёгких участков не портил линейный фит (R² падал с 0.7+ до 0.3 на реальных данных)
4. Сохраняет в `activity_hrv`
5. Если `ScheduledWorkout` с `"Ramp Test"` в имени совпадает по дате/спорту — сразу шлёт в Telegram ramp-test-специфичное уведомление (`tasks/formatter.py:build_ramp_test_message`) с HRVT1/HRVT2, R², confidence и — если дрифт >5% И есть ≥2 валидных HRVT1 в истории — inline-кнопкой `Обновить зоны` (callback `update_zones` → `actor_update_zones`). Если детекция не удалась, `diagnose_hrv_thresholds` возвращает структурированную причину (`too_few_points` / `a1_range_low` / `a1_range_high` / `positive_slope` / `noisy_fit` / `out_of_range` / `unknown`), и formatter показывает её атлету.

Если новые пороги отличаются от текущих (>5%), утренний отчёт следующего дня дополнительно включает threshold drift блок (см. ниже).

### Threshold drift detection

Сравниваем HRVT1/HRVT2 из последних 2-3 ramp-тестов с текущими config-значениями (`ATHLETE_LTHR_RUN`, `ATHLETE_FTP`). Если устойчивый сдвиг — уведомляем атлета.

Логика:

- Берём последние 2-3 валидных HRVT1 из `activity_hrv` (только ramp-тесты или progressive activities)
- Сравниваем среднее с config LTHR/FTP
- Если расхождение >5% и стабильно (2+ теста в одном направлении) → threshold drift alert

Config **не обновляется автоматически** — атлет решает сам. Агент только подсвечивает расхождение.

### Утреннее Telegram-сообщение (обновлённый формат)

Telegram-сообщение — компактный summary, детали в webapp. Формат:

```
Recovery 72 (good), HRV 🟢
🏃 Tempo Run 40min
TSB: -22 ⚠️ (productive overreach)

🔔 ПОРОГИ — РАССМОТРИ ОБНОВЛЕНИЕ
━━━━━━━━━━━━━━━━━━━━━
HRVT1 стабильно 158 bpm (3 теста)
Текущий LTHR: 153 bpm (+3.3%)
→ Обнови LTHR в настройках

[Кнопка: Открыть отчёт]
```

**Блоки:**

1. **Recovery + HRV** — всегда. Одна строка: score, category, HRV emoji (🟢/🟡/🔴)
2. **Тренировка на сегодня** — если есть. Название + "(adapted)" если адаптирована
3. **TSB** — если < -10 (⚠️ productive overreach) или < -25 (🔴 overtraining risk)
4. **Threshold drift** — только если обнаружен сдвиг. Яркий блок с разделителем
5. **Кнопка** "Открыть отчёт" — InlineKeyboardButton с web_app URL

AI-рекомендация **не дублируется** в Telegram — доступна только в webapp.

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

### Этап 1: Фаза 1 — Write API + AI генерация — Done

- [x] `create_event()` / `update_event()` / `delete_event()` в IntervalsClient (`data/intervals/client.py`)
- [x] `PlannedWorkoutDTO` + `WorkoutStepDTO` + `to_intervals_event()` (workout_doc JSON, `data/intervals/dto.py`)
- [x] Таблица `ai_workouts` + Alembic миграция + CRUD (`data/db/workout.py:AiWorkout`)
- [x] Промпт чата с workout-генерацией (`bot/prompts.py:_STATIC_PROMPT_CHAT` + `_zones_block`)
- [x] MCP tools: `suggest_workout`, `remove_ai_workout`, `list_ai_workouts` (`mcp_server/tools/ai_workouts.py`)
- [x] Two-phase dry_run preview (`bot/main.py:_PREVIEWABLE_TOOLS`, workout_push / workout_cancel callbacks)
- [x] Тест: event в Intervals.icu → синхронизация на часы
- [x] 26 unit-тестов (DTO, parsing, CRUD, suggest_workout)
- [~] ~~Интеграция в утренний cron + конфиг `AI_WORKOUT_ENABLED` / `AI_WORKOUT_AUTO_PUSH`~~ — **намеренно не реализованы**: генерация осталась on-demand (см. §«Когда вызывается»), флаги не нужны.

### Этап 2: Фаза 2 — Парсер + адаптация — Done

- [x] Парсер `parse_humango_description()` — HumanGo текст → `WorkoutStepDTO[]` (power/HR/pace targets, repeat groups)
- [x] `compute_constraints()` — recovery + HRV + TSB + Ra → max_zone + duration_factor
- [x] `needs_adaptation()` + `clamp_step()` — проверка/clamping зон (helpers, used by detector)
- [x] Утренний детектор (`tasks/actors/reports.py:130-158`): `parse_humango_description` + `needs_adaptation` → инжектит warning + inline-кнопку «Адаптировать» в morning summary
- [x] LLM-driven генератор по клику кнопки (`bot/main.py:handle_adapt_callback`): передаёт промпт Claude → `suggest_workout(dry_run=True)` → confirm → push с `suffix="adapted"`
- [x] 33 unit-теста на реальных HumanGo описаниях (Bike/Run/Swim)
- [~] `adapt_workout()` — детерминистический pipeline (parse → check → clamp → PlannedWorkoutDTO) реализован, но в проде **не вызывается**. Остался legacy-helper'ом / fallback'ом, покрыт только тестами.

### Этап 3: Фаза 3 — Training Log + обучение — Done (prompt enrichment ATP-finish 2026-05-07)

- [x] Таблица `training_log` (30 полей: pre/actual/post) + Alembic миграция + ORM
- [x] 6 CRUD функций (create, get_for_date, get_range, unfilled_actual, unfilled_post, update)
- [x] Запись pre-контекста в утреннем cron (`_record_training_log_pre`)
- [x] Заполнение actual-данных при sync activities (`_fill_training_log_actual`)
- [x] Заполнение post-данных на следующий день (`_fill_training_log_post` + `recovery_delta`)
- [x] Compliance detection (`_detect_compliance`: followed_original/adapted/ai/modified/skipped)
- [x] MCP tools: `get_training_log` (14-day history), `get_personal_patterns` (90-day analysis)
- [x] 10 unit-тестов (CRUD, unfilled queries, compliance detection 4 scenarios)
- [x] Weekly report: `get_personal_patterns` уже прописан в `SYSTEM_PROMPT_WEEKLY` + whitelist — Claude зовёт on-demand при ≥30 записях. Изменений не нужно.
- [x] `compute_personal_patterns(user_id, days_back=90) -> dict | None` — чистая функция в `data/personal_patterns.py`; `None` при <30 complete-записях. MCP-tool `get_personal_patterns` рефакторен в тонкий wrapper.
- [x] Prompt enrichment в chat: `_render_personal_patterns` + слот `{personal_patterns_block}` в `_ATHLETE_BLOCK_TEMPLATE` (`bot/prompts.py`). Прямой вызов compute-функции в `render_athlete_block`, без cron+персистенса.
- [x] Тесты: compute < 30 → None, ≥30 → dict; cross-tenant guard; рендер блока с/без записей; integration через `render_athlete_block` (11 тестов в `tests/data/test_personal_patterns.py` + `tests/bot/test_personal_patterns_block.py`).

### Этап 4: Фаза 4 — Ramp-тесты + threshold drift — Done

- [x] Ramp протоколы (Ride 8 steps + Run 8 steps) в workout_doc формате (`data/ramp_tests.py`). Run: WU/CD @ 70% LTHR + ступени 70→78→85→92→100→**108%** LTHR, Step 6 добавлен для пробивки HRVT2 (a1<0.5)
- [x] Проверка свежести порогов в утреннем cron (`_maybe_suggest_ramp` в scheduler)
- [x] MCP tools: `get_threshold_freshness`, `create_ramp_test_tool` (`mcp_server/tools/ramp_tests.py`)
- [x] Threshold drift detection: сравнение HRVT с config (LTHR/FTP), alert при >5% сдвиге (2+ теста)
- [x] Обновлённый формат утреннего Telegram-сообщения (compact summary + threshold drift блок)
- [x] AI-рекомендация не дублируется в Telegram (доступна только в webapp)
- [x] HRVT linear fit только по WORK-сегментам (`detect_hrv_thresholds(work_segments=...)`) — WU/CD/recovery исключаются из регрессии; на реальных данных R² поднимается с 0.33 до 0.72+
- [x] Ramp-test-специфичное пост-активити уведомление: новый код-путь в `_actor_send_activity_notification` с `_is_ramp_test_activity` детекцией, `build_ramp_test_message`, `diagnose_hrv_thresholds` (структурированные причины неудачи для i18n), inline-кнопка `Обновить зоны` только при ≥2 валидных HRVT1 (иначе `actor_update_zones` не сработает — требует ≥2 образцов)
- [x] 15 unit-тестов (protocols, creation, morning message format, drift alert)
