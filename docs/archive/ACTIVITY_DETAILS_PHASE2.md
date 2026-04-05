# Activity Details — Phase 2: Web Display + MCP Tool

> Фаза 2: отображение детальной статистики активности в web и MCP.
> Зависит от Phase 1 (`activity_details` таблица, заполнена данными).

---

## Цель

Два уровня отображения деталей:
1. **Inline на `activities.html`** — клик по активности раскрывает сжатую сводку
2. **Отдельная страница `/activity.html?id=...`** — полный разбор с графиками зон и интервалами

Плюс MCP tool для доступа через Claude Desktop.

---

## Что нужно сделать

### 1. API endpoint: `GET /api/activity/{id}/details`

Объединяет данные из `activity_details` + `activity_hrv` (DFA alpha 1, если есть).

**Ответ:**
```json
{
  "activity_id": "i12345",
  "type": "Ride",
  "date": "2026-03-24",
  "moving_time": 5400,
  "duration": "1h 30m",
  "icu_training_load": 85.2,
  "average_hr": 142,
  "details": {
    "max_hr": 168,
    "avg_power": 185,
    "normalized_power": 198,
    "avg_speed": 8.33,
    "max_speed": 12.5,
    "pace": null,
    "gap": null,
    "distance": 45200,
    "elevation_gain": 320,
    "avg_cadence": 82,
    "avg_stride": null,
    "calories": 890,
    "intensity_factor": 0.85,
    "variability_index": 1.07,
    "efficiency_factor": 1.39,
    "power_hr": 1.30,
    "decoupling": 3.2,
    "trimp": 125,
    "hr_zones": [120, 1800, 2400, 900, 180],
    "power_zones": [300, 1200, 2100, 1500, 300],
    "pace_zones": null,
    "intervals": [
      {
        "distance": 5000,
        "moving_time": 600,
        "average_watts": 210,
        "average_heartrate": 155,
        "average_speed": 8.33,
        "average_cadence": 85,
        "decoupling": 2.1
      }
    ]
  },
  "hrv": {
    "dfa_a1_mean": 0.72,
    "dfa_a1_warmup": 0.85,
    "hrv_quality": "good",
    "ra_pct": 3.2,
    "da_pct": -5.1,
    "hrvt1_hr": 148,
    "hrvt1_power": 195,
    "hrvt2_hr": 168,
    "processing_status": "processed"
  }
}
```

`details` = `null` если `activity_details` не заполнен.
`hrv` = `null` если нет записи в `activity_hrv`.

**Без авторизации** на GET (single-user, как остальные GET endpoints).

### 2. Inline раскрытие на `activities.html`

При клике по активности — под ней появляется сжатая сводка (одна карточка, не отдельная страница).

**Что показывать:**

Для **Ride/VirtualRide**:
```
NP 198W · IF 0.85 · EF 1.39 · Decouple 3.2%
⬆️ 320m · 🔄 82rpm · 🔥 890kcal
```

Для **Run**:
```
Pace 5:12/km · GAP 5:05/km · EF 0.98 · Decouple 4.1%
⬆️ 150m · 👣 175spm · Stride 1.12m
```

Для **Swim**:
```
Pace 2:25/100m · 🔥 450kcal
```

Плюс **HR zones bar** — горизонтальный stacked bar с процентами:
```
Z1 ██ 3% | Z2 ████████ 33% | Z3 ██████████ 44% | Z4 ████ 17% | Z5 █ 3%
```

Цвета: Z1 серый, Z2 зелёный, Z3 жёлтый, Z4 оранжевый, Z5 красный.

Power zones bar (если есть) — аналогично, второй ряд.

**Ссылка "Details →"** — ведёт на полную страницу `/activity.html?id=i12345`.

**Логика:**
- Первый клик по активности → `fetch('/api/activity/{id}/details')` → рендер inline блока
- Повторный клик — скрыть (toggle)
- Кешировать ответ в памяти (не перезапрашивать)

### 3. Отдельная страница: `webapp/activity.html`

Полная страница детальной аналитики одной активности.

**URL:** `/activity.html?id=i12345`

#### Layout

**Шапка:**
- Иконка спорта + тип + дата (`🚴 Ride · Mar 24, 2026`)
- Длительность + TSS + avg HR
- Ссылка "← Back to Activities"

**Summary cards (сетка 2-3 колонки):**

| Card | Fields |
|---|---|
| Power (bike) | Avg Power, NP, IF, VI |
| Speed/Pace | Avg Speed, Max Speed, Pace, GAP |
| Heart Rate | Avg HR, Max HR |
| Efficiency | EF, Power:HR, Decoupling % |
| Cadence | Avg Cadence, Avg Stride |
| Other | Distance, Elevation, Calories, TRIMP |

Показывать только непустые карточки (Run не показывает Power, Swim не показывает Power и Pace zones).

**HR Zones chart:**
- Horizontal stacked bar (Chart.js)
- Время в каждой зоне (минуты + %)
- Цвета по зонам

**Power Zones chart** (если `power_zones` не null):
- Аналогичный bar chart

**Pace Zones chart** (если `pace_zones` не null):
- Для run/swim

**Intervals table** (если `intervals` не null):
- Таблица: #, Duration, Distance, Avg Power, Avg HR, Avg Speed, Cadence, Decoupling
- Каждая строка — один интервал

**DFA Alpha 1 блок** (если `hrv` не null):
- Ra: +3.2% (readiness)
- Da: -5.1% (durability)
- HRVT1: 148 bpm / 195W
- HRVT2: 168 bpm
- Quality: good
- DFA a1 mean: 0.72

#### Стилистика
- Тёмная тема, Inter — как `plan.html` / `activities.html`
- Chart.js для zone charts
- Mobile-first, cards wrap на мобильных
- Telegram initData auth gate

### 4. MCP tool: `get_activity_details(activity_id)`

Новый tool в `mcp_server/tools/activity_details.py`.

**Docstring:**
```
Get detailed statistics for a specific activity.

Returns summary metrics (power, HR, pace, efficiency), zone distributions
(HR/power/pace), interval breakdown, and DFA alpha 1 analysis if available.
Combines data from activity_details and activity_hrv tables.
```

**Response:** тот же формат что API endpoint.

### 5. Навигация

- `activities.html` — inline раскрытие + ссылка "Details →" на `activity.html?id=...`
- `activity.html` — ссылка "← Back to Activities" на `activities.html`

---

## Что НЕ делать

- Не парсить FIT файл для дополнительных данных
- Не добавлять map/GPS view
- Не менять Telegram бот или formatter
- Не делать сравнение план vs факт по интервалам (future)

---

## Порядок реализации

1. `GET /api/activity/{id}/details` endpoint в `api/routes.py`
2. MCP tool `get_activity_details` в `mcp_server/tools/activity_details.py`
3. `activities.html` — inline раскрытие при клике (fetch + toggle)
4. `webapp/activity.html` — полная страница с charts
5. Навигация — ссылки между страницами
