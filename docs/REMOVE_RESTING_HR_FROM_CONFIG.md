# Task: Remove ATHLETE_RESTING_HR from config, use DB value

> Resting HR приходит ежедневно из Intervals.icu → `wellness.resting_hr`. Хранить в конфиге не нужно.

---

## Текущее состояние

`ATHLETE_RESTING_HR` используется в 2 местах кода + документация:

### 1. `data/database.py` — строка ~434

```python
banister_r, ess_today = calculate_banister_for_date(
    activities_by_date=activities_by_date,
    target_date=dt_date,
    hr_rest=settings.ATHLETE_RESTING_HR,  # ← заменить
    hr_max=settings.ATHLETE_MAX_HR,
)
```

**Решение:** взять `resting_hr` из текущей `WellnessRow` (переменная `row`), которая уже доступна в этом контексте. Fallback на 42 если `None`.

```python
hr_rest = row.resting_hr if row.resting_hr is not None else 42
banister_r, ess_today = calculate_banister_for_date(
    activities_by_date=activities_by_date,
    target_date=dt_date,
    hr_rest=hr_rest,
    hr_max=settings.ATHLETE_MAX_HR,
)
```

### 2. `mcp_server/resources/athlete_profile.py` — строка 19

```python
f"Resting HR: {settings.ATHLETE_RESTING_HR} bpm\n"
```

**Решение:** убрать строку из статического профиля. Resting HR — динамическое значение, доступное через `get_wellness(date)`. Альтернатива — сделать ресурс async и тянуть последнее значение из БД, но это overengineering для MCP resource.

Заменить на:
```python
f"Resting HR: from daily wellness data (get_wellness tool)\n"
```

---

## Изменения

### Файл 1: `config.py`

Удалить строку:
```python
ATHLETE_RESTING_HR: float = 42
```

### Файл 2: `data/database.py` (~строка 434)

Заменить:
```python
hr_rest=settings.ATHLETE_RESTING_HR,
```
На:
```python
hr_rest=row.resting_hr if row.resting_hr is not None else 42,
```

### Файл 3: `mcp_server/resources/athlete_profile.py` (строка 19)

Заменить:
```python
f"Resting HR: {settings.ATHLETE_RESTING_HR} bpm\n"
```
На:
```python
f"Resting HR: dynamic (from daily wellness sync)\n"
```

### Файл 4: `.env.example`

Удалить строку:
```
ATHLETE_RESTING_HR=42
```

### Файл 5: `CLAUDE.md` — секция Environment Variables

Удалить строку:
```
ATHLETE_RESTING_HR=42         # updated automatically from Intervals.icu
```

### Файл 6: `CLAUDE.md` — секция Athlete thresholds (config.py description)

Обновить описание конфига — убрать ATHLETE_RESTING_HR из списка.

---

## Что НЕ менять

- `data/metrics.py` — `calculate_banister_for_date()` принимает `hr_rest` как аргумент, интерфейс не меняется
- `wellness.resting_hr` — уже приходит из Intervals.icu API, хранится в БД, ничего менять не надо
- Никаких миграций — схема БД не меняется

---

## Проверка после изменений

```bash
grep -r "ATHLETE_RESTING_HR" --include="*.py" --include="*.md" --include="*.example" .
```

Результат должен быть пустым.
