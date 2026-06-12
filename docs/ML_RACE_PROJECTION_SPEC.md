# ML Race Projection Spec

> Прогноз гоночной производительности в двух режимах:
> **(1) «Race today»** — из текущей формы предсказываем сплиты по дисциплинам;
> **(2) «Race day»** — экстраполируем фитнес к дате старта и предсказываем
> сплиты для race day.
>
> **Status:** Phase 1 + 1.5 + 1.6 + 1.7 + 2.0β2 ✅ shipped (2026-05-11/12).
> Phase 2 (scenario engine, chart, pool model) — deferred, см. §18.
> Исходный трекер — issue [#64](https://github.com/radikkhaziev/triathlon-agent/issues/64).

**Related:**

| Spec / code | Связь |
|---|---|
| `data/ml/race_features.py` / `race_train.py` / `race_predict.py` / `noise_classifier.py` / `bias_constants.py` | Реализация |
| `data/db/fitness_projection.py` | CTL/ATL/eFTP кривая из `FITNESS_UPDATED` webhook — Mode 2 input |
| `docs/knowledge/training-data-hygiene.md` | Athlete-side data hygiene (companion к §6.3-6.4) |
| `docs/ML_HRV_PREDICTION_SPEC.md` | Shared ML infra namespace `data/ml/` |
| `docs/WEBHOOK_DATA_CAPTURE_SPEC.md` | CP/W'/pMax + sport_info источники для §6.2, §8 |

---

## 1. Мотивация

Атлет хочет ответы: «если бы гонка была сегодня — что покажу?», «какой
финиш-тайм ожидать на race day при текущем плане?». Intervals.icu даёт
`fitness_projection` (CTL/ATL на будущее), но перевод в конкретные сплиты —
наша задача. Формульные калькуляторы (VDOT и т.п.) не учитывают
recovery/HRV/sleep состояние.

---

## 2. Scope

### Phase 1 (✅ shipped)

- **Mode 1 «today»** — state → predicted splits для Run/Ride/Swim.
- **Mode 2 «race_day»** — CTL/eFTP на race date из `fitness_projection`,
  подставляются в ту же модель. Без scenario engine.
- Per-discipline regression: Run pace @ target HR, Ride avg power/speed, Swim pace /100m.
- MCP tool `get_race_projection` (§9) + bootstrap CI (§10).
- Delivery — текст в чате; chart не входит в MVP.

### Phase 2 — deferred (§18)

Scenario engine («miss 2 weeks»), webapp chart, race-specific Ride/Swim
калибровка (ждёт ≥10 non-Run race записей), cross-athlete pool model.

### Non-goals

- Собственный Banister impulse-response — `fitness_projection` из webhook уже есть.
- Total finish time через транзишны — возвращаем per-discipline сплиты.
- Neural networks — XGBoost + SHAP; данные не поддерживают большего.
- Экстраполяция за пределы дистанций, видных в train data.

---

## 3. Что изменилось vs исходный issue #64

Banister-реализация заменена готовой `fitness_projection`; per-discipline
модели вместо multi-output; HumanGO → `ai_workouts` + Intervals calendar;
scenario engine отложен в Phase 2. Детальная таблица — в истории git (до 2026-06).

---

## 4. Available data

Train-set строится из `wellness` (state features), `activities` +
`activity_details` (targets + discipline features), `races` (race-specific
calibration). Race data сильно скошен в Run — для Ride/Swim модель учится на
всех activities с `is_race` feature. Cold-start (мало данных) → tool возвращает
`{"available": False, "reason": "insufficient_data"}`.

---

## 5. Architecture

### 5.1. Pipeline

**Train** (weekly cron, §12): `cli train-race-models <user_id>` →
`race_features.build_dataset(user_id, discipline)` → XGBRegressor per
discipline → bootstrap residuals (500 resamples) → bias-model fit (§10.5.6) →
`static/models/race_{user}_{discipline}.joblib` (bundle: model, feature_names,
residuals, metrics).

**Inference** (MCP tool): `build_state_row(user_id, today)` (+ Mode 2
overrides, §8) → per-discipline `predict` → CI из residuals (+ inflation, §10.2)
→ bias correction (§10.5.6) → envelope (§9.2).

### 5.2. Module layout

```
data/ml/
├── progression.py          # TRAINING_PROGRESSION (Ride EF model)
├── race_features.py        # per-discipline feature builders + _compute_sport_ctl_series
├── race_train.py           # XGBRegressor + bootstrap residuals + _fit_bias_model
├── race_predict.py         # predict_splits_with_ci(), quality gate, inflation
├── noise_classifier.py     # §6.4 — classify_noise, is_run_walk, is_run_recovery_jog
└── bias_constants.py       # pool bias defaults (§10.5.6)

static/models/race_{user_id}_{discipline}.joblib
```

### 5.3. Per-discipline, не multi-output

Разные целевые метрики (sec/km vs watts vs sec/100m), разная физиология,
разные размеры датасетов. Per-discipline даёт чистую диагностику слабого звена.

---

## 6. Feature engineering

### 6.1. State features (общие)

Из `wellness` на день активности (для предикции — на `target_date`):

- `ctl`, `atl`, `tsb` — глобальные.
- `ctl_run`, `ctl_ride`, `ctl_swim` — per-sport. ⚠ Webhook **не отдаёт**
  per-sport CTL (`sportInfo` содержит только `{type, eftp, wPrime, pMax}`) —
  считаем сами: `race_features.py:_compute_sport_ctl_series(activities_df, sport, tau=42)`,
  pandas-batch EMA над `icu_training_load` (один проход, без N+1 SQL).
- `hrv_ln_rmssd`, `rhr`, `sleep_score_7d_mean`, `stress_avg_7d_mean`, `recovery_score`.
- `compliance_28d_mean` — из `training_log`; высокий CTL при низком compliance =
  накачан compensating hard workouts, худшее качество базы.

### 6.2. Discipline-specific features

**Run** (target: `avg_pace` sec/km): `target_hr` (input param), `distance_m`,
`elevation_per_km`, `surface` (trail/road из `type`), `cumulative_distance_90d`,
`recent_fast_runs_14d`.

**Ride** (target: `avg_power` W + `avg_speed` km/h): `target_hr`, `distance_m`,
`elevation_per_km`, `cumulative_tss_ride_90d`, `recent_high_power_rides_14d`,
`is_indoor`, `current_eftp` (из `wellness.sport_info`, Mode 2 — из
`fitness_projection.sport_info`), `critical_power`/`w_prime`/`p_max`
(атомарные колонки `athlete_settings`, заполняются `SPORT_SETTINGS_UPDATED`),
`ftp_delta_30d`.

**Swim** (target: `pace_per_100m` sec): `target_hr` (optional), `distance_m`,
`is_pool`, `cumulative_swim_distance_90d`.

### 6.3. Target construction

- Exclude warmup/cooldown через `activity_details.intervals`; иначе avg по
  activity только при `moving_time >= 25 min`.
- Exclude recovery noise: `is_run_recovery_jog(zones, tss)` — **Z1 ≥ 70% AND
  TSS < 40**. Оба условия обязательны: zone-only фильтр ломал pro-атлетов со
  структурным 80/20 base (R² 0.44 → 0.04 без TSS gate). Калибровочная история —
  `docs/knowledge/training-data-hygiene.md`.
- Run: trail и road не смешивать (`type=TrailRun` отфильтрован).
- **Race vs training асимметрия**: модель учится на training, предсказывает
  race. Компенсация — `is_race` feature в train + inference set at `True`.

Persisted-фильтрация — §6.4; live-check остаётся fallback'ом для legacy строк.

### 6.4. Webhook-time noise classification (Phase 1.6, ✅)

Классификация шума выполняется один раз при webhook'е (не на каждом retrain'е)
и пишется в `activities.noise_reason` — verdict виден downstream (chat, ATP
compliance, webapp). Гибрид: persisted tag authoritative, live-check fallback
для строк с `noise_scored_at IS NULL`.

