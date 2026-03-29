# Workout Cards

> Библиотека упражнений с HTML-карточками + сборка зарядок из карточек.

---

## Концепция

Claude генерирует зарядку (разминка, силовая, растяжка) через MCP. Тренировка синхронизируется с Intervals.icu → Garmin. Параллельно атлет получает **HTML-страницу** с визуальными карточками всех упражнений — анимации, техника, подходы/повторы, инвентарь.

```
Claude → MCP: create_exercise_card (одноразово, наполнение библиотеки)
Claude → MCP: compose_workout (из карточек библиотеки, с кастомными подходами/повторами)
         ↓
   1. HTML-страница зарядки (все упражнения на одной странице)
   2. Event в Intervals.icu → Garmin sync
```

---

## Архитектура

### Два уровня

| Уровень | Что | Хранение | Пример |
|---|---|---|---|
| **Exercise Card** | Одно упражнение: анимация + техника + метаданные | HTML файл (из Jinja-шаблона) + строка в БД | `clamshell.html` |
| **Workout Page** | Сборка из карточек с кастомными параметрами | Генерируемый HTML | "Утренняя зарядка День Б" |

### Гибридный подход (Вариант C)

Фиксированный Jinja-шаблон для структуры карточки (layout, стили, details, instructions, focus) + уникальная CSS-анимация от Claude для каждого упражнения.

**Что одинаковое (шаблон):** layout карточки, CSS-переменные (цвета, шрифты, размеры), структура секций (header → animation → details → instructions → focus), light theme.

**Что уникальное (от Claude):** `animation_html` (~10-20 строк — HTML-разметка stick figure) + `animation_css` (~30-50 строк — @keyframes и позиционирование). Вместо 300-500 строк полного HTML — 50-80 строк уникального контента.

**Почему не полный HTML (Вариант A):** 80% HTML одинаковое для всех карточек, дублировать 50 раз расточительно, единый стиль ломается.

**Почему не чистый JSON (Вариант B):** каждое упражнение — уникальная поза и движение (stick figure лежит на боку, стоит, на четвереньках). Формализовать это в JSON-параметры — по сути свой animation engine. Overkill.

### Exercise Card — Jinja-шаблон + уникальная анимация

Серверный шаблон `templates/exercise_card.html`:

```html
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ name_ru }} ({{ name_en }})</title>
<style>
  /* === Общие стили (одинаковые для всех карточек) === */
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; ... }
  .card { ... }
  .header { ... }
  .badge { ... }
  .details { ... }
  .instructions { ... }
  .focus { ... }

  /* === Уникальная анимация (от Claude) === */
  {{ animation_css }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <span class="badge">{{ group_tag }}</span>
    {% if equipment != "Без инвентаря" %}
    <span class="badge equip">{{ equipment }}</span>
    {% endif %}
  </div>

  <h1>{{ name_ru }}</h1>
  <p class="subtitle">{{ name_en }} • {{ muscles }}</p>

  <div class="anim-container">
    {% if breath %}
    <div class="breath">
      <div class="breath-dot"></div>
      {{ breath }}
    </div>
    {% endif %}
    {{ animation_html }}
    <div class="floor"></div>
  </div>

  <div class="details">
    <div class="detail-box">
      <div class="detail-value">{{ sets_reps }}</div>
      <div class="detail-label">{{ sets_reps_label }}</div>
    </div>
    <div class="detail-box">
      <div class="detail-value">{{ duration }}</div>
      <div class="detail-label">общее время</div>
    </div>
  </div>

  <div class="instructions">
    <h3>Выполнение</h3>
    {% for step in steps %}
    <div class="step"><span class="step-num">{{ loop.index }}</span> {{ step }}</div>
    {% endfor %}
  </div>

  <div class="focus">
    <strong>Зачем:</strong> {{ focus }}
  </div>
</div>
</body>
</html>
```

### Workout Page — сборная страница

Одна HTML-страница со всеми упражнениями зарядки. Inline-карточки — открыл и листаешь по порядку.

Особенности:
- Нумерация упражнений (1/6, 2/6, ...)
- Общий header с названием, количеством упражнений, временем, инвентарём
- Подходы/повторы **переопределяются** Claude при сборке (карточка содержит дефолт, зарядка — кастом)
- Scroll snap для удобной навигации на телефоне (optional)

---

## Database

### Таблица `exercise_cards`

