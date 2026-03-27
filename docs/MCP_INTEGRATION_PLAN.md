# MCP Integration Plan — Triathlon AI Agent

> Дорожная карта внедрения Model Context Protocol в сервис.

---

## Зачем MCP

Сейчас Claude вызывается один раз в день как «чёрный ящик» — получает фиксированный текстовый промпт, возвращает текст. С MCP Claude получает **инструменты** и сам решает, какие данные запросить. Это открывает:

- Свободные вопросы через Telegram: «как мой HRV за последнюю неделю?», «сравни нагрузку по видам спорта за март»
- Claude сам выбирает, какие данные нужны для ответа, а не получает фиксированный набор
- Утренняя рекомендация становится одним из сценариев, а не единственным

---

## Фаза 1 — MCP-сервер поверх существующих данных ✅ ЗАВЕРШЕНА

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
│   ├── __main__.py              # python -m mcp_server
│   ├── app.py                   # FastMCP instance
│   ├── server.py                # imports all tools + resources
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── wellness.py          # get_wellness, get_wellness_range
│   │   ├── activities.py        # get_activities (with has_hrv_analysis flag)
│   │   ├── hrv.py               # get_hrv_analysis
│   │   ├── rhr.py               # get_rhr_analysis
│   │   ├── training_load.py     # get_training_load
│   │   ├── recovery.py          # get_recovery
│   │   ├── goal.py              # get_goal_progress
│   │   ├── scheduled_workouts.py # get_scheduled_workouts
│   │   └── activity_hrv.py      # get_activity_hrv, get_thresholds_history, get_readiness_history
│   └── resources/
│       └── athlete_profile.py   # read-only: thresholds, zones, goal config
```

### Tools — полный список (12)

| Tool | Параметры | Возвращает | Источник данных |
|---|---|---|---|
| `get_wellness` | `date: str` | Все поля wellness за день | `data/database.py → get_wellness()` |
| `get_wellness_range` | `from_date: str, to_date: str` | Список wellness за диапазон | DB query с фильтром |
| `get_activities` | `target_date?: str, days_back?: int` | Активности с TSS, duration, has_hrv_analysis | `activities` + LEFT JOIN `activity_hrv` |
| `get_hrv_analysis` | `date: str, algorithm?: str` | HRV статус, baseline, bounds, SWC, CV, trend | `hrv_analysis` таблица (оба алгоритма) |
| `get_rhr_analysis` | `date: str` | RHR статус, 7d/30d/60d baseline, trend | `rhr_analysis` таблица |
| `get_training_load` | `date: str` | CTL, ATL, TSB, ramp_rate + per-sport CTL | wellness row + `extract_sport_ctl()` из `data/utils.py` |
| `get_recovery` | `date: str` | Recovery score, category, recommendation, flags | wellness row |
| `get_goal_progress` | — | Event name, weeks remaining, overall + per-sport % | Calculated from settings + current CTL via `extract_sport_ctl()` |
| `get_scheduled_workouts` | `target_date?: str, days_ahead?: int` | Planned workouts with full description | `scheduled_workouts` таблица |
| `get_activity_hrv` | `activity_id: str` | DFA a1, quality, thresholds, Ra, Da | `activity_hrv` таблица |
| `get_thresholds_history` | `sport?: str, days_back?: int` | HRVT1/HRVT2 trend over time | `activity_hrv` WHERE hrvt1_hr IS NOT NULL |
| `get_readiness_history` | `sport?: str, days_back?: int` | Ra trend — warmup power/pace vs baseline | `activity_hrv` WHERE ra_pct IS NOT NULL |

### Resources (3)

| Resource | URI | Описание |
|---|---|---|
| `athlete_profile` | `athlete://profile` | Возраст, пороги (LTHR, FTP, CSS), зоны, max HR |
| `race_goal` | `athlete://goal` | Целевая гонка, дата, CTL targets |
| `thresholds` | `athlete://thresholds` | TSB bounds, ramp rate limits, HRV/RHR interpretation |

### Per-sport CTL в MCP tools

Per-sport CTL рассчитывается из `wellness.sport_info` JSON через утилиту `data/utils.py`:

```python
from data.utils import extract_sport_ctl

# Возвращает {"swim": 12.3, "bike": 28.5, "run": 18.7} или None для отсутствующих
sport_ctl = extract_sport_ctl(row.sport_info)
```

