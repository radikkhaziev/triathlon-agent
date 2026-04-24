# Intervals.icu Webhooks — Исследование

> Цель: задокументировать реальную форму каждого webhook event type от Intervals.icu.
> Cookbook описывает типы, но не shape payload'ов. Мы исследуем на живом трафике.

**Status:** ✅ Завершено — все 10 из 10 event types исследованы, 8 из 10 dispatchers реализованы (2026-04-18)
**Monitoring:** parse/drift ошибки логируются в stdout; Sentry получает только `capture_exception` при падении диспатчера.
**Endpoint:** `POST /api/intervals/webhook` (`api/routers/intervals/webhook.py`)
**Dispatchers:** WELLNESS ✓, CALENDAR ✓, SPORT_SETTINGS ✓, FITNESS ✓, APP_SCOPE ✓, ACHIEVEMENTS ✓, ACTIVITY_UPLOADED ✓, ACTIVITY_UPDATED ✓. Skipped: ACTIVITY_ANALYZED, ACTIVITY_DELETED.

---

## Общая структура payload (подтверждено 2026-04-15)

```json
{
  "secret": "<shared secret из Webhook Secret field>",
  "events": [
    {
      "athlete_id": "i317960",
      "type": "WELLNESS_UPDATED",
      "timestamp": "2026-04-15T16:29:56.819+00:00",
      "records": [ ... ]
    }
  ]
}
```

- `secret` — shared secret, проверяем через `hmac.compare_digest` vs `INTERVALS_WEBHOOK_SECRET`
- `events` — массив (batch), один webhook может содержать несколько событий
- `athlete_id` — формат `i{number}`, маппим на `users.athlete_id`
- `timestamp` — ISO 8601 с timezone
- `records` — содержимое зависит от `type` (см. ниже), **может быть пустым**
- `activity` — **отдельное поле** (не внутри `records`), используется для ACTIVITY_* events

**Request headers (наблюдено):**
```
User-Agent: Java-http-client/17.0.14
Content-Type: application/json
Authorization: <value from webhook auth settings, not used for verification>
```

---

## Четыре паттерна доставки данных

| # | Паттерн | Поле данных | Кто использует |
|---|---|---|---|
| 1 | **Data via `records[]`** | `events[].records` — массив объектов | `WELLNESS_UPDATED`, `FITNESS_UPDATED` |
| 2 | **Data via `activity`** | `events[].activity` — один объект (не массив!) | `ACTIVITY_UPLOADED`, `ACTIVITY_ANALYZED`, `ACTIVITY_UPDATED`, `ACTIVITY_ACHIEVEMENTS`, `ACTIVITY_DELETED` (minimal stub) |
| 3 | **Data via `sportSettings[]`** | `events[].sportSettings` — массив **ВСЕХ** sport settings (не только изменённого) | `SPORT_SETTINGS_UPDATED` |
| 4 | **Notification-only** | все поля пусты / отсутствуют | `CALENDAR_UPDATED` |
| 5 | **Data via top-level event fields** | `client_id`, `client_name`, `scope`, `deauthorized` прямо в event object | `APP_SCOPE_CHANGED` |

**✅ Реализовано:** `IntervalsWebhookEvent` (`api/dto.py`) имеет typed fields для всех паттернов:
- `records: list[dict]` — WELLNESS_UPDATED, FITNESS_UPDATED
- `sport_settings: list[dict] = Field(alias="sportSettings")` — SPORT_SETTINGS_UPDATED
- `activity: dict | None` — ACTIVITY_* events
- `scope: str | None`, `deauthorized: bool | None` — APP_SCOPE_CHANGED
- `extra='allow'` — forward-compat для новых полей
Для **sportSettings delivery** — полные настройки всех видов спорта inline, парсить через `SportSettingsDTO`.
Для **notification-only** — dispatch existing sync actor → он сходит в REST API.

---

## Статус исследования по event types

### ✅ Исследовано

#### WELLNESS_UPDATED

- **Паттерн:** Data delivery
- **DTO:** `WellnessDTO` (`data/intervals/dto.py`) — парсится без ошибок
- **Первый sample:** 2026-04-15
- **`records` shape:**
```json
[{
  "id": "2026-04-15",
  "ctl": 18.411747,
  "atl": 37.385643,
  "rampRate": 4.692177,
  "ctlLoad": 14.0,
  "atlLoad": 14.0,
  "sportInfo": [{"type": "Ride", "eftp": 207.82, "wPrime": 17460.5, "pMax": 642.5}],
  "updated": "2026-04-15T16:29:54.818+00:00",
  "weight": 77.36,
  "restingHR": 59,
  "hrv": 46.0,
  "sleepSecs": 22242,
  "sleepScore": 76.0,
  "sleepQuality": 3,
  "bodyFat": 24.6,
  "steps": 11208,
  "tempWeight": false,
  "tempRestingHR": false
}]
```
- **Заметки:**
  - `id` = дата в ISO (`YYYY-MM-DD`)
  - `tempWeight` / `tempRestingHR` — boolean, отсутствуют в DTO → игнорируются (Pydantic `extra='ignore'`)
  - `vo2max` — появляется после outdoor run (Garmin расчёт). Поле уже есть в `WellnessDTO` как `vo2max: float | None` → парсится корректно.
  - Содержит полный wellness record, достаточный для прямой записи в БД
  - **⚠️ Очень шумный:** Garmin Connect синхронизирует steps, weight, bodyFat в реальном времени → каждое обновление генерирует отдельный webhook. Наблюдение: **6 events за 30 минут** после одной тренировки, из которых 5 отличаются только `steps` (+20-40 шагов) или `weight`/`bodyFat` (Garmin scale sync). CTL/ATL/rampRate/ctlLoad/atlLoad **идентичны** во всех — новой тренировочной нагрузки нет.
  - Реально значимые обновления (с изменением CTL/ATL): ~2-3 раза в день
  - Шумные обновления (только steps/weight): ~10-20 раз в день при активном Garmin
- **Dispatch plan:**
  - Trigger `actor_user_wellness(user, dt, force=True)` для consistency с downstream pipelines (HRV/RHR/recovery score)
  - **Debounce обязателен!** Без debounce получим 10-20 полных HRV/RHR/recovery пересчётов в день при том что данные не изменились. Варианты:
    - Самый простой: пропускать если `ctlLoad == 0 AND atlLoad == 0` (нет новой тренировочной нагрузки → steps/weight drift, можно игнорировать или ставить в low-priority очередь)
    - Или: сравнивать `updated` timestamp с последним обработанным — если delta < 10 min, skip
    - Или: Dramatiq dedup по `(user_id, date)` — если задача с таким ключом уже в очереди, не добавлять

#### CALENDAR_UPDATED

