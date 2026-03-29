# HRV Analysis Module — Архитектурная спецификация

> Модуль для Triathlon AI Agent.
> Двухуровневый HRV-анализ: восстановление в покое + тренировочная готовность через DFA alpha 1.

---

## Обзор архитектуры

```
┌──────────────────────────────────────────────────────────┐
│               Telegram Bot / Mini App                     │
│          (графики Chart.js, алерты, рекомендации)         │
├──────────────────────────────────────────────────────────┤
│              FastAPI Gateway + MCP Server                  │
├───────────────┬───────────────┬──────────────────────────┤
│  HRV Rest     │  HRV Activity │  Recovery Model          │
│  Analyzer     │  Analyzer     │  (Banister + RMSSD)      │
│  (Level 1) ✅ │  (Level 2) 🔜 │                          │
├───────────────┴───────────────┴──────────────────────────┤
│              Data Layer (PostgreSQL + SQLAlchemy async)    │
├───────────────┬──────────────────────────────────────────┤
│ Intervals.icu │  Claude AI (Anthropic API)                │
│    REST API   │  (утренняя рекомендация)                  │
└───────────────┴──────────────────────────────────────────┘
```

### Зависимости (текущие)

```
sqlalchemy[asyncio]         # Async ORM
asyncpg                     # PostgreSQL async driver
pydantic / pydantic-settings # Data models, config
numpy                       # Статистика, линейная регрессия
anthropic                   # Claude AI API
python-telegram-bot>=21     # Telegram Bot
apscheduler                 # Periodic jobs
fastapi + uvicorn           # API server
mcp[cli]                    # MCP server (Claude Desktop)
```

### Зависимости (Level 2) ✅

```
fitparse>=0.0.7             # FIT file parsing (RR-интервалы)
scipy>=1.10                 # DFA alpha 1, Banister calibration
```

---

## Level 1: HRV в покое (RMSSD-based Recovery) ✅

> **Статус: реализовано.** Все компоненты Level 1 работают в продакшене.

### 1.1 Источники данных

Все данные поступают из **Intervals.icu REST API**, который агрегирует показатели с носимых устройств (Garmin, Wahoo и др.):

```python
# Intervals.icu API — через IntervalsClient (data/intervals_client.py)
intervals.get_wellness(date)           # RMSSD, resting HR, sleep, weight, CTL/ATL и др.
intervals.get_activities(oldest, newest)  # Завершённые активности (TSS, длительность, тип)
intervals.get_events(oldest, newest)   # Запланированные тренировки из календаря
```

Sync pipeline (bot/scheduler.py):
- **Wellness**: каждые 15 мин (5:00-23:00) → `daily_metrics_job`
- **Activities**: каждый час в :30 (4:00-23:00) → `sync_activities_job`
- **Scheduled workouts**: каждый час в :00 (4:00-23:00) → `scheduled_workouts_job`

### 1.2 Схема данных (PostgreSQL)

Пять таблиц в PostgreSQL через SQLAlchemy async ORM:

**`wellness`** — ежедневные данные (из Intervals.icu + рассчитанные метрики):
```
id (String PK, "YYYY-MM-DD"), ctl, atl, ramp_rate, ctl_load, atl_load,
sport_info (JSON — per-sport CTL, eftp, wPrime, pMax),
weight, resting_hr, hrv (RMSSD), sleep_secs, sleep_score, sleep_quality,
ess_today, banister_recovery, recovery_score, recovery_category,
recovery_recommendation, readiness_score, readiness_level,
ai_recommendation (Text — Claude AI output)
```

**`hrv_analysis`** — двойной алгоритм HRV baseline:
```
date (String PK, FK→wellness), algorithm (String PK: "flatt_esco"|"ai_endurance"),
status, rmssd_7d, rmssd_sd_7d, rmssd_60d, rmssd_sd_60d,
lower_bound, upper_bound, cv_7d, swc, days_available,
trend_direction, trend_slope, trend_r_squared
```

**`rhr_analysis`** — baseline пульса покоя:
```
date (String PK, FK→wellness), status,
rhr_today, rhr_7d, rhr_sd_7d, rhr_30d, rhr_sd_30d, rhr_60d, rhr_sd_60d,
lower_bound, upper_bound, cv_7d, days_available,
trend_direction, trend_slope, trend_r_squared
```

