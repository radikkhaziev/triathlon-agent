# Progress Tracking — Efficiency Factor & Aerobic Fitness Trends

> Отслеживание аэробного прогресса по трём видам спорта.

---

## Цель

Показать атлету, что при одинаковой нагрузке пульс снижается (или при одинаковом пульсе мощность/темп растёт). Это ключевой индикатор роста аэробной базы.

---

## Метрики по видам спорта

### Bike — Efficiency Factor (EF)

```
EF = Normalized Power (NP) / Average HR
```

- **Рост EF** = прогресс (больше ватт при том же пульсе)
- Пример: EF 1.52 → 1.61 за 8 недель = аэробная база растёт
- Фильтр: только Z2 steady-state сессии (исключить интервальные), минимум 30 мин
- Также полезно: HR at fixed power (e.g., avg HR при 150W) — проще интерпретировать

### Run — Efficiency Factor (EF)

```
EF = Speed (m/s) / Average HR
```

- **Рост EF** = прогресс (быстрее при том же пульсе)
- Альтернатива: HR at fixed pace (e.g., avg HR при 6:00/km)
- Фильтр: только Z2 easy runs, минимум 20 мин, исключить интервалы и hill repeats
- Также: Aerobic Decoupling (Pa:Hr) — drift пульса первая vs вторая половина. < 5% = хорошая аэробная база

### Swim — Pace & SWOLF Trend

Пульс в воде ненадёжен (кроме chest strap под гидрокостюм), мощности нет → EF не применим.

Метрики прогресса:
- **Pace trend** — темп на 100м на стандартных дистанциях (e.g., 10×100 на CSS). Если темп падает при одинаковых сетах — прогресс
- **SWOLF** — `strokes + time per pool length`. Снижение = лучше эффективность гребка
- **CSS (Critical Swim Speed)** — периодический тест 400м + 200м all-out. Текущий CSS: 98 сек/100м. Рост CSS — прямой индикатор порога
- **Pace consistency** — разброс split'ов внутри серии. Чем стабильнее — тем лучше

---

## Необходимые данные из Intervals.icu API

### Текущие поля в `ActivityRow`
```
id, start_date_local, type, icu_training_load, moving_time, average_hr
```

### Новые поля для добавления

| Field | Type | Sport | Source (Intervals.icu API field) |
|---|---|---|---|
| `distance` | Float, nullable | All | `distance` (meters) |
| `average_speed` | Float, nullable | Run/Swim | `icu_average_speed` (m/s) |
| `normalized_power` | Float, nullable | Bike | `icu_weighted_avg_watts` |
| `average_watts` | Float, nullable | Bike | `icu_average_watts` |
| `efficiency_factor` | Float, nullable | Bike/Run | calculated: NP/HR or speed/HR |
| `average_cadence` | Float, nullable | Bike/Run | `icu_average_cadence` |
| `total_strokes` | Integer, nullable | Swim | `total_strokes` |

### Расчёт EF при синхронизации

```python
# Bike
if type in ("Ride", "VirtualRide") and normalized_power and average_hr:
    ef = normalized_power / average_hr

# Run
if type == "Run" and average_speed and average_hr:
    ef = average_speed / average_hr
```

---

## Фильтрация сопоставимых тренировок

Для корректного тренда EF нужно сравнивать только сопоставимые сессии:

1. **Минимальная длительность**: Bike ≥ 30 мин, Run ≥ 20 мин
2. **Только steady-state Z2**: средний HR в пределах 65-80% от LTHR
3. **Исключить**: интервальные тренировки, гонки, brick sessions
4. **Нормализация**: учитывать температуру/влажность (если доступна) — cardiac drift увеличивается в жару

Упрощённый фильтр (v1):
```python
# Bike Z2 filter
is_z2_bike = (average_hr / LTHR_BIKE) between 0.65 and 0.83

# Run Z2 filter
is_z2_run = (average_hr / LTHR_RUN) between 0.65 and 0.82
```

---

## Реализация

### Phase 1 — Данные и расчёт

1. Расширить `ActivityRow` новыми полями (Alembic migration)
2. Обновить `intervals_client.py` — тянуть дополнительные поля при sync
3. Расчёт EF в `data/metrics.py` при сохранении активности
4. Backfill EF для существующих активностей

### Phase 2 — API и отображение

5. Новый MCP tool: `get_efficiency_trend(sport, days_back)` — EF по неделям с фильтрацией Z2
6. API endpoint: `GET /api/progress?sport=bike&days=90`
7. Telegram команда: `/progress` — краткий тренд EF по видам
8. Webapp: график EF over time (Chart.js scatter plot с trend line)

### Phase 3 — Swim analytics

9. Добавить swim-specific поля (strokes, SWOLF) если доступны через API
10. CSS тест tracking — ручной ввод или автодетект по паттерну (400+200)

---

## MCP Tool — Предварительный дизайн

```python
@mcp.tool()
async def get_efficiency_trend(
    sport: str = "",        # "bike", "run", "swim". Empty = all
    days_back: int = 90,    # lookback window
    group_by: str = "week"  # "week" or "activity"
) -> dict:
    """Get aerobic efficiency trend over time.

    Bike: EF = Normalized Power / Avg HR (higher = fitter)
    Run: EF = Speed / Avg HR (higher = fitter)
    Swim: Pace per 100m trend (lower = faster)

    Only includes Z2 steady-state sessions for meaningful comparison.
    Minimum duration: bike 30min, run 20min, swim 15min.
    """
```

### Пример ответа

```json
{
  "sport": "bike",
  "period": "2026-01-01 to 2026-03-24",
  "metric": "efficiency_factor",
  "unit": "W/bpm",
  "trend_direction": "rising",
  "trend_pct": "+5.2%",
  "data_points": 12,
  "weekly": [
    {"week": "2026-W01", "ef_mean": 1.48, "sessions": 2},
    {"week": "2026-W02", "ef_mean": 1.51, "sessions": 3},
    {"week": "2026-W04", "ef_mean": 1.55, "sessions": 2}
  ]
}
```

---

## Связь с существующими метриками

- **CTL trend** показывает рост общей нагрузки, но не эффективность
- **HRVT1 (DFA)** показывает порог, но требует ramp-тренировки
- **EF** — единственная метрика, которая показывает прогресс на обычных Z2 тренировках
- **Recovery score** + **EF trend** вместе = полная картина: восстановление + адаптация

---

## Приоритет

Средний. Зависит от накопления данных — для значимого тренда нужно минимум 4-6 недель Z2 сессий. Рекомендуется реализовать после bot commands (#7) и перед MCP Phase 2 (#9).
