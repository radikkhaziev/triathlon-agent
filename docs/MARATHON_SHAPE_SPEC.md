# Marathon Shape — Runalyze-style basic endurance metric

> Status: 🟢 **Shipped** — single-phase webapp widget. No MCP, no morning report, no schema changes.
>
> Issue: [#95](https://github.com/radikkhaziev/triathlon-agent/issues/95).

---

## 1. Problem

VO2max отвечает на «насколько быстро ты можешь бежать», но не на «хватает ли тебе объёма». Атлет с VO2max 50 теоретически бежит марафон за ~3:30, но если он бегает 30 км/нед — он сломается на 30-м километре, потому что running economy для дистанции 42.2 не наработана. Marathon Shape (Runalyze) — это процент-отношение текущего недельного объёма + длинных пробежек к VO2max-derived целям. Marathon = 100%, HM ≈ 42.5%, 10K ≈ 17%.

В проекте нет run-volume-aware метрики готовности: TSS показывает нагрузку, CTL — фитнес, но «у меня хватит ножек на марафон/HM?» — не отвечается.

## 2. Solution overview

Виджет `MarathonShapeWidget` на `webapp/src/pages/Progress.tsx` при `sport='run'`, разместить **под `PolarizationWidget`** (см. `Progress.tsx:75`). Виджет показывает:

1. **Distance picker** — `10K / HM / Marathon` (3 опции, default HM). 70.3 убран — run-leg = HM по формуле (`21.0975 ** 1.23`), identical %, нет смысла в дубле.
2. **Текущий MS %** + **required для выбранной дистанции** + дельта.
3. **График 12 недель** — кривая MS + horizontal annotation line на required-shape.
4. **Components-блок** — weekly_km / target и longjog_km / target за последнюю неделю окна.

Backend: один новый endpoint `GET /api/marathon-shape?weeks=12` отдаёт time-series. Distance-specific required вычисляется на клиенте (`distance_km ** 1.23`, формула чистая).

Никаких изменений схемы — читаем `activities` (Run only), `activity_details.distance`, `wellness.vo2max`.

## 3. Формулы

Источник: `inc/core/Calculation/BasicEndurance.php` в Runalyze. Pure module `data/marathon_shape.py`, no IO.

### Константы

```python
MINIMAL_EFFECTIVE_VO2MAX = 25.0
MIN_KM_FOR_LONGJOG = 13.0
DAYS_FOR_WEEK_KM = 182        # 26 нед — окно для недельного объёма
DAYS_FOR_WEEK_KM_MIN = 70     # минимум дней (clamp если атлет тренируется <70 дней)
DAYS_FOR_LONGJOGS = 70        # 10 нед — окно для длинных
PERCENTAGE_WEEK_KM = 0.67
PERCENTAGE_LONGJOGS = 0.33
```

### Целевые значения от VO2max

```python
target_weekly_km(vo2max) = max(vo2max, 25) ** 1.135
target_longjog_km(vo2max) = ln(max(vo2max, 25) / 4) * 12 - 13
```

Примеры (точные значения формул — issue body округлял):
- VO2max 45 → weekly 75.2 км, longjog 16.0 км
- VO2max 50 → weekly 84.8 км, longjog 17.3 км
- VO2max 55 → weekly 94.5 км, longjog 18.5 км

### Marathon Shape (%)

```python
# Weekly component
total_km_182d = sum(r.distance_km for r in runs if 0 <= ref_date - r.dt < 182)
actual_training_days = (ref_date - earliest_run_in_window).days + 1  # 1..182
days_for_week = clamp(actual_training_days, 70, 182)
actual_weekly = total_km_182d * 7 / days_for_week
weekly_ratio = actual_weekly / target_weekly_km(vo2max)

# Longjog component (time-decay)
longjog_score = 0
for r in runs:
    days_ago = ref_date - r.dt
    if 0 <= days_ago < 70 and r.distance_km >= 13:
        weight = 2 - (2/70) * days_ago    # 2 для today, 0 для 70-day-old
        longjog_score += weight * ((r.distance_km - 13) / target_longjog_km(vo2max)) ** 2
longjog_ratio = (longjog_score * 7) / 70

shape_pct = 100 * (0.67 * weekly_ratio + 0.33 * longjog_ratio)
```

**`actual_training_days` семантика.** В Runalyze PHP это календарные дни от создания аккаунта — у нас такого якоря нет, поэтому используем дни от **earliest run в 182d-окне** до `reference_date` (`marathon_shape.py:84-86`). Для непрерывно тренирующегося атлета это всегда 182. **Side-effect паузы:** если у атлета был перерыв 2+ месяца, после возобновления `actual_training_days` коллапсирует к небольшому числу → `clamp(70, 182)` срабатывает в нижнюю границу → `weekly_ratio` временно завышается («shape после возвращения отрастает быстро, потому что мы делим небольшой объём на 70 дней, а не на 182»). Это разумное поведение для базовой выносливости — после паузы атлет получает кредит за быстрое восстановление объёма, не штрафуется за полугодовую дыру.

### Required shape (per distance)

```python
required_shape_pct(distance_km) = distance_km ** 1.23
```

- 10K (10.0 км) → 17.0%
- HM (21.0975 км) → 42.5%
- Marathon (42.195 км) → 100%

## 4. Data model

**Никаких изменений схемы.** Источники:

| Поле | Таблица | Колонка | Notes |
|---|---|---|---|
| Run distance | `activity_details` | `distance` (метры) | JOIN `activities ON id = activity_id WHERE type='Run' AND is_race=False` |
| Activity date | `activities` | `start_date_local` | varchar `'YYYY-MM-DD'` |
| VO2max | `wellness` | `vo2max` | per-date snapshot, NULL допустим |

**Window:** для widget'а `weeks=12` нужно `12 + 26 = 38` недель run history (за самой ранней неделей окна — её 26-нед хвост). Single query на ~38 недель run-activity-rows (≤200 строк типично).

**VO2max per week:** snapshot на `week_end` (Sunday). Если NULL — fallback: median последних 30 дней; если всё ещё NULL — возвращаем `shape_pct=null` для этой недели.

## 5. API endpoint

```python
# api/routers/dashboard.py (или новый api/routers/marathon_shape.py)

@router.get("/api/marathon-shape")
async def marathon_shape(
    weeks: int = Query(default=12, ge=1, le=24),
    user: User = Depends(require_viewer),
) -> dict:
    """Weekly Marathon Shape time-series for the Progress widget.

    For each of the last `weeks` Mon-Sun weeks (ending most recent Sunday),
    computes MS using ~26 weeks of Run history before that week's end and the
    VO2max snapshot on that week's last day. Distance-specific required shape
    is computed CLIENT-side from `distance_km ** 1.23` — endpoint returns only
    the absolute MS %.
    """
```

**Response shape:**

```json
{
  "weeks": [
    {
      "week_start": "2026-02-23",
      "week_end": "2026-03-01",
      "shape_pct": 38.2,            // null если vo2max unavailable
      "vo2max_used": 50.2,          // null если vo2max unavailable
      "components": {               // null если vo2max unavailable
        "actual_weekly_km": 28.4,
        "target_weekly_km": 84.8,
        "longjog_score": 0.41,
        "target_longjog_km": 17.3,
        "actual_longjog_km": 18.2   // max distance в DAYS_FOR_LONGJOGS окне
      }
    }
    // ... newest first ...
  ],
  "current_components": {           // = newest week's components + vo2max; null если newest's null
    "actual_weekly_km": 28.4,
    "target_weekly_km": 84.8,
    "longjog_score": 0.41,
    "target_longjog_km": 17.3,
    "actual_longjog_km": 18.2,
    "vo2max": 50.2
  }
}
```

Newest first (как `weekly_recap`).

## 6. Webapp widget

### Placement

В `webapp/src/pages/Progress.tsx:75` после `<PolarizationWidget sport={sport} />` добавить:

```tsx
{sport === 'run' && <MarathonShapeWidget />}
```

### Layout

```
┌────────────────────────────────────────────────┐
│ Marathon Shape                                  │
│ ┌──────────────────────────────────────┐       │
│ │  10K  |  HM  |  Marathon            │       │
│ └──────────────────────────────────────┘       │
│                                                 │
│ Ready for HM:  90%                              │
│ MS 38.2 / target 42.5                           │
│                                                 │
│ ┌─ chart: 12 weeks ────────────────────┐       │
│ │ ────────── required (42.5%) ─────────│       │
│ │                          ▆▇▇         │       │
│ │           ▃▄▅▅▆▆▆                    │       │
│ └───────────────────────────────────────┘       │
│                                                 │
│ Weekly volume: 28.4 / 84.8 km   (33%)           │
│ Long run:      18.2 / 17.3 km   (105%)          │
│ VO2max:        50.2                             │
└────────────────────────────────────────────────┘
```

**Header badge logic:**

```
progress_pct = round(shape_pct / required_shape_for_distance(distance_km) * 100, 0)
```

- `progress_pct >= 100` → зелёный, label «Ready for {distance}»
- `80 <= progress_pct < 100` → жёлтый, label «Almost ready for {distance}»
- `progress_pct < 80` → красный, label «Building for {distance}»

Под progress'ом строка `MS {shape_pct} / target {required}` мелким шрифтом — для тех, кто хочет видеть raw значения.

Распределение цветов — то же что у CTL-delta в `Dashboard.tsx:506-512` (consistent palette).

**Chart Y axis:** остаётся **raw `shape_pct`** (objective metric, не меняется при переключении дистанции). Annotation line двигается — это и есть «required для выбранной дистанции». При переключении HM ↔ Marathon кривая не дрожит, меняется только threshold-line.

### Components

- **Distance picker** — `TabSwitcher` с тремя опциями (`10K` / `HM` / `Marathon`), default `HM`. State в виджете, не в роутинге.
- **Header badge** — current MS / required / Δpp с цветом (зелёный если ≥required, жёлтый 80-100%, красный <80%).
- **Chart** — Chart.js line (как `EFChart`/`DecouplingChart`). X = `week_end` (12 точек), Y = `shape_pct`. Annotation plugin (уже импортирован в Progress.tsx:24) для horizontal `required` линии. Null-points (vo2max missing) — gap в линии.
- **Components-блок** — простая таблица 3 строки из `current_components`.

### Empty/edge states

- **No run activities за 26 недель** → виджет показывает «Marathon Shape unavailable — no run history».
- **No VO2max** → header «VO2max unavailable» + chart всё равно строится по `shape_pct: null` (показывает «недостаточно данных» tooltip).
- **VO2max только за последние недели** — старые точки `shape_pct: null`, новые валидны. Chart обрезает gap.

## 7. Edge cases / fallbacks

| Случай | Поведение |
|---|---|
| `wellness.vo2max` NULL на week_end | Walk back до 30 дней, взять последнее значение. Если за 30d тоже нет — return `shape_pct: null`. |
| Бэкфилл атлета — wellness неполный | Те же fallback'и. Виджет robust к Swiss-cheese истории. |
| Run-activity без `activity_details.distance` (старый бэкфилл) | Skip — distance критична для расчёта. |
| Athlete с VO2max <25 (rare, начинающий) | Clamp к 25 per Runalyze формуле. |
| Run < 13 км | Не считается «длинным», только в `total_km_182d`. |
| Race-effort `is_race=True` | **Исключаем** из всех компонентов. Race — это пиковая нагрузка, не базовая выносливость; включение завышало бы shape перед таперингом и обнуляло бы после гонки. См. `Activity.is_race.is_(False)` в SQL фильтре + регрессионный `test_race_runs_excluded`. |
| Walks / Hike — НЕ в shape | Они приходят с `type='Walk'`/`'Hike'`, фильтр `Activity.type == "Run"` их не пускает. |
| TreadmillRun / TrailRun / VirtualRun | Уже нормализованы при ingestion (`data/utils.py:18-32` + `ActivityDTO` field validator в `data/intervals/dto.py:143`) → лежат в БД как `type='Run'`. Strict-фильтр их корректно подхватывает без отдельного маппинга в endpoint'е. Verified на user 1: 411 `Run` + 19 `Run RACE` за всю историю, ни одного TrailRun/VirtualRun raw value. |
| Атлет с реальным VO2max < 25 (de-trained / новичок) | Внутренний расчёт clamp'ит к 25 (`max(vo2max, 25)` в `target_weekly_km`/`target_longjog_km`). Response `vo2max_used` возвращает clamped значение (25, а не 20) — это «какое значение использовалось в расчёте». Для UI это может быть конфузом («у меня же Garmin показывает 20»), но в реальной аудитории (триатлеты) сценарий не встречается. Если когда-то понадобится — добавить `vo2max_raw` отдельным полем в `current_components`. |

## 8. Tests

```python
# tests/data/test_marathon_shape.py
def test_target_weekly_km_examples():
    # Точные значения формулы — issue body округлял (72/82/93)
    assert round(target_weekly_km(45), 2) == 75.23
    assert round(target_weekly_km(50), 2) == 84.79
    assert round(target_weekly_km(55), 2) == 94.47

def test_target_longjog_km_examples():
    assert round(target_longjog_km(45), 2) == 16.04
    assert round(target_longjog_km(50), 2) == 17.31
    assert round(target_longjog_km(55), 2) == 18.45

def test_required_shape_per_distance():
    assert round(required_shape_for_distance(21.0975), 1) == 42.5  # HM
    assert round(required_shape_for_distance(42.195), 1) == 99.8   # Marathon
    assert round(required_shape_for_distance(10.0), 1) == 17.0     # 10K

# + scenario tests:
# - test_steady_weekly_volume_no_longjogs (26 нед × 80 км/нед без longjog'ов → 60-66%)
# - test_recent_longjog_outweighs_old (today vs 35 дней назад → ratio ≈ 2×)
# - test_longjog_at_window_edge_excluded (ровно 70 дней назад → score 0)
# - test_sub_threshold_run_not_counted_as_longjog (12.9 км — в weekly, не longjog)
# - test_actual_longjog_km_is_max_in_window
# - test_short_history_clamps_to_70_day_denominator
# - test_vo2max_below_minimum_uses_clamp (vo2max=20 → используется 25)
# - test_runs_outside_182d_window_ignored
# - test_ready_for_marathon_scenario (80 км/нед + еженедельный 20км → 65-80%)
```

API integration (`tests/api/test_dashboard.py::TestMarathonShape`):
- `test_returns_12_weeks_newest_first` — длина + newest-first ordering
- `test_no_vo2max_returns_null_shape` — все weeks `shape_pct: null` без wellness vo2max
- `test_vo2max_30d_backfill` — 25-day-old vo2max подхватывается через back-walk
- `test_run_distance_meters_to_km_conversion` — `distance` в метрах → `actual_longjog_km` в км
- `test_race_runs_excluded` — `is_race=True` НЕ входит в shape
- `test_per_user_scoping` — tenant isolation на activities + wellness
- `test_current_components_from_newest_week` — newest week + vo2max в `current_components`

## 9. Out of scope

- **MCP tool `get_marathon_shape`** — viewer-only widget, AI не нужен.
- **Интеграция в утренний отчёт / prompt enrichment** — отдельная история, после валидации виджета.
- **Distance options 5K / Ultra / 70.3 / IM** — picker сужен до 10K/HM/Marathon. 70.3 run-leg математически = HM (формула identical), IM-run = Marathon — без смысла дублировать.
- **VO2max calculation from race results** — issue упоминал как fallback, но `wellness.vo2max` достаточно покрывает наших атлетов.
- **MS per-discipline для triathlon (bike/swim shape)** — Runalyze считает только run-shape.
- **Historical MS chart >12 нед** — `weeks` param ограничен 24, виджет всегда 12. Расширение — Phase 2 если попросят.

## 10. Phases

| Phase | Scope | Status |
|---|---|---|
| **1** | Формулы (`data/marathon_shape.py`) + API endpoint + `MarathonShapeWidget` + unit-тесты | ✅ shipped |

Single-phase спека. Phase 2 — только если появится явный запрос (MCP-tool, morning report integration, история >12 нед, ultra/5K расширение picker'а).

## 11. Acceptance criteria

- [x] `calculate_marathon_shape()` возвращает значения, совпадающие с формулами Runalyze (golden values для VO2max 45/50/55).
- [x] `GET /api/marathon-shape?weeks=12` возвращает 12 weekly buckets, newest first, с per-week `shape_pct` и `current_components`.
- [x] `MarathonShapeWidget` рендерится на `/progress` при `sport='run'` под `PolarizationWidget`.
- [x] Header badge показывает `progress_pct = shape / required * 100` с цветом по диапазону (≥100 зелёный, 80-100 жёлтый, <80 красный) и labels «Ready / Almost ready / Building for {distance}».
- [x] Distance picker (`10K`/`HM`/`Marathon`) переключает annotation line И пересчитывает progress_pct в header без re-fetch'а.
- [x] Chart показывает 12 точек с gap'ами для weeks без VO2max (chart скрывается полностью если все NULL).
- [x] При полном отсутствии run history виджет рендерит badge `Building for {distance}` без crash'а (shape=0, progress=0).
- [x] Tenant isolation: `user_id` фильтр на activities + wellness, регрессионный тест.

## 12. Phasing & GitHub issues

- [x] **MS-1 — `data/marathon_shape.py` (pure formulas) + unit-тесты.** 87 строк модуль + 22 теста (`tests/data/test_marathon_shape.py`).
- [x] **MS-2 — `GET /api/marathon-shape` endpoint.** Single query на 38 недель run history + wellness vo2max + per-week loop в `api/routers/dashboard.py`. 7 интеграционных тестов включая tenant-isolation.
- [x] **MS-3 — `MarathonShapeWidget` на Progress.tsx.** Distance picker + chart с annotation line + components-блок. Без i18n — Progress.tsx использует English-литералы inline, виджет в той же стилистике.
- [x] **MS-4 — Empty/edge states.** «Marathon Shape unavailable» badge при no-data, «VO2max unavailable» при missing vo2max, `spanGaps: true` в chart, скрытие chart'а если все weeks NULL.

## 13. Related

- [BasicEndurance.php — Runalyze source](https://github.com/Runalyze/Runalyze/blob/master/inc/core/Calculation/BasicEndurance.php)
- [Marathon Shape help](https://runalyze.com/help/article/marathon-shape)
- `webapp/src/pages/Progress.tsx` — placement target (line 75, после `PolarizationWidget`)
- `api/routers/dashboard.py:140` — `weekly_recap` как референс для weekly-bucket pattern
- `webapp/src/pages/Dashboard.tsx:546` — `WeekCard` как пример рендера weekly данных
- `data/metrics.py` — соседство для pure-формулы модуля
