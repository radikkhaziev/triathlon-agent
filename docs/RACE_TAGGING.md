# Race Tagging & Analytics

> Спецификация: маркировка гонок в данных, таблица races, MCP-тулы, аналитика.
> Issues: [#108](https://github.com/radikkhaziev/triathlon-agent/issues/108), [#110](https://github.com/radikkhaziev/triathlon-agent/issues/110)

---

## Цель

Сейчас гонки в системе неотличимы от обычных тренировок. Intervals.icu хранит флаг `race: boolean` и `sub_type: RACE` на каждой активности, но мы его не синкаем. Цель — маркировать гонки, хранить расширенные данные (дистанция, финиш, цель, условия), и использовать их для аналитики и ML.

---

## 1. Источники данных

### 1.1 Intervals.icu Activity API

Поля, доступные в GET `/activity/{id}` (OpenAPI spec):

| Поле | Тип | Описание |
|------|-----|----------|
| `race` | `boolean` | Флаг гонки (ставится вручную или импортируется из Strava/Garmin) |
| `sub_type` | `enum` | `NONE` / `COMMUTE` / `WARMUP` / `COOLDOWN` / `RACE` |

Оба поля есть в full activity response. Поле `race` также доступно в списке activities через параметр `fields`.

### 1.2 ScheduledWorkout (Events)

Уже синкается поле `category`: `WORKOUT` / `RACE_A` / `RACE_B` / `RACE_C` / `NOTE`. Связка с `AthleteGoal` через `intervals_event_id`.

### 1.3 AthleteGoal

Уже хранит RACE_A/B/C с `event_name`, `event_date`, `sport_type`, `ctl_target`, `per_sport_targets`.

---

## 2. Схема БД

### 2.1 Изменения в `activities`

Добавить два поля:

```python
# data/db/activity.py — Activity model
is_race: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
sub_type: Mapped[str | None] = mapped_column(String, nullable=True)  # NONE|COMMUTE|WARMUP|COOLDOWN|RACE
```

### 2.2 Изменения в `ActivityDTO`

```python
# data/intervals/dto.py — ActivityDTO
is_race: bool = Field(False, alias="race")
sub_type: str | None = None
```

### 2.3 Новая таблица `races`

Расширенные данные гонки. Не все поля приходят из API — часть заполняется вручную (через MCP или бот).

```python
class Race(Base):
    """Extended race data — enriches Activity with race-specific context."""

    __tablename__ = "races"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    activity_id: Mapped[str] = mapped_column(String, ForeignKey("activities.id"), nullable=False, unique=True)

    # Идентификация
    name: Mapped[str] = mapped_column(String, nullable=False)          # "Novi Sad Marathon"
    race_type: Mapped[str] = mapped_column(String, default="C")        # A / B / C
    goal_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("athlete_goals.id"), nullable=True)

    # Дистанция и результат
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)      # метры (25000 для 25 км)
    finish_time_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)  # финишное время (секунды)
    goal_time_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)    # целевое время (секунды)
    placement: Mapped[int | None] = mapped_column(Integer, nullable=True)        # место в протоколе
    placement_total: Mapped[int | None] = mapped_column(Integer, nullable=True)  # всего участников
    placement_ag: Mapped[str | None] = mapped_column(String, nullable=True)      # место в возрастной группе "12/85"

    # Условия
    surface: Mapped[str | None] = mapped_column(String, nullable=True)    # road / trail / track / mixed
    weather: Mapped[str | None] = mapped_column(String, nullable=True)    # "rain, 8°C" — свободный текст
    elevation_gain_m: Mapped[float | None] = mapped_column(Float, nullable=True)  # набор высоты (м)

    # Контекст на момент гонки (snapshot из wellness)
    race_day_ctl: Mapped[float | None] = mapped_column(Float, nullable=True)
    race_day_atl: Mapped[float | None] = mapped_column(Float, nullable=True)
    race_day_tsb: Mapped[float | None] = mapped_column(Float, nullable=True)
    race_day_hrv_status: Mapped[str | None] = mapped_column(String, nullable=True)
    race_day_recovery_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    race_day_weight: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Рассчитываемые метрики
    avg_pace_sec_km: Mapped[float | None] = mapped_column(Float, nullable=True)  # сек/км
    normalized_pace_sec_km: Mapped[float | None] = mapped_column(Float, nullable=True)  # GAP для trail
    splits: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # [{km: 1, time_sec: 295, hr: 155}, ...]

    # Субъективная оценка
    rpe: Mapped[int | None] = mapped_column(Integer, nullable=True)     # 1-10
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)      # "колено болело с 18 км"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

**Индексы:**

```python
__table_args__ = (
    Index("ix_races_user_date", "user_id", "activity_id"),
)
```

### 2.4 Изменения в `training_log`

Добавить поле для связки:

```python
# data/db/workout.py — TrainingLog model
is_race: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
race_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("races.id"), nullable=True)
```

---

## 3. Синхронизация

### 3.1 Расширение fields в get_activities

```python
# data/intervals/client.py — _spec_get_activities
"fields": "id,start_date_local,type,icu_training_load,moving_time,average_heartrate,race,sub_type"
```

Поле `race` из API маппится в `is_race` (через alias в DTO). `sub_type` — напрямую.

### 3.2 Обновление Activity.save_bulk

`save_bulk` уже использует `ON CONFLICT DO UPDATE`. Добавить `is_race` и `sub_type` в set-клаузу.

### 3.3 Автоматическое создание Race при синке

В `tasks/actors/activities.py` — после `save_bulk`:

```python
for activity in new_activities:
    if activity.is_race:
        await _ensure_race_record(user, activity)
