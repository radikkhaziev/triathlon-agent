# Bike Readiness — durability-aware bike-leg readiness widget

> Status: 🔵 **Draft** — single-phase webapp widget. No new MCP tool, no morning report, no schema changes.
>
> Companion to [`MARATHON_SHAPE_SPEC.md`](MARATHON_SHAPE_SPEC.md) — the bike side of «am I ready for the race-leg».

---

## 1. Problem

Для бега у нас есть `Marathon Shape` — volume + longjog в одном проценте. На велике аналогичной готовности нет, и в индустрии **общепринятого «Bike Shape %» не существует** (Runalyze явно отказались — см. §13; TrainingPeaks / Intervals.icu / Xert также не выпускают единого процента). Причина методологическая: distance на велике плохой прокси для базовой выносливости (60 км по горам ≠ 60 км в парке, indoor — distance фиктивна), поэтому индустрия меряет bike-готовность через **kJ/TSS + duration + decoupling**, а не через distance.

При этом у Радика конкретный продуктовый вопрос «хватит ли мне ножек на 90 км bike-leg в Кальяри 28 июня» — и текущий стек метрик (CTL_bike, Pa:Hr, EF) хорошо отвечает на него **по отдельности**, но не собран в одну карточку с явным verdict'ом. Виджет на `/progress` решает именно эту сборку, не вводя новой синтетической математики.

### Declarative stance

**No industry precedent → no synthetic combo.** В велоспорте нет общепринятой «Bike Shape %» метрики (Runalyze явно отказался, TrainingPeaks/Intervals/Xert — тоже). Поэтому мы **не изобретаем weighted-combo формулу** с весами «пальцем в небо». Вместо этого виджет показывает **три well-validated independent signal'а** (CTL_bike, longest ride duration, decoupling) с traffic-light verdict-логикой — mirror практический pattern Joel Filliol / Coggan style readiness assessment, не новый index.

**Phase 1.5 единственный value-add — ML Predicted Power.** Симметрично Marathon Shape §1: за base readiness берём industry-best multi-signal pattern, добавляем сверху ML inference как value-add. Bike-leg pacing band («target 240W, CI 230-250W») actionable для race-day — это и есть наша инновация над industry baseline.

Decision rule для будущих изменений: «если найдём industry precedent (Runalyze добавит bike, или Coggan published explicit formula) — фиксим в сторону upstream. До тех пор — three signals stay independent, no synthetic».

---

## 2. Solution overview

Виджет `BikeReadinessWidget` на `webapp/src/pages/Progress.tsx` при `sport='bike'`, разместить **после `ProgressionWidget`** (см. `Progress.tsx:77`). Виджет показывает:

1. **Distance picker** — `Olympic / 70.3 / IM` (3 опции, default `70.3`).
2. **Header verdict** — «Ready / Almost ready / Building for {distance}» по логике 3-сигнальной оценки.
3. **Volume chart** — CTL_bike trend за 12 недель + horizontal annotation line на `target_ctl_for_distance`.
4. **Components-блок** — три карточки: Volume (CTL_bike actual/target), Long ride (max ride duration за 4 нед / target), Durability (avg decoupling за 4 нед).

Backend: один endpoint `GET /api/bike-readiness?weeks=12` отдаёт time-series для CTL chart'а + текущие значения трёх сигналов. Distance-specific targets — табличные (см. §3), вычисляются на клиенте.

Никаких изменений схемы — читаем `wellness.sport_info` (CTL_bike), `activities` (longest ride), `activity_details.decoupling` (+ `is_valid_for_decoupling` filter).

**Принципиальное решение:** виджет **не считает синтетический Bike Shape %**. Three signals + traffic-light verdict, без weighted-combo формулы. Обоснование см. §12 Considered alternatives — там же зафиксированы два отвергнутых варианта.

---

## 3. Метрики

Три независимых сигнала. Без weighted combo, без синтетических процентов.

### 3.1 Volume — CTL_bike

Источник: `Wellness.sport_info` (JSON, заполняется из Intervals.icu pipeline) → `extract_sport_ctl(sport_info)["ride"]` из `data/utils.py:81`.

```python
# За newest week_end в окне:
ctl_bike = extract_sport_ctl(wellness_row.sport_info)["ride"]
ctl_ratio = ctl_bike / target_ctl_for_distance(distance)
```

**Targets — calibrated for solid AG-athlete** (top 30-50% age-group finisher, не peak elite, не just-finisher):

| Distance | target_ctl_bike | Solid AG reference profile |
|---|---|---|
| Olympic (40 km bike) | **35** | Total CTL ~70-85 peak, 8-10h/wk training, top 30% AG |
| 70.3 (90 km bike) | **50** | Total CTL ~90-110 peak, 10-13h/wk training, sub-5h finisher / top 30% AG |
| IM (180 km bike) | **80** | Total CTL ~120-150 peak, 15-20h/wk training, sub-11h finisher / top 25% AG |

**Critical assumption — bike-share of total TSS** (sanity-check для derivation):

CTL_bike это **per-sport** CTL (`extract_sport_ctl(sport_info)["ride"]`), а не total. Литературные CTL ranges (Friel/Coggan/Filliol) приводятся как **total CTL**, не per-sport. Bike-share варьируется по distance:
- Olympic: bike ≈ 40-50% of total TSS (короткий bike-leg)
- 70.3: bike ≈ 45-55% (longer bike + ride dominance в training week)
- IM: bike ≈ 55-65% (IM bike-leg = 50%+ race time, bike volume drives prep)

Target derivation: `bike_target = total_CTL_target × bike_share`. Для 70.3: `100 × 0.50 = 50`. Совпадает с table.

