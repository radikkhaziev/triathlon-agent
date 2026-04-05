# Activity Details — Phase 1: Fetch & Store

> Фаза 1: забрать расширенную статистику из Intervals.icu API и сохранить в БД.
> Web и MCP отображение — отдельная фаза.

---

## Цель

Для каждой активности забирать детальную статистику из Intervals.icu API (`GET /api/v1/activity/{id}`) и интервалы (`GET /api/v1/activity/{id}/intervals`), сохранять в таблицу `activity_details`.

---

## Источник данных — Intervals.icu API

### Summary (`GET /api/v1/activity/{id}`)

Всё уже посчитано Intervals.icu. Нужные поля:

**Power (bike):**
- `icu_average_watts` — avg power
- `icu_weighted_avg_watts` — normalized power (NP)
- `icu_intensity` — intensity factor (IF = NP/FTP)
- `icu_variability_index` — variability index (VI = NP/avg power)

**HR:**
- `max_heartrate` — max HR
- `average_heartrate` — уже есть в `ActivityRow`

**Speed/Pace:**
- `average_speed` — m/s
- `max_speed` — m/s
- `pace` — sec/km (бег)
- `gap` — grade adjusted pace (бег с рельефом)

**Efficiency:**
- `icu_efficiency_factor` — EF, уже посчитан (NP/avgHR для bike, speed/avgHR для run)
- `icu_power_hr` — power:HR ratio
- `decoupling` — aerobic decoupling (drift HR во 2-й половине vs 1-й). <5% = хорошая аэробная база

**Other:**
- `distance` — метры
- `total_elevation_gain` — метры набора
- `average_cadence` — rpm (bike) / spm (run)
- `average_stride` — длина шага (run)
- `calories` — kcal
- `trimp` — training impulse

**Зоны (время в секундах в каждой зоне):**
- `icu_hr_zones` — массив int, время в каждой HR зоне
- `icu_power_zones` — массив int, время в каждой power зоне (bike)
- `pace_zones` — массив float, время в каждой pace зоне (run/swim)

### Intervals (`GET /api/v1/activity/{id}/intervals`)

Per-interval breakdown:
- `distance`, `moving_time`, `elapsed_time`
- `average_watts`, `weighted_average_watts`, `max_watts`
- `average_heartrate`, `max_heartrate`
- `average_speed`, `gap`
- `average_cadence`
- `decoupling`
- `training_load`
- `zone`
- `total_elevation_gain`

### Что НЕ берём из FIT

FIT файл уже парсим для DFA alpha 1. На этом этапе дополнительные данные из FIT не нужны — Intervals.icu API покрывает всё. FIT может понадобиться позже для: SWOLF (плавание), per-second streams, кастомных расчётов.

---

## Новая таблица `activity_details`

| Column | Type | Notes |
|---|---|---|
| `activity_id` | String PK, FK → activities | |
| `max_hr` | Integer, nullable | max heart rate |
| `avg_power` | Integer, nullable | average power watts (bike) |
| `normalized_power` | Integer, nullable | NP watts (bike) |
| `max_speed` | Float, nullable | m/s |
| `avg_speed` | Float, nullable | m/s |
| `pace` | Float, nullable | sec/km (run) |
| `gap` | Float, nullable | grade adjusted pace sec/km (run) |
| `distance` | Float, nullable | meters |
| `elevation_gain` | Float, nullable | meters |
| `avg_cadence` | Float, nullable | rpm (bike) or spm (run) |
| `avg_stride` | Float, nullable | meters (run) |
| `calories` | Integer, nullable | kcal |
| `intensity_factor` | Float, nullable | IF = NP/FTP |
| `variability_index` | Float, nullable | VI = NP/avg power |
| `efficiency_factor` | Float, nullable | EF from Intervals.icu |
| `power_hr` | Float, nullable | power:HR ratio |
| `decoupling` | Float, nullable | aerobic decoupling % |
| `trimp` | Float, nullable | training impulse |
| `hr_zones` | JSON, nullable | array of seconds per HR zone |
| `power_zones` | JSON, nullable | array of seconds per power zone (bike) |
| `pace_zones` | JSON, nullable | array of seconds per pace zone (run/swim) |
| `intervals` | JSON, nullable | per-interval breakdown from Intervals.icu |