- **Паттерн:** Notification-only
- **DTO:** `None` (records пуст)
- **Первый sample:** 2026-04-16
- **`records` shape:** `[]` (пустой массив)
- **Sentry status:** `EMPTY` (expected)
- **Заметки:**
  - Срабатывает при любом изменении в календаре (добавление/удаление/перемещение тренировки)
  - Не содержит информации о том ЧТО конкретно изменилось — только факт изменения
  - Для получения актуальных данных нужен `GET /api/v1/athlete/{id}/events`
- **Dispatch plan:** Trigger `actor_fetch_user_workouts(user)` → он заберёт свежий календарь через API

#### FITNESS_UPDATED

- **Паттерн:** Data delivery via `records[]` (fitness projection — subset of wellness)
- **DTO:** `WellnessDTO` — подходит (все поля optional, `extra='ignore'`)
- **Первый sample:** 2026-04-16
- **`records` shape (один record):**
```json
{
  "id": "2026-04-29",
  "ctl": 13.192596,
  "atl": 5.0595965,
  "rampRate": -2.3926125,
  "ctlLoad": 0.0,
  "atlLoad": 0.0,
  "sportInfo": [{"type": "Ride", "eftp": 205.54587, "wPrime": 17269.436, "pMax": 635.4867}],
  "updated": "2026-04-16T04:43:50.268+00:00"
}
```
- **Заметки:**
  - **Только fitness-поля** — нет `weight`, `restingHR`, `hrv`, `sleepSecs`, `sleepScore`, `bodyFat`, `steps` (в отличие от WELLNESS_UPDATED)
  - **Массовый batch**: при пересчёте приходит **14 records** — от сегодня до даты гонки (2026-09-15). Это вся будущая кривая CTL/ATL/eFTP при текущей нагрузке + zero future load assumption.
  - `id` = дата **прогноза** в будущем (от `2026-04-16` до `2026-09-15`)
  - Последний record: CTL падает с 17.9 до 0.48 к дню гонки, eFTP с 207.8 до 178.9 — реалистичная деградация при полном stop
  - `sportInfo` содержит eFTP/wPrime/pMax per sport type (только Ride в данном случае)
  - `WellnessDTO` парсит корректно (confirmed — все поля optional)
  - Частота: после каждого analysis активности (пересчёт всей fitness кривой)
- **Dispatch plan:** **НЕ** записывать в `wellness` таблицу как обычные дневные данные — это **projection от Intervals.icu**, не факт. См. §FITNESS_PROJECTION ниже.

#### FITNESS_UPDATED — расшифровка projection

Intervals.icu после каждой активности пересчитывает **всю будущую кривую fitness** при допущении "zero future load" (юзер больше не тренируется). Результат — batch из 14 records от сегодня до даты гонки:

| # | `id` (дата) | `ctl` | `atl` | `rampRate` | `eFTP Ride` | Комментарий |
|---|---|---|---|---|---|---|
| 1 | 2026-04-16 | 17.98 | 32.81 | +3.26 | 207.82 | Сегодня (после зарядки, 3 TSS) |
| 2 | 2026-04-17 | 17.56 | 28.44 | +2.13 | 207.82 | |
| 3 | 2026-04-18 | 17.14 | 24.65 | +0.74 | 207.82 | |
| 4 | 2026-04-19 | 16.74 | 21.37 | -0.34 | 207.61 | rampRate уходит в минус |
| 5 | 2026-04-20 | 16.35 | 18.53 | -1.37 | 207.41 | |
| 6 | 2026-04-21 | 15.96 | 16.06 | -2.56 | 207.20 | ATL ≈ CTL crossover |
| ... | ... | ↘ | ↘ | ~ -2.5 | ↘ | Exponential decay |
| 13 | 2026-04-29 | 13.19 | 5.12 | -2.39 | 205.55 | 2 недели без нагрузки |
| **14** | **2026-09-15** | **0.48** | **0.00** | **-0.09** | **178.86** | **Дата гонки (Ironman 70.3)** |

**Что это:**
- Тот же график что Intervals.icu показывает в "Fitness" chart → секция "projected fitness"
- CTL деградирует экспоненциально: 17.98 → 0.48 за 5 месяцев (τ_CTL = 42 дня)
- ATL падает быстрее (τ_ATL = 7 дней): 32.81 → 0.00 за ~3 недели
- eFTP тоже деградирует по модели Intervals.icu: 207.82 → 178.86 (−14%)
- Пропущены промежуточные даты (нет 04-24, нет 05-01 → 09-14) — Intervals.icu шлёт только **key points** кривой, не каждый день
- Последний record = целевая дата гонки из athlete_goals — Intervals.icu привязывает проекцию к Race A

**Практическая ценность:**
- Можно визуализировать в Dashboard как "fitness projection" кривую
- Полезно для AI: "если не тренироваться, к гонке CTL = 0.48, eFTP упадёт на 14%"
- Можно показывать в morning report как "дней до гонки: N, projected CTL: X"

**Ограничения:**
- Это **worst case** (zero load assumption) — не прогноз с плановой нагрузкой
- Не писать в `wellness` таблицу — это projection, не реальные наблюдения
- Возможно: отдельная таблица `fitness_projection` или хранить в memory для Dashboard rendering
- Высокая частота (после каждой активности), batch size масштабируется с TSS:
  - 3 TSS (зарядка) → 14 records
  - 23 TSS (Z2 Run) → 38 records
  - 41 TSS (VirtualRide Tempo) → 60 records
  - Больше TSS = более детальная projection-кривая

#### ACTIVITY_UPLOADED

- **Паттерн:** Data delivery via **`activity`** (отдельное поле, не `records[]`!)
- **DTO:** `ActivityDTO` (полное совпадение формы, не проверено парсингом)
- **Первый sample:** 2026-04-16
- **`activity` shape (top-level ключи, 70+ полей):**
```json
{
  "id": "i140242265",
  "start_date_local": "2026-04-16T06:52:46",
  "type": "Workout",
  "name": "AI: Зарядка День Б",
  "moving_time": 887,
  "elapsed_time": 947,
  "has_heartrate": true,
  "average_heartrate": 94,
  "max_heartrate": 118,
  "calories": 74,
  "icu_training_load": 3,
  "device_name": "Garmin fenix 7",
  "source": "GARMIN_CONNECT",
  "external_id": "22542800955",
  "file_type": "fit",
  "icu_athlete_id": "i317960",
  "icu_hr_zones": [145, 153, 162, 171, 176, 181, 190],
  "icu_hr_zone_times": [893, 0, 0, 0, 0, 0, 0],
  "lthr": 172,
  "icu_resting_hr": 57,
  "icu_weight": 77.769,
  "trimp": 4.4902906,
  "compliance": 0.0,
  "hr_load": 3,
  "hr_load_type": "HRSS",
  "athlete_max_hr": 190,
  "icu_intensity": 33.80617,
  "interval_summary": ["4x 48s 84bpm", "3x 13s 86bpm", ...],
  "stream_types": ["time", "cadence", "heartrate", "temp"],
  "skyline_chart_bytes": "...(base64)...",
  "analyzed": "2026-04-16T05:10:24.107+00:00",
  "created": "2026-04-16T05:10:23.545+00:00",
  "icu_sync_date": "2026-04-16T05:10:24.107+00:00",
  "recording_stops": [18, 105, 202, 321, 462],
  "...": "ещё ~40 полей"
}
```
- **Samples:** 3 different activity types observed:
  - **Workout** (зарядка, 3 TSS) — Garmin fenix 7, HR only, minimal fields
  - **VirtualRide** (Tempo, 41 TSS) — Garmin Edge 840, with power (device_watts), power zones, achievements
  - **Run** (Z2 Base, 23 TSS) — Garmin fenix 7, GPS + elevation + weather + pace zones