**Sanity numbers** (bike-CTL 50 для 70.3 solid AG peak):
- Weekly bike TSS ≈ 350-500 (CTL τ=42, daily TSS contribution ≈ CTL)
- В часах ≈ 5-7 hours bike per week at IF ~0.75 average
- Reasonable peak training load для solid AG, achievable but stretching

**Calibration boundaries — что НЕ покрывает:**

- **Elite cyclists** (Cat 1-3 road racers, time-trialists): CTL 100-130 bike-only — Coggan numbers применимы напрямую, потому что bike = 100% TSS. Наш table занижен для них в 1.5-2x. Не наша audience.
- **Just-finishers** (5+ hour 70.3 «complete the distance» mindset, total CTL 60-70): bike-CTL ~30 hits «🔴 building» permanently. Можно argument'ировать что они и есть building — но честнее снизить targets если такая audience станет primary.
- **Bike-focused triathletes** (gravel-converts, ex-cyclists), bike-share 60-65%: targets чуть низковаты, easy 🟢 — но это honest signal что они bike-ready.
- **Swim/run-focused** (bike-share 25-30%): targets чуть высоковаты, harder to hit 🟢 — может ощущаться unfair, но bike-leg для них действительно the weak link.

Это **табличные значения**, не функция FTP. Альтернатив (степенная формула от FTP/VO2max или per-athlete share-of-TSS параметризация) для bike-CTL в литературе нет. Phase 2 option: если production обнаружим material drift для бракeт outside solid AG — parameterise от atletа's historical bike-share of TSS (last 12 weeks, computed from `wellness.sport_info` weekly aggregation).

**Provenance** — числа derived из:
- Joe Friel «The Triathlete's Training Bible» 4e — total CTL bands per race distance (без точных цифр, ranges «70.3 athlete CTL 60-100 в peak phase»).
- Joel Filliol practitioner posts — elite/AG distinction.
- Coggan/Allen «Training and Racing with a Power Meter» — CTL training stress framework.
- Bike-share percentages — typical triathlete training distribution analysis (Joe Friel periodization tables).

**В литературе точные цифры 35/50/80 не написаны** — это **midpoints derived ranges** под solid AG bracket. Calibrated for the typical viewer of this widget, не для outliers.

**Traffic light:**
- 🟢 ctl_ratio ≥ 1.00 (CTL ≥ target)
- 🟡 0.80 ≤ ctl_ratio < 1.00
- 🔴 ctl_ratio < 0.80

### 3.2 Long ride — longest ride за DAYS_FOR_LONGRIDE окно

```python
DAYS_FOR_LONGRIDE = 28  # 4 недели
longest_ride_hours = max(act.moving_time for act in bike_activities_last_28d) / 3600
long_ride_ratio = longest_ride_hours / target_long_ride_hours(distance)
```

**Targets** (long ride должен покрывать race-bike-leg duration с небольшим запасом по верху):

| Distance | target_long_ride_hours | Обоснование |
|---|---|---|
| Olympic | 1.5 | Race bike ~1ч @ 35 km/h → tolerance margin |
| 70.3 | 3.0 | Race bike ~2.5-3ч → ride хотя бы как race |
| IM | 5.0 | Race bike ~5-6ч → стандарт IM-prep |

**Olympic — informational only.** Threshold 1.5h осознанно низкий: bike-leg Olympic короткая, любой consistent rider (4+ часов в неделю) набирает 1.5h ride без специальных усилий. Signal **не дискриминирует** Olympic-ready vs не-готов — для коротких дистанций Volume и Durability сигналы информативнее. Снижать дальше (e.g. 1.2h = race duration) не имеет смысла — атлет на race-leg 1ч должен иметь long ride хотя бы 1.5x race. Олимпийку оставили в picker'е для completeness виджета (другие атлеты могут гоняться Olympic); если будет feedback что эта дистанция misleading — drop из picker'а в Phase 2.

**Filter for «ride»:** `Activity.type IN ('Ride', 'VirtualRide')`, `is_race=False`. Race-effort исключаем — race это пиковая нагрузка, не индикатор тренировочной готовности (consistent с MS §7).

**Traffic light:**
- 🟢 long_ride_ratio ≥ 1.00
- 🟡 0.80 ≤ long_ride_ratio < 1.00
- 🔴 long_ride_ratio < 0.80

### 3.3 Durability — median decoupling из последних 5 валидных rides

**Переиспользуем** `compute_efficiency_trend(user_id, sport='bike', days_back=84, strict_filter=True)` из `mcp_server/tools/progress.py:99`. Эта функция уже возвращает `decoupling_trend` с фиксированной агрегацией:

```python
# Из существующего output:
decoupling_trend = {
  "last_n": 5,                       # сколько rides взято
  "median": 4.2,                     # %, median of last 5 valid rides
  "status": "green",                 # decoupling_status(median) → green/yellow/red
  "values": [3.8, 4.5, 4.2, 3.9, 5.1],
  "latest": {"value": 5.1, "status": "green", "date": "2026-05-10"},
}
```

`strict_filter=True` применяет `is_valid_for_decoupling` (VI ≤ 1.10, >70% Z1+Z2, ride ≥ 60 min, decoupling not NULL — `data/metrics.py:594`). `days_back=84` (12 недель) даёт окно для накопления как минимум 5 валидных rides — у атлета бывает sparse-неделя без long ride'а, 28-дневное окно слишком жёсткое.

**Не пишем свой SQL/Python pipeline для decoupling.** Переиспользуем existing helper, иначе разъедутся definitions «valid ride» в проекте.