`sport_info` обогащается в `daily_metrics_job` (bot/scheduler.py): рассчитанный per-sport CTL (EMA τ=42d из таблицы `activities`) мержится с оригинальными данными Intervals.icu (eftp, wPrime, pMax).

### Claude Desktop конфигурация

```json
{
  "mcpServers": {
    "triathlon": {
      "command": "poetry",
      "args": ["run", "python", "-m", "mcp_server"],
      "cwd": "/path/to/triathlon-agent",
      "env": {
        "DATABASE_URL": "postgresql+asyncpg://...",
        "INTERVALS_API_KEY": "..."
      }
    }
  }
}
```

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
Claude → tool_use: get_scheduled_workouts("2026-03-23")
Claude → tool_use: get_goal_progress()

Claude: [4-секционный ответ с рекомендациями]
```

### Плюсы / Минусы

| Плюсы | Минусы |
|---|---|
| Claude может запросить доп. данные (wellness_range за 7 дней для тренда) | 3-5 tool calls = ~2-3x дороже |
| Не нужно обновлять промпт при новых метриках | Латентность 5-10 сек вместо 2-3 |
| Единая точка правды: tools для ad-hoc и отчёта | Менее предсказуемо |

### Задачи фазы 2

1. Реализовать MCP client в `ai/claude_agent.py`
2. Переписать `get_morning_recommendation()` на tool-use flow
3. Добавить fallback при ошибке tool call
4. A/B сравнение: промпт vs tool-use (2 недели)
5. Решить, оставить ли фиксированный промпт как fallback

---

## Фаза 3 — Свободный диалог через Telegram

### Цель

Пользователь пишет в Telegram любой вопрос — бот пробрасывает в Claude + MCP tools. Claude сам решает, что нужно, дёргает инструменты, отвечает.

### Примеры вопросов

```
"Как мой HRV за последнюю неделю?"
→ Claude вызывает get_wellness_range(7 дней назад, сегодня), анализирует тренд

"Сравни нагрузку по видам спорта за март"
→ Claude вызывает get_wellness_range(2026-03-01, 2026-03-31), агрегирует sport_ctl

"Я чувствую усталость, стоит ли тренироваться?"
→ Claude вызывает get_recovery(today) + get_training_load(today)

"Когда лучше сделать ключевую тренировку на этой неделе?"
→ Claude вызывает get_wellness_range + get_scheduled_workouts(days_ahead=7)
```

### Задачи фазы 3

1. Универсальный message handler в `bot/main.py`
2. Session management — история диалога (in-memory или Redis)
3. Rate limiting — max N AI-вызовов в день через config
4. Команда `/ask <вопрос>` как альтернатива свободному тексту
5. Telegram typing indicator во время tool calls

---

## Расширение MCP при загрузке активностей ✅

Activities синхронизируются в таблицу `activities` (cron `sync_activities_job` каждый час :30). Данные: id, date, type, training_load, moving_time, average_hr. ESS pipeline реализован.

### Реализованные tools

| Tool | Описание | Статус |
|---|---|---|
| `get_activities` | Список тренировок за день/диапазон + has_hrv_analysis | ✅ |
| `get_activity_hrv` | DFA a1, thresholds, Ra, Da для конкретной активности | ✅ |
| `get_thresholds_history` | HRVT1/HRVT2 тренд за N дней | ✅ |
| `get_readiness_history` | Ra тренд за N дней | ✅ |

### Возможные будущие tools

| Tool | Описание |
|---|---|
| `get_activity_detail` | Детали одной тренировки (splits, HR, power) |
| `get_weekly_summary` | Часы по видам, суммарный TSS, распределение зон |
| `get_ess_history` | ESS по дням (вход для Banister) |

---

## Риски и митигации

| Риск | Вероятность | Митигация |
|---|---|---|
| Рост стоимости API | Высокая | Rate limiting, кэширование, haiku для простых вопросов |
| Латентность tool calls | Средняя | Параллельные tool calls, кэш данных за сегодня |
| Claude «забывает» метрику | Средняя | Чёткие docstrings, fallback на промпт |
| MCP SDK breaking changes | Низкая | Зафиксировать версию в pyproject.toml |

---

## Приоритет

```
Фаза 1 (MCP-сервер)         → ✅ ЗАВЕРШЕНА
Фаза 2 (tool-use отчёт)     → после 2 недель использования фазы 1
Фаза 3 (свободный диалог)   → когда будет ясно, что нужен
```