- **Заметки:**
  - **⚠️ Данные в `activity` поле, не в `records[]`!** Наш `IntervalsWebhookEvent` model сейчас не имеет этого поля явно — оно сохраняется через `extra='allow'`, но не парсится и не типизируется. **TODO:** добавить `activity: dict[str, Any] | None = None` в `IntervalsWebhookEvent` (`api/dto.py`).
  - `records=[]` пуст для этого типа — вот почему лог показывал `records=0 parsed=0`
  - **Полные данные** — ID, имя, тип, HR, зоны, training load, device, source, interval summary. Достаточно для direct save без API callback.
  - `source: "GARMIN_CONNECT"` — важно для фильтрации Strava (мы уже фильтруем `source == STRAVA` в polling pipeline)
  - `analyzed` timestamp присутствует — для всех 3 samples analysis произошёл мгновенно (`analyzed == icu_sync_date`). `ACTIVITY_ANALYZED` event отдельно **не приходил**.
  - `compliance` — planned vs actual matching score (0.0 для зарядки без matched plan, 100.2 для VirtualRide, 95.6 для Run)
  - `paired_event_id` — ID запланированной тренировки, если match найден

- **Shape зависит от типа активности:**

| Поля | Workout | VirtualRide | Run (outdoor) |
|---|---|---|---|
| HR data | ✅ | ✅ | ✅ |
| Power data | ❌ | ✅ `device_watts`, `icu_ftp`, power zones | ✅ running power, но без power zones |
| Pace/GAP | ❌ | ❌ | ✅ `pace`, `gap`, `gap_model`, `threshold_pace`, `pace_zones` |
| Elevation | ❌ | ❌ | ✅ `total_elevation_gain/loss`, `altitude` |
| GPS | ❌ | ❌ | ✅ `latlng`, `route_id` |
| Weather | ❌ | ❌ | ✅ `average_weather_temp`, `feels_like`, `wind_speed/gust`, `headwind/tailwind_%`, `clouds`, `rain/snow` |
| Warmup/cooldown | ❌ | ❌ | ✅ `icu_warmup_time: 300`, `icu_cooldown_time: 300` |
| Achievements | ❌ | ✅ (also in separate ACTIVITY_ACHIEVEMENTS event) | ❌ (Z2 run, no PRs) |
| Efficiency | ❌ | ✅ `decoupling`, `efficiency_factor`, `power_hr`, `variability_index` | ❌ |
| stream_types | `time, cadence, heartrate, temp` | `time, watts, cadence, heartrate, distance, velocity_smooth, temp, hrv, respiration, torque` | `time, watts, cadence, heartrate, distance, altitude, latlng, velocity_smooth, hrv, respiration, torque, fixed_altitude` |
| `skyline_chart_bytes` | ✅ | ✅ | ✅ |

- **Dispatch plan:** `actor_fetch_user_activities(user, oldest=dt, newest=dt)` для consistency с существующим pipeline, ИЛИ direct save через `Activity.save_bulk` из webhook payload (рискованнее, но fast). Для DFA analysis всё равно нужен FIT файл → отдельный fetch.

#### ACTIVITY_ACHIEVEMENTS

- **Паттерн:** Data delivery via `activity` (как ACTIVITY_UPLOADED, **не** `records[]`)
- **DTO:** `ActivityDTO` (superset — больше полей чем у ACTIVITY_UPLOADED)
- **Первый sample:** 2026-04-16
- **`activity` дополнительные поля** (отличия от ACTIVITY_UPLOADED для той же активности):
```json
{
  "icu_rolling_w_prime": 17460.543,
  "icu_rolling_p_max": 642.51904,
  "icu_rolling_ftp": 208,
  "icu_rolling_ftp_delta": 0,
  "icu_atl": 38.26616,
  "icu_ctl": 18.94321,
  "carbs_used": 113,
  "icu_achievements": [
    {
      "id": "ps0_5",
      "type": "BEST_POWER",
      "watts": 500,
      "secs": 5,
      "point": {"start_index": 392, "end_index": 397, "secs": 5, "value": 500}
    }
  ]
}
```
- **Заметки:**
  - Приходит через **~60 секунд** после ACTIVITY_UPLOADED — Intervals.icu отдельно считает achievements
  - Enriched version: добавляет rolling FTP/wPrime/pMax, FTP delta (для отслеживания PR), ATL/CTL snapshot на момент активности, carbs_used
  - `icu_rolling_ftp_delta: 0` = FTP не изменился после этой тренировки (не PR)
  - `icu_achievements` — список PR'ов/milestones. В данном случае: 5-second best power 500W
  - Можно использовать для уведомлений в Telegram: "🏆 New 5s power PR: 500W!"
  - Если `icu_rolling_ftp_delta != 0` → FTP change detected → обновить `athlete_settings`
- **Dispatch plan:**
  - Log + Telegram notification при наличии achievements
  - Если `icu_rolling_ftp_delta != 0` → `actor_sync_athlete_settings(user)` для refresh пороговых значений

#### SPORT_SETTINGS_UPDATED