**Traffic light** — берём прямо `decoupling_trend.status` (already computed via `decoupling_status()` helper, thresholds 5% / 10% — consistent across project, см. `docs/knowledge/decoupling.md`):
- 🟢 median ≤ 5.0
- 🟡 5.0 < median ≤ 10.0
- 🔴 median > 10.0
- ⚪ `decoupling_trend` отсутствует или `last_n=0` → «insufficient data», не учитывается в verdict'е

### 3.4 Header verdict logic

```python
signals = [volume_signal, long_ride_signal, durability_signal]
greens = signals.count("green")
yellows = signals.count("yellow")
reds = signals.count("red")
insufficient = signals.count("insufficient")
available = 3 - insufficient

if insufficient >= 2:
    verdict = "insufficient_data"
    label = "Not enough data for {distance}"
    subtext = None
elif reds >= 1:
    verdict = "building"
    label = "Building for {distance}"
    subtext = f"{available - reds} of {available} signals on track"
elif greens == available:
    verdict = "ready"
    label = "Ready for {distance}"
    subtext = "All signals on track ✓"
else:
    verdict = "almost"
    label = "Almost ready for {distance}"
    subtext = f"{greens} of {available} signals on track"
```

Один красный — `building`. Все зелёные (из доступных) — `ready`. Иначе — `almost ready`.

**Subtext под verdict'ом** — gradient information без extra verdict state (YYY и GGY оба «almost», но subtext различает «1 of 3» vs «2 of 3»). Это снимает claim что «almost — too broad»: верхняя категория одна, но точная диспозиция читается субтекстом.

Цвет header'а — те же три (green / yellow / red), что у CTL-delta в `Dashboard.tsx:506-512` (consistent palette across the app).

---

## 4. Data model

**Никаких изменений схемы.** Источники:

| Поле | Таблица | Колонка | Notes |
|---|---|---|---|
| CTL_bike per day | `wellness` | `sport_info` (JSON) | extracted via `extract_sport_ctl(sport_info)["ride"]`. **Sport key normalisation:** Intervals.icu может писать `"type": "Ride"`/`"Bike"`/`"bike"` в JSON entry — `extract_sport_ctl` нормализует через `SPORT_MAP.get(raw_type)` → всегда возвращает под ключом `"ride"`. Edge: missing field → `None` (handled). |
| Bike activity | `activities` | `type`, `is_race`, `moving_time`, `start_date_local` | filter `type IN ('Ride','VirtualRide') AND is_race=False` |
| Ride details for decoupling | `activity_details` | `decoupling`, `variability_index`, `hr_zone_times` | JOIN by `activity_id`. Filter via `is_valid_for_decoupling` |

**Windows** (разные по rationale):

| Signal | Window | Почему именно столько |
|---|---|---|
| CTL chart | 12 weeks (84d) | Standard fitness-trend window, consistent с MS chart и `weekly_recap`. Sunday snapshot `sport_info.ride` per week. |
| Long ride | 28d (4 недели) | Один валидный long ride достаточно показателен; 28d покрывает 1 monthly cycle + tolerance для отпуска/болезни. Меньше окно (14d) — слишком чувствительно к пропуску; больше (8 недель) — затягивает старые данные после смены формы. |
| Durability | 84d (12 weeks) | Нужно accumulate **5 valid rides** для stable median (`is_valid_for_decoupling` фильтр requires ride ≥ 60min + VI ≤ 1.10 + >70% Z1+Z2). У атлета может быть sparse-неделя без long ride'а — 28d window недостаточен (1-2 valid rides → noisy median). 84d = реалистичный 5-sample horizon для AG-атлета. |

Inconsistency между long-ride 28d и durability 84d сознательная: long ride — **single best workout** demonstration, durability — **trend стабильности** требующий N samples.

---

## 5. API endpoint

```python
# api/routers/dashboard.py — рядом с marathon_shape endpoint

@router.get("/api/bike-readiness")
async def bike_readiness(
    weeks: int = Query(default=12, ge=1, le=24),
    user: User = Depends(require_viewer),
) -> dict:
    """Bike readiness — CTL_bike trend + current 3-signal snapshot.

    For each of the last `weeks` Mon-Sun weeks (ending most recent Sunday),
    returns CTL_bike snapshot. `current_components` carries the latest
    longest-ride / decoupling-median / ef-trend for the badge logic.

    Distance-specific targets (Olympic/70.3/IM) and verdict are computed
    CLIENT-side — endpoint returns absolute values only.
    """
```

**Response shape:**

```json
{
  "weeks": [
    {
      "week_start": "2026-02-23",
      "week_end": "2026-03-01",
      "ctl_bike": 68.4                // null если sport_info недоступен
    }
    // ... newest first ...
  ],
  "current_components": {
    "ctl_bike": 68.4,                 // newest week's CTL
    "longest_ride_hours": 2.25,       // max moving_time за 28d, hours
    "longest_ride_date": "2026-05-10",
    "decoupling_median_pct": 4.2,     // median последних 5 valid rides
    "decoupling_status": "green",     // green | yellow | red — из decoupling_status(median)
    "decoupling_n": 5,                // last_n — сколько rides попало
    "ef_trend_pct": 2.3               // % change in EF over period, sign matters: >0 — улучшение, supplementary
  }
}
```

Newest first (consistent с `weekly_recap` и `marathon-shape`).

