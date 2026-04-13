# MCP Triathlon Server — Анализ, баги и запросы на улучшение
## Дата аудита: 7 апреля 2026
## Аудитор: Claude (триатлон-коуч)

---

## 1. БАГИ И ПРОБЛЕМЫ

### 🟡 BUG-004: get_threshold_freshness — не видит обнаруженные пороги
**Описание:** `get_threshold_freshness(sport="Ride")` показывает status="stale", days_since=279, last_date=2025-07-02. Но `get_thresholds_history()` нашёл 2 свежих теста (6-7 апреля) с confidence=high.
**Вероятная причина:** threshold_freshness смотрит только на "официальные" рамп-тесты (create_ramp_test_tool), а не на пороги обнаруженные из обычных тренировок.
**Предложение:** threshold_freshness должен учитывать ВСЕ обнаруженные пороги с confidence=high, не только рамп-тесты.
**Бонус:** drift_alerts работает отлично! Обнаружил что HRVT1 stable at 163 bpm, а configured LTHR=153 (+6.3%). Предложение обновить — очень полезно.

---

## 2. НОВЫЕ ТУЛЫ — ЗАПРОСЫ

### 🔴 HIGH PRIORITY

#### FEAT-001: get_weekly_summary
**Зачем:** Каждое воскресенье коуч вручную считает TSS за неделю, средний HRV, средний сон, средние стики, количество тренировок. Автоматизация сэкономит 5-10 минут на каждом чек-ине.
**Вход:** `week_start_date` (YYYY-MM-DD, понедельник)
**Выход:**
```json
{
  "week": "2026-03-30 to 2026-04-05",
  "training": {
    "sessions_planned": 9,
    "sessions_completed": 6,
    "compliance_pct": 67,
    "total_tss": 152,
    "total_hours": 4.5,
    "by_sport": {
      "bike": {"sessions": 2, "tss": 71, "hours": 2.0},
      "run": {"sessions": 1, "tss": 18, "hours": 0.5},
      "swim": {"sessions": 2, "tss": 34, "hours": 0.9},
      "other": {"sessions": 1, "tss": 3, "hours": 0.1}
    }
  },
  "wellness": {
    "hrv_avg": 32.7,
    "hrv_min": 20,
    "hrv_max": 46,
    "hrv_cv": 28.3,
    "rhr_avg": 64.3,
    "sleep_avg_hours": 6.2,
    "sleep_7h_days": 2,
    "sleep_avg_score": 69.5
  },
  "iqos": {
    "avg_per_day": 12.6,
    "total": 63,
    "days_tracked": 5,
    "days_over_ceiling": 1,
    "ceiling": 18
  },
  "load": {
    "ctl_start": 11.1,
    "ctl_end": 11.4,
    "ctl_delta": 0.3,
    "ramp_rate": 0.9
  }
}
```

#### ~~FEAT-002: get_sleep_details~~ ⛔ BLOCKED
**Статус:** Заблокирован. Intervals.icu API не отдаёт фазы сна (только sleepSecs, sleepScore, sleepQuality, avgSleepingHR). Garmin Connect неофициальный API агрессивно банит (429). Официальный Garmin Health API требует партнёрскую заявку.

#### FEAT-003: get_power_zones / get_hr_zones
**Зачем:** Коуч анализирует HR zone distribution каждой тренировки, но не знает текущие настроенные зоны в Intervals.icu. Нужно видеть конфигурацию чтобы:
- Сравнивать с обнаруженными порогами (HRVT1 HR=163 vs configured LTHR=153)
- Предлагать обновление зон
**Источник:** Intervals.icu API `/api/v1/athlete/{id}/settings`
**Выход:**
```json
{
  "hr_zones": {
    "lthr": 153,
    "max_hr": 190,
    "zones": [
      {"zone": 1, "name": "Recovery", "min_hr": 0, "max_hr": 136},
      {"zone": 2, "name": "Endurance", "min_hr": 136, "max_hr": 153}
    ]
  },
  "power_zones": {
    "ftp": 208,
    "zones": [
      {"zone": 1, "name": "Active Recovery", "min_w": 0, "max_w": 128},
      {"zone": 2, "name": "Endurance", "min_w": 129, "max_w": 174}
    ]
  }
}
```

### 🟡 MEDIUM PRIORITY

#### FEAT-004: predict_ctl
**Зачем:** "Когда CTL достигнет 25?" — считается вручную. Нужна авторасчёт.
**Вход:** `target_ctl`, `sport` (optional)
**Выход:**
```json
{
  "current_ctl": 13.2,
  "target_ctl": 25,
  "current_ramp_rate": 2.06,
  "estimated_date": "2026-05-10",
  "estimated_weeks": 4.7,
  "confidence": "medium",
  "note": "Based on last 14-day ramp rate"
}
```

#### FEAT-005: get_weight_trend
**Зачем:** Цель ≤76кг к 1 мая. Видим разовые значения (76.5, 77.0), но не тренд.
**Вход:** `days_back` (default 30)
**Выход:**
```json
{
  "current": 77.0,
  "target": 76.0,
  "avg_30d": 76.5,
  "trend_direction": "stable",
  "trend_slope": -0.02,
  "estimated_target_date": "2026-05-15"
}
```

#### FEAT-006: update_zones (action tool)
**Зачем:** threshold_freshness обнаружил что LTHR устарел (153 vs 163). Коуч хочет обновить зоны без ручного входа в Intervals.icu.
**Вход:** `sport`, `lthr` или `ftp`
**Действие:** PUT к Intervals.icu API `/api/v1/athlete/{id}/settings`