**`activities`** — завершённые активности из Intervals.icu:
```
id (String PK, e.g. "i12345"), start_date_local (String),
type (String — Ride, Run, Swim, VirtualRide, etc.),
icu_training_load (Float — TSS/hrTSS/ssTSS),
moving_time (Integer — seconds)
```

**`scheduled_workouts`** — запланированные тренировки:
```
id (Integer PK, Intervals.icu event ID), start_date_local, end_date_local,
name, category (WORKOUT/RACE_A/B/C/NOTE), type, description,
moving_time, distance, workout_doc (JSON), updated
```

### 1.3 RMSSD Baseline Analysis — Dual Algorithm ✅

> Реализовано в `data/metrics.py`: `calculate_rmssd_status()`

Оба алгоритма **всегда** рассчитываются и сохраняются в `hrv_analysis`. Настройка `HRV_ALGORITHM` выбирает основной для recovery score.

| | Flatt & Esco (по умолчанию) | AIEndurance |
|---|---|---|
| Сравнивает | сегодня vs 7d среднее | 7d среднее vs 60d среднее |
| Bounds | асимметричные −1/+0.5 SD | симметричные ±0.5 SD |
| Скорость реакции | быстрая (1-2 дня) | медленная (3-4 дня) |
| Лучше для | острых изменений, болезнь | хроническое накопление усталости |
| Минимум данных | 14 дней | 60 дней для надёжных bounds |

**Статус:**
- `green` (выше upper_bound) → тренировка на полной нагрузке
- `yellow` (между bounds) → тренировка по плану, мониторинг
- `red` (ниже lower_bound) → снизить интенсивность или отдых
- `insufficient_data` (< 14 дней) → fallback на readiness

**SWC:** 0.5 × SD_60d. CV: < 5% стабильно, 5-10% нормально, > 10% ненадёжно.

### 1.4 Resting HR Analysis ✅

> Реализовано в `data/metrics.py`: `calculate_rhr_status()`

Baselines по 3 окнам:
- **7-day** — краткосрочное состояние + CV + тренд
- **30-day** — основные bounds (±0.5 SD), статус
- **60-day** — долгосрочный контекст

Инвертированная интерпретация: повышенный RHR = `red` (недовосстановление), низкий RHR = `green`.

### 1.5 External Stress Score (ESS) ✅

> Реализовано в `data/metrics.py`: `calculate_ess()`, `calculate_daily_ess()`

Banister TRIMP-based, нормализация: 1 час на LTHR ≈ 100. `calculate_daily_ess()` суммирует ESS по всем активностям за день. Активности без `average_hr` пропускаются (ESS = 0).

### 1.6 Recovery Model (Banister) ✅

> Реализовано в `data/metrics.py`: `calculate_banister_recovery()`, `calculate_banister_for_date()`

```
R(t+1) = R(t) + (100 - R(t)) * (1 - exp(-1/τ)) - k * ESS(t)
Defaults: k=0.1, τ=2.0 (conservative)
```

Pipeline в `save_wellness()`: activities (90d) → group by date → daily ESS → Banister → persist `ess_today`, `banister_recovery`. Калибровка k/τ через scipy — будущее.

### 1.7 Combined Recovery Score ✅

> Реализовано в `data/metrics.py`: `combined_recovery_score()`

**Весовая модель:**
- RMSSD status: 35% | Banister R(t): 25% | RHR status: 20% | Sleep: 20%

При отсутствии компонентов (sleep_score=None, banister=None) веса перенормируются на доступные.

**Статус → score:** green=100, yellow=65, red=20, insufficient_data=50

**Модификаторы:** late sleep (>23:00) −10, CV>15% −5, RMSSD declining → flag

**Категории:** excellent >85, good 70-85, moderate 40-70, low <40

**Рекомендации:** excellent/good → zone2_ok, moderate → zone1_long, low → zone1_short, red RMSSD → skip

### 1.8 Per-sport CTL ✅

> Реализовано в `data/metrics.py`: `calculate_sport_ctl()`

Intervals.icu API не предоставляет per-sport CTL. Рассчитывается из истории активностей в БД:
- EMA с τ=42d по каждой дисциплине (swim/bike/run)
- Маппинг типов в `data/utils.py`: `SPORT_MAP` (16 типов → 3 дисциплины)
- Cron `sync_activities_job` грузит активности из API → `activities` таблица
- `daily_metrics_job` читает из БД, рассчитывает CTL, обогащает `wellness.sport_info`