**Реализация подсказок:**
- CTL_bike per week — single query на `wellness` за `weeks` Sundays, parse JSON через `extract_sport_ctl`.
- Longest ride — single query на `activities` `WHERE type IN ('Ride','VirtualRide') AND is_race=False AND start_date_local >= today-28d ORDER BY moving_time DESC LIMIT 1`.
- Decoupling **и** EF trend — **один вызов** `compute_efficiency_trend(user_id, sport='bike', days_back=84, group_by='week', strict_filter=True)`. Берём из его output'а: `decoupling_trend.{median, status, last_n}` для durability сигнала + `trend` (percentage) для `ef_trend_pct` supplementary. Не дублируем pipeline.

Three signals → один endpoint, два запроса (wellness + activities) + один helper call (`compute_efficiency_trend`). Linear, без N+1.

---

## 6. Webapp widget

### Placement

В `webapp/src/pages/Progress.tsx:77` после `<ProgressionWidget />` добавить:

```tsx
{sport === 'bike' && <BikeReadinessWidget />}
```

### Layout

```
┌────────────────────────────────────────────────────┐
│ Bike Readiness                                      │
│ ┌──────────────────────────────────┐               │
│ │  Olympic  |  70.3  |  IM         │               │
│ └──────────────────────────────────┘               │
│                                                     │
│ Building for 70.3                                   │
│ 2 of 3 signals on track                             │
│                                                     │
│ Predicted (70.3 bike):                              │
│   Power   240W   (CI 230 – 250W)                    │
│                                                     │
│ ┌─ chart: CTL_bike, 12 weeks ─────────┐            │
│ │ ───────────── target (50) ──────────│            │
│ │                          ▆▇▇        │            │
│ │           ▃▄▅▅▆▆▆                   │            │
│ └─────────────────────────────────────┘            │
│                                                     │
│ ┌─────────────────────────────────────┐            │
│ │ 🟡 Volume      CTL 45 / 50   (90%)  │            │
│ │ 🔴 Long ride   2:15 / 3:00   (75%)  │            │
│ │ 🟢 Durability  Pa:Hr 4.2% (5 rides) │            │
│ │    📈 EF trend +2.3% (12w)          │            │
│ └─────────────────────────────────────┘            │
└─────────────────────────────────────────────────────┘
```

Three signals → один red ⇒ verdict «Building for 70.3», subtext «2 of 3 signals on track» (volume + durability green/yellow, long ride red). Атлет видит, что именно подтянуть — long ride. Volume почти на месте, durability ок. EF trend supplementary sub-line под Durability — долгосрочное улучшение aerobic fitness (positive = improving). Predicted Power block — Phase 1.5 (см. §13).

### Components

- **Distance picker** — `TabSwitcher` с тремя опциями (`Olympic` / `70.3` / `IM`), default `70.3`. State в виджете, не в роутинге.
- **Header badge + subtext** — verdict text (Ready/Almost/Building/Insufficient) + соответствующий цвет (зелёный/жёлтый/красный/серый) + sub-line «N of M signals on track». При переключении дистанции пересчитывается клиентом без re-fetch'а.
- **Predicted block** (Phase 1.5) — рядом с verdict'ом, под header'ом, перед chart'ом. Watts + CI для выбранной distance. Cold-start / below-acceptance — block скрывается. См. §13.
- **Chart** — Chart.js line (как `EFChart`/`MarathonShapeWidget chart`). X = week_end (12 точек), Y = CTL_bike. Annotation plugin (уже импортирован в `Progress.tsx:24`) для horizontal `target_ctl` линии. Null-points (нет sport_info) — gap в линии. Annotation двигается при переключении дистанции — кривая не дрожит.
- **Components-блок** — три строки, каждая с traffic-light dot, label, actual/target, и процентом-в-скобках. Цвет dot'а — current traffic light per signal. **Durability row** дополняется sub-line `📈 EF trend ±X.X% (12w)` — supplementary, не влияет на signal status, но complement к decoupling: decoupling = durability сегодня, EF trend = долгосрочное улучшение aerobic fitness через `compute_efficiency_trend.trend` (positive % = improving). Если `ef_trend_pct` null — sub-line скрыта.

### Empty/edge states

- **No bike activities за 28 дней** → Long ride card: «No bike rides last 28 days», Durability: «No valid rides». Volume может оставаться валидным (CTL обновляется в wellness даже без новых активностей через decay).
- **No CTL_bike** (sport_info не заполнен, например, бэкфилл атлета) → chart показывает gap, Volume card: «CTL unavailable», verdict идёт по двум оставшимся сигналам.
- **Все 3 сигнала insufficient** → header «Not enough data for {distance}», без цвета.

---

## 7. Edge cases / fallbacks

| Случай | Поведение |
|---|---|
| `wellness.sport_info` NULL на week_end | Walk back до 7 дней, взять последнее значение. CTL медленно меняется (τ=42d), 7-дневный fallback безопасен. Если за 7d тоже нет — `ctl_bike: null` для этой недели. |
| Бэкфилл атлета — sport_info неполный | Те же fallback'и. Chart покажет gap'ы, viewer-friendly. |
| Indoor ride (VirtualRide) без power — попадёт в `is_valid_for_decoupling`? | Да, если есть HR + decoupling рассчитан. Если decoupling NULL — отфильтруется. Existing contract. |
| Race-effort `is_race=True` | **Исключаем** из всех компонентов (consistent c MS §7). Race ≠ training base. |
| Athlete с CTL_bike=0 (после длинного перерыва) | Volume → 🔴, остальные сигналы могут быть insufficient (нет recent rides) → verdict «Building». |
| Outlier — одна 6-часовая туристическая поездка повышает long_ride | Не фильтруем. Это и есть нужное — атлет реально может проехать длинно. Если ровно один outlier и далее ничего — durability card покажет «1 ride» и atlete увидит контекст. |
| FTP не задан → power-based durability невалидна? | Decoupling рассчитывается из HR drift (Pa:Hr), не требует FTP. Работает на HR-only rides. |

