# ML HRV/Recovery Prediction Spec

> XGBoost + SHAP для предсказания next-morning HRV/RHR по тренировочной нагрузке,
> сну, образу жизни (IQOS, bedtime, стресс). Индивидуальный анализ причин
> восстановления: «почему сегодня плохо» через SHAP-waterfall, «что системно
> убивает recovery» через SHAP-summary.
>
> Актуализация issue [#63](https://github.com/radikkhaziev/triathlon-agent/issues/63) по состоянию на 2026-04-20.

**Related:**

| Issue / Spec / code | Связь |
|---|---|
| [#63](https://github.com/radikkhaziev/triathlon-agent/issues/63) | Основной трекер; эта спека — актуализация body |
| [#104](https://github.com/radikkhaziev/triathlon-agent/issues/104) | Garmin GDPR import — **closed 2026-04-11**, data pipeline готов |
| [#109](https://github.com/radikkhaziev/triathlon-agent/issues/109) | Training log cleanup — **closed 2026-04-11**, исторические данные нормализованы |
| [#66](https://github.com/radikkhaziev/triathlon-agent/issues/66) | ML readiness prediction — **merged into #63** 2026-04-11 |
| [#215](https://github.com/radikkhaziev/triathlon-agent/issues/215) | Data enrichment для ML — ещё open, покрывает хвостовые фичи |
| `docs/ADAPTIVE_TRAINING_PLAN.md:691-709` | `compute_personal_patterns()` — статистический предшественник POC 1 (без XGBoost) |
| `data/garmin/` | GDPR-парсер и импортер; источник sleep/stress/readiness |
| `data/db/garmin_*` | 9 таблиц, куда ложатся парсенные Garmin-данные |
| `data/db/wellness.py`, `activity.py`, `iqos_daily.py`, `mood_checkins.py` | Остальные data sources |
| `pyproject.toml` | `xgboost>=3.2.0`, `shap>=0.51.0` уже установлены |

---

## 1. Мотивация

Сейчас recovery-аналитика — дескриптивная: `recovery_score` от 0 до 100, categorical (`good` / `moderate` / `low`), пороги по HRV/RHR/sleep/Banister. Пользователь видит **результат**, но не видит **причину**: почему утром HRV упал, что конкретно его тянет вниз.

**Почему это важно именно для этого атлета:**

- `iqos_daily.count` после 20:00 — потенциально сильный предиктор HRV-drop. Никакой публичный сервис про это не знает.
- Bedtime (`sleep.sleep_start`) — уходит в фиче «bedtime_after_23h_flag», проверяется против HRV через SHAP.
- Стресс из Garmin (stress avg, Body Battery drop) — pull есть, корреляции не проверены.
- Mood check-ins — есть, но не использовались как предиктор.

**Что получаем:**

- **Daily SHAP waterfall** в morning report: «HRV 45 (−12 vs baseline). Основные вклады: поздний bedtime −4, IQOS 12 шт −3, тяжёлый workout вчера −3, mood/anxiety 2/5 −2.»
- **Global SHAP summary** (еженедельно): «Топ-5 recovery-killers на 90-дневном окне: late bedtime, IQOS evening, cumulative TSS, low sleep score, high stress avg.»
- **Actionable recommendations**: «Если bedtime < 22:30, ожидаемый HRV +5. Если IQOS ≤ 6 шт, +3.»

---

## 2. Scope

### Phase 1 (MVP) — делаем сейчас

- **POC 1: predict next-morning HRV** (LnRMSSD). Regression, XGBoost, 30-day rolling CV.
- **Feature set §5** — тренировка + сон + образ жизни + состояние.
- **MCP tool `get_hrv_prediction(target_date)`** — возвращает `{predicted_hrv, actual_hrv?, shap: [(feature, contribution), ...top 5]}`.
- **Daily waterfall delivery** — текст в morning report (коротко, top-5 контрибуторов).
- **Global summary** — еженедельно в воскресный report, список «что убивает recovery».
- **Model storage** — локальный `.joblib` на диске VPS, per-user файл. Без S3/Spaces.

### Phase 2 — опционально, по запросу

- **POC 2: predict run pace at given HR** (см. §13 Open question — пересекается с `get_efficiency_trend`).
- **POC 3: unsupervised workout clustering** — UMAP + HDBSCAN. Ценность не доказана, откладываем.
- **Incremental/online learning** вместо weekly full retrain.
- **Visual SHAP PNG** (waterfall plot как PNG в Telegram, по образцу workout card).
- **Cross-user features** — как другие атлеты с похожим профилем реагируют на те же паттерны.

### Non-goals

- Neural networks (LSTM/TCN) — данных <3k точек даже у owner, классический boosting доминирует.
- Real-time streaming predictions — суточный batch достаточен.
- «Рекомендательная» надстройка — даём причинные данные, решение оставляем атлету (и `suggest_workout`'у).
- DigitalOcean Spaces / Parquet для холодного архива — **удалено из scope** vs original issue. FIT-файлы уже на диске локально, wellness/Garmin данные в Postgres.

---

## 3. Что изменилось vs исходный issue #63

| Компонент в issue #63 | Статус | Комментарий |
|---|---|---|
| Data collection (Intervals + Garmin + MCP DB) | ✅ готово | Всё в Postgres; `data/garmin/` парсит GDPR, `actor_fetch_user_activities` тянет Intervals, IQOS/mood — из MCP-тулзов |
| DigitalOcean Spaces + Parquet storage | ❌ отменено | FIT'ы локальные, Garmin в Postgres. Отдельный cold storage не нужен |
| `garminconnect` lib | ❌ отменено | Решили через GDPR-экспорт в #104 |
| `fitdecode` | ❌ заменено | `fitparse` уже используется в `tasks/actors/activities.py` |
| `xgboost`, `shap` | ✅ установлено | `pyproject.toml` |
| `umap-learn`, `hdbscan` | ⏸ отложено | POC 3 в Phase 2, зависимости не добавлять пока не стартанём |
| POC 1 (HRV prediction) | 🟢 ready to start | Блокеры закрыты, данных хватает |
| POC 2 (pace prediction) | 🟡 пересечение | Частично в `get_efficiency_trend`; уточнить дельту |
| POC 3 (clustering) | 🔵 defer | Без доказанной ценности |
| MCP tool `get_hrv_prediction` | ⏳ новый | Часть POC 1 deliverable |
| SHAP waterfall / summary | ⏳ новый | §7, текстовый формат на старте |

---

## 4. Available data (на 2026-04-20)

Объём per user (`SELECT count(*) FROM ...`):

| Таблица | user 1 (owner) | user 2 | user 5 | Примечание |
|---|---|---|---|---|
| `wellness` | ~800 | ~200 | ~50 | Primary HRV/RHR/CTL/ATL/TSB source |
| `activities` | ~950 | ~280 | ~40 | TSS, zones, duration |
| `training_log` | 1631 | 317 | 42 | PRE/ACTUAL/POST + compliance |
| `garmin_sleep` | 823 | — | — | Только owner пока; POC 1 per-user |
| `garmin_daily_summary` | 865 | — | — | Stress avg, Body Battery, RHR |
| `garmin_training_readiness` | 1560 | — | — | Readiness score, HRV, sleep score |
| `iqos_daily` | есть | — | — | Дневной счётчик |
| `mood_checkins` | есть | — | — | 1–5 шкалы |

**Вывод:** POC 1 стартуем **только по user 1** (owner). 3+ года истории, Garmin-данные, IQOS, mood — полный набор. Per-user модель (§8), когда другие юзеры накопят 6+ месяцев — добавим.

`wellness` покрывает 2023-09-01 → 2026-04-20 = ~960 дней. С учётом «next-morning HRV» таргета и drop'а рядов с отсутствующими фичами ожидаем ~700-800 обучающих точек.

---

## 5. Feature engineering

Источники и извлечение. Код живёт в `ml/features.py` (новый модуль).

### 5.1. Training load (вчерашний день → сегодня)

Из `activities` за `target_date - 1`:

- `yesterday_tss` — сумма `icu_training_load`.
- `yesterday_trimp` — **альтернативная метрика нагрузки**, сумма `activity.trimp` (Banister TRIMP). Считается иначе чем hrTSS — чувствительнее к sub-threshold aerobic работам. Модель сама решит какая сильнее. Приходит из ACTIVITY_UPLOADED webhook, лежит в `activity_details.trimp`.
- `yesterday_duration_min` — `moving_time / 60`.
- `yesterday_avg_hr`, `yesterday_max_hr`.
- `yesterday_time_in_z1_pct` … `yesterday_time_in_z5_pct` — из `activity_details.zones`.
- `yesterday_sport` — категориальная (Run / Ride / Swim / Other / None).
- `yesterday_is_intervals` — boolean (есть интервалы в `activity_details.intervals`).
- `yesterday_had_pr` — boolean, **день с PR'ом = максимальное усилие**. Из `activity_details.achievements_json` (поле наполняется ACTIVITY_ACHIEVEMENTS webhook'ом, см. `docs/INTERVALS_WEBHOOKS_RESEARCH.md` §ACTIVITY_ACHIEVEMENTS). Ожидаем сильный ортогональный сигнал к `tss`: при равном TSS день с 5s/1min/FTP PR даст более глубокий HRV-drop.
- `yesterday_pr_count` — int, сколько PR'ов за день (0/1/2+).
- `yesterday_compliance_pct` — `activity.compliance` (0-100%) — попадание в план из `training_log`. «100% по плану» физиологически отличается от «импровизированная горка». Низкий compliance при высоком TSS → больше неопределённости HRV-отклика.
- `yesterday_was_planned` — boolean, derived from `paired_event_id is not None`. Грубее compliance, но чище как binary-сигнал.
- `yesterday_carbs_used` — из `activity_details.carbs_used` (ACTIVITY_ACHIEVEMENTS). Прокси нутриционной нагрузки. Предполагаем сигнал «низкие carbs + высокий TSS → хуже recovery», но подтверждать на данных.

### 5.2. Sleep (ночь перед target)

Из `garmin_sleep` с `sleep_end` в диапазоне `[target_date 00:00, target_date 12:00]`:

- `sleep_duration_min` — из `total_sleep_duration_seconds / 60`.
- `sleep_score` — Garmin's.
- `deep_sleep_pct`, `rem_sleep_pct`, `awake_pct`, `light_sleep_pct` — из breakdown.
- `bedtime_hour` — `sleep_start.hour + sleep_start.minute / 60` (float).
- `bedtime_after_23h` — boolean.
- `awake_count` — `awake_episodes_count`.

### 5.3. Lifestyle (за target_date − 1)

- `iqos_count` — `iqos_daily.count`.
- `iqos_after_20h_count` — нужна расширенная таблица (сейчас только дневной total — см. §13 Open question «нужен ли timestamp»).
- `mood_energy`, `mood_anxiety`, `mood_social` — из `mood_checkins.energy/anxiety/social` за target − 1.

### 5.4. Fitness state (на начало target_date)

Из `wellness` за `target_date - 1`:

- `ctl`, `atl`, `tsb` — прямо.
- `ctl_delta_7d` — `ctl(t-1) - ctl(t-8)`.
- `hrv_7d_mean`, `hrv_30d_mean`, `hrv_60d_mean` — rolling средние.
- `rhr_7d_mean`, `rhr_30d_mean` — rolling.
- `recovery_score_yesterday` — `wellness.recovery_score`.

### 5.5. Stress / readiness (Garmin)

Из `garmin_daily_summary` / `garmin_training_readiness` за target − 1:

- `stress_avg`, `stress_max` — daily summary.
- `body_battery_min`, `body_battery_max`, `body_battery_drop` (`max - min`).
- `training_readiness_score` — из `garmin_training_readiness`.

### 5.6. Cyclical / contextual

- `day_of_week` — 0-6 (one-hot или ordinal).
- `is_weekend` — boolean.
- `month` — 1-12 (для сезонных эффектов).
- `days_since_last_hard_workout` — int, recency.

### 5.7. Weather (только outdoor activities)

ACTIVITY_UPLOADED webhook на outdoor Run отдаёт полный погодный блок (см. `docs/INTERVALS_WEBHOOKS_RESEARCH.md` §ACTIVITY_UPLOADED — Run samples). Хранится в `activity_details` (или ближайшем JSON-поле). Для indoor/treadmill — всегда `NaN` → XGBoost это корректно обрабатывает.

- `yesterday_temp_c` — `average_weather_temp`. Холод (<5°C) и жара (>28°C) оба вызывают симпатическую реакцию → удар по HRV не пропорциональный TSS.
- `yesterday_feels_like_c` — `feels_like` (с учётом влажности/ветра).
- `yesterday_heat_stress` — композит `max(0, temp_c - 20) × yesterday_tss / 100`. Интуитивно: «жаркий tempo-run наносит больше recovery-debt, чем тот же tempo-run при 15°C».
- `yesterday_cold_stress` — `max(0, 5 - temp_c)` (линейный ниже 5°C).
- `yesterday_had_rain` — boolean (`rain > 0`).
- `yesterday_wind_kmh` — `wind_speed`.
- `yesterday_was_outdoor` — boolean (если погодные поля not null). Модель может использовать как gating «если outdoor то смотри погоду, иначе игнор».

### 5.8. Target

`hrv_today_ln_rmssd` — `wellness.hrv_ln_rmssd` за target_date (то, что мы предсказываем).

Alternative target для ablation: `recovery_score` (regression), `recovery_category` (multiclass).

---

## 6. Pipeline architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Training (weekly cron / manual CLI)                             │
│                                                                  │
│   cli train-hrv-model <user_id>                                  │
│       ↓                                                          │
│   ml.features.build_dataset(user_id, period) → pandas DataFrame  │
│       ↓                                                          │
│   train/val time-based split (last 60 days = val)                │
│       ↓                                                          │
│   XGBRegressor.fit() → MAE, R² logged to Sentry                  │
│       ↓                                                          │
│   joblib.dump(model, f"ml/models/hrv_{user_id}.joblib")          │
│       ↓                                                          │
│   shap.TreeExplainer(model) → cached inside model file           │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  Inference (на каждый morning report)                            │
│                                                                  │
│   MCP tool get_hrv_prediction(target_date)                       │
│       ↓                                                          │
│   ml.features.build_row(user_id, target_date)                    │
│       ↓                                                          │
│   model.predict(row) → predicted_hrv                             │
│       ↓                                                          │
│   explainer.shap_values(row) → top 5 contributions               │
│       ↓                                                          │
│   return {predicted, actual, shap_top5, baseline_hrv}            │
└──────────────────────────────────────────────────────────────────┘
```

### 6.1. Module layout

```
ml/
├── __init__.py
├── features.py        # build_dataset(), build_row()
├── train.py           # train_user_model() — CLI entrypoint
├── predict.py         # load_model(), predict_with_shap()
└── models/            # .joblib files, gitignored
    ├── hrv_1.joblib
    ├── hrv_2.joblib
    └── ...
```

---

## 7. SHAP delivery

### 7.1. Daily waterfall (morning report, text)

MCP tool возвращает структурно:

```python
{
    "predicted_hrv": 48.2,
    "actual_hrv": 45.1,
    "baseline_hrv": 55.0,  # 30-day mean
    "delta_vs_baseline": -6.8,
    "shap_top5": [
        {"feature": "bedtime_hour", "value": 23.6, "contribution": -4.1, "direction": "worsen"},
        {"feature": "iqos_count", "value": 12, "contribution": -3.0, "direction": "worsen"},
        {"feature": "yesterday_tss", "value": 145, "contribution": -2.8, "direction": "worsen"},
        {"feature": "sleep_score", "value": 72, "contribution": +1.5, "direction": "improve"},
        {"feature": "stress_avg", "value": 42, "contribution": -1.2, "direction": "worsen"},
    ],
}
```

Бот рендерит как text в morning report:

```
HRV 45 (−7 vs 30d baseline). Главные факторы сегодня:
• поздний bedtime (23:36) −4
• IQOS 12 шт −3
• вчерашний workout 145 TSS −3
• сон 72 балла +1
• Garmin stress avg 42 −1
```

### 7.2. Weekly global summary (воскресный report)

Агрегация `shap_top5` за последние 30 дней:

```
Что убивает твой recovery в этом месяце:
1. Bedtime после 23:00 (27 из 30 дней) — средний вклад −3.4
2. IQOS > 8 шт (23 из 30 дней) — −2.8
3. Кумулятивный TSS > 500/нед (4 недели) — −2.1
...

Что помогает:
1. Sleep score > 80 (12 дней) — +2.9
2. Bedtime < 22:30 (3 дня) — +4.2
```

### 7.3. PNG waterfall (Phase 2)

`shap.plots.waterfall(...)` → PNG → `TelegramTool.send_document(mime_type="image/png")` (для прозрачности фона — как в workout card, см. `tasks/tools.py`).

В MVP — только текст. PNG добавим если UX-фидбек запросит визуал.

---

## 8. Per-user model strategy

### 8.1. Старт: только owner (user 1)

На 2026-04-20 у других юзеров недостаточно Garmin-истории. POC 1 делаем **только под user 1**. `hrv_1.joblib` — единственный артефакт.

### 8.2. Расширение

Для каждого юзера после накопления 180+ дней wellness + Garmin training readiness → тренируем свой `hrv_{user_id}.joblib`. Модель явно per-user (не общий pool), потому что:

- Baseline HRV индивидуальный (у одного 40, у другого 80).
- Чувствительность к факторам разная (один просыпается после 300 TSS, другой ломается на 150).
- Малые датасеты (~700-1000 точек) не поддерживают cross-user generalization с хорошей пользой.

### 8.3. Cold-start fallback

Пока нет модели для юзера — MCP tool возвращает `{"predicted_hrv": null, "reason": "insufficient_data", "min_days_needed": 180}`. Morning report пропускает SHAP-блок.

---

## 9. Training schedule

### 9.1. Initial training

CLI команда (добавляем в `cli.py`):

```bash
python -m cli train-hrv-model <user_id> [--period 2Y]
```

Один проход, логируем metrics в stdout + Sentry, сохраняем `.joblib`. Для user 1 — разовый `poetry run python -m cli train-hrv-model 1` из shell.

### 9.2. Weekly retrain

Dramatiq actor `actor_retrain_hrv_model(user_id)`, scheduler-cron каждое воскресенье в 04:00:

- Для каждого active athlete с `wellness_count >= 180`:
  - Пересобираем датасет, retrain, сохраняем `.joblib`.
  - Логируем MAE delta vs предыдущая неделя (Sentry breadcrumb).

### 9.3. Validation strategy

**Time-based split**, не random:

- Train: всё до `max(wellness.date) - 60`.
- Validation: последние 60 дней.

K-fold на временных рядах опционально (time-series split, не shuffled). Random split приведёт к data leakage через авторегрессионные фичи (`hrv_7d_mean`).

### 9.4. Acceptance bar для deploy

Первый обученный `hrv_1.joblib` считается acceptable если:

- **MAE ≤ 4 ms** на hold-out (baseline: `predicted = hrv_7d_mean` даёт ~5-6 ms).
- **R² ≥ 0.35** — объясняем ≥35% дисперсии HRV (остальное noise + внешние факторы).
- **Top-5 SHAP features** стабильны ≥3 из 5 между train и val (ablation check).

Если хуже — разбираемся с фичами прежде чем раскатывать.

---

## 10. MCP tool: `get_hrv_prediction`

### 10.1. Signature

```python
@mcp.tool()
@sentry_tool
async def get_hrv_prediction(target_date: str = "") -> dict:
    """Predict next-morning HRV (LnRMSSD) with SHAP breakdown of top-5 contributing factors.

    If no model is trained for the current user yet, returns {"available": False, ...}.
    Target date defaults to tomorrow if called before 18:00 local, else today.
    """
```

### 10.2. Return shape

```python
{
    "available": True,                    # False if no model yet
    "target_date": "2026-04-21",
    "predicted_hrv": 48.2,                # LnRMSSD
    "actual_hrv": 45.1,                   # null if target_date >= today
    "baseline_hrv": 55.0,                 # 30d mean
    "delta_vs_baseline": -6.8,
    "shap_top5": [
        {
            "feature": "bedtime_hour",
            "display_name": "Время отбоя",
            "value": 23.6,
            "value_display": "23:36",
            "contribution": -4.1,
            "direction": "worsen",
        },
        ...
    ],
    "model_metadata": {
        "trained_at": "2026-04-14T04:00:00Z",
        "train_rows": 782,
        "val_mae": 3.6,
    },
}
```

### 10.3. Поведение в morning report

`tasks/actors/reports.py` и соответствующий промпт Claude — не вызываем `get_hrv_prediction` автоматически. **Вызов через Claude-инференс как любая другая MCP-тулза** — но в промпте `SYSTEM_PROMPT_V2` добавляем инструкцию: «В утреннем отчёте всегда вызывай `get_hrv_prediction`, если `available=True` — встрой SHAP-объяснение в раздел про recovery».

Если `available=False` — Claude просто пропускает секцию.

---

## 11. Storage

### 11.1. Модели

`ml/models/hrv_{user_id}.joblib` — содержит `{model, explainer, feature_names, trained_at, metrics}`.

- Путь (`ml/models/`) в `.gitignore` — модели не коммитятся.
- Размер файла ~5-10 MB на юзера (XGBoost с ~20 фичами + SHAP TreeExplainer).
- Docker volume (production) — примонтировать `./ml/models` к контейнеру (persistent между redeploy'ями).

### 11.2. Тренировочные датасеты

Не сохраняем. Каждый train вызов пересобирает DataFrame через `build_dataset()` из актуальной БД. Это дёшево (несколько секунд), детерминистично и исключает дрифт между train'ами.

### 11.3. Predictions log (опционально)

Таблица `hrv_predictions`:

```sql
CREATE TABLE hrv_predictions (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id),
    target_date DATE NOT NULL,
    predicted_hrv FLOAT NOT NULL,
    actual_hrv FLOAT,  -- backfilled next day
    shap_json JSONB NOT NULL,
    model_trained_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, target_date)
);
```

Зачем: long-term tracking качества prediction'ов + A/B-сравнение версий модели. Если не критично для Phase 1 — отложить.

---

## 12. Acceptance criteria

### POC 1 (MVP)

- [ ] `ml/features.py` — 30+ фичей из §5 (training/sleep/lifestyle/state/stress/cyclical/weather), unit-тесты на deterministic extraction, включая edge cases (indoor activity → weather=NaN, no PR → flag=False, нет activity вчера → все training-поля NaN).
- [ ] `ml/train.py` — CLI `python -m cli train-hrv-model 1`, производит `.joblib`, логирует MAE/R².
- [ ] `ml/predict.py` — `predict_with_shap(user_id, target_date)` возвращает структуру §10.2.
- [ ] MCP tool `get_hrv_prediction` — регистрируется в `mcp_server/tools/__init__.py`, покрыт тестом с mock model.
- [ ] Model metrics на user 1: MAE ≤ 4 ms, R² ≥ 0.35.
- [ ] Text waterfall в morning report (нужна правка prompt + test что Claude зовёт тулзу).
- [ ] Weekly retrain actor + scheduler cron.
- [ ] Документация: `docs/ML_HRV_PREDICTION_SPEC.md` (эта) + короткая заметка в CLAUDE.md.

### POC 2 / 3 (Phase 2)

Отложены до запроса пользователя. Критерий старта POC 2 — явный бриф «мне нужен прогноз pace по HR на сегодня».

---

## 13. Implementation order

1. **`ml/features.py`** + unit-тесты. Точка фокуса — детерминистичное построение DataFrame из БД, без side-effects.
2. **`ml/train.py`** + CLI `train-hrv-model`. Разовый тренинг user 1, проверяем MAE/R² и распределение SHAP вручную.
3. **`ml/predict.py`** + MCP tool `get_hrv_prediction` с cold-start fallback.
4. **Prompt update**: `bot/prompts.py:SYSTEM_PROMPT_V2` — инструкция про вызов тулзы в morning report.
5. **Weekly retrain actor** + scheduler job.
6. **Tests** — unit (features, predict), integration (MCP tool через пустую модель и с реальной), smoke (CLI train end-to-end).
7. **(Опционально) `hrv_predictions` таблица** + Alembic-миграция.
8. **CLAUDE.md update** — раздел «Implementation Status», MCP tools count.

---

## 14. Testing

### Unit

- `tests/ml/test_features.py` — `build_row()` для ручных fixture-дней (все фичи из §5, edge case: нет Garmin-данных → `None`).
- `tests/ml/test_predict.py` — `predict_with_shap()` с фейковой моделью (XGBoost на синтетических 200 строках).

### Integration

- `tests/mcp/test_hrv_prediction.py` — MCP tool возвращает правильную схему при `available=True` и `available=False`.
- `tests/tasks/test_retrain_actor.py` — actor вызывается, `.joblib` перезаписывается, MAE улучшается ≥ previous week (или flag regression).

### Manual smoke (user 1)

1. `python -m cli train-hrv-model 1`.
2. `python -c "from ml.predict import predict_with_shap; print(predict_with_shap(1, '2026-04-20'))"` → руками проверяем top-5.
3. `/morning` в боте → проверяем что SHAP-блок появился.
4. Неделю ждём → смотрим retrain actor в logs.

---

## 15. Open questions

- **POC 2 (pace prediction) — делать отдельно или выкинуть?** Сейчас `get_efficiency_trend` показывает trend EF/SWOLF/pace на 90-дневном окне — это descriptive, но не predictive. POC 2 = regression «HR → pace at current state». Потенциал есть (decoupling-prediction для негонки), но не очевиден. **Предлагаю отложить до явного запроса.**
- **IQOS timestamps.** `iqos_daily` — дневной total без timestamps. Фича `iqos_after_20h_count` требует таймстемпов каждого `/stick`. Миграция тривиальная, но добавляет поле пользовательского UX (кнопка «отметить IQOS» должна писать время). **Решение:** на старте без этой фичи, добавить в Phase 2.
- **Категория recovery vs continuous HRV.** Модель предсказывает `hrv_ln_rmssd` (continuous). Но actionable — это `recovery_category` (good/moderate/low) для Claude-правил. Нужна доп. обёртка mapping `predicted_hrv → category` (threshold на личные baseline SD). **Решение:** пост-процессинг в MCP tool, не меняет модель.
- **`hrv_predictions` таблица — в MVP или Phase 2?** Даёт long-term observability, но не критична для работы тулзы. **Решение:** добавить сразу (дешёвая миграция + sentry-based observability без logs).
- **Feature importance drift.** Если top-5 SHAP сильно меняется между weekly retrain'ами — это сигнал non-stationarity. Как детектировать автоматически? **Предлагаю:** weekly отчёт `"top-5 features changed: X → Y"` как breadcrumb + в Sentry alert при drift > 3 позиций.
- **Cross-user features (#215).** Issue предлагает обогатить данные Intervals webhook'ами для ML. Релевантно для future pool-based модели. **Предлагаю:** закрывать #215 в рамках POC 1 нет смысла — fичи уже достаточны. Перенести #215 на этап «multi-user pool model» (post-POC 1).
- **Webhook data availability для бэкфилла.** Поля §5.1 (PR flag, compliance, TRIMP, carbs_used) и §5.7 (weather) приходят через ACTIVITY_UPLOADED / ACTIVITY_ACHIEVEMENTS webhook'и, но **исторические активности** до запуска webhook-дispatcher'ов (до 2026-04-11) могут не иметь этих полей в `activity_details`. **Решение:** `actor_update_activity_details` бэкфилл-прогон — если `activity_details.trimp is None` для `start_date_local < 2026-04-11`, сходить в Intervals REST API и дозаписать. Сделать один раз перед первым train-hrv-model. Либо обучать модель только на данных после 2026-04-11 (~10 дней на момент 2026-04-20 → мало).