```sql
CREATE TABLE exercise_cards (
    id                   VARCHAR(50) PRIMARY KEY,   -- "clamshell", "bird-dog", "plank"
    name_ru              VARCHAR(200) NOT NULL,     -- "Ракушка"
    name_en              VARCHAR(200),              -- "Clamshell"
    muscles              VARCHAR(200),              -- "Средняя ягодичная мышца"
    equipment            VARCHAR(100),              -- "Мини-петля" | "Без инвентаря" | "Гантели"
    group_tag            VARCHAR(50),               -- "День А" | "День Б" | "Разминка"
    default_sets         INTEGER DEFAULT 2,
    default_reps         INTEGER DEFAULT 15,
    default_duration_sec INTEGER,                   -- для timed exercises (plank)
    steps                JSONB NOT NULL,            -- ["Ляг на бок...", "Мини-петля чуть выше колен..."]
    focus                TEXT,                      -- "Стабилизация таза при беге..."
    breath               VARCHAR(100),              -- "Выдох при подъёме"
    animation_html       TEXT NOT NULL,             -- HTML-разметка stick figure (~10-20 строк)
    animation_css        TEXT NOT NULL,             -- CSS @keyframes + позиционирование (~30-50 строк)
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);
```

**Нет `html_path`** — путь к файлу строится по convention: `/static/exercises/{id}.html`. HTML генерируется из Jinja-шаблона + данных БД при создании/обновлении.

### Таблица `workout_cards`

```sql
CREATE TABLE workout_cards (
    id                 SERIAL PRIMARY KEY,
    date               VARCHAR(10) NOT NULL,       -- "2026-03-28"
    name               VARCHAR(200) NOT NULL,      -- "Утренняя зарядка — День Б"
    sport              VARCHAR(30) NOT NULL DEFAULT 'Other',  -- "Swim" | "Ride" | "Run" | "Other"
    exercises          JSONB NOT NULL,             -- [{id: "clamshell", sets: 2, reps: 15}, ...]
    total_duration_min INTEGER,
    equipment_summary  VARCHAR(200),               -- "Мини-петля" (comma-separated)
    intervals_id       INTEGER,                    -- ID event в Intervals.icu (optional)
    created_at         TIMESTAMPTZ DEFAULT NOW()
);
```

**`equipment_summary` VARCHAR** вместо `TEXT[]` — проще для SQLAlchemy async.

**`exercises` JSONB** формат:

```json
[
  {"id": "clamshell", "sets": 2, "reps": 15, "duration_sec": null, "note": ""},
  {"id": "plank", "sets": 3, "reps": null, "duration_sec": 45, "note": "Локти под плечами"},
  {"id": "bird-dog", "sets": 2, "reps": 10, "duration_sec": null, "note": "На каждую сторону"}
]
```

**Валидация:** `compose_workout` проверяет все exercise_id перед генерацией. Если ID не найден в библиотеке — возвращает ошибку с перечнем отсутствующих.

---

## MCP Tools

### `create_exercise_card`

Создаёт новое упражнение в библиотеке. Claude передаёт метаданные + уникальную анимацию, сервер рендерит HTML по шаблону.

```python
@mcp.tool()
async def create_exercise_card(
    exercise_id: str,              # "clamshell", "bird-dog"
    name_ru: str,                  # "Ракушка"
    name_en: str,                  # "Clamshell"
    muscles: str,                  # "Средняя ягодичная мышца"
    equipment: str,                # "Мини-петля" | "Без инвентаря"
    group_tag: str,                # "День Б"
    default_sets: int,             # 2
    default_reps: int,             # 15
    steps: list[str],              # ["Ляг на бок...", "Мини-петля чуть выше колен..."]
    focus: str,                    # "Стабилизация таза при беге и на вело..."
    animation_html: str,           # HTML-разметка stick figure (~10-20 строк)
    animation_css: str,            # CSS @keyframes + позиционирование (~30-50 строк)
    breath: str = "",              # "Выдох при подъёме"
    default_duration_sec: int | None = None,
) -> str:
    """Create an exercise card in the library.

    Provide metadata + unique animation (HTML + CSS for stick figure).
    Server renders full HTML from Jinja template with light theme.

    animation_html: HTML markup of the stick figure (~10-20 lines).
    animation_css: CSS @keyframes and positioning (~30-50 lines).
    See clamshell example for reference.
    """
```

