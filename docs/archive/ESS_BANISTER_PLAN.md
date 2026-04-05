# ESS/Banister Pipeline — Реализация

> External Stress Score и Banister Recovery Model — подключены к пайплайну.

---

## Статус: ✅ РЕАЛИЗОВАНО

Весь pipeline работает в `data/database.py → save_wellness()`, шаг 3 recovery pipeline.

---

## Что реализовано

### Функции в `data/metrics.py`

| Функция | Описание |
|---|---|
| `calculate_ess()` | Banister TRIMP, нормализация 1ч@LTHR ≈ 100 |
| `calculate_daily_ess()` | Сумма ESS по всем активностям за день |
| `calculate_banister_recovery()` | Рекурсивная модель: R(t+1) = R(t) + (100 - R(t)) * (1 - exp(-1/τ)) - k * ESS(t) |
| `calculate_banister_for_date()` | Обёртка: строит daily ESS log за N дней → Banister → (recovery_pct, ess_today) |

### Pipeline в `data/database.py → save_wellness()`

```
1. RMSSD → dual algorithm (Flatt & Esco + AIEndurance) → hrv_analysis
2. RHR → 7d/30d/60d baselines → rhr_analysis
3. ESS/Banister:
   a. get_activities_for_banister(days=90) — фильтр: average_hr IS NOT NULL AND > 0
   b. Group by date → calculate_banister_for_date()
   c. Persist: row.ess_today, row.banister_recovery
4. Combined Recovery Score (Banister = 25% weight, не fallback)
5. Readiness (derived)
```

### Данные

- `activities.average_hr` — добавлено в модель, ORM, API запрос, миграция
- `wellness.ess_today` — суммарный ESS за день (0 = отдых, 100 ≈ 1ч на ПАНО)
- `wellness.banister_recovery` — Banister R(t) в процентах (0-100, 100 = полное восстановление)
- `get_activities_for_banister()` в database.py — отдельный query с фильтром по average_hr

### Тесты в `tests/test_metrics.py`

- `TestBanisterRecovery` — empty log, rest day recovery, training reduces, clamped 0-100
- `TestDailyEss` — single/multi activity, no HR → 0, empty list
- `TestBanisterForDate` — end-to-end pipeline, rest vs training, ess_today matches

---

## Параметры (defaults)

| Параметр | Значение |
|---|---|
| k | 0.1 |
| τ (tau) | 2.0 |
| initial_recovery | 100.0 |
| lookback_days | 90 |

ESS нормализует нагрузку так, что 1ч@LTHR ≈ 100. Banister-модель рекуррентно обновляет R(t) на основе суточного ESS и двух параметров (k, τ). R(t) вносит 25% в общий Recovery Score (RMSSD 35%, RHR 20%, Sleep 20%). Defaults консервативные; после 4–6 недель данных можно калибровать через scipy.optimize.minimize.

> Full theory: [docs/knowledge/fitness-fatigue-model.md](knowledge/fitness-fatigue-model.md)