#### ~~FEAT-007: get_garmin_stress~~ ⛔ BLOCKED
**Статус:** Заблокирован по той же причине что FEAT-002 — Garmin Connect неофициальный API банит (429). Stress Level и Body Battery недоступны через Intervals.icu.

### 🟢 LOW PRIORITY

#### FEAT-008: get_workout_compliance
**Зачем:** Hugo запланировал Endurance 60м 119-170W, атлет сделал 60м 134W avg. Насколько попал в план?
**Вход:** `activity_id` + `scheduled_workout_id`
**Выход:**
```json
{
  "planned": {"duration_min": 60, "avg_power_target": [119, 170]},
  "actual": {"duration_min": 60, "avg_power": 134},
  "compliance": {
    "duration_pct": 100,
    "intensity_in_target": true,
    "overall": "good"
  }
}
```

#### FEAT-009: predict_race_readiness
**Зачем:** Общая оценка "на сколько % готов к гонке" на основе всех метрик.
**Вход:** Нет (берёт из goal и текущих данных)
**Выход:**
```json
{
  "race": "Ironman 70.3 Belgrade",
  "date": "2026-09-15",
  "weeks_remaining": 23,
  "readiness_pct": 18,
  "by_factor": {
    "ctl": 19,
    "swim_technique": 25,
    "bike_fitness": 15,
    "run_fitness": 5,
    "consistency": 60,
    "health": 70
  }
}
```

---

## 3. УЛУЧШЕНИЯ СУЩЕСТВУЮЩИХ ТУЛОВ

### get_recovery — добавить AI recommendation summary
Сейчас ai_recommendation содержит длинный Markdown-текст (300+ слов). Для коуча полезнее краткий summary в 1-2 строки + ключевой совет. Предложение: добавить поле `ai_summary` (50 слов max).

### get_threshold_freshness — учитывать workout-detected thresholds
Сейчас freshness показывает "stale" (279 дней) хотя thresholds_history нашёл 2 свежих порога с confidence=high. Предложение: freshness должен использовать ВСЕ обнаруженные пороги, не только рамп-тесты. Рамп-тесты — для "official" обновления, но для freshness monitoring годятся любые high-confidence детекции.

### ~~get_wellness — добавить sleep phases~~
~~Заблокировано: Intervals.icu API не отдаёт фазы сна от Garmin (только sleepSecs/sleepScore/sleepQuality/avgSleepingHR).~~

---

## 4. ДАННЫЕ ОТ АУДИТА — ЦЕННЫЕ НАХОДКИ

### Обнаруженные пороги (впервые!)
Пирамиды 6-7 апреля дали реальные threshold-данные:

| Дата | HRVT1 HR | HRVT1 Power | HRVT2 HR | Confidence | R² |
|------|----------|-------------|----------|------------|----|
| 6 апр | 162.2 | 168W | 175.7 | high | 0.774 |
| 7 апр | 166.0 | 165W | 178.0 | high | 0.713 |

**Drift alert обнаружил:** HRVT1 stable at 163 bpm (3 tests). Current LTHR Ride: 153 bpm (+6.3%). **Рекомендация: обновить LTHR до 163.**

### Readiness (Ra) — первые данные
7 апреля: Ra = +31.9% (excellent). Warmup power 168.7W при DFA baseline. Это означает атлет пришёл на тренировку в отличной форме после дня отдыха.

---

## 5. ПРИОРИТЕТЫ (SUMMARY)

| # | Тип | Описание | Приоритет |
|---|-----|----------|-----------|
| ~~BUG-001~~ | ~~🐛 Баг~~ | ~~training_log таймаут~~ | ✅ Починен: composite index (user_id, date) |
| ~~BUG-002~~ | ~~🐛 Баг~~ | ~~efficiency_trend фильтры слишком строгие (bike)~~ | ✅ Починен: Z2 фильтр теперь только в strict mode |
| ~~BUG-003~~ | ~~🐛 Баг~~ | ~~efficiency_trend нет данных по бегу~~ | ✅ Починен: та же причина |
| BUG-004 | 🐛 Баг | threshold_freshness не видит workout-detected thresholds | 🟡 |
| ~~BUG-005~~ | ~~🐛 Баг~~ | ~~compose_workout external_id (#81)~~ | ✅ Починен: убран check intervals_id в условии сохранения |
| FEAT-001 | ✨ Фича | get_weekly_summary | 🔴 |
| ~~FEAT-002~~ | ~~✨ Фича~~ | ~~get_sleep_details (фазы сна)~~ | ⛔ Blocked: Garmin API 429 ban |
| FEAT-003 | ✨ Фича | get_power_zones / get_hr_zones | 🔴 |
| FEAT-004 | ✨ Фича | predict_ctl | 🟡 |
| FEAT-005 | ✨ Фича | get_weight_trend | 🟡 |
| FEAT-006 | ✨ Фича | update_zones (action) | 🟡 |
| ~~FEAT-007~~ | ~~✨ Фича~~ | ~~get_garmin_stress / body_battery~~ | ⛔ Blocked: Garmin API 429 ban |
| FEAT-008 | ✨ Фича | get_workout_compliance | 🟢 |
| FEAT-009 | ✨ Фича | predict_race_readiness | 🟢 |
