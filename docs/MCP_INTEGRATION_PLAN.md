# MCP Integration Plan — Triathlon AI Agent

> Дорожная карта внедрения Model Context Protocol в сервис.

---

## Зачем MCP

Сейчас Claude вызывается один раз в день как «чёрный ящик» — получает фиксированный текстовый промпт, возвращает текст. С MCP Claude получает **инструменты** и сам решает, какие данные запросить. Это открывает:

- Свободные вопросы через Telegram: «как мой HRV за последнюю неделю?», «сравни нагрузку по видам спорта за март»
- Claude сам выбирает, какие данные нужны для ответа, а не получает фиксированный набор
- Утренняя рекомендация становится одним из сценариев, а не единственным

---

## Фаза 1 — MCP-сервер поверх существующих данных

### Цель

Создать MCP-сервер, экспонирующий данные спортсмена как tools. Подключается к Claude Desktop для ad-hoc вопросов. Существующий пайплайн (scheduler → AI recommendation → Telegram) не затрагивается.

### Технология

- Библиотека: `mcp[cli]` (Python SDK, pip-пакет)
- Транспорт: `stdio` (для Claude Desktop) + `streamable-http` (для будущей интеграции)
- Сервер: `FastMCP` — декларативный API

### Структура файлов

```
triathlon-agent/
├── mcp_server/
│   ├── __init__.py
│   ├── server.py              # FastMCP app, entry point
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── wellness.py        # get_wellness, get_wellness_range
│   │   ├── hrv.py             # get_hrv_analysis
│   │   ├── rhr.py             # get_rhr_analysis
│   │   ├── training_load.py   # get_training_load, get_sport_ctl
│   │   ├── recovery.py        # get_recovery
│   │   └── goal.py            # get_goal_progress
│   └── resources/
│       └── athlete_profile.py # read-only resource: thresholds, zones, goal config
```

### Tools — полный список

| Tool | Параметры | Возвращает | Источник данных |
|---|---|---|---|
| `get_wellness` | `date: str` | Все поля wellness за день | `data/database.py → get_wellness()` |
| `get_wellness_range` | `from_date: str, to_date: str` | Список wellness за диапазон | DB query с фильтром |
| `get_hrv_analysis` | `date: str, algorithm?: str` | HRV статус, baseline, bounds, SWC, CV, trend | `get_hrv_analysis()` |
| `get_rhr_analysis` | `date: str` | RHR статус, 7d/30d/60d baseline, trend | `get_rhr_analysis()` |
| `get_training_load` | `date: str` | CTL, ATL, TSB, ramp_rate + per-sport CTL | wellness row + sport_info parse |
| `get_recovery` | `date: str` | Recovery score, category, recommendation, flags | wellness row |
| `get_goal_progress` | — | Event name, weeks remaining, overall + per-sport % | Calculated from settings + current CTL |

### Resources

| Resource | URI | Описание |
|---|---|---|
| `athlete_profile` | `athlete://profile` | Возраст, пороги (LTHR, FTP, CSS), зоны, max HR |
| `race_goal` | `athlete://goal` | Целевая гонка, дата, CTL targets |
| `thresholds` | `athlete://thresholds` | TSB bounds, ramp rate limits, HRV rules — всё что в Business Rules |

### Пример реализации (server.py)

```python
from mcp.server.fastmcp import FastMCP
from data.database import get_wellness, get_hrv_analysis, get_rhr_analysis
from config import settings

mcp = FastMCP(
    "Triathlon Agent",
    description="Personal triathlon training data and analysis",
)

@mcp.tool()
async def get_training_load(date: str) -> dict:
    """Get CTL/ATL/TSB and per-sport CTL for a given date.

    All values come from Intervals.icu (impulse-response model, τ_CTL=42d, τ_ATL=7d).
    Thresholds are calibrated for Intervals.icu, NOT TrainingPeaks.

    Args:
        date: Date in YYYY-MM-DD format
    """
    row = await get_wellness(date)
    if not row:
        return {"error": f"No data for {date}"}

    tsb = round(row.ctl - row.atl, 1) if row.ctl and row.atl else None
    sport_ctl = _extract_sport_ctl(row.sport_info)

    return {
        "date": date,
        "ctl": row.ctl,
        "atl": row.atl,
        "tsb": tsb,
        "ramp_rate": row.ramp_rate,
        "sport_ctl": sport_ctl,
        "interpretation": {
            "tsb_zone": _tsb_zone(tsb),
            "ramp_safe": row.ramp_rate <= 7 if row.ramp_rate else None,
        },
    }

@mcp.tool()
async def get_recovery(date: str) -> dict:
    """Get composite recovery score and training recommendation.

    Recovery score (0-100) combines: RMSSD 35%, Banister 25%, RHR 20%, Sleep 20%.
    Categories: excellent >85, good 70-85, moderate 40-70, low <40.

    Args:
        date: Date in YYYY-MM-DD format
    """
    row = await get_wellness(date)
    if not row:
        return {"error": f"No data for {date}"}

    return {
        "date": date,
        "score": row.recovery_score,
        "category": row.recovery_category,
        "recommendation": row.recovery_recommendation,
        "readiness_score": row.readiness_score,
        "readiness_level": row.readiness_level,
    }

@mcp.resource("athlete://profile")
def get_athlete_profile() -> str:
    """Static athlete profile: age, thresholds, zones."""
    return f"""
Age: {settings.ATHLETE_AGE}
LTHR Run: {settings.ATHLETE_LTHR_RUN}
LTHR Bike: {settings.ATHLETE_LTHR_BIKE}
Max HR: {settings.ATHLETE_MAX_HR}
Resting HR: {settings.ATHLETE_RESTING_HR}
FTP: {settings.ATHLETE_FTP}W
CSS: {settings.ATHLETE_CSS}s/100m
"""
```