```

`_ensure_race_record`:
1. Проверяет, есть ли уже запись в `races` для этого `activity_id`
2. Если нет — создаёт с автозаполнением:
   - `name` — берём из matched `ScheduledWorkout` (если `category` = RACE_*) или из activity name в detail
   - `race_type` — из matched `ScheduledWorkout.category` (RACE_A→A, RACE_B→B, RACE_C→C) или "C" по умолчанию
   - `goal_id` — через `AthleteGoal.intervals_event_id` → `ScheduledWorkout.id`
   - `distance_m` — из `ActivityDetail.distance`
   - `finish_time_sec` — `Activity.moving_time`
   - Wellness snapshot: из `Wellness.get(user_id, activity.start_date_local)`
   - `avg_pace_sec_km` — рассчитать из distance и moving_time
3. Отправляет Telegram-уведомление: "🏁 Гонка синхронизирована: {name}. Заполни детали через /race"

### 3.4 Бэкфилл исторических гонок

CLI-команда для пересинка с новыми полями:

```bash
python -m cli backfill-races <user_id> [--period 2025-01-01:2025-12-31]
```

Логика: пройти все activities за период, для тех где `is_race=True` — создать Race записи. Для исторических: refetch activity detail для distance/pace.

---

## 4. MCP Tools

### 4.1 `get_races` (новый)

```python
@mcp.tool()
async def get_races(days_back: int = 365, sport: str = "") -> dict:
    """Get race history with pre-race fitness context and results.

    Returns races with: name, distance, finish time, goal time, placement,
    race-day CTL/ATL/TSB/HRV/recovery, avg pace, weather, surface, notes.
    Also includes activity metrics: duration, avg HR, TSS, max zone.
    """
```

**Response:**

```json
{
  "count": 3,
  "races": [
    {
      "date": "2025-10-12",
      "name": "Novi Sad Marathon 25K",
      "race_type": "B",
      "sport": "Run",
      "distance_km": 25.0,
      "finish_time": "2:08:00",
      "finish_time_sec": 7680,
      "goal_time": "2:05:00",
      "goal_time_sec": 7500,
      "avg_pace": "5:07/km",
      "avg_pace_sec_km": 307,
      "placement": null,
      "surface": "road",
      "weather": "cloudy, 14°C",
      "rpe": 8,
      "notes": null,
      "fitness_context": {
        "ctl": 52.3,
        "atl": 38.1,
        "tsb": 14.2,
        "hrv_status": "green",
        "recovery_score": 78,
        "weight": 79.5
      },
      "activity": {
        "id": "i987654",
        "duration_min": 128,
        "avg_hr": 162,
        "max_hr": 178,
        "tss": 185,
        "max_zone": "Z3",
        "efficiency_factor": 1.42,
        "decoupling_pct": 8.2
      },
      "splits": null,
      "linked_goal": "Ironman 70.3 Dubrovnik"
    }
  ]
}
```

### 4.2 `tag_race` (новый)

```python
@mcp.tool()
async def tag_race(
    activity_id: str,
    name: str,
    race_type: str = "C",
    distance_m: float | None = None,
    finish_time_sec: int | None = None,
    goal_time_sec: int | None = None,
    placement: int | None = None,
    placement_total: int | None = None,
    placement_ag: str | None = None,
    surface: str | None = None,
    weather: str | None = None,
    rpe: int | None = None,
    notes: str | None = None,
) -> dict:
    """Tag an activity as a race and add race-specific details.

    Use when athlete mentions completing a race. Auto-fills fitness context
    (CTL/ATL/TSB/HRV/recovery) from wellness data on race day.
    race_type: A (key race), B (important), C (tune-up/test).
    surface: road / trail / track / mixed.
    rpe: 1-10 perceived effort scale.
    """