### 1.9 Trend Analysis ✅

> Реализовано в `data/metrics.py`: `calculate_trend()`

Линейная регрессия на скользящем окне. Пороги по метрике в `TREND_THRESHOLDS`.
Направления: rising_fast / rising / stable / declining / declining_fast.
Показывается только при r² ≥ 0.3.

---

## Level 2: HRV во время тренировки (DFA alpha 1)

> **Статус: ✅ РЕАЛИЗОВАНО.** Post-activity pipeline работает: FIT → RR → DFA a1 → thresholds → Ra/Da. Cron `process_fit_job` каждые 5 мин. 3 MCP tools. Подробнее: `docs/DFA_ALPHA1_PLAN.md`, `docs/PROCESS_FIT_JOB.md`.

### 2.1 Источники данных

```python
# FIT файл — скачивание через Intervals.icu или Garmin Connect
# Парсинг RR-интервалов из FIT через fitparse
def extract_rr_intervals(fit_path: str) -> list[float]:
    """Извлекает RR-интервалы из HRV-записей FIT файла."""
```

### 2.2 Ограничения по датчику

```
┌─────────────────────┬──────────────┬───────────────────────────────┐
│ Датчик              │ RR качество  │ Пригодность для DFA a1        │
├─────────────────────┼──────────────┼───────────────────────────────┤
│ Polar H10 (BLE)     │ Отличное     │ Золотой стандарт              │
│ Garmin HRM-Dual     │ Хорошее      │ Пригоден (нагрудный strap)    │
│  └─ ANT+ connection │ Отличное     │ **Предпочтительно** (0.04% артефактов) │
│  └─ BLE connection  │ Плохое       │ НЕ пригоден — Garmin фрагментирует RR (25% артефактов) │
│ Запястье (Garmin)   │ Плохое       │ НЕ пригоден для DFA a1        │
│ Плавание (любой)    │ Нет данных   │ Нет RR в воде                 │
└─────────────────────┴──────────────┴───────────────────────────────┘
```

### 2.3 Artifact Correction

Коррекция артефактов в RR-ряде (Lipponen & Tarvainen 2019). Если artifact_pct > 10% — результаты DFA a1 ненадёжны.

### 2.4 DFA Alpha 1 Calculation

Detrended Fluctuation Analysis — short-term scaling exponent (window: 4-16 beats).

Интерпретация:
- a1 > 1.0: низкая нагрузка, покой
- a1 ≈ 0.75: аэробный порог (HRVT1)
- a1 ≈ 0.50: анаэробный порог (HRVT2)
- a1 < 0.50: максимальная нагрузка

### 2.5 Time-Varying DFA a1

Скользящее окно (2 мин, шаг 5 сек) по ходу тренировки.

### 2.6 Threshold Detection (HRVT1 / HRVT2)

Автоматическое определение аэробного и анаэробного порогов из DFA a1 time series. Требует ramp test или monotonic intensity increase.

### 2.7 Readiness to Train (Ra)

Сравнение power/pace при фиксированном DFA a1 с baseline за 2 недели. Оценка готовности прямо во время разминки.

### 2.8 Durability (Da)

Сравнение Pa между первой и второй половиной тренировки. Минимум 40 мин.

### Level 2 — Схема данных ✅

Две таблицы реализованы (см. CLAUDE.md для полной схемы):
- `activity_hrv` — DFA a1 summary, thresholds, Ra, Da, raw timeseries, processing_status
- `pa_baseline` — baseline Pa по типу активности для расчёта Ra

---

## Интеграция уровней: Decision Engine

Ежедневная рекомендация на основе всех доступных данных.

Приоритет сигналов:
1. **Level 1 (утро)**: Combined Recovery Score → базовое решение ✅
2. **Level 2 (разминка)**: Ra → корректировка перед тренировкой ✅ (данные в Telegram + утренний AI промпт)

Decision matrix (упрощённая, только Level 1):