---

## 8. Tests

```python
# tests/api/test_dashboard.py::TestBikeReadiness

async def test_returns_12_weeks_newest_first(client):
    """Endpoint returns 12 ctl_bike points, newest first."""

async def test_ctl_bike_extracted_from_sport_info_json(client):
    """sport_info JSON correctly parsed via extract_sport_ctl helper."""

async def test_no_sport_info_returns_null_ctl(client):
    """Empty/None sport_info → ctl_bike: null per week."""

async def test_ctl_bike_7d_backwalk(client):
    """5-day-old CTL is picked up when week_end has NULL sport_info."""

async def test_longest_ride_max_in_28d(client):
    """current_components.longest_ride_hours = max moving_time in last 28d."""

async def test_race_rides_excluded(client):
    """is_race=True rides do NOT count toward longest_ride or durability."""

async def test_decoupling_median_from_compute_efficiency_trend(client):
    """decoupling_median_pct === compute_efficiency_trend(...)['decoupling_trend']['median']."""

async def test_decoupling_status_thresholds(client):
    """status: green ≤5, yellow ≤10, red >10 — consistent с decoupling_status() helper."""

async def test_indoor_ride_counts_if_decoupling_present(client):
    """VirtualRide with valid decoupling included; without — excluded (via strict_filter)."""

async def test_no_valid_rides_returns_null_decoupling(client):
    """No valid rides in 84d window → decoupling_median_pct: null, decoupling_n: 0."""

async def test_per_user_scoping(client):
    """Tenant isolation on activities + activity_details + wellness — regression."""

async def test_ef_trend_pct_field_populated(client):
    """ef_trend_pct — number (percentage), не enum."""
```

Pure-формул в этой спеке нет (всё распадается на DB-запросы + filter helpers), поэтому unit-тесты идут на API integration layer. Если в Phase 2 появится `data/bike_readiness.py` модуль — добавятся unit-тесты на pure helpers (target_ctl_for_distance, verdict logic).

---

## 9. Out of scope

- **MCP tool `get_bike_readiness`** — viewer-only widget, AI не использует. Если в Phase 2 захочется отвечать «хватит ли мне ножек на 70.3» в чате — обёртка над тем же endpoint'ом, copy-paste из marathon_shape MCP wrapper.
- **Интеграция в утренний отчёт / prompt enrichment** — после валидации виджета.
- **Bike Shape % (single synthetic number)** — отвергнут, см. §12.
- **5K bike / 100mi / Sprint** — picker сужен до Olympic/70.3/IM. Sprint bike-leg = 20 км, Olympic покрывает с запасом. 100mi gravel — out of scope для триатлета.
- **FTP-derived target_ctl** — попытка параметризовать target_ctl от FTP не подкреплена литературой. Табличные значения проще и достаточны для три trifecta distances.
- **Per-rider position adjustment (TT vs road vs MTB)** — too granular. Decoupling уже учитывает «как тело держит мощность», независимо от позиции.
- **Calories / kJ as alternative volume metric** — CTL_bike сам по себе включает kJ-derived TSS через Intervals.icu pipeline, дублировать нет смысла.

---

## 10. Phases

| Phase | Scope | Status |
|---|---|---|
| **1** | `GET /api/bike-readiness` endpoint + `BikeReadinessWidget` (verdict + chart + 3 signal cards + EF trend sub-line) + integration-тесты | ✅ done — BR-1…BR-4 landed 2026-05-15 |
| **1.5** | ML Predicted Power block (`predict_splits_with_ci(sport='ride')` integration + Redis cache + uncertainty-aware UI) | 🔵 pending — depends on Phase 1 |

**Ordering:** Phase 1 ships first (core 3-signal readiness widget). Phase 1.5 добавляет Predicted Power block поверх — mirror MS Phase 1 → 1.5 history. Если 1.5 запустить до 1.5 — predicted без verdict не имеет actionable context.