Логика:
1. Сохранить метаданные + animation_html/css в `exercise_cards`
2. Рендерить Jinja-шаблон с данными
3. Сохранить HTML в `static/exercises/{exercise_id}.html`
4. Вернуть URL файла

### `update_exercise_card`

```python
@mcp.tool()
async def update_exercise_card(
    exercise_id: str,
    name_ru: str | None = None,
    steps: list[str] | None = None,
    animation_html: str | None = None,
    animation_css: str | None = None,
    # ... остальные поля optional
) -> str:
    """Update an existing exercise card.

    Only provided fields are updated. HTML file is re-rendered.
    """
```

### `list_exercise_cards`

```python
@mcp.tool()
async def list_exercise_cards(
    equipment: str | None = None,
    group_tag: str | None = None,
    muscles: str | None = None,
) -> str:
    """List available exercise cards in the library.

    Returns exercise metadata (id, name, muscles, equipment, default reps).
    Use this to compose workouts from existing exercises.
    """
```

### `compose_workout`

Собирает зарядку из упражнений библиотеки. Генерирует сборную HTML-страницу.

```python
@mcp.tool()
async def compose_workout(
    name: str,                       # "Утренняя зарядка — День Б"
    exercises: list[dict],           # [{"id": "clamshell", "sets": 2, "reps": 15}, ...]
    target_date: str | None = None,  # "2026-03-28"
    push_to_intervals: bool = False, # создать event в Intervals.icu
    sport: str = "Other",            # "Swim" | "Ride" | "Run" | "Other"
) -> str:
    """Compose a workout from exercise library cards.

    Each exercise entry: {"id": "exercise_id", "sets": N, "reps": N}
    or {"id": "exercise_id", "sets": N, "duration_sec": N} for timed exercises.

    Validates all exercise IDs before generation.
    Generates a single HTML page with all exercise cards inline.
    Returns URL to the workout page.

    If push_to_intervals=True, also creates a WORKOUT event in Intervals.icu.
    Sport type determines how the event appears in Intervals.icu:
    "Swim" for swim drills, "Ride"/"Run" for sport-specific, "Other" for зарядки.
    """
```

### `remove_workout_card`

Удаляет зарядку из БД и из Intervals.icu (если была запушена).

```python
@mcp.tool()
async def remove_workout_card(
    card_id: int,                    # ID из list_workout_cards
) -> str:
    """Remove a composed workout (зарядка) by its ID.

    Deletes from local DB and from Intervals.icu calendar (if it was pushed there).
    Use list_workout_cards to find the card_id.
    """
```

### `compose_workout` — логика

1. **Валидация:** проверить все exercise_id существуют в библиотеке, вернуть ошибку если нет
2. Загрузить данные каждого упражнения из БД
3. Рендерить каждую карточку через Jinja-шаблон с кастомными reps/sets
4. Обернуть в workout page template (header + нумерация + scroll)
5. Сохранить в `static/workouts/{date}-{slug}.html`
6. Если `push_to_intervals` — создать event в Intervals.icu (category=WORKOUT, type=sport)
7. Сохранить запись в `workout_cards`
8. Вернуть URL страницы

---

## Файловая структура и serving

```
static/                          # Docker volume для персистентности
├── exercises/                   # Библиотека упражнений (рендерятся из шаблона)
│   ├── clamshell.html
│   ├── bird-dog.html
│   ├── plank.html
│   └── ...
└── workouts/                    # Собранные зарядки (генерируются)
    ├── 2026-03-28-warmup-b.html
    └── ...

templates/                       # Jinja-шаблоны (в Docker image, не volume)
├── exercise_card.html           # Шаблон одной карточки
└── workout_page.html            # Шаблон сборной страницы
```

### Static file serving — FastAPI StaticFiles

Без nginx. `static/` в корне проекта + `StaticFiles` mount в `api/server.py`:

```python
from starlette.staticfiles import StaticFiles

app.mount("/static", StaticFiles(directory="static"), name="static")
```

### Docker volume

```yaml
# docker-compose.yml
services:
  api:
    volumes:
      - static_data:/app/static    # персистентный volume для карточек и зарядок

volumes:
  static_data:
```

Файлы в `static/` сохраняются между перезапусками контейнера. Данные в БД (exercise_cards, workout_cards) служат source of truth — HTML можно перегенерировать из шаблона + данных БД.

---

## Поток использования

### Наполнение библиотеки (одноразово)