| Recovery Score | TSB        | Рекомендация |
|----------------|------------|--------------|
| excellent + TSB > 0 | > 0   | Любая интенсивность, ключевая тренировка |
| good           | −10..+10   | Z2 полный объём |
| moderate или sleep < 50 | любой | Z1-Z2, 45-60 мин |
| low или RMSSD = red | любой | Отдых или Z1 ≤ 30 мин |
| —              | < −25      | Z1-Z2 cap, flag overreaching |
| HRV delta < −15% | любой   | Z1-Z2 max |
| Ramp rate > 7  | любой      | Flag risk, low-stress session |

---

## Pipeline обработки ✅

### Ежедневный cron (каждые 10 мин, 5:00-23:00)

```
1. Sync wellness из Intervals.icu API
2. Загрузить per-sport CTL из activities в БД
3. Enrich sport_info в wellness
4. Calculate RMSSD status (dual algorithm: Flatt & Esco + AIEndurance)
5. Calculate RHR status
6. Generate Combined Recovery Score
7. Run Claude AI recommendation (только для today, один раз)
8. Send Telegram morning report (при первом появлении AI рекомендации)
```

### Sync activities (каждый час в :30)

```
1. Fetch activities из Intervals.icu API (последние 90 дней)
2. Upsert в таблицу activities (PostgreSQL ON CONFLICT DO UPDATE)
```

### Sync scheduled workouts (каждый час в :00)

```
1. Fetch events из Intervals.icu API (14 дней вперёд)
2. Upsert в таблицу scheduled_workouts
```

### DFA processing (каждые 5 мин, 4:00-23:00)

```
1. Найти необработанные bike/run активности (≥15 мин)
2. Скачать FIT → извлечь RR → artifact correction → DFA a1 timeseries
3. Threshold detection (HRVT1/HRVT2), Ra (Readiness), Da (Durability)
4. Сохранить в activity_hrv + pa_baseline
5. Отправить Telegram уведомление (если status = processed)
```

### Evening report (21:00)

```
1. Собрать активности за день + DFA анализы
2. Отформатировать итог дня (тренировки, TSS, recovery, ESS/Banister, HRV, DFA Ra)
3. Отправить в Telegram
```

---

## Фазовая реализация

### Phase 1 (Level 1 — восстановление в покое) ✅ ЗАВЕРШЕНА

- [x] Data sync из Intervals.icu (wellness, activities, scheduled workouts)
- [x] RMSSD baseline analysis — dual algorithm (Flatt & Esco + AIEndurance)
- [x] RHR analysis (7d/30d/60d baselines)
- [x] Per-sport CTL (EMA τ=42d из activities)
- [x] Combined Recovery Score (0-100)
- [x] Trend analysis (линейная регрессия)
- [x] Claude AI morning recommendation
- [x] Telegram: /morning + утренний отчёт с Mini App кнопкой
- [x] MCP Server: 12 tools + 3 resources
- [x] FastAPI: /api/report с grouped JSON

**Не реализовано (Phase 1):**
- [x] Telegram: /start, /status, /week, /goal, /zones
- [x] Webapp: обновить под новую структуру API

### Phase 2 (Level 2 — DFA alpha 1) ✅ РЕАЛИЗОВАНА

- [x] FIT file download + RR extraction
- [x] Artifact correction (Lipponen & Tarvainen)
- [x] DFA a1 timeseries (скользящее окно 2 мин)
- [x] Ra (Readiness) calculation
- [x] Da (Durability) calculation
- [x] Threshold detection (HRVT1/HRVT2)
- [x] MCP tools: get_activity_hrv, get_thresholds_history, get_readiness_history
- [x] Cron job: process_fit_job (every 5 min)
- [x] Post-activity Telegram notification (DFA summary after FIT processing)
- [x] Evening report (21:00, daily summary with activities + DFA + recovery)
- [x] Morning AI prompt + yesterday's DFA context

### Phase 3 (расширения)

- [x] Banister model calibration (scipy.optimize)
- [x] Pa baseline tracking
- [x] Decision matrix: Level 1 + Level 2 combined
- [x] Race readiness prediction

---

## Ссылки

- Banister et al. — оригинальная модель fitness-fatigue
- Gronwald et al. 2020 — DFA a1 как биомаркер интенсивности
- Rogers et al. 2021 — DFA a1 для определения аэробного порога
- Lipponen & Tarvainen 2019 — artifact correction для RR-интервалов
- AIEndurance blog — recovery model, Ra, Da определения
- Intervals.icu API — https://intervals.icu (wellness, activities, events)
