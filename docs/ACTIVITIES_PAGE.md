# Activities — Dashboard Page

> Архитектурный документ для реализации страницы выполненных активностей. Аналог `plan.html` по структуре.

---

## Цель

Страница показывает выполненные тренировки по неделям (пн-вс) с навигацией prev/next. По каждой активности — базовая информация: вид спорта, длительность, TSS, avg HR. Кнопка ручной синхронизации. Детальная статистика per activity (laps, power, pace) — позже, в рамках Activity Details (#6).

---

## Что нужно сделать

### 1. Новое поле `last_synced_at` в `ActivityRow`

Аналогично `ScheduledWorkoutRow`. Добавить:
```
last_synced_at: DateTime(timezone=True), nullable=True
```

**Alembic миграция** — одна колонка, nullable.

### 2. Обновить `save_activities()`

Сейчас использует bulk `insert().on_conflict_do_update()`. Добавить `last_synced_at` в `set_`:
```python
"last_synced_at": datetime.now(timezone.utc)
```

Так же добавить в `values` list.

### 3. API endpoint: `GET /api/activities-week?week_offset=0`

По аналогии с `GET /api/scheduled-workouts`. Тот же паттерн.

**Параметр:**
- `week_offset` (int, default 0) — 0 = текущая неделя, 1 = следующая, -1 = предыдущая

**Логика:**
- Определить пн-вс по `TIMEZONE` + `week_offset` (скопировать из scheduled-workouts)
- Запросить `ActivityRow` в диапазоне, order by `start_date_local`, `id`
- Запросить `MAX(last_synced_at)`

**Ответ:**
```json
{
  "week_start": "2026-03-23",
  "week_end": "2026-03-29",
  "week_offset": 0,
  "today": "2026-03-25",
  "last_synced_at": "2026-03-25T08:30:00Z",
  "days": [
    {
      "date": "2026-03-23",
      "weekday": "Mon",
      "activities": [
        {
          "id": "i12345",
          "type": "Ride",
          "moving_time": 5400,
          "duration": "1h 30m",
          "icu_training_load": 85.2,
          "average_hr": 142
        }
      ]
    },
    {
      "date": "2026-03-24",
      "weekday": "Tue",
      "activities": []
    }
  ]
}
```

Массив `days` всегда 7 элементов (пн-вс). Пустые дни — `activities: []`.

Без авторизации на GET (single-user, как scheduled-workouts).

### 4. API endpoint: `POST /api/jobs/sync-activities`

Запускает `sync_activities_job()`.

**Авторизация:** Telegram initData (как sync-workouts).

**Ответ:** `200 OK` после завершения. Формат:
```json
{
  "status": "ok",
  "synced_count": 45,
  "last_synced_at": "2026-03-25T14:22:00Z"
}
```

При ошибке — `HTTPException(502)` с описанием.

### 5. Frontend — `webapp/activities.html`

Отдельная страница, по аналогии с `plan.html`. Точно тот же паттерн.

#### Layout

**Шапка:**
- Заголовок "Activities"
- Навигация: `← Prev` | `Mar 23 — Mar 29, 2026` | `Next →`
- Кнопка `🔄 Sync` + `Last sync: 2 hours ago`
- Ссылка назад на report/index

**Карточки по дням (7 штук, пн-вс):**
- Слева: день недели + дата (`Mon 23`)
- Справа: список активностей или "Rest day"
- Иконка спорта по `type`: 🏊 Swim, 🚴 Ride/VirtualRide, 🏃 Run, 🏋️ WeightTraining
- Строка: `🚴 Ride · 1h 30m · TSS 85 · ❤️ 142`
- Подсветка текущего дня (через `today` из API)

**Пока НЕ реализовывать:**
- Клик по активности для детальной статистики (Activity Details #6)
- DFA alpha 1 данные на карточке
- Сравнение план/факт

#### Стилистика

- Тёмная тема, Inter, mobile-first — **как `plan.html`**
- Telegram initData auth gate (как в plan.html — блокировать без initData)
- `sessionStorage` для initData persistence

### 6. Навигация

**`index.html`** — добавить третью кнопку `🏃 Activities` для авторизованных (рядом с Dashboard и Training Plan). Вставить в JS-блок по аналогии с `planBtn`.

**`plan.html`** и **`report.html`** — по желанию добавить ссылку на activities.html в навигацию.

---

## Что НЕ делать

- Не добавлять детальную статистику per activity (HR zones, power, pace, laps) — это Activity Details #6
- Не трогать `sync_activities_job()` логику — только `save_activities()` для `last_synced_at`
- Не менять MCP tools
- Не показывать DFA alpha 1 данные

---

## Порядок реализации

1. Alembic миграция — `last_synced_at` в `ActivityRow`
2. Обновить `save_activities()` — ставить `last_synced_at`
3. `GET /api/activities-week?week_offset=0` endpoint
4. `POST /api/jobs/sync-activities` endpoint
5. `webapp/activities.html` — фронтенд (копировать структуру из `plan.html`)
6. `index.html` — добавить кнопку Activities

---

## Референс

Реализация `plan.html` — полный аналог. См. `docs/SCHEDULED_WORKOUTS_PAGE.md` и готовый код:
- `api/routes.py` → `GET /api/scheduled-workouts`, `POST /api/jobs/sync-workouts`
- `data/database.py` → `get_scheduled_workouts_range()`, `save_scheduled_workouts()`
- `webapp/plan.html` — фронтенд