```

Действия:
1. Проверить что activity существует и принадлежит юзеру
2. Установить `activity.is_race = True`, `activity.sub_type = "RACE"`
3. Создать/обновить запись в `races`
4. Автозаполнить wellness snapshot
5. Рассчитать avg_pace из distance и moving_time
6. Вернуть полную запись Race

### 4.3 `update_race` (новый)

```python
@mcp.tool()
async def update_race(
    activity_id: str,
    # все поля опциональные — обновляем только переданные
    **kwargs
) -> dict:
    """Update race details (placement, notes, RPE, weather, etc.)."""
```

### 4.4 Обновление `get_activities`

Добавить в response каждой activity:

```json
{
  "id": "i987654",
  "is_race": true,
  "sub_type": "RACE",
  ...остальные поля...
}
```

### 4.5 Обновление `get_training_log`

Добавить в response каждого entry:

```json
{
  "is_race": true,
  "race": {
    "name": "Novi Sad Marathon 25K",
    "race_type": "B",
    "distance_km": 25.0,
    "finish_time": "2:08:00",
    "placement": null
  }
}
```

---

## 5. Аналитические use cases

### 5.1 Race Day Fitness Snapshot

**Уже покрывается** через `races.race_day_*` поля. При каждом `get_races` Claude видит CTL/ATL/TSB/HRV/recovery на день гонки. Позволяет ответить на вопрос: "При какой форме я бежал лучше всего?"

### 5.2 Race Progress Tracking (A→A)

MCP tool `get_races` с фильтрацией по `race_type` и `sport`. Claude может сравнить:
- Темп на одинаковой дистанции при разном CTL
- Динамика финишного времени на одной и той же гонке (yearly comparison)
- Корреляция TSB → pace (оптимальный taper)

### 5.3 Race vs Training Performance

Сравнение race activities vs training activities:
- HR distribution: гонки обычно в Z3-Z4, тренировки в Z1-Z2
- Efficiency Factor на гонке vs лучший EF на тренировке
- Decoupling: гонки часто >10%, тренировки <5% при хорошей базе
- Max sustainable pace at given HR

Реализация: Claude использует `get_races` + `get_efficiency_trend` + `get_activity_details` для конкретных активностей.

### 5.4 Taper Analysis

Паттерн нагрузки перед гонкой:
- CTL/ATL/TSB за 14 дней до гонки (wellness range)
- Оптимальный TSB на день гонки (исторически)
- Recovery score trend перед лучшими гонками

Реализация: Claude использует `get_races` (для дат) + `get_wellness_range` (14 дней до каждой гонки).

### 5.5 Recovery Impact

Гонки сильнее нагружают, чем тренировки с тем же TSS:
- `training_log.recovery_delta` для race entries vs non-race entries
- HRV delta post-race (обычно сильнее просаживается)
- Дни до восстановления базового HRV/recovery после гонки

Реализация: через `get_training_log` с фильтром `is_race=True` + POST-контекст.

### 5.6 ML Ground Truth (#63, #64)

Races — идеальный ground truth для предсказательных моделей:
- **Input features**: race_day_ctl, race_day_atl, race_day_tsb, race_day_hrv_status, race_day_recovery_score, race_day_weight, training volume за 4/8/12 недель
- **Target**: finish_time_sec, avg_pace_sec_km
- **Segmentation**: по sport, distance, surface

Минимальный датасет: 5-10 гонок одного типа. Текущий athlete: ~5 гонок за 2025, к лету 2026 будет 10+.

---

## 6. Webapp

### 6.1 Activities page — race badge

В `Activities.tsx` — рядом с иконкой спорта добавить badge 🏁 если `is_race`:

```tsx
{activity.is_race && <span className="text-xs">🏁</span>}
```

### 6.2 Activity detail page — race section

В `/activity/:id` — если `is_race`, показать секцию:

```
🏁 Гонка: Novi Sad Marathon 25K (B-race)
📏 25.0 km | ⏱ 2:08:00 | 🎯 2:05:00
📊 CTL 52.3 | TSB +14.2 | Recovery 78
```

### 6.3 Dashboard — race timeline (future)

На вкладке Load в Dashboard — вертикальные маркеры на графике CTL/ATL для дат гонок. Пока не в скоупе первой итерации.

---

## 7. Промпты

### 7.1 Утренний отчёт

В `bot/prompts.py` — добавить в инструкции:

```
Если сегодня запланирована гонка (category = RACE_A/B/C в scheduled_workouts):
— Показать race-day checklist: CTL, TSB, recovery, HRV, вес
— Напомнить тейпер-стратегию
— Не рекомендовать дополнительных тренировок
```

### 7.2 Post-race анализ

В `tasks/formatter.py` — если активность is_race, другой формат уведомления:

```
🏁 Гонка завершена: Novi Sad Marathon 25K
⏱ 2:08:00 (цель: 2:05:00) | 📏 25.0 km | ⚡ 5:07/km
💓 Avg HR 162 | TSS 185 | RPE: ?
📊 CTL 52.3 → fitness context