#### 6.4.1. Schema

`activities.noise_reason TEXT NULL` + `noise_scored_at TIMESTAMP NULL`
(migration `aab8c9d0e1f2`) + composite index `(user_id, type, noise_reason)`.

| `noise_reason` | `noise_scored_at` | Meaning |
|---|---|---|
| `NULL` | `NULL` | Not classified yet (legacy / webhook race) |
| `NULL` | `<dt>` | Classified, clean — kept by ML |
| `'run_*'` | `<dt>` | Noise — dropped from train-set |

TEXT + Python `Literal`, не PG ENUM — новые значения (Phase 2 Ride) без DDL.

#### 6.4.2. Enum values

```python
NoiseReason = Literal["run_recovery_jog", "run_walk"]   # Phase 1.6
# Phase 2: + "ride_recovery_spin", "ride_commute", "ride_indoor_test"
```

- `run_recovery_jog` — Z1 ≥ 70% AND TSS < 40.
- `run_walk` — pace > `threshold_pace × 1.6` AND avg_hr < `lthr × 0.65`.

Sport-prefix mandatory (`LIKE 'run_%'` grep'абельность, i18n key derivation).
Deferred: `optical_hr_noisy` (нужна device-strap metadata из FIT), `swim_*`
(small-n).

#### 6.4.3. Per-athlete thresholds

Personalized baseline (`athlete_settings.run.{lthr, threshold_pace}`) × global
multipliers `WALK_PACE_MULT=1.6`, `WALK_HR_MULT=0.65` (constants в
`noise_classifier.py`, не per-user — recalibration = code change + backfill).
Fallback без synced settings: `pace_floor=6:30/km`, `hr_ceil=120`.

#### 6.4.4. Priority order

`classify_noise()` возвращает первое попадание: `run_walk` (mistagged sport,
severe) → `run_recovery_jog` (legit но low-signal) → `None` (clean).

#### 6.4.5. Trigger point

`tasks/actors/activities.py:actor_update_activity_details` — **после**
`ActivityDetail.save()` в той же sync-сессии (zone times появляются только
после `get_activity_detail()`, поэтому webhook-dispatcher — рано). Guards:

- `is_changed` guard — detail не поменялась → reason тот же, skip.
- Defense-in-depth tenant guard: `activity_row.user_id != user.id → return`
  + WHERE-scoping в `Activity.set_noise_classification` (тот же паттерн что
  `_actor_send_activity_notification`).
- `set_noise_classification` не коммитит внутри — caller владеет транзакцией
  (actor: 1 commit/activity; backfill CLI: 1 commit/athlete).
- Idempotent; `noise_scored_at` — disambiguator, не change-indicator.
- `ACTIVITY_UPDATED` (rename) не триггерит — zone/pace не меняются.

#### 6.4.6. Read-side integration

`race_features.py:build_dataset`: persisted `noise_reason` → drop; legacy
(`noise_scored_at IS NULL`) → live `is_run_recovery_jog` fallback; иначе keep.
`is_run_recovery_jog` экспортируется из `noise_classifier.py` — единый source
of truth.

#### 6.4.7. Backfill

`python -m cli classify-noise [--user-id=N] [--since-days=365] [--dry-run]` —
manual operation (не на deploy), per-user try/except + Sentry. Сценарий: новый
classifier в Phase 2 → backfill → weekly retrain видит persisted tags.

---

## 7. Mode 1: Race today

`get_race_projection(mode="today", race_distance_*_m, target_hr_*)` → state_row
из текущего wellness → per-discipline predict (`is_race=True`) → bootstrap CI.

Return (сокр.): `{mode, race_date, current_ctl, splits: {swim: {pace_per_100m_sec,
total_sec, ci_low, ci_high}, ride: {avg_power_w, avg_speed_kmh, ...}, run:
{pace_per_km_sec, ...}}, estimated_finish_time_sec, notes}` — сумма без
транзишн, uncertainty = training variance.

Claude форматирует в текст со сплитами ± CI и мостиком к Mode 2 («до цели CTL N
остаётся X пунктов»).

---

## 8. Mode 2: Race day forecast

1. `race_date` — из параметра или `AthleteGoal.get_goal_dto().event_date`.
2. Overrides поверх сегодняшнего state_row (`_mode2_overrides`):
   - `ctl/atl/tsb` → **линейная экстраполяция** `current_CTL → goal.ctl_target`
     с `CTL_PROJECTION_RATIO_CAP=2.0` + флаг `_ctl_target_unrealistic` при cap'е
     (issue #349 — `fitness_projection` decay вместо плана давал мусор).
   - `ctl_run/ride/swim` → proportionally scale по `projected/current` global CTL
     (webhook не отдаёт per-sport projection).
   - `current_eftp` → `fitness_projection.sport_info_by_type("Ride", "eftp")` @ race_date.
   - `critical_power`/`w_prime`/`p_max`, wellness/HRV/sleep — **сегодняшние**
     (Intervals их не прогнозирует; CI inflation §10.2 покрывает uncertainty).
3. Далее как Mode 1 + CI inflation.

Return дополнительно: `projected_ctl/atl/tsb`, `projected_eftp` vs
`current_eftp`, `days_to_race`, `delta_vs_today` (sec saved per leg), `warnings`.

`fitness_projection` есть не у всех (Intervals Premium depth) —
`FitnessProjection.get → None` ⇒ `{"available": False, "reason": "no_fitness_projection"}`.

---

## 9. MCP tool: `get_race_projection`

### 9.1. Signature

```python
async def get_race_projection(
    mode: Literal["today", "race_day"] = "today",
    race_date: str = "",                  # auto-fill from AthleteGoal.RACE_A
    race_distance_swim_m: int | None = None,
    race_distance_ride_m: int | None = None,
    race_distance_run_m: int | None = None,
    target_hr_ride: int | None = None,    # default: ride_lthr × 0.88
    target_hr_run: int | None = None,     # default: run_lthr × 0.90
    include_transitions: bool = False,    # reserved for Phase 2
) -> dict:
```

### 9.2. Return shape

Единый envelope: `mode`, splits per discipline (§7/§8), CI metadata (§10.2),
`bias_correction_applied`/`bias_fit_method` (§10.5.6), `below_acceptance`
(§14.2), `warnings`.

### 9.3. Error cases

| Case | Return |
|---|---|
| Модель не обучена | `{"available": False, "reason": "model_not_trained", "discipline_missing": [...]}` |
| Модель ниже quality floor | `{"available": False, "reason": "model_below_acceptance"}` (§14.2) |
| Нет `fitness_projection` (Mode 2) | `{"available": False, "reason": "no_fitness_projection"}` |
| `race_date` в прошлом | error string `"race_date must be >= today"` |
| Нет race date нигде | `{"available": False, "reason": "no_race_date", "hint": "use suggest_race or pass race_date"}` |
| Distance не задан для дисциплины | дисциплина пропускается, warning |

---

## 10. Confidence intervals

### 10.1. Bootstrap residuals

Residuals `y_true - y_pred` с train-выборки сохраняются в bundle. Inference:
`ci = (pred + percentile(residuals, 5), pred + percentile(residuals, 95))` —
90% prediction interval, без retrain'а.

### 10.2. Inflation для Mode 2

```python
INFLATION_MAX = 1.8                # cap past ~97 days
MIN_RACE_DAYS_FOR_FORECAST = 14    # within taper — inflation = 1.0

inflation = min(INFLATION_MAX, max(1.0, sqrt(days_to_race / 30)))  # if d > 14
```

**Why the cap** (issue #350): на 126d raw `sqrt`=2.05 давал Run CI ±34 мин на
half-marathon — нечитаемо. **Why taper threshold**: в 14d окне projected_ctl ≈
current_ctl, инфлировать CI шире Mode 1 — ложный сигнал.

**Envelope metadata** (issue #361, schema parity — caller не branch'ится на mode):

| Field | today | race_day |
|---|---|---|
| `ci_level` | 0.90 | 0.90 |
| `inflation` | 1.0 | applied multiplier |
| `inflation_raw` | 1.0 | что хотела sqrt-формула до cap'а |
| `inflation_capped` | False | `inflation < inflation_raw` |

Cap — на множитель, не сужение CI level; поля позволяют Claude честно сказать
«cap engaged, sqrt-scaling unreliable past ~3 months».

### 10.4. Out-of-sample CTL warning

XGBoost **не extrapolat'ит** за train distribution (leaf clipping) — issue #359.
Train-time: `metrics.ctl_feature_p90` в bundle. Predict-time: если
`ctl_<discipline> > p90` → warning per leg («out-of-sample, model held
conservative»). Legacy bundles без поля — skip.

### 10.5. Phase 2 horizon correction

#### 10.5.1–10.5.5. Formula blend (vLT × distance penalty) — 🔴 DEPRECATED

Идея: blend ML с формулой `threshold_pace + distance_penalty(race_type)`,
weight по горизонту. Simulation (`tools/race_blend_simulation.py`, user 1 n=22
races × 5 horizons) — **RED по decision gate**: MAE drop 0.69 sec/km (порог ≥5),
z=1.13 (порог >1.65). Root cause: per-race variance (60 sec/km std) доминирует
над horizon effect (16 sec/km). Closed not-planned (#362), pivot → §10.5.6.
Полная история — git до 2026-06 + #362/#363.

#### 10.5.2. Threshold pace source — eFTP primary (переживает deprecation)

Resolution chain (используется и вне formula blend):

1. `fitness_projection.sport_info_by_type("Run", "eftp")` @ race_date — daily-computed;
2. `wellness.sport_info["Run"]["eftp"]` @ today;
3. `athlete_settings.run.threshold_pace` — stale-prone (обновляется только
   `actor_update_zones` после ramp test);
4. `None`.

Stale guards: `threshold_pace_stale` (level 3 + `updated_at` > 90d),
`threshold_pace_pre_ramp` (projected/current CTL > 1.5 без future eFTP) —
warnings в envelope.

#### 10.5.6. Phase 2.0β2 — ML residual bias correction (✅ SHIPPED 2026-05-12)

Per-athlete bias `bias(d) = a + b·d`, walk-forward mini-simulation по
историческим Run races × horizons `[30..150]`, `np.polyfit` residuals.

- **Train** (`race_train.py:_fit_bias_model`) → `bundle.metrics.{bias_intercept,
  bias_slope, bias_n_races_fit, bias_fit_method}`.
- **Cold-start** (`n_races < 5` или <10 точек) → pool constants
  `bias_constants.py: POOL_BIAS_INTERCEPT=6.178, POOL_BIAS_SLOPE=0.126`
  (method=`pool_fallback`).
- **Predict** (`race_predict.py:_predict_one`): `pred -= a + b·max(days_to_race, 0)`,
  оба mode'а (schema parity). Legacy bundles — skip.
- **Envelope**: `bias_correction_applied: float`, `bias_fit_method: str|None`.
- **Run-only**; Ride/Swim — `out_of_scope` до накопления race data (Phase 2.5).

**Validation (LOO CV, user 1, n=22×5):** MAE 55.04 → 50.04 (−9%), на ≥90d
−6.65 sec/km, z=+2.63 → 🟢 GREEN по gate (≥5 sec/km @ ≥90d AND z>1.65).
**Prod verification** (user 1, 70.3 @ d=126): pred −18.4 sec/km оба mode'а,
fit `a≈6.8, b≈0.125`, n=18 — близко к pool defaults. Closes #359 Q1.

Followups: [#365](https://github.com/radikkhaziev/triathlon-agent/issues/365)
β1 race-feature enrichment (Oracle MAE 42.5 vs ML 55 ⇒ ~7.5 sec/km headroom в
per-race context), [#366](https://github.com/radikkhaziev/triathlon-agent/issues/366)
β2.1 pool retune (trigger: ≥3 athletes × ≥10 races), β3 pool model (§10.6).

### 10.6. Cross-athlete pool model (Phase 2 deferred)

Shared model + warm-start per-user. **Prerequisite: §6.4 noise backfill на всех
атлетах** (pooling грязных данных шумит сильнее). Trigger: 5+ athletes, n≥200 each.

### 10.7. Calibration check

Постфактум после каждой гонки: actual ∈ [ci_low, ci_high] в ~90% случаев,
иначе пересмотр метода. См. §16.

---

## 11. Delivery

Текст через Claude в чате (Phase 1). Webapp `/race-projection` chart — Phase 2
(бэкенд `/api/fitness-projection` готов). В morning report **не добавляем** —
шумно; вызов по запросу.

---

## 12. Training schedule

### 12.1. CLI

`python -m cli train-race-models <user_id>` — три модели, MAE/R² в stdout + Sentry.

### 12.2. Weekly cron — isolated ml-worker (issue #348)

`scheduler_ml_retrain_job` Sun **03:00** Belgrade (`misfire_grace_time=7200,
coalesce=True`). `actor_retrain_progression_model` + `actor_retrain_race_models`
→ `queue_name="ml_retrain"` → выделенный `ml-worker` контейнер
(`--queues ml_retrain --threads 1 --processes 1`) — XGBoost CPU-heavy, sequential
by construction, ночной слот вне user activity. Default worker scoped к
`--queues default`. Depth: `redis-cli LLEN dramatiq:ml_retrain`.

### 12.3. Acceptance bar per discipline (mature-system target)

| Discipline | MAE | R² |
|---|---|---|
| Run (sec/km) | ≤ 10 | ≥ 0.50 |
| Ride (W) | ≤ 15 | ≥ 0.40 |
| Swim (sec/100m) | ≤ 8 | ≥ 0.30 |

На реальных данных Phase 1 **не достигнут** — ship'нуто как ranking signal под
quality gate (§14.2, решение Variant A 2026-05-12). Бар остаётся целью для
зрелой системы (3-6 мес чистых данных + Phase 2).

---

## 13. Storage

Bundle: `static/models/race_{user_id}_{discipline}.joblib` —
`{model, feature_names, residuals, trained_at, metrics}` (~5-10 MB each, в
`.gitignore`). Опциональная таблица `race_projections` (predictions log для
post-race calibration §10.7) — Phase 2, не создана.

---

## 14. Acceptance criteria

### 14.1. Shipped phases

- **Phase 1 (✅ 2026-05-11):** `race_features.py` / `race_predict.py` /
  `race_train.py`, MCP tool оба mode'а + error envelopes, bootstrap CI +
  inflation, weekly retrain actor (`time_limit=600s, max_retries=0`,
  `InsufficientDataError` skip), chat prompt секция + tool_filter group,
  weekly report строка (gated `goal_event_date ∈ [30, 200]`), migration
  `b8c9d0e1f2a3` (`fitness_projection.sport_info` + `sport_info_by_type`),
  CLI `train-race-models`.
- **Phase 1.5 (✅ 2026-05-11, TSS gate 2026-05-12):** recovery-jog фильтр §6.3
  (`Z1_RECOVERY_THRESHOLD=0.70`, `RECOVERY_TSS_CEILING=40.0`), Run-only,
  missing-data passthrough, `n_filtered_recovery` лог. Тесты: 13+9+2 cases.
- **Phase 1.6 (✅ 2026-05-12):** §6.4 целиком — migration `aab8c9d0e1f2`,
  `noise_classifier.py`, `Activity.set_noise_classification`, actor wiring +
  tenant guard, read-side fallback, CLI `classify-noise`. Тесты: 33+3+3 cases.
- **Phase 1.7 (✅ 2026-05-12):** inflation cap + OOS warning (§10.2, §10.4),
  7 тестов.
- **Phase 2.0β2 (✅ 2026-05-12):** bias correction §10.5.6.

Открыто:

- [ ] Прогнать утренний / weekly один цикл, убедиться что Claude вызывает тул
  и форматирует строку с CI inflation cap + OOS warning surface'ом.

### 14.2. Quality gate (✅ 2026-05-11)

`ModelBelowAcceptance` + `_enforce_quality_gate(bundle, discipline)` в
`race_predict.py`. Floors `_QUALITY_FLOORS`: Run R²≥0.20/MAE≤40, Ride
0.20/25W, Swim 0.05/15 — калиброваны по 5 реальным атлетам 2026-05-12. Legacy
bundles без `metrics` — passthrough; NaN guard. Envelope:
`below_acceptance: list[str]`; MCP reason `model_below_acceptance` ≠
`model_not_trained`.

**Validation (2026-05-12, model-level):** user 1 — Run R²=+0.22 (ranking
signal), Ride/Swim корректно blocked; user 62 (первый full-triathlon атлет,
чистые данные) — все три прошли gate, Swim MAE 7.2 ниже target ≤8. Run R²
ceiling для clean athletes ~0.22-0.45 — data-distribution-driven (train CTL
15-45 не учит race-pace @ CTL 60+), не noise-driven; root fix — §10.5.6 + #365.

---

## 15. Implementation order

Выполнен — см. §14.1. Историческая последовательность в git.

---

## 16. Testing

Unit: `tests/ml/test_race_features.py`, `test_race_predict.py`,
`test_noise_classifier.py`. Integration: `tests/mcp/test_race_projection.py`,
`tests/tasks/test_retrain_actor.py`, `test_ml_retrain_queue.py`.

### Post-race calibration (2026-09-15 — Ironman 70.3 Belgrade)

1. Actual splits → `races` через `tag_race`.
2. Compare vs прогноз, записанный за N дней до гонки.
3. Проверить попадание в CI per discipline; систематический промах → tune
   inflation (§10.2) / bias constants (§10.5.6).

---

## 17. Open questions

- **Transitions.** Решено для MVP: tool не прибавляет T1/T2 — честный
  swim+bike+run output, юзер накидывает сам. Параметр `transition_sec` —
  возможный Phase 2.
- **Historical race weather в train data.** `ACTIVITY_UPLOADED` приносит
  weather-поля — кандидаты `race_temp_c`, `race_heat_stress`. Phase 2, в
  составе #365 race-feature enrichment.
- **Webhook data availability для бэкфилла.** `ctl_run/ride/swim` (§6.1) и MMP
  (§6.2) надёжно приходят только после включения dispatchers (2026-04-11);
  старые `wellness.sport_info` могут быть `None`. **Pending** — бэкфилл из
  Intervals REST `/wellness/{id}`, см. `WEBHOOK_DATA_CAPTURE_SPEC.md` §6.
- **`fitness_projection` accuracy.** Если атлет не ведёт Intervals calendar —
  projection плоская. Mode 2 уже fallback'ится на линейную экстраполяцию к
  `ctl_target` (#349); warning при обрыве projection раньше race day остаётся
  желательным.

---

## 18. Open issues (post-Phase 1)

Закрытые issue не удаляем — историческая привязка к спеке.

### 🔴 Blockers

(none)

### ✅ Shipped 2026-05-12 — one-liners

- **#349** — Mode 2 линейная экстраполяция к `ctl_target` + `CTL_PROJECTION_RATIO_CAP=2.0` (§8).
- **#350** — CI inflation cap + min-days threshold (§10.2).
- **#359** — closed obsolete-by-children (Q1→#363, Q2→#361).
- **#361** — CI envelope metadata (`ci_level`/`inflation`/`inflation_raw`/`inflation_capped`, §10.2).
- **#362** — formula blend, closed not-planned (🔴 RED gate, §10.5.1-10.5.5).
- **#363** — Phase 2.0β2 bias correction (§10.5.6).
- **#348** — ml_retrain queue isolation + Sun 03:00 cron (§12.2) + fix `static_data` volume mount у default worker.
- **Phase 1.6** — webhook-time noise classification (§6.4).

### 🟢 Phase 2 — deferred

- **[#365](https://github.com/radikkhaziev/triathlon-agent/issues/365)** — β1
  race-feature enrichment (`is_trail`/`surface`/`elevation_per_km`/`weather_temp_c`/
  `race_type`); ~7.5 sec/km headroom. Старт после 2-4 недель prod baseline β2.
- **[#366](https://github.com/radikkhaziev/triathlon-agent/issues/366)** — β2.1
  pool constants retune. Trigger: ≥3 athletes × ≥10 races each.
- **β3 / §10.6** — cross-athlete pool model; prerequisite — noise backfill на всех.
- **Scenario engine** («miss 2 weeks» / «+10% volume») — hypothetical-CTL поверх projection.
- **Webapp `/race-projection` page** — CTL trajectory + CI bars; backend готов.
- **Race-specific Ride/Swim calibration** — ждёт ≥10 non-Run race events.
- **Phase 2 Ride noise classifiers** — `ride_recovery_spin`/`ride_commute`/`ride_indoor_test`
  (§6.4.2); infra готова, нужна empirical calibration.
- **Optical-HR noise detection** — требует FIT device-strap metadata.

### 🟡 Cross-spec deps

- **[#356](https://github.com/radikkhaziev/triathlon-agent/issues/356)** —
  Coach/Trainer skill использует `knowledge/training-data-hygiene.md`; лучше
  данные → больше моделей проходит quality gate.
