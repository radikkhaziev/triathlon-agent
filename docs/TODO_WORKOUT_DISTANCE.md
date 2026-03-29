# TODO: Дистанционные интервалы в тренировках

> Поддержка distance-based шагов для Swim, Run, Bike.
> Дата: 2026-03-29

---

## Контекст

Сейчас все шаги тренировок (`WorkoutStep`) задаются **только по времени** (`duration` в секундах). Для плавания (4×100м), бега (5×1км) и длинных заездов (100км) дистанция — более естественный формат.

Intervals.icu поддерживает дистанционные шаги — подтверждено:
- [Distanced based workouts supported](https://forum.intervals.icu/t/distanced-based-workouts-supported/9973)
- [Workout Builder Syntax Quick Guide](https://forum.intervals.icu/t/workout-builder-syntax-quick-guide/123701)
- OpenAPI spec: `EventEx.distance_target` (float), plain text через `description`

---

## Что поддерживает Intervals.icu (из OpenAPI + форум)

### Два способа создания тренировки

**Способ A — plain text через `description`:**

```json
{
  "category": "WORKOUT",
  "type": "Swim",
  "name": "Swim Intervals",
  "start_date_local": "2026-03-29T06:00:00",
  "description": "200mtr 70% Pace\n4x100mtr 95% Pace 30s rest\n100mtr 60% Pace",
  "target": "PACE"
}
```

Intervals.icu **сам парсит** plain text в шаги. Самый надёжный для дистанций.
Формат: `[distance] [target] [rest]`. Дистанция: `500mtr`, `2km`, `1mi`. Буква `m` = минуты, НЕ метры!

**Способ B — `workout_doc` JSON с `distance` в шагах:**

```json
{
  "category": "WORKOUT",
  "type": "Swim",
  "name": "Swim Intervals",
  "workout_doc": {
    "steps": [
      {"text": "Разминка", "distance": 200, "pace": {"units": "%pace", "value": 70}},
      {
        "text": "4×100м кроль",
        "reps": 4,
        "steps": [
          {"text": "Работа", "distance": 100, "pace": {"units": "%pace", "value": 95}},
          {"text": "Отдых", "duration": 30}
        ]
      },
      {"text": "Заминка", "distance": 100, "pace": {"units": "%pace", "value": 60}}
    ]
  }
}
```

**Важно:** `workout_doc` в OpenAPI — generic `{type: object, additionalProperties: object}`. Внутренняя структура шагов **не типизирована** формально. Поле `distance` в шагах скорее всего поддерживается (парсер Intervals.icu), но нужна **верификация тестовым пушем**.

### Event-level поля (из OpenAPI `EventEx` schema)

| Поле | Тип | Описание |
|---|---|---|
| `distance` | float | Фактическая дистанция (метры) |
| `distance_target` | float | Целевая дистанция (метры) |
| `moving_time` | int | Время движения (секунды) |
| `time_target` | int | Целевое время (секунды) |
| `target` | enum | `AUTO` / `POWER` / `HR` / `PACE` — режим отображения целей |

### Таргеты по видам спорта

| Спорт | Основной таргет | Альтернативный | Дистанция |
|---|---|---|---|
| **Swim** | `pace` (%pace от CSS) | `hr` (%lthr) | Метры (100м, 200м, 400м) |
| **Run** | `pace` (%pace) ИЛИ `hr` (%lthr) | — | Метры/км (400м, 1км, 5км) |
| **Ride** | `power` (%ftp) | `hr` (%lthr) | км (необязательно) |
| **Other** | `hr` или без таргета | — | Не применимо |

### Plain text синтаксис (Intervals.icu native format)

```
# Swim — дистанционные интервалы
200mtr 70% Pace              # разминка 200м
4x100mtr 95% Pace 30s rest   # 4×100м с отдыхом 30с
100mtr 60% Pace              # заминка

# Run — дистанционные интервалы
2km 75% HR                   # разминка 2км
5x1km 90% Pace 90s rest      # 5×1км с отдыхом 90с
1km 65% HR                   # заминка

# Run — временные интервалы (тоже валидно)
10m 75% HR                   # 10 минут (m = minutes!)
5x3m 90% HR 90s rest         # 5×3 мин

# Ride — мощность (обычно по времени)
15m 60% FTP                  # разминка 15 мин
3x10m 85% FTP 5m rest        # 3×10 мин темпо
10m 55% FTP                  # заминка

# Ride — дистанция (менее типично)
40km 75% FTP                 # длинный заезд
```

**Ключевое:** `m` = минуты, `mtr` = метры, `km` = километры, `mi` = мили.

---

## Текущее состояние кода (после реализации Этапов 1-5)

### `WorkoutStep` (data/models.py)

```python
class WorkoutStep(BaseModel):
    text: str = ""
    duration: int = 0                     # секунды
    distance: float | None = None         # метры (mutually exclusive with duration)
    reps: int | None = None
    hr: dict | None = None
    power: dict | None = None
    pace: dict | None = None
    cadence: dict | None = None
    steps: list["WorkoutStep"] | None = None
    # model_validator: duration XOR distance (repeat groups exempt)
    # from_raw_list(): парсит distance из raw dicts
```

**Готово:** `distance`, валидатор, `from_raw_list()` с distance.

### `PlannedWorkout` (data/models.py)

- `has_distance_steps` — проверяет наличие distance в шагах
- `_steps_to_description()` — генерирует plain text (Способ A: `200mtr 70% Pace`)
- `to_intervals_event()` — автовыбор: distance → description + `target: "PACE"`, time-only → workout_doc

### Промпты (ai/prompts.py)

- Swim → `distance` + `pace` (%pace от CSS). Пример: 4×100м дриллы
- Run → `distance` + `pace`/`hr`. Пример: 5×1км интервалы
- Ride → `duration` + `power` (%ftp). Без изменений
- Документировано: когда distance, когда duration

### `suggest_workout` (mcp_server/tools/ai_workouts.py)

Docstring описывает `distance` и distance vs duration по видам спорта. Использует `WorkoutStep.from_raw_list()`.

### `compose_workout` (mcp_server/tools/workout_cards.py)

Использует `PlannedWorkout.to_intervals_event()` для автовыбора description/workout_doc. Поддерживает `distance_m` в exercise entries.

---

## План реализации

### Этап 0 — Верификация API (ручное тестирование)

- [ ] **Тест A:** Запушить Event с `description` (plain text) для Swim: `"200mtr 70% Pace\n4x100mtr 95% Pace 30s rest"`. Проверить, что Intervals.icu парсит шаги и отображает дистанцию.
- [ ] **Тест B:** Запушить Event с `workout_doc` и `"distance": 100` в шагах (вместо `duration`). Проверить, что принимает и отображает.
- [ ] **Тест C:** То же для Run: `"5x1km 90% Pace 90s rest"` через description.
- [ ] **Тест D:** Проверить, что `"target": "PACE"` на уровне Event переключает отображение на темп.
- [ ] Зафиксировать результаты: что работает, что нет, какие форматы Intervals.icu принимает.

### Этап 1 — Модель WorkoutStep ✅

- [x] Добавить `distance: float | None = None` в `WorkoutStep` (data/models.py)
- [x] Добавить `model_validator`: шаг должен иметь **либо** `duration` > 0, **либо** `distance` > 0, не оба (repeat groups exempt)
- [x] `model_dump(exclude_none=True)` уже корректно пропускает None — distance попадает в workout_doc

### Этап 2 — Альтернативный путь через description (Способ A) ✅

- [x] Добавить `_steps_to_description()` в `PlannedWorkout` — генерирует plain text (Intervals.icu native format)
- [x] `to_intervals_event()` автоматически выбирает путь: distance steps → description, time-only → workout_doc
- [x] Для Swim/Run с distance автоматически используется description
- [x] Добавить `target: "PACE"` для Swim и Run событий с distance

### Этап 3 — Промпты ✅

- [x] Обновить `ai/prompts.py`: добавить примеры с `distance` для Swim и Run
- [x] Добавить `pace` как валидный таргет для Run
- [x] Добавить примеры дистанционных интервалов в промпт для AI workout generation
- [x] Документировать разницу: Swim → distance+pace, Run → distance+pace ИЛИ duration+hr, Ride → duration+power

### Этап 4 — compose_workout ✅

- [x] Поддержка `distance_m` в exercise entries для compose_workout
- [ ] Добавить `distance_m` в exercise_card ORM (опционально, когда появятся swim-дриллы)
- [ ] Для плавательных дриллов: переводить reps в distance (reps × pool_length)

### Этап 5 — Документация ✅

- [x] Обновить docstring в `suggest_workout` — добавить `distance` в описание шагов
- [x] Обновить `docs/ADAPTIVE_TRAINING_PLAN.md` — WorkoutStep с distance
- [x] Обновить `CLAUDE.md` — distance support описание
- [ ] Обновить `docs/WORKOUT_CARDS.md` — дистанционные примеры для Swim (когда появятся swim cards)

---

## Примеры готовых тренировок (для верификации)

### Swim — техника + интервалы

```
Warm-up
200mtr 70% Pace

Drills
4x50mtr 60% Pace 20s rest

Main Set
4x100mtr 95% Pace 30s rest

Cool-down
100mtr 60% Pace
```

### Run — темповые интервалы

```
Warm-up
2km 70% HR

Main Set
5x1km 90% Pace 90s rest

Cool-down
1km 65% HR
```

### Run — фартлек (смешанный)

```
Warm-up
10m 70% HR

Main Set
5x3m 88% HR 2m rest

Cool-down
5m 65% HR
```

### Ride — темпо (по времени, как сейчас)

```
Warm-up
15m 55% FTP

Main Set
3x10m 85-90% FTP 5m rest

Cool-down
10m 50% FTP
```

---

## Риски и открытые вопросы

1. **workout_doc + distance** — не документировано формально. Может не работать, тогда fallback на description (Способ A).
2. **Garmin sync** — проверить, что дистанционные шаги корректно синхронизируются на часы через Intervals.icu → Garmin Connect.
3. **Pool length** — нужно ли хранить длину бассейна (25м/50м) в настройках? Пока нет — задаём дистанцию напрямую в метрах.
4. **Pace units** — `%pace` от CSS для Swim понятно. Для Run — от какого порога? Нужно проверить настройки Intervals.icu атлета.
5. **Mixed steps** — может ли одна тренировка содержать шаги с duration И distance? Например: разминка 10 мин → 5×1км → заминка 5 мин.