### Claude Desktop конфигурация

```json
{
  "mcpServers": {
    "triathlon": {
      "command": "poetry",
      "args": ["run", "python", "-m", "mcp_server.server"],
      "cwd": "/path/to/triathlon-agent",
      "env": {
        "DATABASE_URL": "postgresql+asyncpg://...",
        "INTERVALS_API_KEY": "..."
      }
    }
  }
}
```

### Задачи фазы 1

1. Добавить `mcp[cli]` в `pyproject.toml`
2. Создать `mcp_server/server.py` с FastMCP
3. Реализовать 7 tools (wellness, hrv, rhr, training_load, recovery, goal, wellness_range)
4. Реализовать 3 resources (profile, goal, thresholds)
5. Добавить docstrings с контекстом Intervals.icu (чтобы Claude знал про калибровку)
6. Протестировать через `mcp dev mcp_server/server.py`
7. Подключить к Claude Desktop, проверить ad-hoc вопросы

### Что НЕ меняется в фазе 1

- Утренний пайплайн (scheduler → metrics → AI → Telegram) работает как раньше
- `ai/claude_agent.py` и `ai/prompts.py` без изменений
- `api/routes.py` без изменений
- MCP-сервер — параллельный канал доступа к тем же данным

---

## Фаза 2 — Claude через MCP вместо прямого API

### Цель

Заменить фиксированный `MORNING_REPORT_PROMPT` на MCP tool-use. Claude сам решает, какие данные запрашивать для утреннего отчёта.

### Что меняется

- `ai/claude_agent.py` — вместо `messages.create()` с текстовым промптом → MCP client session с tools
- `ai/prompts.py` — `MORNING_REPORT_PROMPT` удаляется. `SYSTEM_PROMPT` остаётся (персона + правила)
- Claude получает system prompt + инструкцию «сгенерируй утренний отчёт» + доступ к tools

### Пример flow

```
System: [SYSTEM_PROMPT — persona, rules, Intervals.icu context]
User: "Сгенерируй утренний отчёт за 2026-03-23"

Claude → tool_use: get_recovery("2026-03-23")
Claude → tool_use: get_training_load("2026-03-23")
Claude → tool_use: get_hrv_analysis("2026-03-23")
Claude → tool_use: get_goal_progress()

Claude: [4-секционный ответ с рекомендациями]
```

### Плюсы

- Гибкость: Claude может запросить доп. данные (например wellness_range за 7 дней для тренда)
- Не нужно обновлять промпт при добавлении новых метрик — достаточно добавить tool
- Единая точка правды: tools используются и для ad-hoc вопросов, и для отчёта

### Минусы

- Стоимость: 3-5 tool calls вместо 1 API call = ~2-3x дороже
- Латентность: каждый tool call — round-trip, отчёт генерируется 5-10 сек вместо 2-3
- Менее предсказуемо: Claude может «забыть» запросить важную метрику

### Задачи фазы 2

1. Реализовать MCP client в `ai/claude_agent.py` (подключение к локальному MCP-серверу)
2. Переписать `get_morning_recommendation()` на tool-use flow
3. Добавить fallback — если tool call не сработал, вернуть «AI unavailable»
4. Сравнить качество рекомендаций: промпт vs tool-use (A/B на 2 неделях)
5. Решить, оставить ли фиксированный промпт как fallback

---

## Фаза 3 — Свободный диалог через Telegram

### Цель

Пользователь пишет в Telegram любой вопрос — бот пробрасывает в Claude + MCP tools. Claude сам решает, что нужно, дёргает инструменты, отвечает.