Заполни детали гонки — я запомню для анализа.
```

---

## 8. Этапы реализации

### Этап 1: Data Layer (1-2 дня)

1. Миграция: `is_race` + `sub_type` на `activities`
2. Миграция: таблица `races`
3. Миграция: `is_race` + `race_id` на `training_log`
4. Обновить `ActivityDTO`: добавить `is_race`, `sub_type`
5. Обновить `Activity.save_bulk`: синкать новые поля
6. Обновить `_spec_get_activities`: добавить `race,sub_type` в fields
7. ORM: `Race` модель с CRUD методами (`save`, `get_by_activity`, `get_range`, `upsert`)
8. Тесты: модель Race, save_bulk с race fields

### Этап 2: MCP Tools (1 день)

1. `get_races` — список гонок с fitness context
2. `tag_race` — ручная маркировка + автозаполнение wellness
3. `update_race` — обновление деталей
4. Обновить `get_activities` — добавить `is_race`
5. Обновить `get_training_log` — добавить race info
6. Тесты: MCP tools

### Этап 3: Sync & Automation (1 день)

1. Автосоздание Race при синке (actor_fetch_user_activities)
2. Telegram notification при новой гонке
3. Обновить `actor_fill_training_log` — заполнять `is_race` + `race_id`
4. CLI backfill-races
5. Обновить промпты (утренний + post-activity)

### Этап 4: Webapp (0.5 дня)

1. Race badge в Activities page
2. Race section в Activity detail
3. `is_race` в API responses (`activities-week`, `activity/{id}/details`)

### Этап 5: Бэкфилл исторических данных

```bash
# 1. Ресинк activities с новыми полями (race, sub_type)
python -m cli sync-activities <user_id> 2025-01-01:2025-12-31 --force

# 2. Создать Race записи для гонок
python -m cli backfill-races <user_id> --period 2025-01-01:2025-12-31

# 3. Вручную дополнить через MCP:
# tag_race(activity_id="i987654", name="Novi Sad Marathon", race_type="B", distance_m=25000, finish_time_sec=7680, goal_time_sec=7500)
```

---

## 9. Вне скоупа (future)

- Race predictions ML model (#63, #64) — требует 10+ гонок
- Dashboard race timeline overlay (вертикальные маркеры на CTL графике)
- Автоимпорт результатов из внешних сервисов (Athlinks, RunSignUp)
- Сравнение с Garmin race predictions (`get_garmin_race_predictions` уже есть)
- Shared races (multi-tenant: командные гонки)

---

## 10. Обновление CLAUDE.md

После реализации добавить:

**В таблицу Database Schema:**
| `races` | (user_id, activity_id) | Race details: name, distance, finish/goal time, placement, conditions, fitness snapshot |

**В MCP tools list:**
`get_races`, `tag_race`, `update_race`

**В Activity model:**
Добавить `is_race` и `sub_type` в описание

**В Next Steps:**
~~Race Tagging~~ — Done
