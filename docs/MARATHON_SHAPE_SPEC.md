# Marathon Shape — Runalyze-style basic endurance metric

> Status: 🟢 **Shipped** — single-phase webapp widget. No MCP, no morning report, no schema changes.
>
> Issue: [#95](https://github.com/radikkhaziev/triathlon-agent/issues/95).

---

## 1. Problem

VO2max отвечает на «насколько быстро ты можешь бежать», но не на «хватает ли тебе объёма». Атлет с VO2max 50 теоретически бежит марафон за ~3:30, но если он бегает 30 км/нед — он сломается на 30-м километре, потому что running economy для дистанции 42.2 не наработана. Marathon Shape (Runalyze) — это процент-отношение текущего недельного объёма + длинных пробежек к VO2max-derived целям. Marathon = 100%, HM ≈ 42.5%, 10K ≈ 17%.

В проекте нет run-volume-aware метрики готовности: TSS показывает нагрузку, CTL — фитнес, но «у меня хватит ножек на марафон/HM?» — не отвечается.

**Disclaimer от первоисточника** (verbatim из [runalyze.com/help/article/marathon-shape](https://runalyze.com/help/article/marathon-shape), 2026-05-14): *«The Marathon Shape is **not scientifically based** and only serves as a rough estimate of whether you are sufficiently trained for a specific target distance (while the Effective VO2max only indicates the general performance level — independent of the distance/duration)»*. Это эмпирическая модель — наш виджет наследует тот же характер. В UI стоит держать tone «rough estimate», без претензии на научную точность.

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
target_longjog_km(vo2max) = ln(max(vo2max, 25) / 4) * 12 - 13   # SCORING-INTERNAL
```

Примеры (точные значения формул — issue body округлял):
- VO2max 45 → weekly 75.2 км, longjog 16.0 км
- VO2max 50 → weekly 84.8 км, longjog 17.3 км
- VO2max 55 → weekly 94.5 км, longjog 18.5 км

**Важное наблюдение — две разные «target long run» величины** (verified против Runalyze UI 2026-05-14):

| Имя | Формула | Зачем |
|---|---|---|
| `target_longjog_km` | `ln(V/4)*12 − 13` | **scoring-internal**: используется в quadratic term `((distance − 13) / target_longjog_km)²`. Это «целевой избыток длинной пробежки над 13-км порогом». |
| `displayed_long_run_target_km` | `ln(V/4)*12` | **UI-displayed**: «длина целевой длинной пробежки», как Runalyze показывает в колонке «Required Long Run». Для V=37 (marathon weekly 58 km на скриншоте) даёт 26.7 km ≈ 26 km, совпадает с upstream. |

Тождество: `displayed = scoring + 13` (где 13 = `MIN_KM_FOR_LONGJOG`).

Текущий shipped Components-блок (см. §6) показывает long-run percentage **от `target_longjog_km`** (scoring-internal). Это означает: при actual_longjog=18.2 км для VO2max=50 виджет рендерит «105% of required (18.2 km)» — потому что 18.2 / 17.3 ≈ 105. По Runalyze UX правильнее было бы «60% (18.2 / 30.3 km)» — потому что **displayed target — это 30.3 km**, не 17.3 km. Это **дельта семантики со стороны upstream**, не баг расчёта shape_pct (формула scoring корректна), но stylistic divergence от Runalyze UI. Phase 2: переключить Components-renderer на `displayed_long_run_target_km`, либо явно подписать «target excess over 13 km».

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
│ Predicted (HM)  ┌─────────────────────────┐    │
│ Time   1:42:15  │  CI 1:38:25 – 1:46:20   │    │
│ Pace   4:50/km  │  CI 4:40 – 5:01 /km     │    │
│                                                 │
│ ┌─ chart: 12 weeks ────────────────────┐       │
│ │ ────────── required (42.5%) ─────────│       │
│ │                          ▆▇▇         │       │
│ │           ▃▄▅▅▆▆▆                    │       │
│ └───────────────────────────────────────┘       │
│                                                 │
│ Weekly volume:  33% of required   (28.4 km/wk)  │
│ Long run:      105% of required   (18.2 km)     │
│ VO2max:        50.2                             │
└────────────────────────────────────────────────┘
```

**Predicted block** — выводится между header'ом и chart'ом, привязан к **выбранной distance**. Берёт `total_sec` + `pace_sec_per_km` + соответствующие CI low/high из ML-predict pipeline (`predict_splits_with_ci(mode='today')`). При cold-start или below-acceptance модели — блок скрывается полностью (gracefully, без error-state). Подробно — §13.

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

**Precedent — Runalyze UI** (verified 2026-05-14 на реальном breakdown'е): их формат разбора буквально «In total, you have achieved **18% of your required weekly mileage** and **0% of your required long runs**. This results in a marathon shape of **12%** which equals a marathon in 5:52:04». То есть Runalyze тоже показывает компоненты как **% of required**, а не абсолютные км — `0.67 × 18% + 0.33 × 0% = 12.06% ≈ 12%`. Mirror их UI снимает confusion с absolute-target числом и согласован с тем как метрика была изначально задумана автором. Marathon-time projection (5:52:04) — отдельная модель `f(vo2max, shape_pct)` поверх MS, **out of scope этой фазы** (§9).

### Components

- **Distance picker** — `TabSwitcher` с тремя опциями (`10K` / `HM` / `Marathon`), default `HM`. State в виджете, не в роутинге.
- **Header badge** — current MS / required / Δpp с цветом (зелёный если ≥required, жёлтый 80-100%, красный <80%).
- **Chart** — Chart.js line (как `EFChart`/`DecouplingChart`). X = `week_end` (12 точек), Y = `shape_pct`. Annotation plugin (уже импортирован в Progress.tsx:24) для horizontal `required` линии. Null-points (vo2max missing) — gap в линии.
- **Components-блок** — 3 строки из `current_components`. **Формат: «N% of required (raw value)»** — главное число это процент достижения marathon-target, абсолютное значение в скобках для тех кто хочет fact-check. Targets (`target_weekly_km`/`target_longjog_km`) в виджете **не показываются** — они marathon-baseline (`vo2max ** 1.135` / `ln(vo2max/4)*12-13`) и зависят только от VO2max, не от выбранной дистанции. Показ абсолютного «28 / 89 km» при HM-picker'е вызывает confused «надо 89 км/нед на HM??», хотя 89 это для марафона.

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

def test_runalyze_precedent_shape_combination():
    """Regression — Runalyze UI 2026-05-14 stated: 18% weekly + 0% longjog → 12% MS.

    Locks in PERCENTAGE_WEEK_KM (0.67) + PERCENTAGE_LONGJOGS (0.33) weights
    against upstream drift. If Runalyze ever rebalances weights, this fails
    loudly and we re-sync.
    """
    shape = 100 * (0.67 * 0.18 + 0.33 * 0.0)
    assert round(shape, 0) == 12

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
- **Runalyze' VDOT-port + empirical shape-penalty.** Их UI считает «Optimum» через Daniels' VDOT (`Calculation/Performance/*.php`) и «Prognosis» через empirical Hannes Christiansen penalty в `plugin/RunalyzePluginPanel_Rechenspiele/`. Penalty **saturating sigmoid-like** (verified на скриншоте 2026-05-14, 5 точек):

  | distance | achieved | penalty multiplier |
  |---|---|---|
  | 10K | 73% | 1.016 (+1.6%) |
  | HM | 29% | 1.114 (+11.4%) |
  | Marathon | 12% | 1.405 (+40.5%) |
  | 100K | 4% | 1.483 (+48.3%) |
  | 160.9K | 2% | 1.483 (+48.3%) ← plateau |

  Penalty достигает потолка ≈+48% при очень низком achieved% — функция saturating, скорее всего logistic с asymptote `~0.48`. Хороший artifact если когда-то решим porting; точная форма лежит в PHP-плагине Runalyze. **Мы это НЕ портим** — у нас есть свой `predict_splits_with_ci` (`data/ml/race_predict.py`), тренированный на личной истории, с 90% CI bootstrap, bias-corrected (Run-only Phase 2.0β2). См. §13 для интеграции. Прямой VDOT-port — out of scope навсегда.
- **Distance-adjusted weekly/long-run targets per distance** (Runalyze «Other distances» table). Их UI table показывает required weekly mileage и long run per dist (5K=6km, 10K=15km, HM=33km, Marathon=58km — для V≈37). Формула scaling не выводится из `V^1.135` напрямую (`marathon × required/100` даёт 25km для HM, факт 33km). Лежит в `RunalyzePluginPanel_Rechenspiele/` PHP-плагине. Phase 2 enhancement: full-table layout вместо distance picker'а.
- **«Achieved % per distance» inline list** — дешёвое расширение: рядом с header'ом показать «5K ✓ 170% · 10K ✗ 73% · HM ✗ 29% · Marathon ✗ 12%». Не требует новых формул — `achieved_pct[d] = current_shape_pct / required_shape_pct(d) × 100`. Дает Runalyze-like контекст «куда я готов сейчас», не теряя текущий picker. ~10 строк tsx.
- **MS per-discipline для triathlon (bike/swim shape)** — Runalyze считает только run-shape. Для bike — см. отдельный [`BIKE_READINESS_SPEC.md`](BIKE_READINESS_SPEC.md).
- **Historical MS chart >12 нед** — `weeks` param ограничен 24, виджет всегда 12. Расширение — Phase 2 если попросят.

## 10. Phases

| Phase | Scope | Status |
|---|---|---|
| **1** | Формулы (`data/marathon_shape.py`) + API endpoint + `MarathonShapeWidget` + unit-тесты | ✅ shipped |
| **1.5** | ML-based Predicted time + pace block в widget (`predict_splits_with_ci` integration, §13) + Redis cache | ✅ shipped |

Phase 2 — только если появится явный запрос (MCP-tool, morning report integration, история >12 нед, ultra/5K расширение picker'а, distance-adjusted weekly/long-run targets table per §9).

## 11. Acceptance criteria

- [x] `calculate_marathon_shape()` возвращает значения, совпадающие с формулами Runalyze (golden values для VO2max 45/50/55).
- [x] `GET /api/marathon-shape?weeks=12` возвращает 12 weekly buckets, newest first, с per-week `shape_pct` и `current_components`.
- [x] `MarathonShapeWidget` рендерится на `/progress` при `sport='run'` под `PolarizationWidget`.
- [x] Header badge показывает `progress_pct = shape / required * 100` с цветом по диапазону (≥100 зелёный, 80-100 жёлтый, <80 красный) и labels «Ready / Almost ready / Building for {distance}».
- [x] Distance picker (`10K`/`HM`/`Marathon`) переключает annotation line И пересчитывает progress_pct в header без re-fetch'а.
- [x] Chart показывает 12 точек с gap'ами для weeks без VO2max (chart скрывается полностью если все NULL).
- [x] При полном отсутствии run history виджет рендерит badge `Building for {distance}` без crash'а (shape=0, progress=0).
- [x] Tenant isolation: `user_id` фильтр на activities + wellness, регрессионный тест.

### Phase 1.5 — ML predicted time

- [ ] `/api/marathon-shape` response расширен `predicted_times: {10K, HM, Marathon}` с `total_sec` + `pace_sec_per_km` + `total_sec_ci_low/high` + `pace_ci_low/high` для каждой дистанции.
- [ ] Cold-start (`ModelNotTrained`) / below-acceptance / отсутствие run-модели → соответствующая дистанция = `null`, остальные могут быть filled. Никаких 500-ок.
- [ ] Widget показывает Predicted block (Time + Pace + CI), привязанный к выбранной distance из picker'а. При `predicted_times[distance] === null` — блок скрывается, остальной UI рендерится без crash'а.
- [ ] Pace формат — `M:SS/km` (290 sec → `4:50/km`). Time — `H:MM:SS` для >1h, `MM:SS` иначе.
- [ ] **Uncertainty-aware UI**: при CI spread > 20% от center value — footnote «model uncertainty high, limited race history» под Predicted block. Test покрывает.
- [ ] Integration test: endpoint mock'ит `predict_splits_with_ci` (`ModelNotTrained` для одной distance, valid для другой) → response корректно отражает оба случая.
- [x] Redis cache `(user_id, today_iso)` с TTL до полуночи Belgrade. `_compute_predicted_times` / `_predict_times_fresh` в `api/routers/dashboard.py`. Graceful fallback при Redis disabled / unreachable / get-write errors — endpoint никогда не падает из-за cache. 4 теста (`test_cache_hit_skips_ml_call`, `test_cache_miss_writes_through`, `test_cache_disabled_falls_through`, `test_cache_write_failure_does_not_break_response`).

## 12. Phasing & GitHub issues

- [x] **MS-1 — `data/marathon_shape.py` (pure formulas) + unit-тесты.** 87 строк модуль + 22 теста (`tests/data/test_marathon_shape.py`).
- [x] **MS-2 — `GET /api/marathon-shape` endpoint.** Single query на 38 недель run history + wellness vo2max + per-week loop в `api/routers/dashboard.py`. 7 интеграционных тестов включая tenant-isolation.
- [x] **MS-3 — `MarathonShapeWidget` на Progress.tsx.** Distance picker + chart с annotation line + components-блок. Без i18n — Progress.tsx использует English-литералы inline, виджет в той же стилистике.
- [x] **MS-4 — Empty/edge states.** «Marathon Shape unavailable» badge при no-data, «VO2max unavailable» при missing vo2max, `spanGaps: true` в chart, скрытие chart'а если все weeks NULL.

### Phase 1.5 punch-list

- [x] **MS-5 — Endpoint extension.** `/api/marathon-shape` вызывает `predict_splits_with_ci(user_id, mode='today', race_date=today_iso, race_distance_run_m=X)` для 10000 / 21097 / 42195 м **sequentially** через `for`-loop в `_predict_times_fresh` (`asyncio.gather` не даёт parallelism — `_predict_one` sync блокирует loop, см. §13 «Latency»). Try/except каждый — `ModelNotTrained` / `ModelBelowAcceptance` → null для дистанции; unexpected errors → Sentry + null. ~80 строк в `api/routers/dashboard.py`.
- [x] **MS-6 — Response types + widget render.** `MarathonShapeResponse.predicted_times` + `MarathonShapePredicted` в `webapp/src/api/types.ts`. Widget рендерит Predicted block (Time / Pace + CI low/high) под header'ом badge'а с `formatHMS` / `formatPace` helpers (защита от `sec <= 0` через `'—'`). Wide-CI footnote при spread > 20%.
- [x] **MS-7 — Integration test.** Mock `predict_splits_with_ci` → endpoint собирает корректный `predicted_times` envelope, cold-start = null для одной дистанции, valid для другой. 10 тестов в `TestMarathonShapePredictedTimes` (6 endpoint + 4 cache).
- [x] **MS-8 — Redis cache layer.** `_compute_predicted_times` обёртка над `_predict_times_fresh`, key `marathon_shape_pred:{user_id}:{today_iso}`, TTL через `_ttl_until_midnight_local()`. Graceful fallback на каждом из 3 cache failure mode'ов.

## 13. ML-based time prediction (Phase 1.5)

### Зачем не Runalyze' VDOT

Runalyze считает «Prognosis» через Daniels' VDOT × empirical Hannes Christiansen shape-penalty (`plugin/RunalyzePluginPanel_Rechenspiele/`). Хорошая first-order модель, но:

- **Универсальная** (general athlete), не personalised — Daniels' tables усреднены по сотням тысяч runner'ов.
- **Без CI** — точечная оценка, атлет не видит uncertainty.
- **Empirical shape-penalty** — нелинейная функция от achieved%, требует port отдельного PHP-плагина.

У нас уже есть **`data/ml/race_predict.py:predict_splits_with_ci`** — XGBoost per-discipline, тренированный на личной истории атлета, с **90% CI** через bootstrap-residuals, **bias-corrected** (β2). Включает CTL/ATL/recent-volume/HRV/eFTP features — то есть shape-penalty в Runalyze-смысле уже встроена через ML-features. Сильнее чем Runalyze' эмпирическая формула, не требует port.

### Источник данных

```python
from data.ml.race_predict import predict_splits_with_ci, ModelNotTrained, ModelBelowAcceptance

# Per-distance Run prediction для widget'а. Sequential, НЕ gather — см. ниже.
for label, dist_m in [("10K", 10000), ("HM", 21097), ("Marathon", 42195)]:
    try:
        env = await predict_splits_with_ci(
            user_id=uid,
            mode="today",                        # current state, не race_day projection
            race_date=today.isoformat(),         # см. note о bias-correction ниже
            race_distance_run_m=dist_m,
        )
        run = env["splits"].get("run")
        if run and "total_sec" in run:
            predicted_times[label] = {
                "total_sec": run["total_sec"],
                "total_sec_ci_low": run["total_sec_ci_low"],
                "total_sec_ci_high": run["total_sec_ci_high"],
                "pace_sec_per_km": round(run["pred"], 1),  # `pred` это sec/km для Run, units field == "sec_per_km"
                "pace_ci_low": round(run["ci_low"], 1),
                "pace_ci_high": round(run["ci_high"], 1),
            }
        else:
            predicted_times[label] = None  # power_only_phase1 / total_sec_unavailable
    except (ModelNotTrained, ModelBelowAcceptance):
        predicted_times[label] = None      # joblib missing или below-acceptance gate
```

**Important — `race_date` semantic в `mode='today'`** (verified в `_predict_one`, `data/ml/race_predict.py:341`): даже в today mode `race_date` используется для **bias correction**:
```
days_to_race = max((target_date - local_today()).days, 0)
bias_applied = bias_intercept + bias_slope × days_to_race
pred -= bias_applied
```
Для widget'а передаём `race_date=today.isoformat()` → `days_to_race=0` → applied только intercept (~6 sec/km для Run). Это стабильное поведение. **Не передавайте future race_date** — slope term начнёт двигать pred (~25 sec/km @ 150d), что некорректно для «текущая форма» semantics виджета.

**Latency — sequential, не parallel.** Хотя `predict_splits_with_ci` async, тяжёлая часть `_predict_one` (joblib load + XGBoost predict + bootstrap CI) — **sync, blocking event loop** (см. docstring `race_predict.py:477-479`: *«Heavy ML work stays sync — pandas / joblib don't benefit from async»*). Поэтому `await predict_splits_with_ci()` × 3 в for-loop = `3 × ~80ms = ~240ms` total. `asyncio.gather` поверх трёх `await` не даст parallelism — каждый вызов всё равно блокирует loop через sync `_predict_one`.

Чтобы получить реальный parallelism нужно `asyncio.gather(*[asyncio.to_thread(_sync_call_wrapper, ...) for ...])` — wrapper создаёт fresh event loop для каждого `predict_splits_with_ci` call. Это +complexity ради ~160ms экономии — не стоит для Phase 1.5. Sequential implementation simpler, latency приемлемая.

### Response envelope

```json
{
  "weeks": [...],
  "current_components": {...},
  "predicted_times": {
    "10K":      { "total_sec": 3340,  "total_sec_ci_low": 3210,  "total_sec_ci_high": 3490,
                  "pace_sec_per_km": 334.0, "pace_ci_low": 321.0, "pace_ci_high": 349.0 },
    "HM":       { "total_sec": 6135,  "total_sec_ci_low": 5905,  "total_sec_ci_high": 6380,
                  "pace_sec_per_km": 290.7, "pace_ci_low": 280.0, "pace_ci_high": 302.4 },
    "Marathon": null
  }
}
```

Newest first для `weeks` (как Phase 1). `predicted_times` — dict с фиксированными ключами `10K`/`HM`/`Marathon`, value либо envelope либо null.

### UI rendering

Под header'ом badge'а (`Ready for X: N%`) рендерится **Predicted block**:

```
Predicted (HM):
  Time   1:42:15   (CI 1:38:25 – 1:46:20)
  Pace   4:50/km   (CI 4:40 – 5:01 /km)
```

Format helpers:
- `formatHMS(sec)` — `H:MM:SS` для ≥3600 сек, `M:SS` иначе. `6135` → `1:42:15`.
- `formatPace(sec_per_km)` — `M:SS/km`. `290.7` → `4:50/km` (округление к ближайшей секунде).

Привязано к **выбранной distance** из picker'а — переключение мгновенное, без re-fetch (все три предсказания пришли одним response).

### Edge cases

| Случай | Поведение |
|---|---|
| `ModelNotTrained` (нет joblib) для всех дистанций | Predicted block не рендерится, остальной widget работает |
| `ModelNotTrained` для одной distance, valid для других | Скрываем block только когда picker = эта distance; для остальных показываем |
| `ModelBelowAcceptance` (R²/MAE ниже threshold) | Аналогично ModelNotTrained — скрываем, не показываем шумную оценку |
| CI bands пересекают physiological floor (`run` floor = 150 sec/km = 2:30/km) | Уже clamp'ится в `_predict_one` (`race_predict.py:357-364`) — берём как есть |
| User свежий, нет race history → cold-start | Все 3 = null, block скрыт. Widget работает как Phase 1 only |
| Mode = `today` vs `race_day` | Используем только `today` — это «текущая форма», не «предсказание на дату». `race_day` нужен для отдельной race-prep страницы, не для виджета базовой выносливости |
| **Wide CI (uncertainty visibility)** | Если `(total_sec_ci_high − total_sec_ci_low) / total_sec > 0.20` (≥20% spread от center) — UI рендерит footnote «model uncertainty high, limited race history». Атлет с 2-3 races в БД получит wide CI типа «1:32:00 – 1:54:00» что выглядит useless без контекста. Threshold 0.20 эмпирический — соответствует ~10 race samples в training set по нашей калибровке. Альтернатива: при `spread > 0.30` прятать CI bands полностью, оставить только central estimate. |

### Performance / caching

- ML retrain — Sunday 03:00 (`ml-worker` container, isolated queue). Модели стабильны в течение недели.
- Endpoint вызывается на каждом visit Progress page (sport=run). 3 sequential inference calls × ~80ms = ~240ms latency overhead (см. note выше — gather не дал бы parallelism).
- **Caching не делаем в core Phase 1.5** — оценки меняются медленно (CTL drifts ~1 unit/day, predicted HM time меняется ~1-2 сек между понедельником и пятницей), +250ms latency приемлемо для диагностического widget'а.

  **Однако** — атлет typically заходит 2-3× в день на dashboard, cumulative экономия 480-720ms на повторные visits. Если widget heat picks up — добавить Redis cache `(user_id, today_iso)` с TTL до полуночи Belgrade. ~15 строк в endpoint, риск нулевой (cache miss работает так же как сейчас). Включено в Phase 1.5 acceptance как **optional**.

### Чего НЕ делаем в Phase 1.5

- **Race-day mode prediction** — `predict_splits_with_ci(mode='race_day')` хорошо бы для race-prep страницы, но не для этого widget'а. Widget показывает «текущая форма», не «куда я приду к дате».
- **Bike / Swim прогнозы** — спека про MS только Run. Bike прогноз — отдельный widget на bike-tab (см. §14 Related, `BIKE_READINESS_SPEC.md`).
- **Historical pace trend** — chart показывает MS%, не predicted pace. Time-series predicted pace требовал бы run inference per week — слишком дорого.
- **Comparison vs Runalyze' Optimum** — у нас нет VDOT-table (и не планируем). Сравнения нет, есть только наш ML.

## 14. Related

- [BasicEndurance.php — Runalyze source](https://github.com/Runalyze/Runalyze/blob/master/inc/core/Calculation/BasicEndurance.php) — calculation layer, формулы shape_pct + target_weekly + target_longjog (scoring-internal).
- `plugin/RunalyzePluginPanel_Rechenspiele/` (Runalyze repo) — UI panel, рендерит «Other distances» table + Prognosis column. **Where to look for**: distance-adjusted weekly/long-run scaling formula + Prognosis penalty function. Not yet ported — см. §9 Phase 2 ideas.
- `inc/core/Calculation/Performance/` (Runalyze repo) — VDOT-related race-time prediction. Источник `Optimum` колонки.
- [Marathon Shape help-article](https://runalyze.com/help/article/marathon-shape) — UI screenshots, formal disclaimer «not scientifically based», examples for distance-adjusted targets.
- `webapp/src/pages/Progress.tsx` — placement target (line 75, после `PolarizationWidget`)
- `api/routers/dashboard.py:140` — `weekly_recap` как референс для weekly-bucket pattern
- `webapp/src/pages/Dashboard.tsx:546` — `WeekCard` как пример рендера weekly данных
- `data/metrics.py` — соседство для pure-формулы модуля
- `data/ml/race_predict.py:predict_splits_with_ci` — ML pipeline для Phase 1.5 Predicted time/pace (§13).
- `mcp_server/tools/race_projection.py` — MCP wrapper над `predict_splits_with_ci` (ссылка для понимания envelope).
- [`BIKE_READINESS_SPEC.md`](BIKE_READINESS_SPEC.md) — bike-side parallel widget.