- **Паттерн:** Data delivery via **`sportSettings[]`** (4-й паттерн — ни `records`, ни `activity`)
- **DTO:** `SportSettingsDTO` (частичный match — модель покрывает subset полей)
- **Первый sample:** 2026-04-16 (тест: FTP Ride 208 → 210 → 208)
- **`sportSettings` shape:** массив **ВСЕХ** sport settings (Ride, Run, Swim, Other), даже если изменился только один:
```json
{
  "sportSettings": [
    {
      "id": 1340913,
      "athlete_id": "i317960",
      "types": ["Ride", "VirtualRide", "MountainBikeRide", "GravelRide", "TrackRide", "Cyclocross"],
      "ftp": 210,
      "lthr": 163,
      "max_hr": 179,
      "warmup_time": 1200,
      "cooldown_time": 600,
      "power_zones": [55, 75, 90, 105, 120, 150, 999],
      "power_zone_names": ["Active Recovery", "Endurance", "Tempo", "Threshold", "VO2 Max", "Anaerobic", "Neuromuscular"],
      "sweet_spot_min": 84,
      "sweet_spot_max": 97,
      "hr_zones": [131, 145, 151, 162, 167, 172, 179],
      "hr_zone_names": ["Recovery", "Aerobic", "Tempo", "SubThreshold", "SuperThreshold", "Aerobic Capacity", "Anaerobic"],
      "hr_load_type": "HRSS",
      "load_order": "POWER_HR_PACE",
      "mmp_model": {"type": "FFT_CURVES", "criticalPower": 180, "wPrime": 11760, "pMax": 612, "ftp": 183},
      "display": {"colorScheme": "SOLID", "color": "#0863b2", "...": "~20 display prefs"},
      "created": "2025-02-28T13:30:16.589+00:00",
      "updated": "2026-04-16T08:56:23.068+00:00"
    },
    {"id": 1340914, "types": ["Run", "VirtualRun", "TrailRun"], "lthr": 153, "threshold_pace": 3.3898, "pace_zones": [...], "...": "full Run settings"},
    {"id": 1340915, "types": ["Swim", "OpenWaterSwim"], "lthr": 172, "threshold_pace": 0.7092, "...": "full Swim settings"},
    {"id": 1340916, "types": ["Other"], "lthr": 172, "...": "fallback settings"}
  ]
}
```
- **Заметки:**
  - **⚠️ Данные в `sportSettings` поле, не в `records[]`!** Наш `IntervalsWebhookEvent` не имеет этого поля — сохраняется через `extra='allow'`. **TODO:** добавить `sport_settings: list[dict] | None = Field(None, alias="sportSettings")`.
  - **ВСЕ виды спорта** присылаются при изменении **любого одного** — нет diff'а, нет указания что именно поменялось. Нужно сравнивать с cached `athlete_settings` в БД.
  - `records=[]` пуст — данные полностью в `sportSettings`
  - Каждый sport settings объект содержит: пороги (FTP, LTHR, max_hr, threshold_pace), зоны (power, HR, pace) с именами, display preferences, MMP model, warmup/cooldown times, created/updated timestamps
  - `mmp_model` в Ride settings — критическая мощность (CP), W', pMax. Полезно для AI-анализа.
  - `types[]` array позволяет маппить настройки на наш `AthleteSettings.sport` field (Ride → "Ride", Run → "Run", Swim → "Swim")
  - `updated` timestamp показывает момент изменения — можно использовать для dedup
- **Dispatch plan:**
  - `actor_sync_athlete_settings(user)` для полного refresh, ИЛИ direct upsert из webhook payload
  - Direct upsert предпочтительнее: payload содержит ВСЕ данные, не нужен обратный API call
  - Стоит логировать что именно изменилось: сравнить `ftp`/`lthr`/`max_hr`/`threshold_pace` из webhook vs текущие `athlete_settings` в БД → Telegram notification "FTP updated: 208 → 210W"

---

### Timeline: цепочка events после upload одной активности

#### VirtualRide (Tempo, 41 TSS, Garmin Edge 840, with power)

```
T+0s    ACTIVITY_UPLOADED       — полные данные (70+ полей) через `activity`, power zones
T+2s    FITNESS_UPDATED         — 60 records projection (CTL/ATL/eFTP от сегодня до гонки)
T+2s    WELLNESS_UPDATED        — обновлённые CTL/ATL с учётом новой активности
T+60s   ACTIVITY_ACHIEVEMENTS   — та же activity + rolling FTP + CTL/ATL + 5s power PR 500W
```

#### Run (Z2 Base, 23 TSS, Garmin fenix 7, outdoor GPS)

```
T+0s    ACTIVITY_UPLOADED       — полные данные через `activity`, GPS/weather/pace/elevation
T+2s    FITNESS_UPDATED         — 38 records projection
T+2s    WELLNESS_UPDATED        — обновлённые CTL/ATL + vo2max (впервые появился после outdoor run)
        (нет ACTIVITY_ACHIEVEMENTS — Z2 run без PR)
```

#### Workout (зарядка, 3 TSS, Garmin fenix 7, HR only)

```
T+0s    ACTIVITY_UPLOADED       — минимальный набор полей через `activity`
T+2s    FITNESS_UPDATED         — 14 records projection
        (нет ACTIVITY_ACHIEVEMENTS — зарядка без PR)
```

**Общие наблюдения:**
- `ACTIVITY_ANALYZED` **ни разу не прилетел** за все 3 тренировки (analysis мгновенный). Вероятно этот тип появляется только при **re-analysis** (ручной перезапуск в Intervals.icu UI), или для очень тяжёлых FIT файлов.
- `ACTIVITY_ACHIEVEMENTS` приходит **только если есть PR/milestone** (VirtualRide had 5s power PR, Run и Workout нет)
- `WELLNESS_UPDATED` после активности обновляет CTL/ATL, но затем идут **множественные шумные обновления** steps/weight от Garmin (см. debounce рекомендацию в WELLNESS_UPDATED §)

---

### ⏳ Ожидает samples

---

## Полный список типов (Intervals.icu)

Из настроек OAuth app → Manage App → Webhook Types. Все типы указаны ниже с текущим статусом исследования.

| # | Webhook Type | Scope Required | Description | Enabled | Статус |
|---|---|---|---|---|---|
| 1 | `APP_SCOPE_CHANGED` | — | Athlete changed permissions for this app | ☑️ | ✅ Top-level fields (scope, deauthorized) |
| 2 | `CALENDAR_UPDATED` | CALENDAR | Calendar events updated and/or deleted | ☑️ | ✅ Notification-only |
| 3 | `CALENDAR_EVENT_UPDATED` | CALENDAR | Calendar event updated (deprecated) | ☐ | — не включён |
| 4 | `CALENDAR_EVENT_DELETED` | CALENDAR | Calendar event deleted (deprecated) | ☐ | — не включён |
| 5 | `ACTIVITY_UPLOADED` | ACTIVITY | New activity uploaded | ☑️ | ✅ Data via `activity` |
| 6 | `ACTIVITY_ANALYZED` | ACTIVITY | Existing activity re-analyzed | ☑️ | ✅ Data via `activity` (re-analysis only) |
| 7 | `ACTIVITY_UPDATED` | ACTIVITY | Activity updated (e.g. name changed) | ☑️ | ✅ Data via `activity` |
| 8 | `ACTIVITY_DELETED` | ACTIVITY | Activity deleted | ☑️ | ✅ Minimal `activity` (id + stubs) |
| 9 | `ACTIVITY_ACHIEVEMENTS` | ACTIVITY | Athlete achieved something (FTP up etc.) | ☑️ | ✅ Data via `activity` (enriched) |
| 10 | `WELLNESS_UPDATED` | WELLNESS | Weight, resting HR, HRV etc. updated | ☑️ | ✅ Data delivery |
| 11 | `FITNESS_UPDATED` | WELLNESS | Fitness, fatigue, eFTP etc. updated | ☑️ | ✅ Data delivery (subset) |
| 12 | `SPORT_SETTINGS_UPDATED` | SETTINGS | Sport settings (FTP, zones etc.) updated | ☑️ | ✅ Data via `sportSettings[]` |
| 13 | `CHAT_UPDATE` | CHATS | New chat message, updated chats etc. | ☐ | — не включён, не нужен |

