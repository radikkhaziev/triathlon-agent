# Training Progression Model

> ML-модель, предсказывающая **Δ threshold с последнего PB** на основе тренировочных паттернов. Отвечает на вопрос «что менять в тренировках, чтобы прогрессировать», а не «как я восстановился» (#63) и не «как проеду гонку» (#64).

---

## Мотивация

В статье [Ryan Anderson — Running Smart with Machine Learning and Strava](https://medium.com/data-science/running-smart-with-machine-learning-and-strava-9ba186decde0) автор собрал данные Strava от множества бегунов и показал:

1. **Лучшая target variable — не абсолютный VDOT, а Δ VDOT между PB'ами.** Модель на Δ устойчивее к confounders (возраст, генетика, дистанция).
2. **Decision Tree / XGBoost с R² 0.6–0.89** — достаточно. Нейросети не нужны, фокус на feature engineering.
3. **Главные предикторы PB:** объём, количество пробежек, **поляризация зон** (больше Z1+Z2 и Z4+Z5, меньше Z3).

У нас есть данные **богаче**, чем у автора (Strava only):
- `thresholds_history` — история LTHR/FTP/pace (наша target variable).
- `races` — race-day снапшоты (CTL/ATL/TSB/HRV/recovery).
- `activity_details.intervals` — точные распределения по зонам.
- Wellness / HRV / RHR / Sleep / IQOS / Mood — lifestyle-факторы, которых у Ryan'а не было.

Это позволяет построить **coaching-модель**: показывать атлету через SHAP, какие паттерны тренировок/восстановления двигают его форму.

---

## Scope и разграничение

| Модель                             | Target                | Horizon       | Use case                                    |
| ---------------------------------- | --------------------- | ------------- | ------------------------------------------- |
| **#63 Recovery prediction**        | HRV / RHR завтра      | 1 день        | «Нагрузить или отдохнуть»                   |
| **#64 Race projection**            | Сплиты на гонке       | 2–5 месяцев   | «Как проеду 70.3»                           |
| **Этот spec (Training Progression)** | Δ threshold до след. PB | недели–месяцы | «Какие паттерны двигают мою форму»          |

Все три — разные задачи. Общая инфра (feature store, pipeline, SHAP) переиспользуется.

---

## Target Variable

### Определение

**`delta_threshold`** — изменение sport-specific threshold между двумя последовательными улучшениями («PB»):

- Run → `threshold_pace` (сек/км), инверт: меньше = лучше.
- Ride → `ftp` (Вт/кг, нормализация по весу).
- Swim → `css` (пороговая плавательная скорость, сек/100м), инверт.

### Извлечение PB из данных

```python
# Алгоритм формирования примеров для обучения:
# 1. Из thresholds_history берём все записи по sport, сортируем по дате.
# 2. Идём вперёд, ищем точки-улучшения (PB): monotonic improvement vs last_pb.
# 3. Для каждой пары (pb_prev → pb_next):
#    - features рассчитываются на окне [pb_prev, pb_next − 7d] (хвост не включаем)
#    - target = pb_next.value − pb_prev.value (с учётом инверта)
#    - weeks_since_last_pb = weeks_between — как feature, не weight
#    NOTE: не штрафуем длинные gap'ы вручную — модель сама решит,
#    является ли длинный gap plateau или травмой (через HRV/wellness features)
```

### Альтернативные targets (Phase 2)

- **`time_to_pb`** — сколько недель до следующего улучшения.
- **`race_performance_delta`** — изменение race-pace между гонками одной дистанции (использует `races`).

---

## Feature Set

Все фичи считаются **на окне перед PB** (не после — избегаем leakage).

### Training volume

- `weekly_hours_mean`, `weekly_hours_std`, `weekly_hours_max` за 4/8/12 недель.
- `n_sessions_per_week_mean` — количество тренировок.
- `longest_session_hours` — самая длинная тренировка окна.
- `volume_ramp_rate` — наклон линейной регрессии объёма (TSS/неделю).
- `weeks_since_last_pb` — сколько недель с предыдущего PB (длинный gap = plateau или травма, модель различит через wellness/HRV).

### Polarization (см. [POLARIZATION_INDEX.md](POLARIZATION_INDEX.md))

- `low_pct`, `mid_pct`, `high_pct` — 4/8-недельное окно.
- `polarization_pattern` — one-hot encoding (`polarized` / `pyramidal` / `threshold` / `too_easy` / `too_hard`).
- `z3_drift` — тренд `mid_pct` (растёт ли серая зона?).

### Training load

- `ctl_mean`, `ctl_max`, `ctl_ramp_rate` за 8 недель.
- `atl_max` — пик острой усталости.
- `tsb_days_below_minus_20` — сколько дней глубокой усталости.
- `tsb_days_above_plus_10` — сколько дней «детренированности».

### Wellness / Recovery (наше преимущество над Strava)

- `hrv_rmssd_mean`, `hrv_baseline_trend` — динамика HRV.
- `rhr_mean`, `rhr_ratio_vs_baseline` — RHR-стресс.
- `sleep_hours_mean`, `sleep_hours_std`.
- `recovery_score_mean`, `days_recovery_below_40` — сколько «красных» дней.

### Efficiency

- `ef_trend_run`, `ef_trend_ride` — наклон aerobic efficiency.
- `decoupling_median` — медиана Pa:Hr decoupling за окно.
- `dfa_a1_mean` — индикатор aerobic base (когда данные есть).

### Compliance

- `compliance_ratio` — доля выполненных запланированных тренировок (из `training_log`).
- `unplanned_sessions` — доля «импульсивных» активностей.

---

## Архитектура

### Pipeline

```
1. Feature extraction (Python, pandas):
   extract_progression_dataset(user_id) →
     DataFrame of (pb_prev, pb_next) rows with features + target
   ↓
2. Model training (XGBoost):
   - Walk-forward cross-validation (chronological, TimeSeriesSplit)
   - Hyperparameter search: Optuna, 50 trials
   - Metrics: MAE, R², Spearman correlation (ranking важнее абсолюта)
   ↓
3. SHAP explainability:
   - Global: SHAP summary plot (какие фичи важны в среднем)
   - Local: SHAP waterfall для последнего PB атлета
   ↓
4. Serving:
   - MCP tool get_progression_analysis()
   - Возвращает: top-5 positive factors, top-5 negative, advice_text
```

### Хранение

Новая таблица (миграция Alembic):

```sql
CREATE TABLE progression_model_runs (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    sport VARCHAR(16) NOT NULL,
    trained_at TIMESTAMP NOT NULL,
    n_examples INT,
    mae FLOAT,
    r2 FLOAT,
    model_path VARCHAR(200), -- static/models/{user_id}_{sport}_{timestamp}.joblib
    shap_global_json JSONB,  -- feature importances для UI
    CONSTRAINT uq_user_sport_trained UNIQUE(user_id, sport, trained_at)
);
```

Модели хранятся на диске в `static/models/` — joblib файлы. В БД только путь + метрики.

### Частота переобучения

Cron: **раз в неделю**, воскресенье 20:00 (после weekly report). Запуск через Dramatiq actor `actor_retrain_progression_model`.

Триггер принудительного переобучения — новый PB в `thresholds_history` (webhook от Intervals.icu SPORT_SETTINGS_UPDATED).

---

## Phases

### Phase 1 — Offline POC (1–2 недели)

1. Jupyter-ноутбук в `docs/knowledge/progression_poc.ipynb`.
2. Собрать датасет на owner (user_id=1) за всю историю.
3. Прогнать XGBoost с walk-forward CV.
4. Построить SHAP summary + waterfall.
5. Принять решение go/no-go по R² и интерпретируемости.

**Acceptance POC:** R² ≥ 0.5 хотя бы по одному виду спорта. Если меньше — пересмотреть target / фичи, не переходить в Phase 2.

### Phase 2 — Production pipeline

1. Модуль `data/ml/progression.py` — feature extraction + training.
2. Миграция `progression_model_runs`.
3. Dramatiq actor `actor_retrain_progression_model` + weekly scheduler.
4. MCP tool `get_progression_analysis(sport)`.
5. Bot command `/progress` — показать SHAP waterfall атлету.
6. Webapp `/dashboard` — вкладка «Progression» с графиком и топ-факторами.

### Phase 3 — Coaching integration

1. Morning report: если прошло >6 недель без PB — включать SHAP top-3 в контекст Claude.
2. Weekly report: рекомендация «на основе твоей модели — больше Z4+Z5 сократит время до следующего PB».
3. A/B-эксперимент: рекомендации → compliance → реальный результат. Фиксировать в `training_log.post_notes`.

---

## Data sparsity — риски

- У одного атлета **мало PB**: 5–15 за год. Модель может переобучиться.
- **Mitigation:** leave-one-out CV, Bayesian priors (sklearn's `TransformedTargetRegressor`), или pooling нескольких атлетов (anonymized) после Phase 2.
- **Cold start:** если у пользователя <3 PB по виду спорта — не обучаем, показываем «нужно больше данных».

---

## Отличие от #63 и #64

| Аспект             | #63                  | #64                    | Training Progression     |
| ------------------ | -------------------- | ---------------------- | ------------------------ |
| Target             | HRV/RHR завтра       | Race splits            | Δ threshold между PB     |
| Horizon            | 1 день               | 5 месяцев              | 4–16 недель              |
| Модель             | XGBoost              | Banister + XGBoost     | XGBoost + SHAP           |
| Physics            | Нет                  | Banister impulse-resp  | Нет                      |
| Главная фича       | Training load / sleep | CTL проекция          | Polarization + volume    |
| Обновление         | Ежедневно (inference) | По запросу             | Еженедельно (retrain)    |
| Shared infra       | Feature store        | Feature store + CTL    | Feature store + Polarization |

Polarization Index (см. отдельный spec) — shared infra для всех трёх.

---

## Acceptance

### Phase 1 (POC) — ✅ Завершён (2026-04-19)

**Target variable изменён:** discrete PB events → continuous Δ EF (weekly efficiency factor change).
Причина: слишком мало PB events (Run: 3, Ride: 4 → 2-3 training examples, XGBoost не работает).
С Δ EF: Run 89 examples, Ride 30 examples.

**EF формула:** `speed(m/min) / avg_HR`. Для Run используется GAP (grade-adjusted pace) для нормализации рельефа. EF backfill сохраняется в `activity_details.efficiency_factor` при sync.

**Результаты walk-forward CV (TimeSeriesSplit, 5 folds):**

| Sport | Examples | R² | Correlation | Verdict |
|---|---|---|---|---|
| **Ride** | 30 | 0.035 | **0.332** | ✅ Go — ranking signal, SHAP insights логичны |
| **Run** | 89 | -0.304 | -0.022 | ❌ No-go — нет предсказательной силы |

**SHAP insights (Ride — top features):**
- `total_tss_all` ↓ — больше общего объёма → EF падает (перетренировка)
- `n_sessions` ↑ — consistency → EF растёт
- `decoupling_median` ↓ — меньше cardiac drift → EF растёт
- `recovery_mean` — лучше recovery → EF растёт
- `ctl_delta` ↑ — рост CTL → рост EF

**Почему Run не работает:** EF бега слишком зависит от внешних условий (температура, поверхность, обувь, ноги после вчерашней вело). GAP нормализует рельеф, но не остальные факторы. Bike на тренажёре + power meter — контролируемые условия, чистый сигнал.

**Вывод:** Phase 2 только для Ride. Run — отложить до:
- Фильтрации только treadmill / flat road runs
- Или использования race pace delta вместо EF (22 гонки у user 1)

**Файлы POC:** `docs/knowledge/progression_poc.py` (v1, discrete PB), `docs/knowledge/progression_poc_v2.py` (v2, continuous EF).

### Phase 2 (Production) — ✅ Завершён (2026-04-19), Ride only
- [x] `data/ml/progression.py` — feature extraction, training, SHAP analysis.
- [x] Alembic миграция `progression_model_runs` (model_path в `static/models/`).
- [x] Dramatiq actor `actor_retrain_progression_model` + weekly cron (Sunday 16:00).
- [x] MCP tool `get_progression_analysis(sport='Ride')` (51-й tool).
- [x] SHAP top-5 factors в weekly report (секция "ML insights").
- [x] Webapp widget — `ProgressionWidget` на `/progress` (Ride), `/api/progression` endpoint.

### Phase 3 (Coaching) — ✅ Завершён
- [x] Weekly report enrichment — `get_progression_analysis` в tool sequence + секция "ML insights".
- Morning report enrichment — решено не делать (SHAP insights долгосрочные, не actionable для одного дня).
- Лог рекомендаций в `post_notes` — отложен до масштабирования (нужно 5+ атлетов и 3+ месяцев данных для A/B).

---

## Вне scope

- **Run progression** — EF сигнал слишком шумный (R²=-0.3). Ждёт: treadmill filter или race pace delta target.
- **Multi-athlete pooled model** — отложено до Phase 4 (нужен consent + anonymization).
- **Unsupervised workout clustering** — остаётся в #63 POC 3.
- **Real-time inference** — retrain еженедельно, inference раз в неделю. Не real-time.
- **Swim progression** — откладываем: PB по CSS редки, zone mapping слабее.

---

## Связанные issue / spec

- **Depends on:** [POLARIZATION_INDEX.md](POLARIZATION_INDEX.md) — ✅ реализован (MCP tool + API + webapp widget).
- **Related:**
  - Issue #63 (Recovery prediction) — другой target, общая инфра SHAP + feature store.
  - Issue #64 (Race projection) — другой horizon, общая инфра.
- **Reference:**
  - [Ryan Anderson — Running Smart with ML and Strava](https://medium.com/data-science/running-smart-with-machine-learning-and-strava-9ba186decde0)
  - Banister, T.W. (1991) "Modeling elite athletic performance."
  - Lundberg, S. (2017) "A Unified Approach to Interpreting Model Predictions" (SHAP).
