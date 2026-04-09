# Progress Tracking — Efficiency Factor & Aerobic Fitness Trends

> Отслеживание аэробного прогресса по трём видам спорта.
> Обновлено: 2026-03-29

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

### Swim — Pace + SWOLF Trend

Пульс в воде ненадёжен (кроме chest strap под гидрокостюм), мощности нет → EF не применим.

Метрики прогресса:

- **Pace trend** — темп на 100м (из `pace` в `activity_details`). Если темп падает — прогресс
- **SWOLF** — `time_per_length + strokes_per_length`. Комплексная метрика эффективности (ниже = лучше)
- **CSS (Critical Swim Speed)** — периодический тест 400м + 200м all-out. Текущий CSS: 141 сек/100м (из `ATHLETE_CSS`). Рост CSS — прямой индикатор порога
- **Pace consistency** — разброс split'ов внутри серии. Чем стабильнее — тем лучше

#### Расчёт SWOLF

`total_strokes` в API нет, но `average_stride` (м/гребок) и `pace` (м/с) **уже есть** в `activity_details`. SWOLF вычисляется:

```
time_per_length  = pool_length / pace           (секунды)
strokes_per_length = pool_length / average_stride (гребки)
SWOLF = time_per_length + strokes_per_length
```

**Пример** (i135084514, Swim 27.03): pool=25м, stride=0.99 м/гребок, pace=0.74 м/с:
- time_per_length = 25 / 0.74 = **33.8 сек**
- strokes_per_length = 25 / 0.99 = **25.3 гребка**
- **SWOLF ≈ 59**

#### SWOLF на уровне интервалов (точнее)

Каждый WORK-интервал в `intervals` имеет `average_stride`, `distance`, `moving_time`. Формула та же:

```
time_per_length    = moving_time * pool_length / distance   (секунды)
strokes_per_length = pool_length / average_stride           (гребки)
SWOLF = time_per_length + strokes_per_length
```

Позволяет трекать SWOLF по сетам внутри тренировки и между тренировками.

#### Что нужно для SWOLF

`pool_length` — единственное недостающее поле. Есть в Intervals.icu API (`pool_length: float` в Activity response), но **не сохраняется** в БД. Два варианта:

1. **Колонка в ActivityDetailRow** + маппинг `"pool_length": "pool_length"` + миграция (1 колонка)
2. **Env-переменная** `ATHLETE_POOL_LENGTH=25` — если атлет всегда плавает в одном бассейне

Вариант 1 надёжнее (бассейны могут быть 25м и 50м). Вариант 2 проще для старта.

---

## Источники данных

### Все поля уже есть в `ActivityDetailRow`

Миграция и расширение `ActivityRow` **не требуются**. Все нужные данные уже в таблице `activity_details`:

| Поле в БД | Тип | Маппинг из API | Спорт | Назначение |
|---|---|---|---|---|
| `normalized_power` | int | `icu_weighted_avg_watts` | Bike | Числитель EF для Bike |
| `avg_power` | int | `icu_average_watts` | Bike | Альтернатива NP |
| `avg_speed` | float | `average_speed` | Run/Swim | Числитель EF для Run, pace для Swim |
| `pace` | float | `pace` | Run/Swim | Темп (м/с). Swim: используется в расчёте SWOLF |
| `distance` | float | `distance` | All | Фильтрация по дистанции |
| `efficiency_factor` | float | `icu_efficiency_factor` | Bike/Run | **EF уже рассчитан Intervals.icu** |
| `decoupling` | float | `decoupling` | Bike/Run | Aerobic decoupling (%) |
| `avg_cadence` | float | `average_cadence` | All | Bike/Run каденс, Swim strokes/min |
| `avg_stride` | float | `average_stride` | Swim/Run | Swim: м/гребок → SWOLF. Run: длина шага |
| `intervals` | JSON | `icu_intervals` | All | WORK-интервалы с per-interval stride/cadence/speed |

**Нужно добавить** (одна миграция):