### Детали по ожидающим типам

#### ~~ACTIVITY_ANALYZED~~ → перенесён в ✅ Исследовано (см. ниже)

#### ACTIVITY_ANALYZED

- **Паттерн:** Data via `activity` (идентичен ACTIVITY_UPLOADED/ACTIVITY_UPDATED)
- **DTO:** `ActivityDTO`
- **Первый sample:** 2026-04-16 (ручной re-analysis через Intervals.icu UI → Actions → Re-analyze)
- **`activity` shape:** идентичен ACTIVITY_UPDATED — полный объект 70+ полей
- **Заметки:**
  - **Не приходит при первом upload'е** если analysis мгновенный. За 3 тренировки ни разу не пришёл автоматически.
  - Trigger: **ручной re-analysis** через Intervals.icu UI (Actions → Re-analyze)
  - `icu_sync_date` и `analyzed` обновляются на время re-analysis
  - Новое поле vs остальные ACTIVITY_*: `icu_intervals_edited: false`
  - Shape идентичен ACTIVITY_UPDATED (включая `icu_atl`/`icu_ctl` snapshot)
- **Dispatch plan:** re-fetch activity details + пересчитать DFA/HRV pipeline (результаты analysis могут измениться)

#### ~~ACTIVITY_UPDATED~~ → перенесён в ✅ Исследовано (см. ниже)

#### ~~ACTIVITY_DELETED~~ → перенесён в ✅ Исследовано (см. ниже)

_Старая запись:_
~~- **Паттерн:** ❓ Вероятно notification с activity_id в records~~
- **Предполагаемый DTO:** `None`
- **Trigger:** удалить активность в Intervals.icu
- **Что проверить:** есть ли `records` с id удалённой активности
- **Предполагаемый dispatch:** delete Activity + ActivityDetail rows из БД

#### ~~ACTIVITY_ACHIEVEMENTS~~ → перенесён в ✅ Исследовано (см. выше)

#### ACTIVITY_UPDATED

- **Паттерн:** Data delivery via `activity` (тот же что ACTIVITY_UPLOADED)
- **DTO:** `ActivityDTO`
- **Первый sample:** 2026-04-16 (переименование Run в Intervals.icu UI)
- **`activity` shape:** **идентичен ACTIVITY_UPLOADED** (полные данные, 70+ полей, не diff!)
- **Отличия от ACTIVITY_UPLOADED:**
  - `name: "AI: Z2 Base Run — Renamed"` — обновлённое поле
  - Дополнительно: `icu_atl: 41.33`, `icu_ctl: 19.48` — ATL/CTL snapshot (как у ACTIVITY_ACHIEVEMENTS)
  - Все остальные поля — те же что в оригинальном ACTIVITY_UPLOADED
- **Заметки:**
  - Intervals.icu присылает **полный объект**, не patch/diff — не нужно мержить, можно перезаписать
  - `icu_weight: 77.23` (vs 77.769 при upload) — вес обновился от Garmin между upload и update
  - Trigger: любое изменение в Intervals.icu UI (переименование, смена типа, edit fields)
  - Прилетает через несколько секунд после сохранения
- **Dispatch plan:** Update `Activity` row в БД (в первую очередь `name`, `type`, `rpe`, `feel`). Либо re-fetch, либо direct upsert из payload.

#### ACTIVITY_DELETED

- **Паттерн:** Data via `activity` — **минимальный объект** (только ID + defaults/zeroes)
- **DTO:** нет (слишком мало полей, парсить не нужно — достаточно `activity.id`)
- **Первый sample:** 2026-04-16 (удаление зарядки "AI: Зарядка День Б" через Intervals.icu UI)
- **`activity` shape:**
```json
{
  "id": "i140242265",
  "commute": false,
  "race": false,
  "file_sport_index": 0,
  "icu_athlete_id": "i317960",
  "icu_sweet_spot_min": 0,
  "icu_sweet_spot_max": 0,
  "has_weather": false,
  "has_segments": false,
  "icu_lap_count": 0,
  "source": "GARMIN_CONNECT"
}
```
- **Заметки:**
  - **Минимальный payload** — данные активности уже удалены, остался только ID + stub fields (все boolean = false, counts = 0)
  - `source: "GARMIN_CONNECT"` сохраняется — можно определить источник удалённой активности
  - Нет `name`, `type`, `start_date`, `moving_time` и т.д. — всё уже gone
  - После delete сразу прилетает `FITNESS_UPDATED` (пересчёт projection без этой активности): ATL 41.33 → 40.93 (зарядка 3 TSS removed)
- **Dispatch plan:**
  - По `activity.id` найти `Activity` в БД → удалить `Activity` + связанные `ActivityDetail`, `ActivityHrv`, `TrainingLog` rows
  - Или soft-delete (пометить deleted, не удалять физически)
  - Telegram notification owner'у: "Activity deleted: {name} ({date})"

#### ~~SPORT_SETTINGS_UPDATED~~ → перенесён в ✅ Исследовано (см. ниже)

#### APP_SCOPE_CHANGED

- **Паттерн:** Data via **top-level event fields** (5-й паттерн — ни records, ни activity, ни sportSettings)
- **DTO:** нет (3 поля, парсить не нужно — `extra='allow'` в `IntervalsWebhookEvent` ловит)
- **Первый sample:** 2026-04-16 (re-auth с добавлением ACTIVITY:WRITE)
- **Shape:** поля прямо в event object:
```json
{
  "athlete_id": "i317960",
  "type": "APP_SCOPE_CHANGED",
  "timestamp": "2026-04-16T11:39:11.429+00:00",
  "client_id": 315,
  "client_name": "EndurAI",
  "scope": "ACTIVITY:WRITE,WELLNESS:READ,CALENDAR:WRITE,SETTINGS:WRITE",
  "deauthorized": false
}
```
- **Заметки:**
  - `scope` — **новый** scope после изменения (полная строка, не diff)
  - `deauthorized: false` — юзер расширил/изменил scope, НЕ отключил приложение
  - **`deauthorized: true`** → юзер полностью отозвал доступ → наш токен невалиден → `User.clear_oauth_tokens()`
  - `client_id: 315`, `client_name: "EndurAI"` — наши OAuth app реквизиты
  - `records=[]` пуст, `activity` отсутствует — всё в top-level
  - Trigger: re-authorize OAuth (новый consent screen), или disconnect в Connected Apps