**Alembic миграция** — создать таблицу.

---

## IntervalsClient — новые методы

### `get_activity_detail(activity_id: str) -> dict`

```
GET /api/v1/activity/{activity_id}
```

Возвращает raw JSON. Нужные поля перечислены выше.

### `get_activity_intervals(activity_id: str) -> list[dict]`

```
GET /api/v1/activity/{activity_id}/intervals
```

Возвращает массив интервалов.

---

## Database — CRUD

### `ActivityDetailRow` — SQLAlchemy модель

По схеме таблицы выше.

### `save_activity_details(activity_id, detail_json, intervals_json)`

Upsert одной строки. Маппинг полей из Intervals.icu JSON → колонки:

```python
FIELD_MAP = {
    "max_heartrate": "max_hr",
    "icu_average_watts": "avg_power",
    "icu_weighted_avg_watts": "normalized_power",
    "max_speed": "max_speed",
    "average_speed": "avg_speed",
    "pace": "pace",
    "gap": "gap",
    "distance": "distance",
    "total_elevation_gain": "elevation_gain",
    "average_cadence": "avg_cadence",
    "average_stride": "avg_stride",
    "calories": "calories",
    "icu_intensity": "intensity_factor",
    "icu_variability_index": "variability_index",
    "icu_efficiency_factor": "efficiency_factor",
    "icu_power_hr": "power_hr",
    "decoupling": "decoupling",
    "trimp": "trimp",
    "icu_hr_zones": "hr_zones",
    "icu_power_zones": "power_zones",
    "pace_zones": "pace_zones",
}
```

`intervals` — сохранить raw JSON массив.

### `get_activity_details(activity_id) -> ActivityDetailRow | None`

Простой get by PK.

---

## Заполнение — sync pipeline

### Автоматическое (при sync_activities_job)

После `save_activities()` — для каждой **новой** активности (у которой нет записи в `activity_details`) запросить detail + intervals и сохранить.

Логика:
1. `save_activities()` возвращает list upserted activity IDs
2. Для каждого ID проверить: есть ли `ActivityDetailRow`?
3. Если нет — `get_activity_detail(id)` + `get_activity_intervals(id)` → `save_activity_details()`
4. Пауза 1 сек между запросами (rate limit Intervals.icu)

**Важно:** не запрашивать detail для ВСЕХ активностей при каждом sync — только для новых. Иначе при 90 днях истории — 90+ API calls каждые 15 минут.

### Backfill CLI

```bash
python -m bot.cli backfill-details              # all activities without details
python -m bot.cli backfill-details 30            # last 30 days only
```

Запрашивает detail для всех `ActivityRow` у которых нет `ActivityDetailRow`. Пауза 2 сек между запросами.

---

## Pydantic модели

### `ActivityDetail` в `data/models.py`

Модель для передачи данных между слоями. Поля соответствуют таблице.

---

## Что НЕ делать на этом этапе

- Не добавлять MCP tool (Phase 2)
- Не добавлять web endpoint / отображение в activities.html (Phase 2)
- Не парсить FIT файл для дополнительных данных
- Не считать EF/decoupling самостоятельно — брать из Intervals.icu
- Не показывать в Telegram

---

## Порядок реализации

1. Alembic миграция — создать таблицу `activity_details`
2. `ActivityDetailRow` модель в `data/database.py`
3. `IntervalsClient.get_activity_detail()` и `get_activity_intervals()` в `data/intervals_client.py`
4. `save_activity_details()` и `get_activity_details()` CRUD в `data/database.py`
5. Обновить `sync_activities_job()` — после sync запрашивать detail для новых
6. CLI `backfill-details` в `bot/cli.py`
7. Проверить: запустить backfill, убедиться что данные сохраняются

---

## Phase 2 (следующий этап)

- MCP tool: `get_activity_details(activity_id)` — объединяет `activity_details` + `activity_hrv`
- Web: раскрытие деталей на `activities.html` — клик по активности показывает stats, zones, intervals
- Telegram: опционально расширить пост-активити нотификацию