Phase 2 — только если явный запрос:
- MCP tool wrapper для chat-/morning-доступа.
- FTP-parameterised target_ctl (если table drift'ит на elite/beginner brackets).
- Synthetic Bike Shape % (если виджет провалидируется и появится явная потребность в одной цифре для chart-trend'а — see §12, currently rejected).
- Sport-Specific Readiness combo на одной странице (`BikeReadiness` + `MarathonShape` + (потенциально) `SwimReadiness` рядом).

---

## 11. Acceptance criteria

### Phase 1 — core readiness ✅

- [x] `GET /api/bike-readiness?weeks=12` возвращает 12 weekly buckets с `ctl_bike` + `current_components` (longest_ride, decoupling_median, decoupling_status, decoupling_n, ef_trend_pct).
- [x] `BikeReadinessWidget` рендерится на `/progress` при `sport='bike'` после `ProgressionWidget`.
- [x] Distance picker (`Olympic`/`70.3`/`IM`) переключает target для chart annotation и пересчитывает verdict в header без re-fetch'а.
- [x] Header verdict следует логике §3.4: ≥1 red → Building, all green (из доступных) → Ready, иначе Almost ready. Subtext «N of M signals on track» под verdict'ом.
- [x] Three components carry traffic-light dots: Volume / Long ride / Durability. Durability card имеет sub-line «📈 EF trend ±X.X% (12w)» если `ef_trend_pct` non-null.
- [x] Chart показывает 12 точек с gap'ами для weeks без `sport_info.ride`.
- [x] При полном отсутствии bike-rides виджет рендерит «Not enough data» без crash'а.
- [x] Race rides (`is_race=True`) НЕ входят в long_ride и avg_decoupling. Регрессионный `test_race_rides_excluded` (+ helper-level fix in `compute_efficiency_trend` — раньше races leaked through Durability, fixed 2026-05-15).
- [x] Tenant isolation: `user_id` фильтр на activities + activity_details + wellness, регрессионный `test_per_user_scoping`.
- [x] Durability сигнал считается через `compute_efficiency_trend(sport='bike', strict_filter=True)` — `is_valid_for_decoupling` НЕ дублируется в `dashboard.py`, переиспользуется existing pipeline.

### Phase 1.5 — ML Predicted Power

- [ ] `/api/bike-readiness` response расширен `predicted_power: {Olympic, "70.3", IM}` с `watts` + `watts_ci_low/high` для каждой дистанции.
- [ ] Cold-start (`ModelNotTrained`) / below-acceptance / отсутствие ride-модели → соответствующая дистанция = `null`, остальные могут быть filled. Никаких 500-ок.
- [ ] Widget показывает Predicted block (Power + CI), привязанный к выбранной distance из picker'а. При `predicted_power[distance] === null` — блок скрывается, остальной UI рендерится без crash'а.
- [ ] Power формат — `XXX W` (240W). CI — `(CI XXX – XXX W)`.
- [ ] **Uncertainty-aware UI**: при CI spread > 20% от center value — footnote «model uncertainty high, limited race history» под Predicted block.
- [ ] Integration test: endpoint mock'ит `predict_splits_with_ci` (`ModelNotTrained` для одной distance, valid для другой) → response корректно отражает оба случая.
- [ ] Redis cache `(user_id, today_iso)` с TTL до полуночи Belgrade (mirror MS pattern из `_compute_predicted_times` в `api/routers/dashboard.py`). Graceful fallback при Redis disabled / unreachable / errors.

---

## 12. Phasing & GitHub issues

### Phase 1 punch-list ✅ (landed 2026-05-15)

- [x] **BR-1 — `GET /api/bike-readiness` endpoint.** Single SQL query на `wellness` за 12 weeks (extract `sport_info.ride` per Sunday + 7d back-walk) + single query на `activities` за 28d (longest Ride, no race — `type == "Ride"` post-normalisation, see `data/intervals/dto.py:_normalize_type` for VirtualRide → Ride mapping) + single helper call `compute_efficiency_trend(sport='bike', days_back=84, strict_filter=True)`. Response shape per §5. `api/routers/dashboard.py:747-881`.
- [x] **BR-2 — `BikeReadinessWidget` на Progress.tsx.** Distance picker + verdict badge + chart + 3 component cards + EF trend sub-line. Client-side computes traffic-light status + verdict + subtext «N of M signals on track» при переключении picker'а. Two-effect chart pattern (lifecycle + annotation patch) mirrors MarathonShapeWidget. `webapp/src/pages/Progress.tsx:886-1188`.
- [x] **BR-3 — Integration tests.** 12 тестов в `tests/api/test_dashboard.py::TestBikeReadiness` + 3 helper-level regression tests в `tests/mcp/test_efficiency_trend.py` (race-exclusion in `compute_efficiency_trend`, since `strict_filter=True` alone wasn't dropping `is_race=True` activities — affects BR-1, `/api/progress`, and the AI MCP tool).
- [x] **BR-4 — Empty/edge states.** «Not enough data» при insufficient signals, «No bike rides last 28 days» при пустом окне, «No valid rides» при NULL decoupling, chart hidden при полностью пустом `sport_info.ride`. Verified by `test_no_sport_info_returns_null_ctl` + `test_no_valid_rides_returns_null_decoupling`.

### Phase 1.5 punch-list

- [ ] **BR-5 — Endpoint extension.** `/api/bike-readiness` вызывает `predict_splits_with_ci(user_id, mode='today', race_date=today_iso, race_distance_ride_m=X)` для 40000 / 90000 / 180000 м sequentially (sync `_predict_one` inside — gather не даёт parallelism, см. MS §13 «Latency»). Try/except каждый — `ModelNotTrained` / `ModelBelowAcceptance` → null для дистанции. ~80 строк mirror'я MS pattern.
- [ ] **BR-6 — Response types + widget Predicted block.** `BikeReadinessResponse.predicted_power` + types в `webapp/src/api/types.ts`. Widget рендерит Power + CI под header'ом badge'а. Wide-CI footnote при spread > 20%.
- [ ] **BR-7 — ML Integration test.** Mock `predict_splits_with_ci` → endpoint собирает корректный `predicted_power` envelope, cold-start = null для одной дистанции, valid для другой.
- [ ] **BR-8 — Redis cache layer.** `_compute_bike_predicted_power` обёртка над `_predict_power_fresh`, key `bike_readiness_pred:{user_id}:{today_iso}`, TTL через `_ttl_until_midnight_local()` (reuse from MS).

---

## 13. ML-based power prediction (Phase 1.5)

### Зачем

Mirror Marathon Shape §13 pattern. Для bike атлет получает actionable pacing band: «target 240W on race-leg, CI 230-250W». Это сильнее чем «у тебя FTP 250W», потому что:

- **Personalised** через XGBoost на личной race-history (включает CTL/ATL/HRV/eFTP features).
- **CI bands** — атлет видит uncertainty.
- **Distance-aware** — predicted power для 70.3 bike-leg отличается от Olympic (более длинный → ниже target watts).

У нас уже есть `predict_splits_with_ci(sport='ride', ...)` — same pipeline что и для run, units = watts (per `_DISCIPLINE_META["ride"]`).

### Источник данных

```python
from data.ml.race_predict import predict_splits_with_ci, ModelNotTrained, ModelBelowAcceptance

# Per-distance Ride prediction. Sequential — same as MS rationale.
DISTANCE_M = {"Olympic": 40000, "70.3": 90000, "IM": 180000}
predicted_power: dict[str, dict | None] = {}

for label, dist_m in DISTANCE_M.items():
    try:
        env = await predict_splits_with_ci(
            user_id=uid,
            mode="today",
            race_date=today.isoformat(),  # см. MS §13 note про bias correction
            race_distance_ride_m=dist_m,
        )
        ride = env["splits"].get("ride")
        if ride and "pred" in ride:
            predicted_power[label] = {
                "watts": round(ride["pred"]),
                "watts_ci_low": round(ride["ci_low"]),
                "watts_ci_high": round(ride["ci_high"]),
            }
        else:
            predicted_power[label] = None
    except (ModelNotTrained, ModelBelowAcceptance):
        predicted_power[label] = None
```

**Note:** Ride возвращает `total_sec_unavailable: True` + `total_sec_reason: "power_only_phase1"` — у нас нет speed sub-model. Это OK для widget'а: показываем только watts.

**Bias correction для Ride disabled** (per `race_predict.py:328-333`): `bias_fit_method='out_of_scope'` для Ride/Swim, корректировка не применяется. Это нормально — calibration Run-only Phase 2.0β2. Watts prediction идёт raw от XGBoost.

### Response envelope

```json
{
  "weeks": [...],
  "current_components": {...},
  "predicted_power": {
    "Olympic": { "watts": 252, "watts_ci_low": 240, "watts_ci_high": 264 },
    "70.3":    { "watts": 240, "watts_ci_low": 230, "watts_ci_high": 250 },
    "IM":      { "watts": 218, "watts_ci_low": 205, "watts_ci_high": 232 }
  }
}
```

### UI rendering

Под header'ом verdict'а + subtext, перед chart'ом:

```
Predicted (70.3 bike):
  Power   240W   (CI 230 – 250W)
```

Привязано к **выбранной distance** из picker'а — переключение мгновенное, все три предсказания пришли одним response. При `predicted_power[selected] === null` — block скрывается.

Wide-CI footnote (spread > 20% от center) — «model uncertainty high, limited race history». Для bike это особенно релевантно: атлет с малым race-history имеет CI типа «200-300W» что useless без контекста.

### Edge cases (mirror MS §13)

| Случай | Поведение |
|---|---|
| `ModelNotTrained` для всех дистанций | Predicted block не рендерится, остальной widget работает |
| `ModelNotTrained` для одной distance, valid для других | Скрываем block только когда picker = эта distance |
| `ModelBelowAcceptance` | Аналогично — скрываем |
| CI bands пересекают physiological floor (`ride` floor = 50W в `_DISCIPLINE_META`) | Уже clamp'ится в `_predict_one` (`race_predict.py:357-364`) |
| User свежий, нет race history → cold-start | Все 3 = null, block скрыт. Widget работает как Phase 1 only |

### Performance / caching

- 3 sequential inference calls × ~80ms = ~240ms latency overhead. Same constraint as MS (`_predict_one` sync).
- Redis cache `(user_id, today_iso)` с TTL до полуночи Belgrade — mirror MS pattern.

---

## 14. Considered alternatives

Три подхода были рассмотрены. Этот раздел — audit trail для будущих контрибьюторов, чтобы не было соблазна «давайте лучше Bike Shape %».

### 12.1 ✅ Combo-widget (chosen)

3 независимых сигнала (Volume / Long ride / Durability), verdict по traffic-light logic. Без synthetic %.

**Trade-offs:**
- ✅ Каждая цифра имеет физическую интерпретацию. Атлет видит, где именно недотягивает.
- ✅ Никакой новой математики, переиспользуем существующие helpers (`extract_sport_ctl`, `is_valid_for_decoupling`, `compute_efficiency_trend`).
- ✅ Низкий риск: не ввели новую формулу, которую нужно валидировать.
- ❌ Нет single trend-line «насколько я в форме» как у Marathon Shape. Chart ограничен volume-метрикой (CTL_bike).

### 12.2 ❌ Bike Shape % (single synthetic number) — rejected

```
shape_pct = 100 * (0.45 * ctl_ratio + 0.30 * long_ride_ratio + 0.25 * durability_score)
```

По образцу Marathon Shape: один процент, chart 12 недель trend, шкала «70.3 = 100%».

**Trade-offs:**
- ✅ Эстетическая симметрия с Marathon Shape.
- ✅ Trend chart по одной метрике.
- ❌ **В индустрии нет прецедентов этой формулы.** Runalyze явно отказывается, TrainingPeaks / Intervals / Xert тоже нет. Не на что калибровать.
- ❌ Веса 0.45/0.30/0.25 — пальцем в небо. В Runalyze 0.67/0.33 валидированы годами на тысячах атлетов, у нас валидации нет.
- ❌ Target CTL и long ride — табличные, не функция FTP. В Runalyze всё параметризовано VO2max, here — нет аналогичного параметра.
- ❌ CTL-based shape пляшет с тренировочным циклом: в таперинге CTL падает → shape % падает, хотя атлет как раз готов. Известная проблема CTL-метрик, не решена.

**Почему отвергнут:** низкая валидируемость + риск дать атлету misleading цифру, которая снижается на таперинге.

### 12.3 ❌ Hybrid (combo + synthetic %) — rejected

Combo-widget из 12.1 плюс одна цифра Bike Shape % из 12.2 наверху, chart по Shape %.

**Trade-offs:**
- ✅ Best of both: видно и общую цифру, и breakdown.
- ❌ Все минусы 12.2 остаются (синтетика, веса с потолка).
- ❌ UX-сложность: «у меня CTL ок и long ride ок, durability ок, а Bike Shape 78% — почему?» — расходящиеся подписи путают атлета.
- ❌ Двойное обслуживание: и cards, и %. Любая доработка метрики — пересмотр обоих представлений.

**Почему отвергнут:** добавляет cognitive load без новой информации.

---

## 15. Architectural decisions — standing audit trail

> Standing decisions, зафиксированные при первой итерации спеки (2026-05-14). В отличие от §14 (rejected approaches) этот раздел документирует **принятые** internal choices с rationale, чтобы при будущих pull requests разработчики не переоткрывали уже закрытые вопросы.
>
> Аналог §14 в MS-спеке (где decisions были post-divergence). Здесь — proactive audit, для bike нет upstream-reference чтобы расходиться, но **внутренние** trade-off'ы есть.

| ID | Decision | Date | Rationale |
|---|---|---|---|
| **B1** | **Traffic-light verdict, no synthetic %** | 2026-05-14 | No industry precedent (§1 stance). Weighted-combo формула требует калибровки которой не на что опираться. Three independent signals + multi-state verdict дают больше actionable info чем single %. |
| **B2** | **Table targets, not FTP-parameterised** | 2026-05-14 | В литературе для bike-CTL точной формулы `f(FTP, age)` нет. Friel/Coggan приводят bands, не функции. Table 35/50/80 — calibrated empirical для **solid AG-athlete** (top 30-50% AG, не finisher, не elite), см. §3.1 caveat. Phase 2 option: parameterise через per-athlete historical bike-share of TSS если обнаружим drift на other brackets. |
| **B3** | **Long ride: 28d window, single-best** | 2026-05-14 | Один валидный long ride демонстрирует physical capability. 28d покрывает monthly cycle + tolerance. Не требует N-sample median — long ride binary «сделал хотя бы раз». |
| **B4** | **Durability: 84d window, median of 5** | 2026-05-14 | Decoupling — стабильность через trend, требует sample stability. `is_valid_for_decoupling` фильтр строгий (ride ≥60min + VI ≤1.10 + Z1+Z2 >70%), у атлета 1-2 valid rides per 28d window — noisy median. 84d даёт реалистичный 5-sample horizon. |
| **B5** | **Olympic distance kept (informational only)** | 2026-05-14 | Threshold 1.5h не дискриминирует (любой consistent rider clearит). Оставляем в picker'е для completeness виджета — другие атлеты могут гоняться Olympic. Если feedback что misleading — drop в Phase 2 (см. §3.2 disclaimer). |
| **B6** | **Verdict subtext «N of M signals on track»** | 2026-05-14 | 4-state verdict (Ready/Almost/Building/Insufficient) огрубляет YYY vs GGY (оба → almost). Subtext gradient information без extra verdict state — UX simplicity сохранена. |
| **B7** | **EF trend surface as supplementary, not signal** | 2026-05-14 | EF trend (от `compute_efficiency_trend`) — долгосрочное aerobic improvement. **Не** включать в traffic-light decision (durability — durability сегодня, EF — несколько месяцев). Render как sub-line под Durability card, не дублирует pipeline. |
| **B8** | **Race-effort excluded from all signals** | 2026-05-14 | Race ≠ training base (consistent c MS §7 до Phase 1.6). Bike-races редкие, инфлюенс на 28d/84d медиану марginal. **Note:** если в Phase 2 MS-style alignment придёт идея «mirror Runalyze includes races» — для bike нет upstream, decision stays. Можем пересмотреть если найдём industry argument. |
| **B9** | **Phase 1.5 ML Predicted Power** | 2026-05-14 | Symmetric MS Phase 1.5. `predict_splits_with_ci(sport='ride')` уже в pipeline, copy pattern. Actionable: race-day pacing band «target watts CI». Single value-add над industry baseline. |

**Когда возвращаться к этому разделу:**
- New PR хочет изменить thresholds (5%/10% decoupling, 0.80 ratio) → проверить B-row, документировать new decision если меняется.
- New PR хочет добавить 4th signal (HRV bike-specific? Power curve?) → добавить B-row, не пересматривать существующие.
- New Phase wants synthetic % → revisit §14 (Considered alternatives) **и** B1.

---

## 16. Related

- [`MARATHON_SHAPE_SPEC.md`](MARATHON_SHAPE_SPEC.md) — run-side parallel, similar layout pattern, тот же подход «pure helpers + REST + widget».
- [Runalyze docs — нет аналога для bike](https://runalyze.com/help/article/marathon-shape) — Runalyze явно ограничен бегом.
- `webapp/src/pages/Progress.tsx:77` — placement target (после `ProgressionWidget`).
- `data/utils.py:81` — `extract_sport_ctl(sport_info)` per-sport CTL extractor.
- `data/metrics.py:594` — `is_valid_for_decoupling(...)` filter contract.
- `mcp_server/tools/progress.py:99` — `compute_efficiency_trend(user_id, sport, ...)` reusable helper для `ef_trend`.
- `api/routers/dashboard.py:468` — `/api/marathon-shape` endpoint как референс для week-bucket pattern.
- `data/db/activity.py:542-545` — `ActivityDetail.variability_index`, `efficiency_factor`, `decoupling`, `hr_zone_times`.