- **Dispatch plan:**
  - Обновить `User.intervals_oauth_scope` новым значением из `scope`
  - Если `deauthorized == true` → `User.clear_oauth_tokens()` + Telegram "⚠️ Intervals.icu отключён. Переподключите в Settings."
  - Если scope **уменьшился** (сравнить с текущим) → Telegram warning "Permissions изменились, некоторые функции могут не работать"

---

## Как собирать новые samples

### Через docker logs

```bash
docker compose logs -f api | grep "Intervals webhook"
```

Если в handler'е есть `logger.info("... raw body=%s", raw_body)` — полный payload виден в stdout.

### Провоцирование событий

| Действие в Intervals.icu | Ожидаемые events |
|---|---|
| Upload FIT файл | `ACTIVITY_UPLOADED` → (через 1-5 мин) `ACTIVITY_ANALYZED` |
| Изменить название тренировки | `ACTIVITY_UPDATED` |
| Удалить активность | `ACTIVITY_DELETED` |
| Добавить planned workout в календарь | `CALENDAR_UPDATED` |
| Изменить FTP в Settings | `SPORT_SETTINGS_UPDATED` |
| Изменить scope приложения в Connected Apps | `APP_SCOPE_CHANGED` |
| Обновить вес / sleep / HRV | `WELLNESS_UPDATED` (автоматически через Garmin sync) |

---

## Dispatch план — реализация

Все dispatchers в `api/routers/intervals/webhook.py`. Dispatch table в `_handle_webhook_event`.

| Event type | Статус | Dispatcher | Action |
|---|---|---|---|
| `WELLNESS_UPDATED` | ✅ Impl | `_dispatch_wellness` | Parse `records[]` → `WellnessDTO`, pass to `actor_user_wellness(user, dt, wellness=dto)` per record. Sort by `updated` asc. |
| `CALENDAR_UPDATED` | ✅ Impl | `_dispatch_calendar` | `actor_user_scheduled_workouts(user)` + `actor_sync_athlete_goals(user)` |
| `SPORT_SETTINGS_UPDATED` | ✅ Impl | `_dispatch_sport_settings` | Parse `event.sport_settings` → `list[SportSettingsDTO]`, pass to `actor_sync_athlete_settings(user, sport_settings=list)` |
| `FITNESS_UPDATED` | ✅ Impl | `_dispatch_fitness` | `FitnessProjection.save_bulk(user_id, records)` — upsert CTL/ATL/rampRate projection |
| `APP_SCOPE_CHANGED` | ✅ Impl | `_dispatch_scope_changed` | If `deauthorized` → `clear_oauth_tokens()`. Else update `intervals_oauth_scope` in DB. |
| `ACTIVITY_ACHIEVEMENTS` | ✅ Impl | `_dispatch_achievements` | `actor_send_achievement_notification(user, activity)` → Telegram notification (FTP, PRs) |
| `ACTIVITY_UPLOADED` | ✅ Impl | `_dispatch_activity` | `Activity.save_bulk` from payload + `actor_update_activity_details` (details → FIT → DFA → notification) |
| `ACTIVITY_UPDATED` | ✅ Impl | `_dispatch_activity` | Same as UPLOADED — payload is identical (full object, not diff) |
| `ACTIVITY_ANALYZED` | ⏭ Skip | — | Rare event (manual re-analysis only). Not implemented. |
| `ACTIVITY_DELETED` | ⏭ Skip | — | Not implemented. |
| `CALENDAR_EVENT_UPDATED` | — | — | Deprecated, не включён |
| `CALENDAR_EVENT_DELETED` | — | — | Deprecated, не включён |
| `CHAT_UPDATE` | — | — | Не включён, не нужен |
4. Обновить dispatch план
5. Добавить полный JSON в Appendix A

---

## Appendix A — Raw JSON samples

> Полные payload'ы webhook'ов, наблюдённые в production. PII-sensitive
> поля (weight, hrv, restingHR, etc.) — реальные данные из dev-окружения.

### A.1 WELLNESS_UPDATED (2026-04-15)

```json
{
  "secret": "***",
  "events": [
    {
      "athlete_id": "i317960",
      "type": "WELLNESS_UPDATED",
      "timestamp": "2026-04-15T16:29:56.819+00:00",
      "records": [
        {
          "id": "2026-04-15",
          "ctl": 18.411747,
          "atl": 37.385643,
          "rampRate": 4.692177,
          "ctlLoad": 14.0,
          "atlLoad": 14.0,
          "sportInfo": [
            {"type": "Ride", "eftp": 207.82047, "wPrime": 17460.543, "pMax": 642.51904}
          ],
          "updated": "2026-04-15T16:29:54.818+00:00",
          "weight": 77.36,
          "restingHR": 59,
          "hrv": 46.0,
          "sleepSecs": 22242,
          "sleepScore": 76.0,
          "sleepQuality": 3,
          "bodyFat": 24.6,
          "steps": 11208,
          "tempWeight": false,
          "tempRestingHR": false
        }
      ]
    }
  ]
}
```

### A.2 CALENDAR_UPDATED (2026-04-16)

```json
{
  "secret": "***",
  "events": [
    {
      "athlete_id": "i317960",
      "type": "CALENDAR_UPDATED",
      "timestamp": "2026-04-16T...",
      "records": []
    }
  ]
}
```

### A.3 FITNESS_UPDATED (2026-04-16, после VirtualRide 41 TSS)

60 records — показаны первый, промежуточный и последний (race day).

