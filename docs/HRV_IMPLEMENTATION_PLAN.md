# План внедрения HRV RMSSD анализа (Level 1)

> На основе [HRV_MODULE_SPEC.md](HRV_MODULE_SPEC.md), секции 1.3–1.7

---

## Статус: ✅ ЗАВЕРШЕНО

Все 8 шагов реализованы. Данные поступают из Intervals.icu API (не Garmin напрямую). БД — PostgreSQL + SQLAlchemy async. Dual HRV алгоритм работает.

---

## Реализованные шаги

### Шаг 1. Схема БД ✅

Три таблицы в PostgreSQL (Alembic migrations):
- `wellness` — ежедневные метрики (RMSSD, RHR, sleep, CTL/ATL, recovery score, AI recommendation)
- `hrv_analysis` — dual-algorithm HRV baseline (composite PK: date + algorithm)
- `rhr_analysis` — RHR baseline (7d/30d/60d)
- `activities` — завершённые активности для per-sport CTL (id: String, e.g. "i12345")
- `scheduled_workouts` — запланированные тренировки из Intervals.icu календаря

Файл: `data/database.py` (WellnessRow, HrvAnalysisRow, RhrAnalysisRow, ActivityRow, ScheduledWorkoutRow)

### Шаг 2. Data sync ✅

`data/intervals_client.py` — IntervalsClient:
- `get_wellness(date)` → Wellness model
- `get_activities(oldest, newest)` → list[Activity]
- `get_events(oldest, newest)` → list[ScheduledWorkout]

`bot/scheduler.py` — три cron задачи:
- `daily_metrics_job` — каждые 15 мин (5-23ч): wellness + HRV/RHR + recovery score + AI
- `sync_activities_job` — каждый час :30 (4-23ч): activities из API → БД
- `scheduled_workouts_job` — каждый час :00 (4-23ч): planned workouts → БД

### Шаг 3. RMSSD status ✅

`data/metrics.py`: `calculate_rmssd_status()` — dual algorithm (Flatt & Esco + AIEndurance).
Оба алгоритма всегда рассчитываются и сохраняются. `HRV_ALGORITHM` выбирает основной для recovery.

Вспомогательные: trend analysis (линейная регрессия), CV, SWC.

### Шаг 4. RHR status ✅

`data/metrics.py`: `calculate_rhr_status()` — 7d/30d/60d baselines, инвертированная интерпретация.

### Шаг 5. Recovery Score ✅

`data/metrics.py`: `combined_recovery_score()` — composite 0-100.
Weights: RMSSD 35%, Banister 25%, RHR 20%, Sleep 20% (перенормировка при None).

### Шаг 6. Pydantic модели ✅

`data/models.py`: `RmssdStatus`, `RhrStatus`, `TrendResult`, `RecoveryScore`, `Wellness`, `Activity`, `ScheduledWorkout`, `GoalProgress`.

### Шаг 7. Тесты ⚠️

Базовые тесты для metrics существуют, но покрытие неполное. Нужно расширить.

### Шаг 8. Интеграция в отчёт ✅

- `ai/prompts.py` — SYSTEM_PROMPT + MORNING_REPORT_PROMPT с HRV, RHR, recovery, per-sport CTL, planned workouts
- `ai/claude_agent.py` — Claude API call (sonnet-4-6), один раз в день
- `bot/formatter.py` — Telegram summary с recovery score
- `mcp_server/` — 12 tools + 3 resources для Claude Desktop

---

## Холодный старт

Для 60-дневного baseline нужно ~2 месяца данных:
- **< 14 дней** → `insufficient_data` (fallback на readiness)
- **14–60 дней** → используем сколько есть
- **Backfill** — `python -m bot.cli backfill` загружает историю из Intervals.icu (до 180 дней)

---

## Что ещё не реализовано (из Level 1)

1. **Тесты** — расширить покрытие `tests/test_metrics.py`
2. **Bot commands** — /start, /status, /week, /goal, /zones
3. **Webapp** — обновить под новую API структуру (/api/report grouped JSON)