| Поле | Тип | Маппинг из API | Назначение |
|---|---|---|---|
| `pool_length` | float, nullable | `pool_length` | Длина бассейна (25м/50м). Нужна для SWOLF |

### Что это значит

- **EF не нужно считать** — Intervals.icu уже вычисляет `icu_efficiency_factor` и мы его сохраняем
- **Decoupling** тоже уже есть — `decoupling` в `ActivityDetailRow`
- Для запроса нужен JOIN: `activities` (type, date, moving_time, average_hr) + `activity_details` (efficiency_factor, decoupling, pace, etc.)

### Получение HR для фильтра Z2

`average_hr` живёт в `ActivityRow` (таблица `activities`), не в `activity_details`. Фильтрация Z2:

```python
# Bike Z2: 68-83% LTHR (из CLAUDE.md)
is_z2_bike = 0.68 <= (activity.average_hr / ATHLETE_LTHR_BIKE) <= 0.83

# Run Z2: 72-82% LTHR (из CLAUDE.md)
is_z2_run = 0.72 <= (activity.average_hr / ATHLETE_LTHR_RUN) <= 0.82
```

> **Важно:** пороги Z2 из CLAUDE.md (Bike 68-83%, Run 72-82%), не 65-80% как было в старой версии спеки.

---

## Фильтрация сопоставимых тренировок

Для корректного тренда EF нужно сравнивать только сопоставимые сессии:

1. **Минимальная длительность**: Bike ≥ 30 мин, Run ≥ 20 мин, Swim ≥ 15 мин
2. **Только steady-state Z2**: средний HR в пределах Z2 от LTHR (см. выше)
3. **Исключить**: интервальные тренировки (высокий variability_index), гонки, brick sessions
4. **Типы активностей**: нормализованы на входе в DTO → `Ride`, `Run`, `Swim`, `Other`

Дополнительный фильтр (v2, опционально):
- `variability_index < 1.05` — исключить интервальные сессии
- `decoupling < 10%` — исключить сессии с сильным cardiac drift

---

## Реализация

### Шаг 1 — MCP Tool

**Файл:** `mcp_server/tools/progress.py`

```python
@mcp.tool()
async def get_efficiency_trend(
    sport: str = "",        # "bike", "run", "swim". Empty = all
    days_back: int = 90,    # lookback window
    group_by: str = "week"  # "week" or "activity"
) -> dict:
    """Get aerobic efficiency trend over time.

    Bike: EF = Normalized Power / Avg HR (higher = fitter). From icu_efficiency_factor.
    Run: EF = Speed / Avg HR (higher = fitter). From icu_efficiency_factor.
    Swim: Pace per 100m trend (lower = faster).

    Only includes Z2 steady-state sessions for meaningful comparison.
    Minimum duration: bike 30min, run 20min, swim 15min.

    Data source: activity_details table (no new fields needed).
    """
```

**Логика:**
1. JOIN `activities` + `activity_details` за `days_back` дней
2. Фильтр по спорту, длительности, Z2 HR range
3. Для Bike/Run: читать `efficiency_factor` из `activity_details`
4. Для Swim: читать `pace` (сек/100м)
5. Группировка по неделям: среднее EF/pace + count
6. Расчёт тренда: `(last_week - first_week) / first_week * 100`

### Шаг 2 — API Endpoint

**Файл:** `api/routes.py`

```
GET /api/progress?sport=bike&days=90
```

Вызывает ту же логику что MCP tool. Возвращает JSON для webapp.

### Шаг 3 — Webapp

**Файл:** `webapp/src/pages/Dashboard.tsx` — новый таб "Progress" или отдельная страница.

Chart.js scatter plot с trend line. Точки = активности, линия = тренд.

### Шаг 4 — Swim Pace (отдельно)

Swim не использует EF. Отдельная секция в ответе:
- `pace` из `activity_details` (сек/100м)
- Тренд по неделям
- Минимальная длительность: ≥ 15 мин

---

## Пример ответа MCP Tool