```
Атлет: "Создай карточку упражнения Bird-Dog"
Claude: → create_exercise_card(
    exercise_id="bird-dog",
    name_ru="Птица-собака",
    name_en="Bird-Dog",
    muscles="Кор, стабилизаторы",
    equipment="Без инвентаря",
    group_tag="День А",
    default_sets=2,
    default_reps=10,
    steps=["Встань на четвереньки...", "Вытяни правую руку и левую ногу...", ...],
    focus="Стабильность кора — основа передачи усилий между верхней и нижней частью тела...",
    breath="Выдох при вытягивании",
    animation_html="<div class='figure'>...stick figure on all fours...</div>",
    animation_css=".right-arm { animation: extend 2s... } @keyframes extend { ... }",
)
→ Сервер: рендерит шаблон → сохраняет /static/exercises/bird-dog.html
```

### Составление зарядки (регулярно)

```
Атлет: "Составь мне зарядку на сегодня"
Claude: → list_exercise_cards()  # смотрит что есть в библиотеке
       → compose_workout(
           name="Утренняя зарядка — День Б",
           exercises=[
               {"id": "clamshell", "sets": 2, "reps": 15},
               {"id": "bird-dog", "sets": 2, "reps": 10},
               {"id": "plank", "sets": 3, "duration_sec": 45},
               {"id": "monster-walk", "sets": 2, "reps": 20},
           ],
           target_date="2026-03-28",
           push_to_intervals=True,
       )
→ HTML: https://app.example.com/static/workouts/2026-03-28-warmup-b.html
→ Intervals.icu: event создан → Garmin sync
```

### Автоматическая зарядка (будущее)

Утренний cron или Claude по запросу:
1. Проверяет какой сегодня день (А/Б/С по ротации)
2. Учитывает recovery — если low, легче набор
3. Подбирает упражнения из библиотеки
4. Корректирует подходы/повторы под состояние
5. `compose_workout()` → ссылка в Telegram

---

## Дизайн-решения

### Карточка упражнения

- **Light theme** — согласована с webapp (Inter font, светлый фон)
- **Stick figure animation** — CSS only, `@keyframes`, цвета: `#60a5fa` тело, `#34d399` движущаяся часть, `#fbbf24` pivot point
- **Mobile-first** — max-width 380px, touch-friendly
- **Рендерится из шаблона** — единый стиль для всех карточек, уникальная только анимация
- **Breathing indicator** — пульсирующая точка + текст ("Выдох при подъёме")
- **Zero JS dependencies** — только HTML + CSS

### Workout page

- Vertical scroll с карточками
- Header: название, кол-во упражнений, время, инвентарь
- Нумерация: "1 / 6", "2 / 6"
- Разделитель между карточками
- CSS scroll-snap (optional) для snap-to-exercise

---

## Пример MCP-вызова: clamshell

Для справки — как clamshell-exercise.html разделяется на шаблон + уникальные данные:

```python
create_exercise_card(
    exercise_id="clamshell",
    name_ru="Ракушка",
    name_en="Clamshell",
    muscles="Средняя ягодичная мышца",
    equipment="Мини-петля",
    group_tag="День Б",
    default_sets=2,
    default_reps=15,
    steps=[
        "Ляг на бок, колени согнуты 90°, стопы вместе",
        "Мини-петля чуть выше колен",
        "Раскрывай верхнее колено вверх, стопы не разъединяй",
        "Медленно вернись. Таз неподвижен!",
    ],
    focus="Стабилизация таза при беге и на вело. Слабая средняя ягодичная = колени заваливаются внутрь, теряется мощность, растёт риск травмы.",
    breath="Выдох при подъёме",
    animation_html="""<div class="figure">
  <div class="head"></div>
  <div class="torso"></div>
  <div class="arm"></div>
  <div class="lower-leg-upper"></div>
  <div class="lower-leg-lower"></div>
  <div class="upper-leg-upper"></div>
  <div class="upper-leg-lower"></div>
  <div class="knee"></div>
  <div class="band"></div>
</div>""",
    animation_css=""".figure { position: absolute; bottom: 42px; left: 50%; transform: translateX(-50%); }
.head { width: 28px; height: 28px; border: 3px solid #60a5fa; border-radius: 50%; position: absolute; left: -80px; bottom: 42px; }
.torso { width: 70px; height: 3px; background: #60a5fa; position: absolute; left: -55px; bottom: 55px; transform: rotate(-5deg); }
.arm { width: 35px; height: 3px; background: #60a5fa; position: absolute; left: -30px; bottom: 68px; transform: rotate(-30deg); transform-origin: left center; }
.lower-leg-upper { width: 45px; height: 3px; background: #60a5fa; position: absolute; left: 15px; bottom: 45px; transform: rotate(40deg); transform-origin: left center; }
.lower-leg-lower { width: 40px; height: 3px; background: #60a5fa; position: absolute; left: 47px; bottom: 22px; transform: rotate(-20deg); transform-origin: left center; }
.upper-leg-upper { width: 45px; height: 3px; background: #34d399; position: absolute; left: 15px; bottom: 50px; transform: rotate(40deg); transform-origin: left center; animation: clamshell-thigh 2s ease-in-out infinite; }
.upper-leg-lower { width: 40px; height: 3px; background: #34d399; position: absolute; left: 47px; bottom: 27px; transform: rotate(-20deg); transform-origin: left center; animation: clamshell-shin 2s ease-in-out infinite; }
.knee { width: 8px; height: 8px; background: #fbbf24; border-radius: 50%; position: absolute; left: 46px; bottom: 26px; animation: clamshell-knee 2s ease-in-out infinite; z-index: 2; }
.band { position: absolute; left: 38px; bottom: 24px; width: 20px; height: 12px; border: 2px solid #f472b6; border-radius: 4px; animation: clamshell-band 2s ease-in-out infinite; z-index: 1; }
@keyframes clamshell-thigh { 0%, 100% { transform: rotate(40deg); } 50% { transform: rotate(-10deg); } }
@keyframes clamshell-shin { 0%, 100% { transform: rotate(-20deg); left: 47px; bottom: 27px; } 50% { transform: rotate(-20deg); left: 30px; bottom: 72px; } }
@keyframes clamshell-knee { 0%, 100% { left: 46px; bottom: 26px; } 50% { left: 30px; bottom: 68px; } }
@keyframes clamshell-band { 0%, 100% { height: 12px; left: 38px; bottom: 24px; } 50% { height: 30px; left: 33px; bottom: 30px; } }""",
)
```

`animation_css` — ~50 строк. Компактно для MCP-параметра, при этом полная свобода анимации.

---

## План реализации

| # | Задача | Файлы |
|---|---|---|
| 1 | Таблицы `exercise_cards` + `workout_cards` + Alembic | `data/database.py`, миграция |
| 2 | CRUD для exercise_cards и workout_cards | `data/database.py` |
| 3 | Jinja-шаблоны (exercise_card.html, workout_page.html) | `templates/` |
| 4 | MCP tool: `create_exercise_card` (рендер шаблона + сохранение) | `mcp_server/tools/workout_cards.py` |
| 5 | MCP tool: `update_exercise_card` | `mcp_server/tools/workout_cards.py` |
| 6 | MCP tool: `list_exercise_cards` | `mcp_server/tools/workout_cards.py` |
| 7 | MCP tool: `compose_workout` + HTML generation + валидация | `mcp_server/tools/workout_cards.py` |
| 8 | Static file serving (StaticFiles mount) | `api/server.py` |
| 9 | Docker volume для static/ | `docker-compose.yml` |
| 10 | Наполнение библиотеки (10-15 базовых упражнений) | через MCP |
| 11 | Интеграция с Intervals.icu (push_to_intervals) | `data/intervals_client.py` |

### Критерии готовности

- [x] `create_exercise_card` рендерит HTML из шаблона + сохраняет метаданные в БД
- [x] `update_exercise_card` обновляет поля + перегенерирует HTML
- [x] `list_exercise_cards` возвращает библиотеку с фильтрами
- [x] `compose_workout` валидирует exercise_id + генерирует сборную HTML-страницу с кастомными reps/sets
- [x] HTML-страницы доступны по URL и корректно отображаются на мобильном
- [x] Static файлы переживают перезапуск контейнера (Docker volume)
- [x] Push to Intervals.icu работает (с workout_doc steps для Garmin sync, sport type: Swim/Ride/Run/Other)
- [x] `remove_workout_card` удаляет из БД + из Intervals.icu
- [x] Минимум 10 упражнений в библиотеке
- [x] MCP tool `list_workout_cards` — просмотр созданных зарядок (аналог `list_ai_workouts` для `workout_cards` таблицы)
- [x] DB функция `get_workout_cards()` в database.py — запрос за последние N дней
