# process_fit_job — Post-activity DFA Alpha 1 Pipeline

## Расписание

```
:30  sync_activities_job   → загружает activities из Intervals.icu API в БД
*/5  process_fit_job       → обрабатывает те, что ещё не проанализированы (каждые 5 мин)
```

## Что делает process_fit_job

**1. Находит необработанные активности** (`get_unprocessed_activities` в `database.py`):
- Тип = Ride/VirtualRide/GravelRide/MountainBikeRide/Run/VirtualRun/TrailRun
- `id NOT IN (SELECT activity_id FROM activity_hrv)` — ещё нет записи в таблице результатов
- `moving_time >= 900` (≥15 мин)
- Сортировка по дате DESC (от новых к старым), `LIMIT 5` (batch_size)

**2. Для каждой активности запускает `process_activity_hrv`** (`hrv_activity.py`), который последовательно:

| Шаг | Что делает | Ранний выход |
|-----|-----------|-------------|
| 1 | `intervals.download_fit(activity_id)` — скачивает оригинальный FIT с Intervals.icu | FIT нет → `no_rr_data` |
| 2 | `extract_rr_intervals(fit_bytes)` — парсит HRV messages из FIT через fitparse | < 300 RR интервалов → `too_short` или `no_rr_data` |
| 3 | `correct_rr_artifacts(rr_ms)` — коррекция спайков (медианный фильтр, окно 5 бит) | artifact > 10% → `low_quality` |
| 4 | `extract_records(fit_bytes)` — HR/power/speed из Record messages того же FIT | — |
| 5 | `calculate_dfa_timeseries()` — скользящее окно 2 мин, шаг 5 сек, DFA a1 + HR + power | пустой → `too_short` |
| 6 | `detect_hrv_thresholds()` — линейная регрессия DFA a1 vs HR, интерполяция HRVT1 (a1=0.75) / HRVT2 (a1=0.50) | нет ramp → None (ок) |
| 7 | `calculate_readiness_ra()` — сравнивает warmup power/pace с 14-дневным baseline из `pa_baseline` | нет baseline → None |
| 8 | Сохраняет Pa warmup в `pa_baseline` для будущих Ra | — |
| 9 | `calculate_durability_da()` — первая vs вторая половина активности | < 40 мин → None |
| 10 | Сохраняет всё в `activity_hrv` + timeseries (каждые 30 сек) | — |

**3. Каждый ранний выход тоже сохраняет запись** в `activity_hrv` с соответствующим `processing_status`. Это ключевой момент — запись есть всегда, поэтому `NOT IN (SELECT activity_id FROM activity_hrv)` в следующем запуске пропустит эту активность. Job не будет повторно скачивать FIT.

## Качество RR-данных: ANT+ vs BLE

> Исследовано на реальных данных атлета (Garmin HRM-Dual, март 2026).

### Результаты тестирования

| Протокол | Устройство записи | Артефакты | Фрагменты <200ms | Quality | DFA пригоден |
|----------|-------------------|-----------|-------------------|---------|-------------|
| **ANT+** | Garmin watch (бег, дек 2025) | 0.04% | 0% | good | да |
| **BLE** | Garmin watch (бег, мар 2026) | 25% | 17.5% | poor | нет |

### Проблема BLE

Garmin watch принимает RR от HRM-Dual по BLE и **фрагментирует пакеты** — один реальный удар сердца дробится на 2-3 части. Например:
- Реальный RR: 662 ms (один удар)
- BLE запись: 373 ms + 289 ms (два фрагмента)

Это приводит к 17-25% значений < 200 ms, которые artifact correction расценивает как `poor` quality (>10%). Pipeline возвращает `low_quality` и пропускает активность.

**ANT+ не имеет этой проблемы** — RR передаются без фрагментации, артефакты < 0.25%.

### Рекомендуемая конфигурация оборудования

| Дисциплина | Запись | Датчик HR | Протокол | RR данные |
|-----------|--------|-----------|----------|-----------|
| **Бег** | Garmin watch | HRM-Dual | **ANT+** | да |
| **Велотренажёр** | Garmin Edge | HRM-Dual | **ANT+** | да |
| **Велотренажёр** | Rouvy (без Garmin) | HRM-Dual | BLE/ANT+ | **нет** — Rouvy не пишет HRV messages в FIT |

Rouvy (как и Zwift, TrainerRoad) записывает только HR/power/cadence, но не RR-интервалы. Для DFA на велотренажёре нужно записывать через Garmin Edge параллельно с Rouvy.

### Что не обрабатывается и почему

| Источник | processing_status | Причина |
|----------|-------------------|---------|
| Rouvy FIT (VirtualRide) | `no_rr_data` | Нет HRV messages в FIT |
| Garmin watch + HRM-Dual по BLE | `low_quality` | Фрагментация RR, >10% артефактов |
| Запястный датчик (без HRM strap) | `no_rr_data` | Запястье не записывает RR |
| Swim (любой датчик) | не обрабатывается | Нет RR в воде, нет в `_ELIGIBLE_TYPES` |

### FIT Record fields

Garmin FIT использует `enhanced_speed` (не `speed`) для скорости. Учтено в `extract_records()` — парсятся оба варианта.

## Ограничения

- **batch_size=5** — максимум 5 FIT за запуск (rate limit Intervals.icu)
- **Часы работы** — 4:00–23:00 (как и другие jobs)
- При первом запуске (backfill) со 100+ активностями — потребуется ~20 часов (5 шт/час)
- Swim, WeightTraining, Walk — не обрабатываются (нет в `_ELIGIBLE_TYPES`)
- Для threshold detection (HRVT1/HRVT2) нужна тренировка с ramp (прогрессивный набор интенсивности). Ровная Z2 пробежка — thresholds = None (нормальное поведение)
- Durability (Da) требует ≥40 мин активности