```json
{
  "sport": "bike",
  "period": "2026-01-01 to 2026-03-29",
  "metric": "efficiency_factor",
  "unit": "W/bpm",
  "trend_direction": "rising",
  "trend_pct": "+5.2%",
  "data_points": 8,
  "weekly": [
    {"week": "2026-W08", "ef_mean": 0.88, "sessions": 1, "decoupling_mean": 10.6},
    {"week": "2026-W09", "ef_mean": 0.91, "sessions": 2, "decoupling_mean": 8.2},
    {"week": "2026-W11", "ef_mean": 0.93, "sessions": 3, "decoupling_mean": 6.1}
  ],
  "activities": [
    {"date": "2026-03-28", "id": "i135330872", "ef": 0.88, "duration_min": 31, "avg_hr": 129, "decoupling": 10.6}
  ]
}
```

Для Swim:
```json
{
  "sport": "swim",
  "period": "2026-01-28 to 2026-03-29",
  "metrics": {
    "pace_100m": {"unit": "sec/100m", "trend_direction": "falling", "trend_pct": "-3.1%"},
    "swolf": {"unit": "points", "trend_direction": "falling", "trend_pct": "-2.4%"}
  },
  "data_points": 10,
  "weekly": [
    {"week": "2026-W11", "pace_mean": 145.2, "swolf_mean": 62.1, "sessions": 2},
    {"week": "2026-W12", "pace_mean": 142.8, "swolf_mean": 60.5, "sessions": 3}
  ],
  "activities": [
    {"date": "2026-03-27", "id": "i135084514", "pace_100m": 134.7, "swolf": 59.1, "pool_length": 25, "distance": 900, "duration_min": 20}
  ]
}
```

---

## Связь с существующими метриками

- **CTL trend** показывает рост общей нагрузки, но не эффективность
- **HRVT1 (DFA)** показывает порог, но требует ramp-тренировки
- **EF** — единственная метрика, которая показывает прогресс на обычных Z2 тренировках
- **Recovery score** + **EF trend** вместе = полная картина: восстановление + адаптация
- **Decoupling** — дополняет EF: < 5% = хорошая аэробная база на данной интенсивности

---

## Текущие данные (на 2026-03-29)

За последние 60 дней: ~8 Ride, 3 Run, ~10 Swim. Для начального тренда Ride/Run достаточно. Swim — pace trend возможен.

---

## Порядок реализации

| # | Задача | Файлы | Зависимости |
|---|---|---|---|
| 1 | Добавить `pool_length` в `ActivityDetailRow` + маппинг + миграция | `data/database.py`, миграция | — |
| 2 | `refetch-details` для swim-активностей (подхватит pool_length) | CLI | #1 |
| 3 | MCP tool `get_efficiency_trend` (Bike/Run EF + Swim SWOLF + pace) | `mcp_server/tools/progress.py` | #1 |
| 4 | API endpoint `GET /api/progress` | `api/routes.py` | #3 |
| 5 | Webapp: Progress chart | `webapp/src/pages/Dashboard.tsx` | #4 |
| 6 | Тесты | `tests/test_progress.py` | #3 |

**Одна миграция** (pool_length). Остальные данные уже в БД.

---

## Критерии готовности

- [x] `pool_length` колонка в `ActivityDetailRow` + миграция
- [x] MCP tool `get_efficiency_trend` возвращает EF для bike/run, SWOLF + pace для swim
- [x] Z2 фильтрация работает корректно (пороги из CLAUDE.md: Bike 68-83%, Run 72-82%)
- [x] SWOLF вычисляется из `pace` + `avg_stride` + `pool_length`
- [ ] SWOLF по интервалам (WORK-type) для детального тренда (v2)
- [x] Группировка по неделям с расчётом тренда (%)
- [x] API endpoint возвращает данные
- [ ] Webapp отображает chart (v2)
- [x] Тесты: фильтрация Z2, расчёт SWOLF, расчёт тренда, пустые данные (23 теста в test_progress.py)