```json
{
  "secret": "***",
  "events": [
    {
      "athlete_id": "i317960",
      "type": "FITNESS_UPDATED",
      "timestamp": "2026-04-16T06:37:42.279+00:00",
      "records": [
        {
          "id": "2026-04-16",
          "ctl": 18.94321,
          "atl": 38.26616,
          "rampRate": 4.228853,
          "ctlLoad": 41.0,
          "atlLoad": 44.0,
          "sportInfo": [{"type": "Ride", "eftp": 207.82047, "wPrime": 17460.543, "pMax": 642.51904}],
          "updated": "2026-04-16T06:37:40.273+00:00"
        },
        {
          "id": "2026-05-21",
          "ctl": 8.232687,
          "atl": 0.25783536,
          "rampRate": -1.493082,
          "ctlLoad": 0.0,
          "atlLoad": 0.0,
          "sportInfo": [{"type": "Ride", "eftp": 201.27235, "wPrime": 16910.385, "pMax": 622.2743}],
          "updated": "2026-04-16T06:37:40.273+00:00"
        },
        {
          "id": "2026-09-15",
          "ctl": 0.50783354,
          "atl": 1.42043195e-08,
          "rampRate": -0.09210092,
          "ctlLoad": 0.0,
          "atlLoad": 0.0,
          "sportInfo": [{"type": "Ride", "eftp": 179.0387, "wPrime": 15042.37, "pMax": 553.53467}],
          "updated": "2026-04-16T06:37:40.273+00:00"
        }
      ]
    }
  ]
}
```

### A.4 ACTIVITY_UPLOADED (2026-04-16, VirtualRide Tempo)

```json
{
  "secret": "***",
  "events": [
    {
      "athlete_id": "i317960",
      "type": "ACTIVITY_UPLOADED",
      "timestamp": "2026-04-16T06:37:40.151+00:00",
      "activity": {
        "id": "i140254351",
        "start_date_local": "2026-04-16T07:28:47",
        "type": "VirtualRide",
        "name": "CYCLING:Tempo w/ 1min Cadence-5Tempo w/ 1min Cadence-5",
        "start_date": "2026-04-16T05:28:47Z",
        "distance": 28451.42,
        "moving_time": 3307,
        "elapsed_time": 3307,
        "trainer": true,
        "commute": false,
        "race": false,
        "has_heartrate": true,
        "average_heartrate": 132,
        "max_heartrate": 145,
        "average_cadence": 93.21714,
        "calories": 517,
        "device_watts": true,
        "icu_average_watts": 131,
        "icu_weighted_avg_watts": 139,
        "icu_ftp": 208,
        "icu_training_load": 41,
        "icu_intensity": 66.82692,
        "icu_efficiency_factor": 1.0530303,
        "icu_power_hr": 0.99242425,
        "icu_variability_index": 1.0610687,
        "decoupling": 4.5139484,
        "trimp": 70.59505,
        "compliance": 100.21212,
        "paired_event_id": 103471545,
        "device_name": "Garmin Edge 840",
        "source": "GARMIN_CONNECT",
        "external_id": "22543294456",
        "file_type": "fit",
        "icu_athlete_id": "i317960",
        "created": "2026-04-16T06:37:39.861+00:00",
        "analyzed": "2026-04-16T06:37:40.111+00:00",
        "icu_hr_zones": [131, 145, 151, 162, 167, 172, 179],
        "lthr": 163,
        "icu_resting_hr": 57,
        "icu_weight": 77.769,
        "icu_power_zones": [55, 75, 90, 105, 120, 150, 999],
        "icu_zone_times": [
          {"id": "Z1", "secs": 1619},
          {"id": "Z2", "secs": 556},
          {"id": "Z3", "secs": 1027},
          {"id": "Z4", "secs": 92},
          {"id": "Z5", "secs": 4},
          {"id": "Z6", "secs": 1},
          {"id": "Z7", "secs": 8},
          {"id": "SS", "secs": 211}
        ],
        "icu_hr_zone_times": [1054, 2253, 0, 0, 0, 0, 0],
        "icu_achievements": [
          {
            "id": "ps0_5",
            "type": "BEST_POWER",
            "watts": 500,
            "secs": 5,
            "point": {"start_index": 392, "end_index": 397, "secs": 5, "value": 500}
          }
        ],
        "stream_types": ["time", "watts", "cadence", "heartrate", "distance", "velocity_smooth", "temp", "hrv", "respiration", "torque"],
        "power_load": 41,
        "hr_load": 41,
        "hr_load_type": "HRSS",
        "strain_score": 53.58951,
        "session_rpe": 165,
        "icu_rpe": 3,
        "feel": 3,
        "average_speed": 8.609,
        "max_speed": 11.309,
        "average_temp": 28.984276,
        "icu_lap_count": 57,
        "polarization_index": -0.12
      }
    }
  ]
}
```

### A.5 ACTIVITY_UPLOADED (2026-04-16, Workout/зарядка, 3 TSS)

```json
{
  "secret": "***",
  "events": [
    {
      "athlete_id": "i317960",
      "type": "ACTIVITY_UPLOADED",
      "timestamp": "2026-04-16T05:10:24.158+00:00",
      "activity": {
        "id": "i140242265",
        "start_date_local": "2026-04-16T06:52:46",
        "type": "Workout",
        "name": "AI: Зарядка День Б",
        "start_date": "2026-04-16T04:52:46Z",
        "moving_time": 887,
        "elapsed_time": 947,
        "has_heartrate": true,
        "average_heartrate": 94,
        "max_heartrate": 118,
        "average_cadence": 47.0,
        "calories": 74,
        "icu_training_load": 3,
        "trimp": 4.4902906,
        "compliance": 0.0,
        "device_name": "Garmin fenix 7",
        "source": "GARMIN_CONNECT",
        "external_id": "22542800955",
        "file_type": "fit",
        "icu_athlete_id": "i317960",
        "created": "2026-04-16T05:10:23.545+00:00",
        "analyzed": "2026-04-16T05:10:24.107+00:00",
        "icu_hr_zones": [145, 153, 162, 171, 176, 181, 190],
        "lthr": 172,
        "icu_resting_hr": 57,
        "icu_weight": 77.769,
        "icu_hr_zone_times": [893, 0, 0, 0, 0, 0, 0],
        "stream_types": ["time", "cadence", "heartrate", "temp"],
        "hr_load": 3,
        "hr_load_type": "HRSS",
        "icu_intensity": 33.80617,
        "interval_summary": [
          "4x 48s 84bpm", "3x 13s 86bpm", "3x 33s 83bpm", "1x 39s 84bpm",
          "4x 13s 111bpm", "7x 32s 105bpm", "2x 11s 100bpm", "1x 18s 73bpm",
          "1x 40s 70bpm", "1x 7s 112bpm", "1x 48s 103bpm", "1x 56s 99bpm",
          "1x 9s 114bpm", "2x 18s 101bpm", "1x 64s 87bpm", "1x 1s 80bpm"
        ],
        "athlete_max_hr": 190
      }
    }
  ]
}
```

### A.6 ACTIVITY_ACHIEVEMENTS (2026-04-16, VirtualRide enriched)

