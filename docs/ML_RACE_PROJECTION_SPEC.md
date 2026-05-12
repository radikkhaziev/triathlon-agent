# ML Race Projection Spec

> Прогноз гоночной производительности в двух режимах:
> **(1) «Race today»** — из текущей формы предсказываем сплиты по дисциплинам;
> **(2) «Race day»** — экстраполируем фитнес к дате старта и предсказываем
> сплиты для race day, включая сценарии типа «пропуск 2 недель» (Phase 2).
>
> Актуализация issue [#64](https://github.com/radikkhaziev/triathlon-agent/issues/64)
> по состоянию на 2026-04-20.

**Related:**

| Issue / Spec / code | Связь |
|---|---|
| [#64](https://github.com/radikkhaziev/triathlon-agent/issues/64) | Основной трекер |
| [#63](https://github.com/radikkhaziev/triathlon-agent/issues/63) / `docs/ML_HRV_PREDICTION_SPEC.md` | HRV prediction spec — prerequisite для shared ML infra (`ml/features.py`, `ml/predict.py`) |
| `data/db/fitness_projection.py` | **Уже работает**: CTL/ATL/rampRate кривая из `FITNESS_UPDATED` webhook, даты могут быть в будущем (CLAUDE.md:57) |
| `api/routers/intervals/webhook.py` | `FITNESS_UPDATED` dispatcher — источник projection |
| `GET /api/fitness-projection` | Уже есть эндпоинт, уже дёргается из webapp Dashboard |
| `data/db/athlete.py:AthleteGoal` | Race date + `ctl_target` (local-only overlay, `upsert_from_intervals` не трогает) |
| `data/db/activity.py`, `activity_details.py` | Source для per-discipline performance features |
| `data/db/race.py` | `races` таблица — 22 записи у user 1 (mostly Run) |
| `docs/BUSINESS_RULES.md:53` | Banister recovery model (наш, НЕ тот Banister что из issue — просто confusing terminology) |

---

## 1. Мотивация

У атлета есть цель — Ironman 70.3 Belgrade 15 сентября 2026. В `athlete_goals`
висит `ctl_target=75`. Сейчас 20 апреля — 148 дней до старта. CTL сейчас 21.

Вопросы, на которые атлет хочет ответы:

1. **«Если бы гонка была сегодня — что я покажу?»** Быстрый snapshot текущей
   формы в гоночных сплитах. Полезно для тестовых гонок, check-in'ов, и чтобы
   понимать дистанцию между текущей формой и целевой.
2. **«Если я продолжу по плану — какой финиш-тайм ожидать 15 сентября?»**
   Прогноз на race day с учётом ожидаемого роста CTL.
3. **«А что будет если я пропущу 2 недели в июле из-за поездки?»** Scenario —
   как меняется прогноз при отклонениях от плана (Phase 2).

Сейчас этого нет. Intervals.icu даёт `fitness_projection` (CTL/ATL на будущее),
но переводить в конкретные сплиты — наша задача. Race times калькуляторы (например,
Runner's World / Jack Daniels) работают по формулам на основе одной тестовой
дистанции и не учитывают recovery/HRV/sleep состояние.

---

## 2. Scope

### Phase 1 (MVP, только user 1)

- **Mode 1: «Race today»** — state → predicted splits для Run/Ride/Swim.
- **Mode 2 basic: «Race day»** — из `fitness_projection` берём `CTL(race_date)`,
  подставляем в ту же performance модель вместо current CTL → получаем сплиты.
  Без scenario engine.
- **Performance regression per discipline:**
  - Run: predicted pace @ target HR (median из race pacing history).
  - Ride: predicted avg power, predicted avg speed.
  - Swim: predicted pace per 100m.
- **MCP tool `get_race_projection(mode, race_date, target_hr, ...)`** — возвращает
  структурный JSON со сплитами + uncertainty (§9).
- **Confidence intervals** через bootstrap residuals на train-выборке.
- **Delivery:**
  - Текст в ответе Claude: «Если бы гонка была сегодня, ожидаемый Swim 2:10/100m,
    Bike 32 km/h @ 180W, Run 5:30/km @ HR 145. Range ±…».
  - Phase 1 без chart — для MVP достаточно текста.

### Phase 2 — по запросу

- **Scenario engine** — «miss 2 weeks», «+10% volume», «custom CTL target».
  Требует нашего Banister-решения поверх `fitness_projection`, потому что
  Intervals экстраполирует из текущего календаря и не умеет «а что если».
- **Chart в webapp**: CTL trajectory с overlay нескольких сценариев +
  predicted splits table.
- **Ride/Swim race-specific калибровка** — когда в `races` накопится ≥10
  non-Run записей (сейчас: Ride 2, Swim 1).
- **Cross-athlete pool model** — общая регрессия на нескольких юзерах,
  warm-start per-user.

### Non-goals

- Собственный Banister impulse-response model (Busso/Clarke-Skiba). Intervals.icu
  уже даёт projection через `FITNESS_UPDATED` webhook — используем его, не
  переизобретаем.
- Total finish time через транзишны. Возвращаем per-discipline сплиты; финальное
  время собирать в webapp/UI со ссылкой на пользовательскую оценку транзишнов.
- Neural networks — данные не поддерживают, intuition через XGBoost + SHAP важнее.
- Кросс-race-type generalization (same model for sprint / 70.3 / full IM) — race
  distance как feature, но явно не экстраполируем за пределы дистанций, видных
  в обучающих данных.

---

## 3. Что изменилось vs исходный issue #64

| Компонент в issue #64 | Статус | Комментарий |
|---|---|---|
| Banister impulse-response model (наша реализация) | ❌ заменено | `fitness_projection` таблица + `FITNESS_UPDATED` webhook уже работают |
| Mode 2 hybrid (Banister + ML) | 🟡 упрощено | Теперь «fitness_projection → ML», без наших impulse-response формул |
| Mode 1 (pure ML, state → performance) | 🟢 ready | Для Run — данных хватает, Ride/Swim — race-specific data мало |
| Scenario engine (miss 2w / +10% volume) | ⏸ отложено в Phase 2 | Требует hypothetical CTL calculation поверх existing projection |
| Per-discipline models (swim/bike/run) | 🟢 будем делать | Отдельно для Run/Ride/Swim, не multi-output |
| HumanGO source of plan | ❌ переопределено | У нас не HumanGO, а `ai_workouts` + Intervals calendar + ATP |
| MCP tool `get_race_projection` | ⏳ новый | Часть Phase 1 |
| CTL trajectory chart | ⏸ Phase 2 | В MVP текст, визуал — после feedback |
| Confidence intervals | ⏳ новый | Bootstrap residuals |

---

## 4. Available data (на 2026-04-20, user 1)

| Источник | Объём | Использование |
|---|---|---|
| `fitness_projection` | 110 rows, 2026-04-16 → 2026-10-11 | Mode 2 input — CTL/ATL на race day |
| `wellness` | ~800 дней (с 2023-09) | State features (CTL/ATL/TSB/HRV/RHR/sleep) |
| `activities` Run | 417 | Run performance regression (pace @ HR) |
| `activities` Ride | 186 | Ride performance regression (power, speed) |
| `activities` Swim | 145 | Swim pace regression |
| `activity_details` | ≥1 per activity | Zones, EF, decoupling, intervals |
| `races` | 22 (19 Run / 2 Ride / 1 Swim) | Race-specific calibration — только Run в MVP |
| `athlete_goals` | 1 upcoming RACE_A | Target race date + `ctl_target` |

**Критическое ограничение:** race-specific data у user 1 сильно скошен в Run.
Для Ride/Swim в Phase 1 строим модель на **всех** activities (не только race),
выделяя high-intensity как прокси для race pacing. Это даёт систематический bias
(race pace обычно агрессивнее training), но пока достаточно для MVP.

Для user 2/5/6 — данных недостаточно. Cold-start fallback: tool возвращает
`{"available": False, "reason": "insufficient_data"}`.

---

## 5. Architecture

### 5.1. Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Training (weekly, общий с HRV retrain actor — один job, два артефакта) │
│                                                                         │
│   cli train-race-models <user_id>                                       │
│       ↓                                                                 │
│   ml.race_features.build_dataset(user_id, discipline) → DataFrame       │
│       ↓                                                                 │
│   XGBRegressor per discipline → MAE, R² в Sentry                        │
│       ↓                                                                 │
│   Bootstrap residuals → confidence band (500 resamples)                 │
│       ↓                                                                 │
│   joblib.dump({model, explainer, residuals}, "ml/models/race_{user}_{discipline}.joblib") │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  Inference (MCP tool call)                                              │
│                                                                         │
│   get_race_projection(mode, race_date, target_hr, race_distance_m)      │
│       ↓                                                                 │
│   Mode 1 (today):                                                       │
│     state = build_state_row(user_id, today)                             │
│   Mode 2 (race_day):                                                    │
│     projected_ctl = fitness_projection.get(user_id, race_date).ctl      │
│     state = build_state_row(user_id, today) + override(ctl=projected_ctl) │
│       ↓                                                                 │
│   for discipline in (Run, Ride, Swim):                                  │
│     model, residuals = load(race_{user}_{discipline}.joblib)            │
│     pred = model.predict(state, target_hr, distance)                    │
│     ci = (pred + percentile(residuals, 5), pred + percentile(resid, 95))│
│     splits[discipline] = {"value": pred, "ci_low": ..., "ci_high": ...} │
│       ↓                                                                 │
│   return structured JSON (§9.2)                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2. Module layout

Файлы кладём в **`data/ml/`** (existing convention — `data/ml/progression.py` shipped в TRAINING_PROGRESSION 2026-04-19, см. `bot/scheduler.py:actor_retrain_progression_model`). Модели — в **`static/models/`**, как у progression (`static/models/{user}_{discipline}_{date}.joblib`). HRV spec, когда стартует, подцепит общие helpers (`build_state_row`) рядом — отдельной root-папки `ml/` не заводим.

```
data/ml/
├── __init__.py
├── progression.py          # existing — TRAINING_PROGRESSION (Ride EF model)
├── race_features.py        # NEW — performance regression features (per-discipline)
├── race_train.py           # NEW — XGBRegressor per discipline + bootstrap residuals
├── race_predict.py         # NEW — predict_splits_with_ci()
└── (features.py / predict.py)  # будущая HRV spec расширит этим же namespace

static/models/
├── 1_ride_2026-04-19.joblib       # existing progression model
├── race_{user_id}_run.joblib      # NEW
├── race_{user_id}_ride.joblib     # NEW
└── race_{user_id}_swim.joblib     # NEW
```

### 5.3. Почему per-discipline, не multi-output

- Разная целевая метрика (pace sec/km для Run, watts для Ride, pace sec/100m для Swim).
- Разная физиология (HR-driven для Run, power-driven для Ride).
- Разные размеры датасетов (Run 417 vs Swim 145) — multi-output усреднит
  качество по наихудшему.
- Per-discipline даёт чистую диагностику: «Run модель MAE 8 sec/km, Ride MAE 12W,
  Swim неуверена» — видно где слабое звено.

---

## 6. Feature engineering (performance regression)

### 6.1. State features (общие для всех дисциплин)

Из `wellness` на день активности (или для предикции — на `target_date`):

- `ctl`, `atl`, `tsb` — глобальные (по всем видам спорта).
- **`ctl_run`, `ctl_ride`, `ctl_swim` — per-sport load split.** ⚠ **Не** из `wellness.sport_info` — webhook `sportInfo` содержит только `{type, eftp, wPrime, pMax}`, **без per-sport CTL** (подтверждено в `INTERVALS_WEBHOOKS_RESEARCH.md` §WELLNESS_UPDATED payload sample). Intervals.icu показывает per-sport CTL в своём UI, но через webhook/REST API не отдаёт. **Считаем сами:** helper `data/ml/race_features.py:_compute_sport_ctl_series(activities_df, sport, tau=42)` — pandas-batch EMA над `icu_training_load` с фильтром `type=sport`, возвращает date-indexed series. Вызывается один раз при построении dataset / inference row над предварительно загруженной DataFrame всех активностей (избегаем N+1 SQL для каждой исторической строки). Для **per-discipline predict**'а per-sport CTL важнее глобального: Run pace зависит от run-specific нагрузки, не от того что атлет много плавал.
- `hrv_ln_rmssd`, `rhr`
- `sleep_score_7d_mean`, `stress_avg_7d_mean` (из Garmin)
- `recovery_score`
- **`compliance_28d_mean`** — средний `training_log.compliance` за последние 28 дней. Проверка «качества» тренировочного плана: высокий CTL при compliance 40% (половину пропустил) = CTL накачан compensating hard workouts, худшее качество базы. Для race-pace предиктор существенный.

### 6.2. Discipline-specific features

**Run (regression on `avg_pace` in sec/km):**

- `target_hr` (ENUM: input parameter, not activity field)
- `distance_m` (фича — модель знает что pace меняется с дистанцией)
- `elevation_per_km` — из `activity_details.elevation_gain / distance * 1000`
- `surface` — categorical (`trail` / `road`), эвристика по `type=TrailRun` vs `Run`
- `cumulative_distance_90d` — сумма distance за 90 дней (training volume proxy)
- `recent_fast_runs_14d` — count of runs in top pace quartile за 14 дней

**Ride (regression on `avg_power` in watts, отдельно `avg_speed` в km/h):**

- `target_hr`
- `distance_m`
- `elevation_per_km`
- `cumulative_tss_ride_90d`
- `recent_high_power_rides_14d`
- `is_indoor` — boolean (trainer vs outdoor).
- **`current_eftp`** — из `wellness.sport_info[type=Ride].eftp` на текущую дату (для Mode 1) или на race date (для Mode 2 — берётся из `fitness_projection.sport_info`, см. §8). Это **ceiling для sustainable power**. В 70.3 Bike leg (2h51m) целевая мощность — 85-88% от eFTP. Намного прямее предсказывает race power, чем CTL.
- **`critical_power`, `w_prime`, `p_max`** — из атомарных колонок `athlete_settings.{critical_power, w_prime, p_max}` (`data/db/athlete.py:83-85`; в БД они уже развёрнуты в отдельные столбцы, не JSON-blob). Заполняются `SPORT_SETTINGS_UPDATED` webhook'ом, см. research §SPORT_SETTINGS_UPDATED. Критическая мощность (CP) — физиологически обоснованный predictor sustainable power на длинные дистанции (>20 min), лучше FTP. W' — анаэробный запас (Дж), определяет допустимые supra-CP усилия на подъёмах. pMax — пиковая мощность, для sprint-distance и первых минут.
- **`ftp_delta_30d`** — `current_ftp - ftp_30_days_ago`. Растёт ли FTP за окно. Derived из `athlete_settings` history (или из `activity_details.rolling_ftp` если хранится).

**Swim (regression on `pace_per_100m` in sec):**

- `target_hr` (опционально — swim pace often CSS-driven, not HR)
- `distance_m`
- `is_pool` (vs open water) — из `type=Swim` + manual heuristics
- `cumulative_swim_distance_90d`

### 6.3. Target construction

**Train target — avg pace/power from non-race activity, filtered:**

- Exclude warmup/cooldown: use `activity_details.intervals` если есть, иначе
  берём avg по всей activity но только если `moving_time >= 25 min` (короткие
  не репрезентативны).
- Exclude low-intensity recovery via combined helper `is_run_recovery_jog(zones, tss)`
  — Z1 ≥ 70% **AND** TSS < 40. Zone-only filter ломал pro-атлетов со
  структурным 80/20 base; TSS gate отличает 25-min recovery jog от 90-min
  base session. Server-side фильтр: Phase 1.5 reality calibration в
  `docs/IMPLEMENTATION_STATUS.md`. **Phase 1.6 (см. §6.4)** заменяет live-check
  на persisted `activities.noise_reason` tag, классифицируемый при webhook'е;
  live-check сохраняется как fallback для legacy строк без `noise_scored_at`.
- Для Run: exclude trail if `type=Run` (road), и наоборот — не смешивать.

**Athlete-side data hygiene.** Server-side фильтрация ловит явный мусор (recovery
jogs, trail-mixed), но качество данных в итоге определяется поведением атлета —
тэги Intervals, выбор HR-strap, дисциплина регулярных steady-state сессий.
Подробно: `docs/knowledge/training-data-hygiene.md`. Knowledge-док rendered into
AI training-skill context, чтобы Claude мог сказать атлету «у тебя 4 прогулки за
неделю помечены как Run — это шумит ML и мои prediction'ы».

### 6.4. Webhook-time noise classification (Phase 1.6)

Phase 1.5 фильтр в `race_features.py` запускается каждый Sunday retrain'е —
каждый раз re-checking ~365 дней истории. Phase 1.6 переносит классификацию в
**webhook-time**: при `ACTIVITY_UPDATED` (после Intervals'овской аналитики, когда
zone_times и tss уже есть) считаем `noise_reason` один раз и пишем в
`activities.noise_reason`. Plus делает signal видимым downstream: Claude в чате,
ATP compliance, webapp activity card. Архитектура **гибрид** — persisted tag is
authoritative когда есть, live-check как fallback для legacy строк.

#### 6.4.1. Schema

```python
# activities table
noise_reason: Mapped[str | None]        # nullable, no DB-level CHECK
noise_scored_at: Mapped[datetime | None]  # disambiguator для not-yet-classified
```

Composite index `(user_id, type, noise_reason)` — third column оптимизирован
под Phase 2 поверхности (chat/UI с `WHERE noise_reason IS NOT NULL`), а не под
текущий retrain query (он не WHERE-фильтрует на noise_reason — пулит всё,
фильтрует в Python). **TODO** перед Phase 2 surface ship'ом: либо рерайтить
filter в SQL чтобы воспользоваться третьей колонкой (`AND noise_reason IS NULL`),
либо менять index на `(user_id, type, start_date_local)` если Phase 2 surface
так и не материализуется и retrain хочет O(log) на cutoff lookup.

**Three-state semantics:**

| `noise_reason` | `noise_scored_at` | Meaning |
|---|---|---|
| `NULL` | `NULL` | Not classified yet (legacy / pre-backfill / webhook race) |
| `NULL` | `<dt>` | Classified, clean signal — kept by ML |
| `'run_*'` / `'ride_*'` | `<dt>` | Noise — dropped from ML train-set |

Three states важны: SQL `noise_reason IS NOT NULL` = «весь шум»; differentiation
between unscored и scored-clean позволяет `race_features.py` падать на live-check
fallback только для unscored строк.

**Почему TEXT + Python Literal, не PG ENUM:** добавление нового enum value
(Phase 2 Ride classifiers) не требует DDL migration. Trade-off: schema-level
validation теряется, но Python boundary (`classify_noise()` typed Literal + DTO
validation) надёжнее enough.

#### 6.4.2. Enum values

**Phase 1.6 (ship):**

```python
NoiseReason = Literal["run_recovery_jog", "run_walk"]
```

- `run_recovery_jog` — Z1 ≥ 70% AND TSS < 40 (existing Phase 1.5 logic, перенесена).
- `run_walk` — pace > `threshold_pace × 1.6` AND avg_hr < `lthr × 0.65`.

**Phase 2 (после empirical calibration на тех же 5 атлетах):**

```python
# add: "ride_recovery_spin", "ride_commute", "ride_indoor_test"
```

- `ride_recovery_spin` — Z1 ≥ 70% AND TSS < 30.
- `ride_commute` — avg_speed < 22 km/h AND duration < 45 min (calibrate before ship).
- `ride_indoor_test` — is_indoor=True AND duration < 45 min AND has_max_effort_interval.

**Phase 3 / deferred:**

- `optical_hr_noisy` — нужен device-strap metadata из FIT files (не парсим сейчас).
- `swim_*` — n слишком маленький у топ-атлета (44 у user 1); фильтр на small-n
  убирает легитимные данные.

**Convention — sport-prefix mandatory.** Bare `recovery_jog` исключён: когда
`ride_recovery_spin` добавится, sport-prefix даёт self-documenting grep'абельность
(`LIKE 'run_%'`) и тривиальный i18n key derivation (`noise.{value}.label_{ru,en}`).

#### 6.4.3. Per-athlete thresholds

Принцип: **personalized baseline × fixed multipliers**. Baseline — `athlete_settings.run.{lthr, threshold_pace}` (уже синкаются с Intervals через `SPORT_SETTINGS_UPDATED` webhook). Multipliers — global constants в `data/ml/noise_classifier.py`, не per-user (избегаем drift; recalibration = code change + re-run backfill).

```python
WALK_PACE_MULT = 1.6   # pace slower than 1.6× threshold_pace
WALK_HR_MULT   = 0.65  # avg HR below 0.65× LTHR
```

**Worked examples:**

| Athlete | threshold_pace | LTHR | pace_floor | hr_ceil |
|---|---|---|---|---|
| Sub-3 marathoner | 3:30/km | 178 | 5:36/km | 116 |
| Mid-pack | 4:30/km | 170 | 7:12/km | 110 |
| 60yo athlete | 5:00/km | 158 | 8:00/km | 103 |

Y sub-3'a recovery jog 6:00/km @ HR 130 НЕ попадёт под walk (HR 130 > 116);
walk-with-dog 7:30/km @ HR 100 → попадёт. У 60yo recovery jog 7:30/km @ HR 120 → НЕ попадёт (HR 120 > 103); 8:30/km @ HR 95 → попадёт.

**Fallback** для атлетов без synced settings (`threshold_pace IS NULL` или `lthr IS NULL`): global constants `pace_floor=6:30/km`, `hr_ceil=120`. Новые атлеты обычно подцепляют settings в течение первых дней через webhook, fallback закрывает onboarding window.

`run_recovery_jog` остаётся как есть — Z1 уже personalized через athlete zone definitions (Z1 определяется относительно LTHR).

#### 6.4.4. Priority order

`classify_noise()` проверяет в строгом порядке, возвращает первое попадание:

```python
def classify_noise(act, thresholds) -> NoiseReason | None:
    if is_run_walk(act, thresholds):
        return "run_walk"            # mistagged sport — самая severe
    if is_run_recovery_jog(act):
        return "run_recovery_jog"    # legit recovery, но noise для ML
    return None                       # clean signal
```

**Почему `run_walk` > `run_recovery_jog`:** walk-paced low-HR Run — это mistagged
sport (структурно walk), не training. Recovery jog — legit но low-signal training.
Если оба триггерятся, severe категория идёт первой для accurate downstream
classification.

#### 6.4.5. Trigger point

Trigger: `tasks/actors/activities.py:actor_update_activity_details` — **после**
`ActivityDetail.save(...)` коммита, в той же sync-сессии. Это правильный момент
потому что:

1. **`hr_zone_times` и `pace` живут в `ActivityDetail`, не на `Activity`.**
   Webhook `ACTIVITY_UPLOADED` несёт только Activity-level поля (avg_hr, tss,
   moving_time, type) — zone times появляются после `client.get_activity_detail()`
   в actor'е. Поэтому хук в `_dispatch_activity_updated`/`_dispatch_activity_uploaded`
   рано — там `hr_zone_times=None` всегда.
2. **Actor уже sync с готовой сессией.** Не нужно отдельной транзакции —
   `Activity.set_noise_classification(..., session=session)` пишет в той же.
3. **`is_changed` guard.** Actor возвращается рано при `not result.is_changed and not force`,
   и noise re-classification тоже скипается — если ActivityDetail не поменялась,
   zones/pace те же → noise_reason тот же.

```python
# tasks/actors/activities.py:actor_update_activity_details (excerpt)
with get_sync_session() as session:
    result: ORMDTO = ActivityDetail.save(activity_id, detail_data, intervals_data, session=session)
    if not result.is_changed and not force:
        return
    activity_row: Activity = session.get(Activity, result.row.activity_id)

    # Defense-in-depth tenant guard — `session.get(Activity, ...)` is a PK-only
    # lookup, so a forged / replayed Dramatiq message with another tenant's
    # activity_id would land us reading the wrong tenant's thresholds. Same
    # pattern as `_actor_send_activity_notification` (~line 488 in the file).
    if activity_row is None or activity_row.user_id != user.id:
        logger.warning("noise classify: tenant mismatch — skip"); return

    # Noise classification — runs only when ActivityDetail actually changed.
    # Always pass `user.id` (the dispatched tenant), never `activity_row.user_id` —
    # invariant enforced by the guard above. `set_noise_classification` doesn't
    # commit internally; caller (this block) commits at the end.
    thresholds = AthleteSettings.get_thresholds(user.id, session=session)
    reason = classify_activity_row(activity_row, result.row, thresholds)
    Activity.set_noise_classification(
        user.id, activity_row.id,
        reason=reason, scored_at=datetime.now(timezone.utc), session=session,
    )
    session.commit()
```

`classify_activity_row(activity, detail, thresholds)` — convenience-обёртка над
`classify_noise(...)` в `data/ml/noise_classifier.py`. Derives `avg_pace_sec_per_km`
из `moving_time / (distance / 1000)` — та же формула что в `race_features.py:486`.

**Tenant guard — defense-in-depth.** Two layers: (1) explicit equality check
`activity_row.user_id != user.id → return` (above), (2) WHERE-clause scoping
in `Activity.set_noise_classification` (foreign activity_id under our user_id
→ 0 rows updated, silent no-op). Both fire for the same threat (replayed
Dramatiq message with foreign activity_id), but the first one prevents the
preceding work (thresholds fetch + classify call) from running with cross-tenant
context. Standard pattern из CLAUDE.md «MT Phase 1.3» + `_actor_send_activity_notification`.

**Commit ownership.** `set_noise_classification` is sync-only (`@with_sync_session`),
doesn't commit internally — caller decides transaction boundary. Actor commits
once per activity here; backfill CLI (§6.4.7) commits once per athlete (batched
across N activities → 1 round-trip instead of N).

**Idempotent:** force-rerun actor'а пересчитает reason с теми же входами →
тот же результат. `noise_scored_at` обновляется каждый раз — это disambiguator
для backfill, не indicator изменения.

**ACTIVITY_UPDATED branch.** `_dispatch_activity_updated` (rename / metadata
обновление) НЕ вызывает `actor_update_activity_details` — zone/pace данные
не меняются на rename'е, переклассифицировать нечего. Если Intervals когда-нибудь
шлёт `ACTIVITY_ANALYZED` (re-analysis) — добавим trigger там же.

#### 6.4.6. Read-side integration

`data/ml/race_features.py:build_dataset` приоритезирует persisted tag:

```python
# Simplified — actual impl handles NaN/empty-string defensively:
# `isinstance(noise_reason, str) and noise_reason.strip()` (see race_features.py)
if act["noise_reason"]:
    continue                                          # persisted noise → drop

# Legacy fallback for rows scored before Phase 1.6
if pd.isna(act["noise_scored_at"]) and is_run_recovery_jog(zones, tss):
    continue

# Otherwise: classified clean (or unscored non-Run), keep
```

После backfill'а (см. §6.4.7) fallback ветка не должна срабатывать в normal flow.
Оставляем как safety net + для disaster recovery (если backfill упал на каком-то юзере).

Также экспортируем `is_run_recovery_jog` из `noise_classifier.py` — единый source of
truth, `race_features.py` импортирует, не дублирует.

#### 6.4.7. Backfill

CLI: `python -m cli classify-noise [--user-id=N] [--since-days=365] [--dry-run]`.

- `--since-days=365` default — соответствует `RACE_FEATURE_WINDOW_DAYS`; старее
  не влияет на retrain.
- `--user-id` опциональный; без него обходит всех `is_active=True` атлетов.
- Per-user `try/except` + `sentry_sdk.capture_exception` — один битый user не валит батч.
- `--dry-run` — печатает counts без UPDATE.

Output:
```
user 1:   classified=287 (walk=12, recovery_jog=89, clean=186)
user 14:  classified=153 (walk=3,  recovery_jog=8,  clean=142)
...
```

Backfill — manual operation, не на deploy. Сценарий: добавили `ride_recovery_spin`
в Phase 2 → `python -m cli classify-noise --since-days=365` для всех → новый
weekly retrain видит persisted tags.

#### 6.4.8. Phase 1.6 acceptance

См. §14 «Phase 1.6 — webhook-time noise classification».

**Inference target — race pacing, не training pacing:**

Это фундаментальная асимметрия: модель учится на training activities, а
предсказывает race. Компенсация через:

- `is_race` feature в train (19 Run races у user 1 — достаточно) + inference
  set at `True`.
- Пока не накопится race-data для Ride/Swim — race inference на non-race модели
  с системным negative offset (конкретный процент подбираем на Run и
  переиспользуем как эвристику).

---

## 7. Mode 1: Race today

### 7.1. Flow

1. Юзер: «Если бы гонка была сегодня — что покажу на Ironman 70.3 дистанции?»
2. Claude вызывает `get_race_projection(mode="today", race_distance_swim=1900,
   race_distance_ride=90000, race_distance_run=21000, target_hr_ride=150, target_hr_run=160)`.
3. Tool:
   - Собирает `state_row` из текущего wellness.
   - Для каждой дисциплины грузит `race_{user}_{discipline}.joblib`.
   - `predict(state + distance + target_hr, is_race=True)`.
   - Bootstrap CI из сохранённых residuals.
4. Return:
   ```json
   {
     "mode": "today",
     "race_date": "2026-04-20",
     "current_ctl": 21.0,
     "splits": {
       "swim": {"pace_per_100m_sec": 130, "total_sec": 2470, "ci_low": 2340, "ci_high": 2610},
       "ride": {"avg_power_w": 170, "avg_speed_kmh": 31.5, "total_sec": 10290, "ci_low": 9950, "ci_high": 10600},
       "run":  {"pace_per_km_sec": 340, "total_sec": 7140, "ci_low": 6900, "ci_high": 7420}
     },
     "estimated_finish_time_sec": 19900,  // sum, без transitions
     "finish_time_formatted": "5h31m",
     "notes": "Transitions not included. Uncertainty reflects training variance, not race-day conditions."
   }
   ```

### 7.2. Что Claude возвращает юзеру

Claude из tool-result'а делает текст примерно так:

```
При текущем CTL 21 и HRV 45 ожидаемое время на Ironman 70.3:
• Swim 1.9 km — ~41 мин (2:10/100m) ±2 мин
• Bike 90 km — ~2h51m (170W, 31.5 km/h) ±5 мин
• Run 21 km — ~1h59m (5:40/km) ±4 мин

Total (без транзишн): ~5h31m. До цели CTL 75 остаётся 54 пункта — хватит на
улучшение ~30 мин в Bike + ~15 мин в Run при текущем плане.
```

Финальная фраза — логический мостик к Mode 2.

---

## 8. Mode 2: Race day forecast

### 8.1. Flow

1. `get_race_projection(mode="race_day", race_distance_*, target_hr_*)`.
2. Tool берёт `race_date` из `AthleteGoal.get_goal_dto(user_id).event_date`
   (если явно не указан).
3. Из `fitness_projection` достаёт CTL/ATL **и per-sport eFTP** на ту дату:
   ```python
   row = await FitnessProjection.get(user_id, race_date)
   projected_ctl, projected_atl = row.ctl, row.atl
   # sport_info — JSON, содержит [{type: "Ride", eftp: ..., wPrime: ..., pMax: ...}, ...]
   projected_eftp_ride = row.sport_info_by_type("Ride", "eftp")
   projected_eftp_run  = row.sport_info_by_type("Run",  "eftp")  # если есть
   ```
   См. `docs/INTERVALS_WEBHOOKS_RESEARCH.md` §FITNESS_UPDATED — `sportInfo` array приходит в каждом record'е projection.
4. `state_row = build_state_row(user_id, today)` — **все фичи из сегодня**, с заменами под race day:
   - `ctl`, `atl`, `tsb` → projected values.
   - `ctl_run`, `ctl_ride`, `ctl_swim` → **proportionally scale** текущие per-sport CTL по отношению `projected_global_ctl / current_global_ctl`. Webhook `fitness_projection.sport_info` не содержит per-sport CTL (только eFTP/W'/pMax), см. §6.1 note.
   - `current_eftp` (Ride feature §6.2) → `projected_eftp_ride`.
   - `critical_power`, `w_prime`, `p_max` → **остаются сегодняшними** (MMP model из `athlete_settings`, Intervals не прогнозирует их на будущее).
   - Wellness / HRV / sleep — **тоже сегодняшние** (прогнозировать их на 5 месяцев бессмысленно, а CI §10.2 уже инфлятит uncertainty).
5. Далее как в Mode 1: `predict` per discipline + bootstrap CI (с inflation).

### 8.2. Что важно для юзера увидеть

- Projected CTL on race day (e.g. 72 если план идёт) vs текущий 21.
- Dates to race: 148 дней.
- Splits с СИЛЬНО более широкими CI, чем Mode 1 (далеко в будущем → больше
  uncertainty). Это покажется из bootstrap на validation residuals.

Пример return:

```json
{
  "mode": "race_day",
  "race_date": "2026-09-15",
  "days_to_race": 148,
  "current_ctl": 21.0,
  "projected_ctl": 72.0,
  "projected_atl": 68.0,
  "projected_tsb": 4.0,
  "projected_eftp": {"ride": 225, "run": null, "swim": null},
  "current_eftp": {"ride": 208, "run": null, "swim": null},
  "splits": { ...как в Mode 1 но с projected state... },
  "delta_vs_today": {
    "swim_sec_saved": 180,
    "ride_sec_saved": 720,
    "run_sec_saved": 540,
    "total_sec_saved": 1440,
    "total_sec_saved_formatted": "24 min faster than if raced today"
  },
  "warnings": [
    "5+ months projection — CI wider than recent data",
    "projected_ctl 72 is below ctl_target 75 — plan may need +volume",
    "projected_eftp Ride 225W — 88% target power = 198W for race pace (vs current 88% = 183W)"
  ]
}
```

### 8.3. Fitness projection availability

`fitness_projection` покрыт webhook'ом только для юзеров с активным Intervals
Premium (бесплатный тариф даёт меньшую глубину). Если `FitnessProjection.get`
вернул `None` → tool: `{"available": False, "reason": "no_fitness_projection"}`.

Упрощённый fallback (Phase 2) — прогонять линейную интерполяцию от текущего CTL
до `ctl_target` к race date. Неточно, но лучше чем ничего.

---

## 9. MCP tool: `get_race_projection`

### 9.1. Signature

```python
@mcp.tool()
@sentry_tool
async def get_race_projection(
    mode: Literal["today", "race_day"] = "today",
    race_date: str = "",                  # auto-fill from AthleteGoal.RACE_A if empty
    race_distance_swim_m: int | None = None,
    race_distance_ride_m: int | None = None,
    race_distance_run_m: int | None = None,
    target_hr_ride: int | None = None,    # if None — use ride_lthr × 0.88 (IM 70.3 default)
    target_hr_run: int | None = None,     # if None — use run_lthr × 0.90
    include_transitions: bool = False,    # reserved for Phase 2
) -> dict:
    """Predict race splits for current state or race-day projected state.

    mode="today":    uses current wellness + Intervals state.
    mode="race_day": replaces CTL/ATL/TSB with fitness_projection.get(race_date).

    Returns per-discipline predicted pace/power + CI from bootstrap residuals.
    """
```

### 9.2. Return shape

См. §7.1 (Mode 1) и §8.2 (Mode 2). Единый «envelope» с `mode`, списком дисциплин,
их сплитами и массивом warnings.

### 9.3. Error cases

| Case | Return |
|---|---|
| Модель не обучена (cold-start) | `{"available": False, "reason": "model_not_trained", "discipline_missing": [...]}` |
| Нет `fitness_projection` на race_date (Mode 2) | `{"available": False, "reason": "no_fitness_projection"}` |
| `race_date` в прошлом | 400-like error string (Claude передаст атлету): `"race_date must be >= today"` |
| Нет `ctl_target` / race в `athlete_goals` и `race_date` пустой | `{"available": False, "reason": "no_race_date", "hint": "use suggest_race or pass race_date"}` |
| Distance не задан для дисциплины | Пропускаем эту дисциплину, возвращаем остальные + warning |

---

## 10. Confidence intervals

### 10.1. Метод: bootstrap residuals

На train-выборке собираем residuals `y_true - y_pred`. Сохраняем массив в
`.joblib`. На inference:

```python
pred = model.predict(x)
ci_low  = pred + np.percentile(residuals, 5)
ci_high = pred + np.percentile(residuals, 95)
```

Это 90% CI (prediction interval). Не требует retrain'а, быстро.

### 10.2. Inflation для Mode 2

5+ месяцев forecast → шире CI. Множитель `sqrt(days_to_race / 30)` на residuals
(эвристика — проверим на hold-out race 2026-09-15 постфактум). С двумя
ограничениями (issue #350, 2026-05-12):

```python
INFLATION_MAX = 1.8                # cap past ~97 days
MIN_RACE_DAYS_FOR_FORECAST = 14    # within taper window — fall back to Mode 1

if days_to_race > MIN_RACE_DAYS_FOR_FORECAST:
    inflation = min(INFLATION_MAX, max(1.0, sqrt(days_to_race / 30)))
else:
    inflation = 1.0  # taper-CTL ≈ today, wider band misleads
ci_low  = pred + np.percentile(residuals, 5)  * inflation
ci_high = pred + np.percentile(residuals, 95) * inflation
```

**Why the cap.** Empirical: at 126 days out, raw `sqrt(126/30)=2.05` gave Run CI
±34 minutes on a half-marathon — unreadable for race planning. `INFLATION_MAX=1.8`
corresponds to ~97 days; past that the formula says «I don't know how much
uncertainty grows here» rather than pretending we do.

**Why the taper threshold.** Within 14 days of the race, `projected_ctl` ≈ `current_ctl`
(taper window, ATL drops but CTL barely moves). Inflating CI past Mode 1's width
implies more uncertainty about the future than the present, which is false.

### 10.4. Out-of-sample CTL warning

Issue #359 surfaced that for user 1 (training CTL distribution 15-45), Mode 2 race-day
projection at CTL=66 produces only ~4 sec/km Run improvement because **XGBoost trees
don't extrapolate** — they clip to nearest observed leaf. Output is correct (the model
shouldn't fabricate fitness gains it hasn't seen) but consumer needs to know.

Mechanism (2026-05-12):

1. **Train time** (`race_train.py`): record `metrics.ctl_feature_p90 = df["ctl_<discipline>"].quantile(0.90)` in the saved bundle.
2. **Predict time** (`race_predict.py:_predict_one`): after applying Mode 2 `_ctl_ratio` scaling, if `features["ctl_<discipline>"] > ctl_feature_p90`, attach private `_ctl_out_of_sample = {projected, train_p90}` to the leg output.
3. **Envelope aggregation** (`predict_splits_with_ci`): strip private key, emit one-line warning per affected leg:
   ```
   run: projected ctl_run=66.0 > train p90=30.0 — out-of-sample, model held conservative
   (no training data above this CTL)
   ```

Caller (Claude prompt) renders this honestly to the athlete: «на CTL 66 модель видела мало данных,
прогноз держу осторожно — реальный темп при достижении CTL 66 может быть быстрее».

Backwards-compat: legacy bundles without `metrics.ctl_feature_p90` skip the check entirely.

### 10.5. Future expansion (Phase 2 / deferred)

- **Formula-anchored baseline blend.** For Run, Jack Daniels equivalent-pace formula
  computed from `threshold_pace × (1 + bias)` (or pace-at-VO2max scaling) extrapolates
  cleanly even outside ML training distribution. Blend with ML prediction weighted by
  `days_to_race`: closer to race → more ML (modeled state vs base physiology); farther →
  more formula (XGBoost can't extrapolate; formula can). Same for Ride (FTP-anchored)
  and Swim (CSS-anchored). Mitigates OOS issue at root rather than just warning.

- **Cross-athlete pool model.** Train shared model on all `is_active=True` athletes,
  warm-start per-user. User with no CTL=66 examples gets context from athletes who do.
  Risk: per-athlete bias propagation if dataset isn't clean → Phase 1.6 noise tag becomes
  a **prerequisite** (don't pool dirty data). Trigger: 5+ athletes with n≥200 each.

### 10.3. Calibration check

Постфактум (после каждой состоявшейся гонки в `races`): проверяем что actual
сплит попадает в [ci_low, ci_high] примерно в 90% случаев. Если систематически
выбивает — пересматриваем метод.

---

## 11. Delivery

### 11.1. Text (Phase 1)

Через Claude в чате — см. примеры §7.2 и §8.2. Claude сам форматирует с учётом
языка атлета.

### 11.2. Webapp chart (Phase 2)

`/race-projection` страница — CTL trajectory (из `fitness_projection` + scenario
overlays) + splits table для Mode 2 с CI bars. Бэкенд уже готов
(`/api/fitness-projection`), фронт дорисуем.

### 11.3. Morning report

**Не добавляем** в ежедневный morning report — слишком шумно. Race projection
вызывается по запросу («как я пойду гонку?») или явной команде `/forecast`.

---

## 12. Training schedule

### 12.1. CLI

```bash
python -m cli train-race-models <user_id>
```

Обучает три модели (Run/Ride/Swim), сохраняет `.joblib` файлы. Логирует per-model
MAE/R² в stdout + Sentry.

### 12.2. Weekly cron

Точка расширения — существующий `actor_retrain_progression_model` (`bot/scheduler.py:19,96`, Sun 16:00 Belgrade, `misfire_grace_time=7200, coalesce=True`), shipped с TRAINING_PROGRESSION 2026-04-19. Добавляем race-цикл в тот же actor (или, чтобы не раздувать одну функцию, отдельный `actor_retrain_race_models` со своим scheduler slot — решение по месту, оба варианта приемлемы). Зависимость от HRV spec — необязательная: когда HRV spec стартует, она встанет тем же расширением.

```python
# Эскиз — adapt to existing actor's pattern in bot/scheduler.py
@dramatiq.actor(max_retries=0)
def actor_retrain_race_models(user_id: int):
    from data.ml import race_train
    for discipline in ("Run", "Ride", "Swim"):
        try:
            race_train.train_user_model(user_id, discipline=discipline)
        except race_train.InsufficientDataError:
            logger.info("skip race_%s — need 100+ activities", discipline)
```

Запускается тем же scheduler-slot'ом что и progression retrain (Sun 16:00), либо отдельным slot'ом если время выполнения совокупно вырастает.

### 12.3. Acceptance bar per discipline (user 1)

Для deploy первой версии:

| Discipline | MAE target | R² target | Дополнительно |
|---|---|---|---|
| Run (pace sec/km) | ≤ 10 | ≥ 0.50 | Top-5 SHAP стабильны |
| Ride (power W) | ≤ 15 | ≥ 0.40 | Separate MAE for `is_race` rows |
| Swim (pace /100m) | ≤ 8 | ≥ 0.30 | Наиболее слабая, просто fit baseline |

Ниже — блокируем deploy, поднимаем feature quality.

---

## 13. Storage

### 13.1. Модели

`ml/models/race_{user_id}_{discipline}.joblib` — содержит `{model, feature_names, residuals, trained_at, metrics}`.

Путь уже в `.gitignore` (из HRV spec). Размер ~5-10 MB per model × 3 discipline × N users. Для MVP (user 1): ~30 MB.

### 13.2. Predictions log (опционально)

Если делаем `hrv_predictions` таблицу (§11.3 HRV spec), добавляем параллельную
`race_projections`:

```sql
CREATE TABLE race_projections (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id),
    mode VARCHAR(16) NOT NULL,                 -- today / race_day
    race_date DATE NOT NULL,
    projected_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    splits_json JSONB NOT NULL,                -- предсказанные сплиты + CI
    actual_splits_json JSONB,                  -- backfilled после гонки
    model_versions_json JSONB NOT NULL         -- {run: "trained_2026-04-20", ...}
);
```

Зачем: post-race calibration check (§10.3). Если не критично — Phase 2.

---

## 14. Acceptance criteria

### Phase 1 (MVP, user 1) — ✅ shipped 2026-05-11

- [x] `data/ml/race_features.py` — per-discipline feature builders, unit-тесты (31 cases incl. z1-dominated primitive ×13, recovery-jog combined ×9, pipeline-integration guards ×2).
- [x] `data/ml/race_predict.py` — `predict_splits_with_ci()` возвращает структуру §9.2 (16 cases incl. physiological floor clamp + quality gate).
- [x] `data/ml/race_train.py` — XGBRegressor per discipline + bootstrap residuals (500 resamples) → `static/models/race_{user}_{discipline}.joblib`.
- [x] MCP tool `get_race_projection` — оба режима + cold-start fallback + all error envelopes §9.3 (7 cases).
- [x] Bootstrap residuals → CI в ответе, inflation `sqrt(days/30)` для Mode 2.
- [x] **Weekly retrain actor** — `actor_retrain_race_models` (separate actor от progression), shared `scheduler_progression_model_job` Sun 16:00 Belgrade slot с 15s offset; `time_limit=600s, max_retries=0`. Skip via `InsufficientDataError`.
- [x] **Chat:** Claude корректно зовёт тулзу — `_STATIC_PROMPT_CHAT` секция `## Race projection` с триггерами в `bot/tool_filter.py:analysis` group.
- [x] **Weekly report:** `SYSTEM_PROMPT_WEEKLY` step 8 + `WEEKLY_TOOL_NAMES` — one-line «🏁 Race-day прогноз» в 📈 Прогресс gated на `goal_event_date ∈ [30, 200]` дней.
- [x] **Schema deps:** `fitness_projection.sport_info JSONB` (migration `b8c9d0e1f2a3`) + `FitnessProjection.{get, sport_info_by_type}`. Per-sport CTL helper lives inline at `data/ml/race_features.py:_compute_sport_ctl_series` (pandas-batch, ORM-method form turned out zero-caller and was removed).
- [x] CLI `train-race-models <user_id>`.
- [x] Документация: этот файл + секция в CLAUDE.md + развёрнутая секция в `docs/IMPLEMENTATION_STATUS.md`.

### Phase 1.5 — recovery-jog filter (✅ shipped 2026-05-11, TSS gate added 2026-05-12)

- [x] `_is_z1_dominated(hr_zone_times)` zone-composition primitive + `Z1_RECOVERY_THRESHOLD = 0.70` (§6.3).
- [x] `_is_recovery_jog(hr_zone_times, tss)` combined check + `RECOVERY_TSS_CEILING = 40.0`. Both conditions required (Z1≥70% AND TSS<40). Refined 2026-05-12 — zone-only filter broke pro athletes who do structured 80/20 base (one cohort regressed R² 0.44 → 0.04 before TSS gate). TSS gate distinguishes 25-min recovery jog (TSS ~25) from 90-min Z1-base session (TSS ~70).
- [x] Filter applied in `build_dataset` loop for Run sport only (Ride uses `is_indoor` + power corridor, Swim has no zone splits).
- [x] Missing zone data OR missing TSS → activity passes through (don't filter what we can't safely classify).
- [x] Log line per training run reports `n_filtered_recovery` count for debugging.
- [x] Tests: zone-only primitive (13 cases incl. type-strictness, NaN coercion, negative clamping) + recovery-jog combined (9 cases incl. long-Z1-base kept, TSS missing/NaN paths, boundary at TSS_CEILING) + pipeline-integration regression guards (2 cases — short jog dropped, long Z1-base kept).

### Phase 1.6 — webhook-time noise classification (pending)

- [ ] Migration: `activities.noise_reason TEXT NULL` + `noise_scored_at TIMESTAMP NULL` + composite index `(user_id, type, noise_reason)`.
- [ ] `data/ml/noise_classifier.py` — `classify_noise(act_dto, thresholds) -> NoiseReason | None`, `is_run_walk()`, `is_run_recovery_jog()` (relocated from race_features). Module constants `WALK_PACE_MULT=1.6`, `WALK_HR_MULT=0.65`. Typed `NoiseReason = Literal["run_walk", "run_recovery_jog"]`.
- [ ] `data/db/activity.py` — `Activity.noise_reason` + `noise_scored_at` columns + `Activity.set_noise_classification(user_id, activity_id, *, reason, scored_at)` (`@dual`, tenant guard via WHERE user_id).
- [ ] `tasks/actors/activities.py:actor_update_activity_details` — после `ActivityDetail.save()`: fetch `athlete_settings`, call `classify_activity_row()`, `Activity.set_noise_classification()` в той же session. Runs only when `is_changed or force` (gated by existing early-return). Idempotent.
- [ ] `data/ml/race_features.py:build_dataset` — приоритет `noise_reason` drop (truthy-string check); fallback на live `is_run_recovery_jog` для `noise_scored_at IS NULL` legacy строк. Import `is_run_recovery_jog` из `noise_classifier`, не дублировать.
- [ ] CLI `python -m cli classify-noise` — `--since-days=365`, `--user-id`, `--dry-run`. Per-user `try/except` + `sentry_sdk.capture_exception`.
- [ ] Tests `tests/ml/test_noise_classifier.py` — `TestIsRunWalk` (3 athlete cohorts × walk/jog/threshold/no-thresholds = 12 cases), `TestIsRunRecoveryJog` (8 cases — Z1/TSS boundaries), `TestClassifyNoise` (priority order + non-Run pass-through).
- [ ] Tests `tests/api/test_intervals_webhook.py` — dispatcher вызывает classifier + sets reason; idempotent re-fire; tenant guard regression.
- [ ] Tests `tests/ml/test_race_features.py::TestBuildDataset` — extended for `noise_reason IS NULL` filter + legacy fallback path.
- [ ] Run backfill `python -m cli classify-noise --since-days=365` for active users.
- [ ] Retrain user 1 → Run R² не хуже текущего baseline (0.22). Если значительно лучше — обновить §14 «Phase 1 runtime acceptance — фактический статус».
- [ ] Docs: этот файл §6.4 + `docs/knowledge/training-data-hygiene.md` секция «How models use these signals» обновление + CLAUDE.md Implementation Status строка.

### Quality gate (✅ shipped 2026-05-11)

- [x] `ModelBelowAcceptance` exception + `_enforce_quality_gate(bundle, discipline, *, user_id)` (`data/ml/race_predict.py`).
- [x] Per-discipline floor `_QUALITY_FLOORS` (Run r²≥0.20/mae≤40, Ride 0.20/25W, Swim 0.05/15 sec/100m). Calibrated against user 1/14/23/39/62 real metrics 2026-05-12.
- [x] Legacy bundles without `metrics` field pass through (backwards-compat).
- [x] NaN guard in metric comparison (defensive — `nan < 0.20` would silently admit garbage).
- [x] Envelope gains `below_acceptance: list[str]` field; MCP tool emits `reason="model_below_acceptance"` distinct from `model_not_trained`.

### Phase 1 runtime acceptance (pending)

- [ ] Запустить `python -m cli train-race-models 1` — обучить три модели на user 1.
- [ ] Run MAE ≤ 10 sec/km / R² ≥ 0.50, Ride MAE ≤ 15W / R² ≥ 0.40, Swim MAE ≤ 8 sec/100m / R² ≥ 0.30 (§12.3). Below — block deploy.
- [ ] Прогнать утренний / weekly один цикл, убедиться что Claude вызывает тул и форматирует строку.

### Phase 2 — deferred

Scenario engine ("miss 2 weeks" / "+10% volume" / custom CTL target), webapp `/race-projection` chart (CTL trajectory + per-leg CI bars), race-specific Ride/Swim calibration (await ≥10 non-Run race events), cross-athlete pool model. Spec §2 unchanged.

---

## 15. Implementation order

1. **`ml/race_features.py`** + unit-тесты на детерминистичное построение.
2. **`ml/train.py` extension** — `--target=race_run|race_ride|race_swim` в CLI.
3. **`ml/race_predict.py`** — load + predict + bootstrap CI.
4. **MCP tool `get_race_projection`** — Phase 1 режимы, cold-start, все error cases §9.3.
5. **Prompt update**: `bot/prompts.py:_STATIC_PROMPT_CHAT` — раздел
   «Race projection» с триггерами и правилами вызова.
6. **Weekly retrain actor** — объединение с HRV.
7. **Tests** — unit (features, predict, CI), integration (MCP tool end-to-end
   с фейковыми моделями), smoke (CLI train на user 1).
8. **CLAUDE.md update** — раздел «Implementation Status», MCP tools count,
   новая документация.

---

## 16. Testing

### Unit

- `tests/ml/test_race_features.py` — per-discipline builders, edge cases (no
  elevation, no HR, indoor bike), детерминистичный выход.
- `tests/ml/test_race_predict.py` — bootstrap CI shape, inflation scaling,
  cold-start когда model missing.

### Integration

- `tests/mcp/test_race_projection.py` — Mode 1 возвращает правильную envelope;
  Mode 2 с mock `FitnessProjection.get` возвращает projected state; error
  cases из §9.3.
- `tests/tasks/test_retrain_actor.py` — actor вызывает race train для Run, скипает
  Ride/Swim если данных меньше порога.

### Manual smoke (user 1, сейчас)

1. `python -m cli train-race-models 1` → три модели сохранены.
2. `python -c "from ml.race_predict import predict; print(predict(1, mode='today', ...))"` → разумные splits.
3. То же с `mode='race_day'` → сплиты чуть быстрее (projected CTL 72 vs текущий 21).
4. В чате: «прогноз на гонку» → Claude зовёт тулзу, возвращает текстом.

### Post-race calibration (2026-09-15 — Ironman 70.3 Belgrade)

После гонки:
1. Actual splits попадают в `races` через `tag_race`.
2. Compare vs `race_projections.splits_json` записанный за N дней до гонки.
3. Проверяем попадание в CI (`ci_low ≤ actual ≤ ci_high` для каждой дисциплины).
4. Если систематически выбивает — tune inflation factor в §10.2.

---

## 17. Open questions

- **Transitions.** Нужно ли прибавлять оценку T1/T2 (4-6 мин + 2-3 мин типично
  для 70.3) к `total_sec`? Варианты: (a) юзер сам накидывает, tool этого не
  делает — чисто swim+bike+run; (b) параметр `transition_sec` в tool signature;
  (c) автоматически из `races` среднего атлета. **Предлагаю (a)** для MVP —
  честный output, никакого tacked-on guessing.
- **target_hr для Ride/Run: где брать default?** В MVP — эвристика 88%/90%
  LTHR. Идеально — из `athlete_goals.per_sport_targets` если юзер задал. Или
  из истории последних race activities (median race HR). **Предлагаю:** в
  MVP эвристика из LTHR, в Phase 2 — из race history если есть.
- **Race-pacing vs training pacing bias.** Train data в основном non-race →
  инференс на race даст оптимистично-быстрые (training) или пессимистично-
  медленные (не-race, низкая интенсивность) сплиты? Скорее второе. **Предлагаю:**
  `is_race` как feature, для Run это работает (19 race activities); для Ride/Swim
  применяем systematic offset из Run-bias (e.g., `race_pred - training_pred`
  в Run даёт -X% скорости, применяем тот же % к Ride/Swim). Явно флажкуем в
  warnings.
- **Per-race-distance модели vs one model with distance feature.** Ironman 70.3
  ≠ Sprint ≠ Full IM. Pace modes разные. **Предлагаю:** distance как feature +
  explicit warning если `race_distance_*_m` > max seen in training data. В
  Phase 2 — отдельные модели per distance class если данных накопится.
- **Intervals.icu fitness_projection accuracy.** Intervals считает projection
  через свои Banister-параметры и текущий календарь. Если атлет не заносит
  планируемые тренировки в Intervals calendar — projection плоская от
  сегодняшнего дня. Надо явно проверять `fitness_projection.max(date) >=
  race_date` и warning если projection обрывается раньше race day. **Предлагаю:**
  в Phase 1 warning в response; в Phase 2 fallback на линейную интерполяцию.
- **Chart в webapp — Phase 1 или 2?** Issue #64 явно просит visualization. В
  спеке я вынес в Phase 2. **Аргумент:** MVP в чате + MCP tool доказывает модель;
  chart — UX-слой поверх стабильного API. Но если «visualization» — must-have
  для issue closure, берём в Phase 1 и делаем минимальный static PNG через matplotlib.
- **Historical race weather в train data.** ACTIVITY_UPLOADED webhook приносит
  `average_weather_temp`, `feels_like`, `wind_speed`, `rain/snow` для outdoor-активностей (see `docs/INTERVALS_WEBHOOKS_RESEARCH.md` §ACTIVITY_UPLOADED — Run sample). Если обучающие race activities имеют weather-поля, можно добавить их как фичи (`race_temp_c`, `race_heat_stress = max(0, temp-20) × distance_km`) — поможет откалибровать предсказания на белградскую сентябрьскую погоду (20-28°C). Weather на race day 2026-09-15 — неизвестен, но можно взять median из исторических races на той же неделе года (если в `races` есть хотя бы одна September race) или climate normals из внешнего API. **Предлагаю:** Phase 1 — игнорируем, Phase 2 — добавляем в Run-модель (19 race activities), для Ride/Swim ждём накопления race data.
- **Webhook data availability для бэкфилла.** `ctl_run`/`ctl_ride`/`ctl_swim`
  (§6.1) и MMP model (§6.2) начали надёжно приходить только после включения
  webhook dispatchers (2026-04-11). Для исторических `wellness` rows поле
  `sport_info` может быть `None`. **Решение:** перед первым `train-race-models`
  прогнать бэкфилл-actor, который дозаполнит `wellness.sport_info` из Intervals
  REST `/wellness/{id}` для дат до 2026-04-11. Одноразовая операция.

---

## 18. Open issues (post-Phase 1)

Журнал нерешённых проблем + связанных инфраструктурных задач. Обновляется при появлении нового открытия. **Закрытые issue NE удаляем из списка** — историческая привязка к спеке (по дате создания + комментарию о fix-commit).

### 🔴 Blockers for production use

- **[#349](https://github.com/radikkhaziev/triathlon-agent/issues/349) — `fitness_projection` decay вместо плана.**
  Mode 2 race_day режим возвращает `projected_ctl=1.62` для гонки через 127 дней, потому что Intervals.icu `fitness_projection.ctl` — это **zero-load decay curve** (что было бы если перестать тренироваться), а не план. У атлетов которые не пишут будущие workout'ы в Intervals calendar — projection схлопывается к нулю. Fix: переписать `_mode2_overrides` на линейную экстраполяцию `current_CTL → goal.ctl_target` (spec §8.3 уже предсказала это решение как Phase 2 fallback, но без него Phase 1 Mode 2 непригоден).
  - **Impact:** Mode 2 race_day выдаёт мусорные сплиты (Run 2:38 вместо 2:05 на 70.3) — quality gate не ловит т.к. метрики модели хорошие, ломается inference-side `projected_ctl`.
  - **Effort:** ~30 строк в `_mode2_overrides` + 2-3 unit-теста.

### ✅ UX polish — shipped 2026-05-12

- **[#350](https://github.com/radikkhaziev/triathlon-agent/issues/350) — CI inflation cap + min-days threshold.** ✅ Shipped 2026-05-12. `INFLATION_MAX = 1.8` + `MIN_RACE_DAYS_FOR_FORECAST = 14` в `data/ml/race_predict.py`. CI на 200d остановился на 1.8× вместо 2.6× — Run CI на 21k шрнк с ±34min до ~±24min. См. §10.2.
- **[#359](https://github.com/radikkhaziev/triathlon-agent/issues/359) Q2 — duplicate of #350**, закрыт вместе.
- **[#359](https://github.com/radikkhaziev/triathlon-agent/issues/359) Q1 (a) — out-of-sample CTL warning.** ✅ Shipped 2026-05-12. `metrics.ctl_feature_p90` в bundle (`race_train.py`), check + private flag в `_predict_one`, aggregated в warnings в `predict_splits_with_ci`. См. §10.4. Phase 2 fix (formula-anchored blend) — deferred ниже.

### 🟢 In-flight (Phase 1.6)

- **Webhook-time noise classification — hybrid.** Persisted `activities.noise_reason` tag, классифицируется при `_dispatch_activity_updated`; live-check как fallback. Phase 1.6 ship: `run_recovery_jog` + `run_walk`. Phase 2 add: `ride_*` (после empirical calibration). Phase 3 deferred: `optical_hr_noisy`, `swim_*`. Full design: §6.4. Acceptance: §14 «Phase 1.6».

### 🟡 Infrastructure (не блокирует функциональность)

- **[#348](https://github.com/radikkhaziev/triathlon-agent/issues/348) — ML retrain в отдельной Dramatiq очереди + ночной cron.**
  Sun 16:00 → Sun 03:00, `queue_name="ml_retrain"`, отдельный worker с `--threads 1`. Изолирует CPU spike (XGBoost bootstrap) от Telegram/wellness/webhook workers. ~10 строк правок + 1 service в `docker-compose.yml`.

- **[#346](https://github.com/radikkhaziev/triathlon-agent/issues/346) — Sentry alerts не приходят.**
  Не специфично для ML. Spike protection / rate limit / отсутствующее alert rule. Race-projection capture'ы тоже не видны → если что-то сломается в production retrain'е, узнаем по логам, не Sentry.

### 🟢 Phase 2 — deferred (по запросу или накоплению данных)

- **Scenario engine** («miss 2 weeks» / «+10% volume» / custom CTL target) — требует hypothetical-CTL поверх existing projection. Spec §2.
- **Webapp `/race-projection` page** — CTL trajectory chart + per-leg CI bars. Backend готов, нужен UI-спринт. Spec §11.2.
- **Race-specific Ride/Swim calibration** — ждём ≥10 non-Run race events (сейчас Ride 2 / Swim 1 у user 1). Spec §2 Phase 2.
- **Cross-athlete pool model** — общая регрессия + warm-start per-user. После накопления данных по 5+ атлетам. Spec §2 Phase 2. **Trigger from #359 Q1:** user 1's training-set CTL distribution (15-45) makes Mode 2 race-day projection at CTL=66 out-of-sample → tree clipping → only 4 sec/km improvement (см. §10.4). Pool model gives user 1 access to higher-CTL examples seen by other athletes. **Prerequisite: Phase 1.6 noise tag backfill** must be complete на всех атлетах — pooling грязные данные шумит сильнее, чем помогает (walks-as-Run у одного атлета испортят сигнал для всех).
- **Formula-anchored baseline blend (issue #359 Q1, root fix).** XGBoost не extrapolat'ит — на out-of-sample CTL модель выдаёт conservative output. Решение: blend ML prediction с physiology-based formula (Run: Jack Daniels equivalent-pace из `threshold_pace`; Ride: FTP-anchored from `eFTP × race-intensity-target` per distance; Swim: CSS-anchored). Weight by `days_to_race` — close to race → ML wins (modeled state), far → formula wins (clean extrapolation). Mitigates the issue described в §10.4 (currently surfaced only as warning, not corrected). Effort: ~150 LoC + per-discipline calibration constants from training literature.
- **Phase 1.5+ feature improvements** — surface filter for Run (`type=Run` strictly), duration condition для recovery-jog filter (если атлет ведёт длинные walks-as-Run, см. user 1 calibration в §6.3). По запросу.
- **Phase 2 Ride noise classifiers** — `ride_recovery_spin` / `ride_commute` / `ride_indoor_test` (§6.4.2). Требует empirical calibration на 5 атлетах (как Run в Phase 1.5 calibration story). Schema + classifier infrastructure уже стоит после Phase 1.6 — добавление = одна строка в Literal type + три helpers + retest.
- **Optical-HR noise detection** — `optical_hr_noisy` requires device-strap metadata parsing из FIT files (не реализовано). Defer пока чек не приоритет.

### 🟡 Cross-spec deps

- **[#356](https://github.com/radikkhaziev/triathlon-agent/issues/356) — Coach/Trainer skill.**
  Использует `docs/knowledge/training-data-hygiene.md` (создан 2026-05-12) для рекомендаций атлетам как улучшить data quality. Влияет на race-projection косвенно — лучше данные → выше MAE/R² → больше моделей проходит quality gate.

### Phase 1 runtime acceptance — фактический статус

§12.3 формально **не пройден** на user 1 (Run MAE=36 vs floor 10; R²=0.22 vs floor 0.50). Spec говорит «block deploy», но мы сознательно выбрали **Variant A — ship as ranking signal под quality gate**:

- Quality gate (§14 «Quality gate» секция) автоматически блокирует модели ниже R²=0.20 (Run/Ride) / R²=0.05 (Swim).
- Атлет получает либо реальный прогноз с честным CI диапазоном, либо `reason=model_below_acceptance` сообщение — никогда мусор.
- Acceptance bar §12.3 целевой для **зрелой системы** (3-6 месяцев чистых данных), а не для Phase 1 MVP с n=300-400.

Решение зафиксировано 2026-05-12 после retrain across 5 атлетов. Бар может быть пересмотрен в Phase 2 если накопится evidence что MAE 10 sec/km нереалистичен на real-world data hygiene даже у дисциплинированных атлетов.
