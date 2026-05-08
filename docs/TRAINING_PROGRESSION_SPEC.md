# Training Progression Model

> ML-модель Δ EF (weekly efficiency factor change) — отвечает на вопрос «какие паттерны тренировок двигают форму», а не «как я восстановился» (#63) и не «как проеду гонку» (#64).

**Status:** ✅ Phase 1 (POC) + Phase 2 (Production) + Phase 3 (Coaching integration) shipped 2026-04-19. **Ride only** — Run/Swim не прошли validation.

**Code anchors:**

| Concern | File |
|---|---|
| Feature extraction + training + SHAP | `data/ml/progression.py` |
| Migration | `progression_model_runs` table |
| Weekly retrain cron | `actor_retrain_progression_model` (Sunday 16:00) |
| MCP tool | `get_progression_analysis(sport='Ride')` |
| Weekly report enrichment | «ML insights» секция |
| Webapp | `ProgressionWidget` на `/progress`, `/api/progression` endpoint |

---

## 1. Мотивация

[Ryan Anderson — Running Smart with ML and Strava](https://medium.com/data-science/running-smart-with-machine-learning-and-strava-9ba186decde0): Decision Tree / XGBoost с R² 0.6–0.89 на простых фичах (volume, polarization). Главные предикторы PB — объём, количество пробежек, поляризация (больше Z1+Z2 и Z4+Z5, меньше Z3). Нейросети не нужны, фокус на feature engineering.

Наши данные богаче Strava: `thresholds_history`, `races`, `activity_details.intervals` (точные зоны), wellness/HRV/RHR/sleep/IQOS/mood. Coaching-модель показывает атлету через SHAP, какие паттерны двигают форму.

---

## 2. Scope vs #63 / #64

| Модель | Target | Horizon | Use case |
|---|---|---|---|
| **#63 Recovery prediction** | HRV / RHR завтра | 1 день | «Нагрузить или отдохнуть» |
| **#64 Race projection** | Сплиты на гонке | 2–5 месяцев | «Как проеду 70.3» |
| **Этот spec** | Δ EF (weekly) | недели–месяцы | «Какие паттерны двигают форму» |

Все три — разные задачи. Общая инфра (feature store, pipeline, SHAP) переиспользуется. Polarization Index — shared infra.

---

## 3. Target Variable — Δ EF (после Phase 1 pivot)

**Original plan:** Δ threshold между последовательными PB'ами (`threshold_pace` для Run, `ftp` для Ride, `css` для Swim).

**Pivoted в Phase 1:** discrete PB events дают слишком мало training examples (Run: 3 PB → 2-3 examples, XGBoost не работает). Перешли на continuous Δ EF (weekly efficiency factor change).

**EF формула:** `speed(m/min) / avg_HR`. Для Run использовался GAP (grade-adjusted pace) для нормализации рельефа. EF backfill сохраняется в `activity_details.efficiency_factor` при sync.

**Examples после pivot:** Ride 30, Run 89.

---

## 4. Feature Set

Все фичи считаются на окне перед training point (не после — leakage prevention). Шесть категорий:

- **Training volume** — `weekly_hours_{mean,std,max}` (4/8/12 нед.), `n_sessions_per_week_mean`, `longest_session_hours`, `volume_ramp_rate` (slope регрессии TSS/неделю), `weeks_since_last_pb`.
- **Polarization** (см. POLARIZATION_INDEX.md) — `low_pct` / `mid_pct` / `high_pct` (4/8 нед.), `polarization_pattern` one-hot (`polarized` / `pyramidal` / `threshold` / `too_easy` / `too_hard`), `z3_drift` (тренд mid_pct).
- **Training load** — `ctl_{mean,max}`, `ctl_ramp_rate` за 8 нед., `atl_max`, `tsb_days_{below_minus_20,above_plus_10}`.
- **Wellness / Recovery** (наше преимущество над Strava) — `hrv_rmssd_mean`, `hrv_baseline_trend`, `rhr_{mean,ratio_vs_baseline}`, `sleep_hours_{mean,std}`, `recovery_score_mean`, `days_recovery_below_40`.
- **Efficiency** — `ef_trend_{run,ride}`, `decoupling_median`, `dfa_a1_mean`.
- **Compliance** — `compliance_ratio` (доля выполненных запланированных тренировок), `unplanned_sessions`.

---

## 5. Architecture

**Pipeline:** feature extraction (Python / pandas) → XGBoost training с walk-forward TimeSeriesSplit CV (Optuna 50 trials, hyperparameter search) → SHAP global summary + local waterfall → MCP tool возвращает top-5 positive / top-5 negative factors + advice_text.

**Storage:** `progression_model_runs` table tracks runs (user_id, sport, trained_at, n_examples, mae, r2, model_path, shap_global_json JSONB). Joblib-файлы моделей живут на диске в `static/models/{user_id}_{sport}_{timestamp}.joblib`. В БД только путь + метрики.

**Cadence:** weekly retrain Sunday 16:00 (после weekly report). Триггер принудительного — новый PB через `SPORT_SETTINGS_UPDATED` webhook.

---

## 6. Phase 1 POC results (2026-04-19)

**Walk-forward CV (TimeSeriesSplit, 5 folds):**

| Sport | Examples | R² | Correlation | Verdict |
|---|---|---|---|---|
| **Ride** | 30 | 0.035 | **0.332** | ✅ Go — ranking signal, SHAP insights логичны |
| **Run** | 89 | -0.304 | -0.022 | ❌ No-go — нет предсказательной силы |

**SHAP insights (Ride — top features):**
- `total_tss_all` ↓ — больше общего объёма → EF падает (перетренировка)
- `n_sessions` ↑ — consistency → EF растёт
- `decoupling_median` ↓ — меньше cardiac drift → EF растёт
- `recovery_mean` ↑ — лучше recovery → EF растёт
- `ctl_delta` ↑ — рост CTL → рост EF

**Почему Run не работает:** EF бега слишком зависит от внешних условий (температура, поверхность, обувь, ноги после вчерашней вело). GAP нормализует рельеф, но не остальные факторы. Bike на тренажёре + power meter — контролируемые условия, чистый сигнал.

**Вывод:** Phase 2 только для Ride. Run — отложить до:
- Фильтрации только treadmill / flat road runs.
- Или использования race pace delta вместо EF (у user 1 — 22 гонки).

---

## 7. Data sparsity — open caveat

- У одного атлета мало training points: 30 (Ride) — это всё ещё тонкий датасет, R²=0.035 коррелирует, но не предсказывает абсолют.
- **Mitigation:** leave-one-out CV, Bayesian priors (sklearn's `TransformedTargetRegressor`), или pooling нескольких атлетов (anonymized) после Phase 4.
- **Cold start:** если у пользователя <3 PB / <10 examples по виду спорта — не обучаем, показываем «нужно больше данных».

---

## 8. Decisions log

- **Δ EF, не Δ threshold.** Phase 1 pivot — discrete PB events дают 2-3 examples, XGBoost не работает. Continuous Δ EF даёт 30+ (Ride) / 89 (Run) examples.
- **Ride only.** Run R²=-0.3 — внешние факторы (рельеф, поверхность, температура, обувь) затмевают тренировочный сигнал. Bike на trainer'е + power meter = controlled conditions, чистый сигнал.
- **Morning report enrichment отменён.** SHAP insights долгосрочные, не actionable для одного дня. ML insights только в weekly report.
- **A/B logging в `post_notes` отложен.** Нужно 5+ атлетов и 3+ месяцев данных для значимого A/B. Возвращаемся при scaling.

---

## 9. Вне scope

- **Run progression** — EF сигнал шумный (R²=-0.3). Ждёт: treadmill filter или race pace delta target.
- **Multi-athlete pooled model** — Phase 4, нужен consent + anonymization.
- **Unsupervised workout clustering** — остаётся в #63 POC 3.
- **Real-time inference** — retrain еженедельно, inference раз в неделю.
- **Swim progression** — PB по CSS редки, zone mapping слабее.

---

## 10. References

- **Depends on:** [POLARIZATION_INDEX.md](POLARIZATION_INDEX.md) — ✅ реализован (MCP tool + API + webapp widget).
- **Related:** Issue #63 (Recovery prediction), Issue #64 (Race projection) — другой target/horizon, общая инфра SHAP + feature store.
- **Theory:**
  - [Ryan Anderson — Running Smart with ML and Strava](https://medium.com/data-science/running-smart-with-machine-learning-and-strava-9ba186decde0)
  - Banister T.W. (1991) «Modeling elite athletic performance»
  - Lundberg S. (2017) «A Unified Approach to Interpreting Model Predictions» (SHAP)
