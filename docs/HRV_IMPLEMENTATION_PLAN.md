# План внедрения HRV RMSSD анализа (Phase 1)

> На основе [HRV_MODULE_SPEC.md](HRV_MODULE_SPEC.md), секции 1.3–1.7

---

## Текущее состояние

- Garmin API возвращает `hrv_last_night` и `hrv_weekly_avg` (7-дневное среднее от Garmin)
- `calculate_readiness()` использует только одноразовый дельта-процент HRV — нет анализа трендов
- В БД таблица `daily_metrics` хранит только `sleep_hrv_avg`, колонки для HRV закомментированы
- Нет хранения RMSSD истории для 60-дневного baseline

---

## Шаги реализации

### Шаг 1. Расширить схему БД — таблица `daily_hrv`

Добавить новую таблицу (из спеки) для хранения ежедневных RMSSD значений.
Без неё невозможно считать 60-дневный baseline — Garmin API даёт только 7-дневное среднее.

Колонки: `date`, `rmssd_night`, `rmssd_morning`, `resting_hr`, `min_hr`,
`sleep_score`, `sleep_start`, `body_battery_am`, `stress_avg`, `garmin_readiness`.

### Шаг 2. Расширить Garmin sync — сохранять RMSSD в `daily_hrv`

В текущем sync pipeline добавить запись `hrv_last_night` в новую таблицу при каждом sync.
Это позволит накопить историю для baseline.

### Шаг 3. Реализовать `calculate_rmssd_status()` в `data/metrics.py`

Функция из спеки — 7d vs 60d baseline, CV, SWC, тренд. Нужны вспомогательные:

- `_classify_recovery(mean_7d, lower, upper)` → `'low'` / `'normal'` / `'elevated'`
- `_calculate_trend(values_14d)` → `'rising'` / `'stable'` / `'declining'` (линейная регрессия наклона)

Зависимость: `numpy` — добавить в requirements.

### Шаг 4. Реализовать `calculate_rhr_status()` в `data/metrics.py`

30-дневный baseline RHR с инвертированной интерпретацией
(повышенный RHR = плохое восстановление).

### Шаг 5. Обновить `calculate_readiness()`

Заменить текущую упрощённую логику HRV-дельты на вызов `calculate_rmssd_status()`. Добавить:

- Penalty за нестабильный CV (>15%) → -5
- Penalty за declining trend >3 дня → warning flag
- Вернуть расширенный результат с компонентами (`components` dict)

### Шаг 6. Новая Pydantic модель `RMSSDStatus` в `data/models.py`

```python
class RMSSDStatus(BaseModel):
    status: str          # 'low' | 'normal' | 'elevated' | 'insufficient_data'
    rmssd_7d: float
    rmssd_60d: float
    lower_bound: float
    upper_bound: float
    cv_7d: float
    swc: float
    trend: str           # 'rising' | 'stable' | 'declining'
```

### Шаг 7. Тесты в `tests/test_metrics.py`

- `test_rmssd_status_normal` — 7d среднее внутри нормы
- `test_rmssd_status_low` — 7d ниже lower_bound
- `test_rmssd_status_elevated` — 7d выше upper_bound
- `test_rmssd_insufficient_data` — менее 14 дней данных
- `test_rmssd_trend_declining` / `rising`
- `test_rhr_status_*` — аналогичные кейсы

### Шаг 8. Интеграция в утренний отчёт

Добавить RMSSD status в prompt (`ai/prompts.py`):

```
RMSSD Status: {status} (7d: {rmssd_7d}, norm: {lower}–{upper}, CV: {cv}%)
```

Обновить `bot/formatter.py` — отображать тренд стрелкой.

---

## Порядок и зависимости

```
Шаг 1 (БД) ──→ Шаг 2 (sync) ──→ Шаг 3-4 (расчёты) ──→ Шаг 5 (readiness)
                                        ↑                       ↓
                                   Шаг 6 (модели)          Шаг 8 (отчёт)
                                        ↓
                                   Шаг 7 (тесты)
```

---

## Холодный старт

Для полноценного 60-дневного baseline нужно ~2 месяца данных. На старте:

- **Минимум 14 дней** — иначе возвращаем `insufficient_data`
- **14–60 дней** — используем сколько есть (спека допускает: `recent_60 = rmssd_values[-60:]`)
- **Backfill** — можно загрузить историю через `garmin_client` за прошлые даты при первом запуске
