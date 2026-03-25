# Scheduled Workouts — Dashboard Page

> Архитектурный документ для реализации страницы запланированных тренировок в веб-дашборде.

---

## Цель

Страница в дашборде показывает запланированные тренировки по неделям с возможностью навигации (текущая / следующая неделя). По каждой тренировке — полная информация от HumanGo (описание, интервалы, зоны, целевые значения). Кнопка ручной синхронизации плана с Intervals.icu.

---

## Что нужно сделать

### 1. Новое поле `last_synced_at` в `ScheduledWorkoutRow`

Добавить в модель `ScheduledWorkoutRow` поле:
```
last_synced_at: DateTime(timezone=True), nullable=True
```

Это поле обновляется на **каждой** записи при вызове `save_scheduled_workouts()` — ставим `datetime.now(UTC)` на все upserted строки. Таким образом `MAX(last_synced_at)` даёт время последней синхронизации.

**Alembic миграция** — одна колонка, nullable, без default.

### 2. API endpoints

#### `GET /api/scheduled-workouts?week_offset=0`

Возвращает тренировки на неделю (пн-вс).

**Параметр:**
- `week_offset` (int, default 0) — 0 = текущая неделя, 1 = следующая, -1 = предыдущая

**Логика:**
- Определить начало недели (понедельник) по `TIMEZONE` + `week_offset`
- Конец = начало + 6 дней (воскресенье)
- Запросить `ScheduledWorkoutRow` в этом диапазоне, order by `start_date_local`
- Также запросить `MAX(last_synced_at)` из таблицы

**Ответ:**
```json
{
  "week_start": "2026-03-23",
  "week_end": "2026-03-29",
  "week_offset": 0,
  "last_synced_at": "2026-03-25T08:30:00Z",
  "days": [
    {
      "date": "2026-03-23",
      "weekday": "Mon",
      "workouts": [
        {
          "id": 12345,
          "type": "Ride",
          "name": "CYCLING:Endurance w/ 2min tempo",
          "category": "WORKOUT",
          "duration": "1h 30m",
          "duration_secs": 5400,
          "distance_km": 45.0,
          "description": "Full HumanGo workout text with intervals, zones, power targets..."
        }
      ]
    },
    {
      "date": "2026-03-24",
      "weekday": "Tue",
      "workouts": []
    }
  ]
}
```

Массив `days` всегда содержит 7 элементов (пн-вс), даже если тренировок нет — пустой `workouts: []`.

#### `POST /api/jobs/sync-workouts`

Запускает `scheduled_workouts_job()` из `bot/scheduler.py`.

**Авторизация:** Telegram initData (как `/api/report`).

**Ответ:** `200 OK` после завершения (джоб быстрый, 1-2 сек). Возвращает:
```json
{
  "status": "ok",
  "synced_count": 12,
  "last_synced_at": "2026-03-25T14:22:00Z"
}
```

Не 202 Accepted — джоб достаточно быстрый, чтобы ждать синхронно.

### 3. Frontend — `webapp/plan.html`

Отдельная страница (не вкладка в dashboard.html). Ссылка с лендинга или из навигации дашборда.

#### Layout

**Шапка:**
- Заголовок "Training Plan"
- Навигация недели: `← Prev Week` | `Mar 23 — Mar 29, 2026` | `Next Week →`
- Кнопка `🔄 Sync Plan` + текст `Last sync: 2 hours ago`

**Основная часть — 7 карточек по дням:**

Каждый день — горизонтальная карточка:
- Слева: день недели + дата (`Mon 23`)
- Справа: список тренировок или "Rest day"
- Иконка спорта по `type`: 🏊 Swim, 🚴 Ride/VirtualRide, 🏃 Run, 🏋️ WeightTraining
- Короткая строка: `🚴 Endurance w/ 2min tempo · 1h 30m · 45km`
- Клик по тренировке → раскрывает `description` (полный текст от HumanGo с интервалами)

**Раскрытое описание тренировки** (collapsible):
- Полный текст `description` — preformatted (monospace), чтобы сохранить структуру интервалов от HumanGo
- Это ключевая информация: зоны, мощность, пульс, темп, длительность каждого интервала

#### Стилистика

- Тёмная тема (как лендинг и report.html)
- Inter шрифт
- Без фреймворков: HTML + vanilla JS + Tailwind CDN (или inline CSS как в report.html)
- Mobile-first

#### JS логика

- При загрузке: `fetch('/api/scheduled-workouts?week_offset=0')`
- Кнопки prev/next: меняют `week_offset`, перезапрашивают
- Кнопка Sync: `POST /api/jobs/sync-workouts`, после ответа — рефетч текущей недели, обновить `last_synced_at`
- `last_synced_at` показывать как relative time: "2 hours ago", "just now"

### 4. Обновить `save_scheduled_workouts()`

При upsert каждой строки ставить `row.last_synced_at = datetime.now(timezone.utc)`.

### 5. Навигация

Добавить ссылку на `/plan.html` из:
- Лендинга (`index.html`) — новая кнопка или пункт
- Report.html — если есть навигация

---

## Что НЕ делать

- Не трогать существующий `scheduled_workouts_job()` — только добавить `last_synced_at`
- Не менять MCP tools
- Не добавлять авторизацию на GET endpoint (страница публичная для single-user)
- POST `/api/jobs/sync-workouts` — с авторизацией initData (мутирующий endpoint)

---

## Порядок реализации

1. Alembic миграция — `last_synced_at` колонка
2. Обновить `save_scheduled_workouts()` — ставить `last_synced_at`
3. `GET /api/scheduled-workouts` endpoint
4. `POST /api/jobs/sync-workouts` endpoint
5. `webapp/plan.html` — фронтенд
6. Навигация — ссылки с лендинга и report
