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

Расширяем существующий (из HRV spec) `ml/` новыми файлами:

```
ml/
├── __init__.py
├── features.py        # HRV (существует в HRV spec)
├── race_features.py   # NEW — performance regression features
├── train.py           # расширяем: --target=hrv | --target=race
├── predict.py         # hrv_prediction (существует)
├── race_predict.py    # NEW — predict_splits_with_ci
└── models/
    ├── hrv_{user_id}.joblib          # from HRV spec
    ├── race_{user_id}_run.joblib     # NEW
    ├── race_{user_id}_ride.joblib    # NEW
    └── race_{user_id}_swim.joblib    # NEW
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
- **`ctl_run`, `ctl_ride`, `ctl_swim` — per-sport load split.** ⚠ **Не** из `wellness.sport_info` — webhook `sportInfo` содержит только `{type, eftp, wPrime, pMax}`, **без per-sport CTL** (подтверждено в `INTERVALS_WEBHOOKS_RESEARCH.md` §WELLNESS_UPDATED payload sample). Intervals.icu показывает per-sport CTL в своём UI, но через webhook/REST API не отдаёт. **Считаем сами:** helper `Activity.compute_sport_ctl(user_id, sport, target_date, tau=42)` — EMA над `activity.icu_training_load` с фильтром `type=sport`. Вызывается при построении train-set и inference-row. Для **per-discipline predict**'а per-sport CTL важнее глобального: Run pace зависит от run-specific нагрузки, не от того что атлет много плавал.
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
- **`critical_power`, `w_prime`, `p_max`** — из `athlete_settings.mmp_model` (приходит SPORT_SETTINGS_UPDATED webhook'ом, см. research §SPORT_SETTINGS_UPDATED). Критическая мощность (CP) — физиологически обоснованный predictor sustainable power на длинные дистанции (>20 min), лучше FTP. W' — анаэробный запас (Дж), определяет допустимые supra-CP усилия на подъёмах. pMax — пиковая мощность, для sprint-distance и первых минут.
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
- Exclude low-intensity recovery: `z1_time_pct < 70%` (иначе модель учится на
  чиловых пробежках).
- Для Run: exclude trail if `type=Run` (road), и наоборот — не смешивать.

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
(эвристика — проверим на hold-out race 2026-09-15 постфактум):

```python
inflation = max(1.0, sqrt(days_to_race / 30))
ci_low  = pred + np.percentile(residuals, 5)  * inflation
ci_high = pred + np.percentile(residuals, 95) * inflation
```

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

Объединяем с HRV retrain actor (§9 в HRV spec). Один job, разные таргеты:

```python
@dramatiq.actor
def actor_retrain_ml_models(user_id: int):
    from ml import train
    train.train_user_model(user_id, target="hrv")
    for discipline in ("Run", "Ride", "Swim"):
        try:
            train.train_user_model(user_id, target=f"race_{discipline.lower()}")
        except InsufficientDataError:
            logger.info("skip race_%s — need 100+ activities", discipline)
```

`scheduler_retrain_ml_job` каждое воскресенье 04:00.

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

### Phase 1 (MVP, user 1)

- [ ] `ml/race_features.py` — per-discipline feature builders, unit-тесты.
- [ ] `ml/race_predict.py` — `predict_splits_with_ci()` возвращает структуру §9.2.
- [ ] Три обученных `.joblib` (Run/Ride/Swim) с метриками в §12.3.
- [ ] MCP tool `get_race_projection` — оба режима + cold-start fallback.
- [ ] Bootstrap residuals → CI в ответе, inflation для Mode 2.
- [ ] Weekly retrain actor расширен (из HRV spec).
- [ ] Claude в чате корректно зовёт тулзу по запросам типа «прогноз на гонку»
      (обновить `SYSTEM_PROMPT_CHAT` — добавить раздел `## Race projection`).
- [ ] Документация: этот файл + короткая секция в CLAUDE.md.

### Phase 2

Отложено до явного запроса или накопления race-data для Ride/Swim.

---

## 15. Implementation order

1. **`ml/race_features.py`** + unit-тесты на детерминистичное построение.
2. **`ml/train.py` extension** — `--target=race_run|race_ride|race_swim` в CLI.
3. **`ml/race_predict.py`** — load + predict + bootstrap CI.
4. **MCP tool `get_race_projection`** — Phase 1 режимы, cold-start, все error cases §9.3.
5. **Prompt update**: `bot/prompts.py:SYSTEM_PROMPT_CHAT` — раздел
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
  для issue closure, берём в Phase 1 и делаем минимальный static PNG (как
  workout card) через matplotlib.
- **Historical race weather в train data.** ACTIVITY_UPLOADED webhook приносит
  `average_weather_temp`, `feels_like`, `wind_speed`, `rain/snow` для outdoor-активностей (see `docs/INTERVALS_WEBHOOKS_RESEARCH.md` §ACTIVITY_UPLOADED — Run sample). Если обучающие race activities имеют weather-поля, можно добавить их как фичи (`race_temp_c`, `race_heat_stress = max(0, temp-20) × distance_km`) — поможет откалибровать предсказания на белградскую сентябрьскую погоду (20-28°C). Weather на race day 2026-09-15 — неизвестен, но можно взять median из исторических races на той же неделе года (если в `races` есть хотя бы одна September race) или climate normals из внешнего API. **Предлагаю:** Phase 1 — игнорируем, Phase 2 — добавляем в Run-модель (19 race activities), для Ride/Swim ждём накопления race data.
- **Webhook data availability для бэкфилла.** `ctl_run`/`ctl_ride`/`ctl_swim`
  (§6.1) и MMP model (§6.2) начали надёжно приходить только после включения
  webhook dispatchers (2026-04-11). Для исторических `wellness` rows поле
  `sport_info` может быть `None`. **Решение:** перед первым `train-race-models`
  прогнать бэкфилл-actor, который дозаполнит `wellness.sport_info` из Intervals
  REST `/wellness/{id}` для дат до 2026-04-11. Одноразовая операция.