### Что меняется

- `bot/main.py` — новый handler для произвольных сообщений (не только команды)
- Добавить контекст разговора (история сообщений в рамках сессии)
- Rate limiting — ограничить количество AI-вызовов в день

### Примеры вопросов

```
"Как мой HRV за последнюю неделю?"
→ Claude вызывает get_wellness_range(7 дней назад, сегодня), анализирует тренд

"Сравни нагрузку по видам спорта за март"
→ Claude вызывает get_wellness_range(2026-03-01, 2026-03-23), агрегирует sport_ctl

"Я чувствую усталость, стоит ли тренироваться?"
→ Claude вызывает get_recovery(today) + get_training_load(today), даёт рекомендацию

"Когда лучше сделать ключевую тренировку на этой неделе?"
→ Claude вызывает get_wellness_range за последние 3 дня, оценивает тренд TSB
```

### Задачи фазы 3

1. Универсальный message handler в `bot/main.py`
2. Session management — хранить историю диалога (in-memory или Redis)
3. Rate limiting — max N AI-вызовов в день через config
4. Добавить команду `/ask <вопрос>` как альтернативу свободному тексту
5. Telegram typing indicator во время tool calls

---

## Расширение MCP при загрузке активностей

Когда будет реализована синхронизация активностей из Intervals.icu (ESS/Banister pipeline), MCP-сервер расширяется новыми tools. Существующие tools не меняются — только добавляются новые.

### Новые tools

| Tool | Параметры | Описание |
|---|---|---|
| `get_activities` | `date: str` | Список тренировок за день: спорт, длительность, TSS, зоны, HR/power |
| `get_activities_range` | `from_date: str, to_date: str` | Тренировки за диапазон — для анализа объёма и баланса |
| `get_activity_detail` | `activity_id: int` | Детали одной тренировки: splits, HR distribution, power curve |
| `get_weekly_summary` | `week_start: str` | Агрегат: часы по видам, суммарный TSS, распределение зон |
| `get_ess_history` | `from_date: str, to_date: str` | ESS (External Stress Score) по дням — вход для Banister модели |

### Новые файлы

```
mcp_server/
├── tools/
│   ├── activities.py      # get_activities, get_activities_range, get_activity_detail
│   ├── weekly.py          # get_weekly_summary
│   └── ess.py             # get_ess_history
```

### Какие вопросы это открывает

```
"Сколько я проплыл на этой неделе?"
→ get_weekly_summary(this_week) → swim hours + distance

"Покажи распределение зон за март"
→ get_activities_range(2026-03-01, 2026-03-31) → aggregate zone distribution

"Какая тренировка дала больше всего нагрузки?"
→ get_activities_range(last 7 days) → sort by TSS → top 1

"Как мой бег по пульсу в последних 5 тренировках?"
→ get_activities_range + filter sport=run → HR trends

"Какой средний TSS на этой неделе vs прошлой?"
→ get_weekly_summary(this_week) + get_weekly_summary(prev_week) → compare
```

### Влияние на утренний отчёт (Phase 2)

Когда активности доступны, Claude в tool-use режиме может дополнительно запросить:
- `get_activities(yesterday)` — чтобы учесть вчерашнюю тренировку при рекомендации
- `get_weekly_summary(this_week)` — чтобы оценить недельный объём и предложить балансировку
- `get_ess_history(last 7 days)` — чтобы учесть накопленный стресс в Banister модели

Это делает утренний отчёт значительно информативнее без изменения system prompt.

### Зависимости

Эти tools зависят от:
1. Синхронизации активностей из Intervals.icu API (endpoint: `GET /api/v1/athlete/{id}/activities`)
2. Таблицы `activities` в PostgreSQL (новая миграция)
3. ESS калькулятора в `data/metrics.py`

Порядок: сначала пайплайн активностей → потом MCP tools.

---

## Риски и митигации

| Риск | Вероятность | Митигация |
|---|---|---|
| Рост стоимости API | Высокая | Rate limiting, кэширование ответов, haiku для простых вопросов |
| Латентность tool calls | Средняя | Параллельные tool calls, кэш данных за сегодня |
| Claude «забывает» запросить метрику | Средняя | Чёткие docstrings в tools, fallback на промпт |
| MCP SDK breaking changes | Низкая | Зафиксировать версию в pyproject.toml |
| Сложность debugging | Средняя | Логирование всех tool calls, MCP Inspector |

---

## Приоритет

```
Фаза 1 (MCP-сервер)     → сейчас, 2-3 дня работы
Фаза 2 (tool-use отчёт) → после 2 недель использования фазы 1
Фаза 3 (свободный диалог) → когда будет ясно, что нужен
```