```json
{
  "secret": "***",
  "events": [
    {
      "athlete_id": "i317960",
      "type": "ACTIVITY_ACHIEVEMENTS",
      "timestamp": "2026-04-16T06:38:40.209+00:00",
      "activity": {
        "id": "i140254351",
        "start_date_local": "2026-04-16T07:28:47",
        "type": "VirtualRide",
        "name": "CYCLING:Tempo w/ 1min Cadence-5Tempo w/ 1min Cadence-5",
        "icu_training_load": 41,
        "icu_ftp": 208,
        "icu_weighted_avg_watts": 139,
        "icu_average_watts": 131,
        "icu_rolling_w_prime": 17460.543,
        "icu_rolling_p_max": 642.51904,
        "icu_rolling_ftp": 208,
        "icu_rolling_ftp_delta": 0,
        "icu_atl": 38.26616,
        "icu_ctl": 18.94321,
        "carbs_used": 113,
        "decoupling": 4.5139484,
        "compliance": 100.21212,
        "source": "GARMIN_CONNECT",
        "icu_achievements": [
          {
            "id": "ps0_5",
            "type": "BEST_POWER",
            "watts": 500,
            "secs": 5,
            "point": {"start_index": 392, "end_index": 397, "secs": 5, "value": 500}
          }
        ],
        "...": "остальные ~70 полей идентичны ACTIVITY_UPLOADED (см. A.4)"
      }
    }
  ]
}
```

### A.7 ACTIVITY_UPLOADED — Run (outdoor, GPS + weather, 2026-04-16)

```json
{
  "secret": "***",
  "events": [
    {
      "athlete_id": "i317960",
      "type": "ACTIVITY_UPLOADED",
      "timestamp": "2026-04-16T07:40:42.089+00:00",
      "activity": {
        "id": "i140263995",
        "start_date_local": "2026-04-16T09:02:09",
        "type": "Run",
        "name": "AI: Z2 Base Run — Aerobic Build (generated)",
        "start_date": "2026-04-16T07:02:09Z",
        "distance": 4497.53,
        "moving_time": 2179,
        "elapsed_time": 2286,
        "total_elevation_gain": 22.115707,
        "total_elevation_loss": 19.682693,
        "has_heartrate": true,
        "average_heartrate": 128,
        "max_heartrate": 150,
        "average_cadence": 72.86063,
        "calories": 331,
        "device_watts": true,
        "icu_training_load": 23,
        "trimp": 41.34806,
        "compliance": 95.570175,
        "paired_event_id": 104649834,
        "device_name": "Garmin fenix 7",
        "source": "GARMIN_CONNECT",
        "file_type": "fit",
        "pace": 2.0640337,
        "gap": 2.080476,
        "gap_model": "STRAVA_RUN",
        "threshold_pace": 3.3898,
        "pace_zones": [77.5, 87.7, 94.3, 100.0, 103.4, 111.5, 999.0],
        "pace_zone_times": [2176, 22, 0, 0, 0, 0, 0],
        "gap_zone_times": [2054, 144, 0, 0, 0, 0, 0],
        "icu_hr_zones": [129, 136, 144, 152, 157, 161, 179],
        "icu_hr_zone_times": [812, 407, 791, 172, 0, 0, 0],
        "lthr": 153,
        "icu_resting_hr": 57,
        "icu_weight": 77.769,
        "icu_warmup_time": 300,
        "icu_cooldown_time": 300,
        "icu_rpe": 2,
        "feel": 2,
        "icu_intensity": 61.390354,
        "route_id": 6896974,
        "average_altitude": 77.163826,
        "min_altitude": 74.11739,
        "max_altitude": 82.287445,
        "average_weather_temp": 17.871986,
        "min_weather_temp": 17.274,
        "max_weather_temp": 18.264627,
        "average_feels_like": 17.000792,
        "average_wind_speed": 1.2219555,
        "average_wind_gust": 3.6236732,
        "prevailing_wind_deg": 67,
        "headwind_percent": 22.60431,
        "tailwind_percent": 20.174232,
        "average_clouds": 0,
        "max_rain": 0.0,
        "max_snow": 0.0,
        "stream_types": ["time", "watts", "cadence", "heartrate", "distance", "altitude", "latlng", "velocity_smooth", "hrv", "respiration", "torque", "fixed_altitude"],
        "has_weather": true,
        "hr_load": 33,
        "pace_load": 23,
        "hr_load_type": "HRSS",
        "pace_load_type": "RUN",
        "interval_summary": ["3x 6m54s 134bpm", "1x 8m8s 140bpm"],
        "average_speed": 2.057,
        "max_speed": 2.93,
        "average_stride": 0.8498556,
        "athlete_max_hr": 179
      }
    }
  ]
}
```

### A.8 SPORT_SETTINGS_UPDATED (2026-04-16, FTP Ride 208→210)

Показан только Ride settings (из 4 — Ride/Run/Swim/Other):

```json
{
  "secret": "***",
  "events": [
    {
      "athlete_id": "i317960",
      "type": "SPORT_SETTINGS_UPDATED",
      "timestamp": "2026-04-16T08:56:23.098+00:00",
      "sportSettings": [
        {
          "id": 1340913,
          "athlete_id": "i317960",
          "types": ["Ride", "VirtualRide", "MountainBikeRide", "GravelRide", "TrackRide", "Cyclocross"],
          "ftp": 210,
          "lthr": 163,
          "max_hr": 179,
          "warmup_time": 1200,
          "cooldown_time": 600,
          "power_zones": [55, 75, 90, 105, 120, 150, 999],
          "power_zone_names": ["Active Recovery", "Endurance", "Tempo", "Threshold", "VO2 Max", "Anaerobic", "Neuromuscular"],
          "sweet_spot_min": 84,
          "sweet_spot_max": 97,
          "power_spike_threshold": 30,
          "hr_zones": [131, 145, 151, 162, 167, 172, 179],
          "hr_zone_names": ["Recovery", "Aerobic", "Tempo", "SubThreshold", "SuperThreshold", "Aerobic Capacity", "Anaerobic"],
          "hr_load_type": "HRSS",
          "load_order": "POWER_HR_PACE",
          "tiz_order": "POWER_HR_PACE",
          "mmp_model": {
            "type": "FFT_CURVES",
            "criticalPower": 180,
            "wPrime": 11760,
            "pMax": 612,
            "inputPointIndexes": [93, 101],
            "ftp": 183
          },
          "display": {
            "colorScheme": "SOLID",
            "color": "#0863b2",
            "showNormalizedWatts": true,
            "showRPE": true,
            "showFeel": true,
            "showSkylineChart": true,
            "...": "ещё ~20 display preferences"
          },
          "created": "2025-02-28T13:30:16.589+00:00",
          "updated": "2026-04-16T08:56:23.068+00:00",
          "other": false,
          "eFTPSupported": true
        }
      ]
    }
  ]
}
```
