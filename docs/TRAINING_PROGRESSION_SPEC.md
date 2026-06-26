# Training Progression Model

**Status:** ✅ Phase 1 (POC) + Phase 2 (Production) + Phase 3 (Coaching integration) shipped 2026-04-19. **Ride only** — Run/Swim не прошли validation.

Target: Δ EF (weekly efficiency factor change) — отвечает на «какие паттерны тренировок двигают форму». Сосед: `#63` (recovery prediction, 1-day target), `#64`/`ML_RACE_PROJECTION_SPEC` (race splits, 2-5 month target). Общая инфра — `data/ml/`, SHAP, feature store, polarization helper.

---

## Where the code lives

| Concern | File |
|---|---|
| Feature extraction + training + SHAP | `data/ml/progression.py` |
| State table | `progression_model_runs` (run metadata, SHAP global JSON, model path, MAE/R²) |
| Weekly retrain cron | `actor_retrain_progression_model` (Sunday 03:00 Belgrade via `scheduler_ml_retrain_job`, `misfire_grace_time=7200, coalesce=True`, isolated `ml_retrain` Dramatiq queue — see issue #348) |
| MCP tool | `get_progression_analysis(sport='Ride')` |
| Weekly report enrichment | tool whitelisted in `tasks/tools.py` weekly-report MCP loop → «ML insights» в `get_system_prompt_weekly` (`bot/prompts.py`) |
| Model artifacts | `static/models/{user_id}_{sport}_{timestamp}.joblib` |

> No dedicated webapp surface or `/api/progression` endpoint — read-only via MCP / weekly report only (Phase 3 = coaching integration, not a dashboard).

---

## Phase 1 POC results (2026-04-19)

Walk-forward CV (TimeSeriesSplit, 5 folds):

| Sport | Examples | R² | Correlation | Verdict |
|---|---|---|---|---|
| **Ride** | 30 | 0.035 | 0.332 | ✅ Go — ranking signal valid, SHAP insights логичны |
| **Run** | 89 | -0.304 | -0.022 | ❌ No-go — нет предсказательной силы |

EF бега слишком зависит от внешних условий (температура, поверхность, обувь, fatigue от вчерашней вело). GAP нормализует рельеф, но не остальные факторы. Bike на тренажёре + power meter — controlled, чистый сигнал.

---

## Decisions log

1. **Δ EF, не Δ threshold (Phase 1 pivot).** Original plan: discrete PB events (Δ `threshold_pace` / `ftp` / `css`). Run давал 3 PB → 2-3 examples, XGBoost не работает. Pivot на continuous Δ EF (weekly EF change) дал Ride 30 / Run 89 examples. EF = `speed(m/min) / avg_HR`; Run uses GAP-adjusted pace; backfill в `activity_details.efficiency_factor` при sync.
2. **Ride only.** Run R²=-0.3 — external factors затмевают training signal. Откладываем до treadmill-filter или smene target'а (race pace Δ; у user 1 22 race events).
3. **Morning report enrichment отменён.** SHAP insights долгосрочные, не actionable для одного дня. Только в weekly report.
4. **A/B logging в `post_notes` отложен.** Нужно 5+ атлетов и 3+ месяцев данных для значимого A/B — возвращаемся при scaling.

---

## Pending / out-of-scope

- **Run progression** — ждёт treadmill-filter или race-pace-Δ target. Cold start если у пользователя <3 PB или <10 examples → не обучаем.
- **Multi-athlete pooled model** — Phase 4, требует consent + anonymization.
- **Swim progression** — PB по CSS редки, zone mapping слабее. Не в плане.
- **Unsupervised workout clustering** — `#63` POC 3.

---

## References

- `docs/INTENSITY_DISTRIBUTION_SPEC.md` — polarization helper, shared infra dependency (реализован).
- `docs/ML_RACE_PROJECTION_SPEC.md`, `docs/ML_HRV_PREDICTION_SPEC.md` — sibling ML specs, общий `data/ml/` namespace.
- Ryan Anderson — *Running Smart with ML and Strava* (Medium): простые фичи (volume + polarization), XGBoost R² 0.6-0.89 — основа подхода.
- Banister T.W. (1991) — *Modeling elite athletic performance*.
- Lundberg S. (2017) — *SHAP: A Unified Approach to Interpreting Model Predictions*.
