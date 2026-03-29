# Спека: заполнение `actual_max_zone_time` в training_log

> Блокер для Gemini Role Spec (#21) — матрица recovery × intensity.
> Дата: 2026-03-29

---

## Контекст

Поле `actual_max_zone_time` объявлено в `TrainingLogRow` (`String(10)`, nullable), но **нигде не заполняется**. Оно должно хранить зону, в которой атлет провёл больше всего времени во время тренировки (например `"Z2"`, `"Z3"`). Без этого поля Gemini не может построить матрицу «recovery × intensity» для анализа персональных паттернов.

### Где объявлено

- **`data/database.py`**, строка ~1322:
  ```python
  actual_max_zone_time: Mapped[str | None] = mapped_column(String(10), nullable=True)
  ```
- **Миграция:** `b2c3d4e5f6a7_add_training_log_table.py`, строка 54
- **Документация:** `docs/ADAPTIVE_TRAINING_PLAN.md`, строка 516: `"Z2" | "Z3" | "Z4" — реально достигнутая макс зона`

### Где должно заполняться

- **`bot/scheduler.py`**, функция `_fill_training_log_actual()` (строка ~487)
- Сейчас заполняет: `actual_activity_id`, `actual_sport`, `actual_duration_sec`, `actual_avg_hr`, `actual_tss`, `compliance`
- **Не заполняет:** `actual_max_zone_time`

### Откуда брать данные о зонах

- **`data/database.py`**, `ActivityDetailRow`:
  - `hr_zones: JSON` — **пороги HR-зон** (bpm), НЕ время. Из `icu_hr_zones` Intervals.icu API
  - `power_zones: JSON` — **пороги power-зон** (watts). Из `icu_power_zones`
  - `pace_zones: JSON` — **пороги pace-зон**
  - `hr_zone_times: JSON` — **✅ НОВОЕ** — секунды в каждой HR-зоне. Из `icu_hr_zone_times`
  - `power_zone_times: JSON` — **✅ НОВОЕ** — секунды в каждой power-зоне. Из `icu_zone_times` (ZoneTime[].secs)
  - `pace_zone_times: JSON` — **✅ НОВОЕ** — секунды в каждой pace-зоне. Из `pace_zone_times`

- **⚠️ Важно:** `icu_hr_zones` / `icu_power_zones` хранят **пороги** (bpm/watts), а `icu_hr_zone_times` / `icu_zone_times` / `pace_zone_times` хранят **время** (секунды). Для `_compute_max_zone()` нужно время, не пороги.

- **Формат данных:** `icu_hr_zone_times` возвращает массив int (секунды). Код обрабатывает оба варианта: 6 элементов `[below_z1, z1..z5]` → `zones[1:6]`, 5 элементов `[z1..z5]` → `zones[:5]`.

- **Метод получения:** `ActivityDetailRow.get(activity_id)` — возвращает строку с зонами

### Зоны по видам спорта (из CLAUDE.md)

| Спорт | Зоны (% LTHR) | Основной источник зон |
|---|---|---|
| Run | Z1 0-72%, Z2 72-82%, Z3 82-87%, Z4 87-92%, Z5 92-100% | `hr_zones` |
| Ride | Z1 0-68%, Z2 68-83%, Z3 83-94%, Z4 94-105%, Z5 105-120% | `power_zones` (приоритет) → `hr_zones` (fallback) |
| Swim | Зоны по темпу от CSS | `pace_zones` (приоритет) → `hr_zones` (fallback) |
| WeightTraining / Other | HR-зоны если есть | `hr_zones` |

---

## Что нужно сделать

### 1. Добавить функцию `_compute_max_zone()`

**Файл:** `bot/scheduler.py`

```python
async def _compute_max_zone(activity_id: str, sport: str | None = None) -> str | None:
    """Determine the zone where the athlete spent the most time.

    Returns "Z1".."Z5" or None if no zone data available.

    Priority by sport:
    - Ride: power_zones > hr_zones
    - Swim: pace_zones > hr_zones
    - Run / Other: hr_zones
    """
    detail = await ActivityDetailRow.get(activity_id)
    if not detail:
        return None

    # Choose zone array by sport priority
    zones = None
    if sport == "Ride" and detail.power_zones:
        zones = detail.power_zones
    elif sport == "Swim" and detail.pace_zones:
        zones = detail.pace_zones

    # Fallback на hr_zones если спорт-специфичных зон нет
    if not zones and detail.hr_zones:
        zones = detail.hr_zones

    if not zones:
        return None

    # NB: sport приходит из matched.type (Intervals.icu ActivityRow).
    # Intervals.icu всегда возвращает "Ride", "Run", "Swim" с заглавной.
    # Но на всякий случай можно добавить sport = (sport or "").capitalize()

    # zones — массив секунд от Intervals.icu.
    # ⚠️ ИНДЕКСАЦИЯ: icu_hr_zones возвращает 6 элементов:
    #   [below_z1, z1, z2, z3, z4, z5] — индекс 0 = ниже Z1.
    # Берём zones[1:6] для Z1-Z5.
    # ВАЖНО: проверить на реальных данных перед деплоем (SQL в секции ниже).
    # Если формат 5 элементов — заменить на zones[:5].
    if len(zones) >= 6:
        zone_values = zones[1:6]  # skip below_z1
    elif len(zones) == 5:
        zone_values = zones[:5]
    else:
        return None  # неожиданный формат — не угадываем

    if not zone_values or all(v == 0 for v in zone_values):
        return None

    # При равных значениях берём наименьшую зону (Z2 важнее Z4 при tie).
    # min() с инвертированным ключом: максимальное время, минимальный индекс.
    max_idx = min(range(len(zone_values)), key=lambda i: (-zone_values[i], i))
    return f"Z{max_idx + 1}"
```

**⚠️ БЛОКЕР — проверка индексации перед реализацией:**

Intervals.icu `icu_hr_zones` скорее всего возвращает **6 элементов** (`[below_z1, z1, z2, z3, z4, z5]`), но это нужно подтвердить на реальных данных. Код выше обрабатывает оба варианта (6 и 5 элементов), но для уверенности **обязательно** выполнить SQL:

```sql
SELECT activity_id,
       json_array_length(hr_zones::json) as hr_len,
       hr_zones,
       json_array_length(power_zones::json) as pwr_len,
       power_zones
FROM activity_details
WHERE hr_zones IS NOT NULL
LIMIT 10;
```

Что проверить:
- Длина массива (5 или 6?)
- Если 6: `zones[0]` — это время ниже Z1? (обычно маленькое число или 0)
- То же самое для `power_zones` и `pace_zones` — одинаковая ли индексация?

**Код НЕ деплоить до проверки.** Результат определяет ветку `if len(zones) >= 6` vs `elif len(zones) == 5`.

### 2. Вызвать в `_fill_training_log_actual()`

**Файл:** `bot/scheduler.py`, строки ~546-554

**Было:**
```python
await TrainingLogRow.update(
    log.id,
    actual_activity_id=matched.id,
    actual_sport=matched.type,
    actual_duration_sec=matched.moving_time,
    actual_avg_hr=matched.average_hr,
    actual_tss=matched.icu_training_load,
    compliance=compliance,
)
```

**Стало:**
```python
max_zone = await _compute_max_zone(matched.id, sport=matched.type)

await TrainingLogRow.update(
    log.id,
    actual_activity_id=matched.id,
    actual_sport=matched.type,
    actual_duration_sec=matched.moving_time,
    actual_avg_hr=matched.average_hr,
    actual_tss=matched.icu_training_load,
    actual_max_zone_time=max_zone,
    compliance=compliance,
)
```

### 3. Бэкфилл для существующих записей

Записи в `training_log` с `actual_activity_id IS NOT NULL` и `actual_max_zone_time IS NULL` нужно дозаполнить.

**Вариант A — CLI команда:**

Добавить в `bot/cli.py`:
```python
@cli.command()
def backfill_max_zone():
    """Backfill actual_max_zone_time for existing training_log entries."""
    asyncio.run(_backfill_max_zone())

async def _backfill_max_zone():
    rows = await TrainingLogRow.get_range(days_back=365)
    count = 0
    for row in rows:
        if row.actual_activity_id and not row.actual_max_zone_time:
            zone = await _compute_max_zone(row.actual_activity_id, sport=row.actual_sport)
            if zone:
                await TrainingLogRow.update(row.id, actual_max_zone_time=zone)
                count += 1
    print(f"Backfilled {count} entries")
```

~~**Вариант B — SQL**~~ — не рекомендуется: не учитывает приоритет зон по спорту, индексацию below_z1, и tie-breaking логику. Использовать только CLI.

---

## Импорты

В `bot/scheduler.py` добавить (если ещё не импортирован):
```python
from data.database import ActivityDetailRow
```

---

## Edge Cases

| Кейс | Поведение |
|---|---|
| `ActivityDetailRow` не существует для activity | `actual_max_zone_time = None` |
| `hr_zones` / `power_zones` = `None` или `[]` | `actual_max_zone_time = None` |
| Все зоны = 0 (нет HR-данных) | `actual_max_zone_time = None` |
| Ride без power_zones, но с hr_zones | Fallback на hr_zones |
| Swim без pace_zones | Fallback на hr_zones |
| WeightTraining / Other | Используем hr_zones |
| Несколько зон с одинаковым временем | Берём наименьшую зону (Z2 > Z4 при tie) — `min()` с `(-value, index)` ключом |
| Массив зон < 5 элементов | `None` — неожиданный формат, не угадываем |
| `compliance = "skipped"` | `actual_activity_id = None` → зона не вычисляется |

---

## Тесты

**Файл:** `tests/test_max_zone.py`

### Минимальный набор:

1. **test_max_zone_hr_run_6elem** — Run с `hr_zones = [30, 60, 1800, 600, 120, 30]` (6 элементов, `[0]` = below Z1) → `"Z2"`
2. **test_max_zone_hr_run_5elem** — Run с `hr_zones = [60, 1800, 600, 120, 30]` (5 элементов) → `"Z2"`
3. **test_max_zone_power_ride** — Ride с `power_zones = [0, 120, 300, 1200, 600, 60]` и `hr_zones = [...]` → использует power_zones → `"Z3"`
4. **test_max_zone_ride_fallback_hr** — Ride без power_zones → fallback на hr_zones
5. **test_max_zone_swim_pace** — Swim с `pace_zones = [0, 200, 400, 800, 100, 0]` → `"Z3"`
6. **test_max_zone_no_detail** — activity без ActivityDetailRow → `None`
7. **test_max_zone_empty_zones** — `hr_zones = []` → `None`
8. **test_max_zone_all_zeros** — `hr_zones = [0, 0, 0, 0, 0, 0]` → `None`
9. **test_max_zone_short_array** — `hr_zones = [100, 200, 300]` (< 5 элементов) → `None`
10. **test_max_zone_tie_takes_lower** — `hr_zones = [0, 100, 500, 200, 500, 50]` → Z2 и Z4 равны (500) → берём `"Z2"` (наименьшую)
11. **test_fill_actual_includes_zone** — интеграционный: `_fill_training_log_actual()` пишет `actual_max_zone_time` в БД
12. **test_backfill_existing_entries** — CLI backfill дозаполняет старые записи

---

## Выходные значения

| Значение | Описание |
|---|---|
| `"Z1"` | Преимущественно восстановительная / лёгкая |
| `"Z2"` | Аэробная база (endurance) |
| `"Z3"` | Темпо |
| `"Z4"` | Пороговая (threshold) |
| `"Z5"` | VO2max / анаэробная |
| `None` | Нет данных о зонах |

---

## Зависимости

- **Не требует миграций** — поле уже существует в схеме
- **Не требует новых таблиц** — использует существующую `activity_details`
- **Требует:** `ActivityDetailRow.get()` — уже реализован

---

## Порядок реализации

1. Проверить индексацию `icu_hr_zones` на реальных данных (SQL-запрос выше)
2. Написать `_compute_max_zone()` в `bot/scheduler.py`
3. Добавить вызов в `_fill_training_log_actual()`
4. Добавить CLI backfill
5. Написать тесты
6. Запустить backfill на проде

**Оценка:** ~2-3 часа работы.

---

## Связанные документы

- `docs/GEMINI_ROLE_SPEC.md` — основной потребитель (матрица recovery × intensity)
- `docs/ADAPTIVE_TRAINING_PLAN.md` — описание training_log схемы
- `CLAUDE.md` — HR-зоны по видам спорта
