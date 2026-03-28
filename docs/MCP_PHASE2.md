# MCP Phase 2 — Tool-Use для утреннего анализа

> Замена фиксированного промпта на Claude tool-use. Claude сам решает какие данные запросить.

---

## Текущая архитектура (до Phase 2)

```
daily_metrics_job (scheduler)
    ↓
save_wellness (DB)
    ↓  ai_is_new = True
get_morning_recommendation (claude_agent.py)
    ↓
build_morning_prompt()
  → вручную собирает: wellness, HRV (flatt + aie), RHR, TSB, sport CTL,
    scheduled workouts, yesterday DFA
  → форматирует ~40 переменных в MORNING_REPORT_PROMPT шаблон
    ↓
Claude messages.create(system=SYSTEM_PROMPT, messages=[{user: prompt}])
    ↓
Текстовая рекомендация → wellness.ai_recommendation → Telegram
```

**Проблемы:**
- `build_morning_prompt()` — 100 строк хардкоженного сбора данных
- При добавлении нового tool (training_log, threshold_freshness, mood) — нужно менять код
- Claude не может запросить доп. данные если что-то подозрительное
- Промпт раздувается с каждой новой метрикой

---

## Новая архитектура (Phase 2)

```
daily_metrics_job (scheduler)
    ↓
save_wellness (DB)
    ↓  ai_is_new = True
get_morning_recommendation_v2 (claude_agent.py)
    ↓
Claude messages.create(
    system = SYSTEM_PROMPT_V2,
    messages = [{user: "Сгенерируй утренний отчёт за {date}"}],
    tools = MORNING_TOOLS,          ← определения tools
    max_tokens = 4096,              ← больше из-за tool-use overhead
)
    ↓
Claude решает какие tools вызвать
    ↓  tool_use blocks
Код выполняет tool calls → возвращает результаты
    ↓  tool_result blocks
Claude синтезирует ответ
    ↓
Текстовая рекомендация → wellness.ai_recommendation → Telegram
```

---

## Как работает Anthropic Tool-Use API

Не через MCP-сервер. Используем Anthropic Python SDK напрямую — `tools` параметр в `messages.create()`.

```python
response = await client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=4096,
    system=system_prompt,
    messages=messages,
    tools=tool_definitions,  # список dict с name, description, input_schema
)
```

Claude может вернуть `tool_use` блоки вместо текста. Код выполняет вызовы, добавляет `tool_result` в messages, и повторяет запрос. Цикл продолжается пока Claude не вернёт текстовый ответ.

```python
# Цикл tool-use
while response.stop_reason == "tool_use":
    tool_results = []
    for block in response.content:
        if block.type == "tool_use":
            result = await execute_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

    # response.content — list of ContentBlock objects; SDK принимает их as-is
    messages.append({"role": "assistant", "content": response.content})
    messages.append({"role": "user", "content": tool_results})

    response = await client.messages.create(
        model=model, max_tokens=4096,
        system=system_prompt, messages=messages, tools=tool_definitions,
    )
```

---

## Tool Definitions

Не все 28 MCP tools нужны для утреннего анализа. Определяем минимальный набор + опциональные.

### Основные tools (всегда доступны)

| Tool | Описание | Маппинг на существующий код |
|---|---|---|
| `get_wellness` | Wellness за день: recovery, sleep, HRV, CTL/ATL | `data/database.py → get_wellness()` |
| `get_hrv_analysis` | HRV статус + baselines (оба алгоритма) | `data/database.py → get_hrv_analysis()` |
| `get_rhr_analysis` | RHR статус + baselines | `data/database.py → get_rhr_analysis()` |
| `get_recovery` | Recovery score + category + recommendation | `data/metrics.py → compute_recovery()` (вынести из `mcp_server/tools/recovery.py`) |
| `get_training_load` | CTL/ATL/TSB/ramp_rate + per-sport CTL | `data/metrics.py → compute_training_load()` (вынести из `mcp_server/tools/training_load.py`) |
| `get_scheduled_workouts` | Запланированные тренировки на день | `data/database.py → get_scheduled_workouts_for_date()` |
| `get_goal_progress` | Race goal progress (overall + per-sport %) | `mcp_server/tools/goal.py` |
| `get_activity_hrv` | DFA a1 за вчера (Ra, Da, thresholds) | `data/database.py → get_activity_hrv_for_date()` |

### Опциональные tools (Claude вызывает если нужно)

| Tool | Когда полезен |
|---|---|
| `get_wellness_range` | TSB подозрительный → Claude смотрит тренд за неделю |
| `get_activities` | Хочет посмотреть что было за последние дни |
| `get_training_log` | Есть данные в training_log → compliance, patterns |
| `get_threshold_freshness` | Проверить нужен ли ramp test |
| `get_readiness_history` | Ra тренд за N дней |
| `get_mood_checkins` | Недавние mood check-ins → коррелировать настроение с recovery |
| `get_iqos_sticks` | Стики за день/неделю → коррелировать с recovery и HRV |

### Tool definitions формат

```python
MORNING_TOOLS = [
    {
        "name": "get_wellness",
        "description": "Get wellness data for a specific date. Returns recovery score, sleep, HRV (RMSSD), CTL, ATL, body metrics, and AI recommendations if available.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"}
            },
            "required": ["date"]
        }
    },
    {
        "name": "get_hrv_analysis",
        "description": "Get HRV analysis with dual-algorithm baselines. Returns status (green/yellow/red), 7d/60d means, bounds, CV, SWC, trend. Algorithm: 'flatt_esco' or 'ai_endurance'. Empty = both.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "algorithm": {"type": "string", "description": "Algorithm: 'flatt_esco', 'ai_endurance', or empty for both"}
            },
            "required": ["date"]
        }
    },
    # ... остальные tools аналогично
]
```

### Маппинг tool_name → функция

```python
TOOL_HANDLERS = {
    "get_wellness": handle_get_wellness,
    "get_hrv_analysis": handle_get_hrv_analysis,
    "get_rhr_analysis": handle_get_rhr_analysis,
    "get_recovery": handle_get_recovery,
    "get_training_load": handle_get_training_load,
    "get_scheduled_workouts": handle_get_scheduled_workouts,
    "get_goal_progress": handle_get_goal_progress,
    "get_activity_hrv": handle_get_activity_hrv,
    "get_wellness_range": handle_get_wellness_range,
    "get_activities": handle_get_activities,
    "get_training_log": handle_get_training_log,
    "get_threshold_freshness": handle_get_threshold_freshness,
    "get_readiness_history": handle_get_readiness_history,
    "get_mood_checkins": handle_get_mood_checkins,
    "get_iqos_sticks": handle_get_iqos_sticks,
}
```

Handlers — тонкие обёртки. Вызывают DB/metrics функции **напрямую** (не через MCP tool layer). Возвращают dict → сериализуется в JSON для Claude.

> **Решение по архитектуре:** handlers НЕ реиспользуют MCP tool функции — вызывают `data/database.py` и `data/metrics.py` напрямую. MCP tools — это обёртки для внешнего доступа (MCP протокол), handlers — для внутреннего (Claude API tool-use). Двойной слой (handler → MCP tool → DB) избыточен. Исключения: `get_recovery` и `get_training_load` — их логика живёт в `mcp_server/tools/`, поэтому выносим расчётную часть в `data/metrics.py` (или вызываем MCP-функцию напрямую как обычную async функцию, без MCP протокола).

---

## System Prompt V2

```python
SYSTEM_PROMPT_V2 = """
You are a personal AI triathlon coach. Your role is to analyze an athlete's
physiological data and provide specific, actionable training recommendations.

Athlete profile:
- Experienced triathlete, age {athlete_age}
- Target race: {goal_event} ({goal_date})
- LTHR Run: {lthr_run}, LTHR Bike: {lthr_bike}, FTP: {ftp}W, CSS: {css}s/100m
- Data source: Intervals.icu (Garmin wearable sync)

Important context on training load data:
- CTL, ATL, TSB, and ramp rate come directly from Intervals.icu (impulse-response model,
  τ_CTL=42d, τ_ATL=7d). Do NOT apply TrainingPeaks PMC thresholds.
- Per-sport CTL (swim, bike, run) is also from Intervals.icu sport-specific breakdown.

## Инструкции для утреннего отчёта

Используй доступные tools чтобы собрать данные о состоянии атлета.
Рекомендуемая последовательность:
1. get_recovery — текущий recovery score и категория
2. get_hrv_analysis — HRV статус (оба алгоритма)
3. get_rhr_analysis — пульс покоя
4. get_training_load — CTL/ATL/TSB/ramp_rate + per-sport CTL
5. get_scheduled_workouts — что запланировано на сегодня
6. get_goal_progress — прогресс к цели

Если какие-то данные вызывают подозрение (TSB < -20, HRV red, recovery low),
можешь запросить дополнительные данные: get_wellness_range за неделю,
get_activities за 3 дня, get_training_log для паттернов,
get_mood_checkins для эмоционального контекста,
get_iqos_sticks для корреляции с recovery.

## Формат ответа

Дай ответ в 4 секциях (Russian, max 250 words):
1. Оценка готовности (🟢/🟡/🔴) + краткое обоснование с цифрами
2. Оценка запланированной тренировки — подходит ли? Корректировка если нет
3. Одно наблюдение о тренде нагрузки
4. Короткая заметка о прогрессе к цели

## Правила
- Be specific — mention numbers, zones, durations
- If HRV is more than 15% below baseline → recommend reducing intensity
- If TSB < −25 → recommend a rest or recovery day
- If ramp rate > 7 TSS/week → flag overreaching risk
- Respond in Russian
"""
```

**Ключевое отличие от V1:** нет данных в промпте — только инструкции. Claude сам собирает данные через tools.

---

## Изменения в коде

### `ai/claude_agent.py`

```python
class ClaudeAgent:

    async def get_morning_recommendation_v2(self, target_date: date) -> str:
        """Generate morning AI recommendation using tool-use.

        Claude decides which tools to call to gather data,
        then synthesizes a recommendation.
        """
        system = get_system_prompt_v2()
        messages = [{"role": "user", "content": f"Сгенерируй утренний отчёт за {target_date.strftime('%Y-%m-%d')}"}]

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=messages,
            tools=MORNING_TOOLS,
        )

        # Tool-use loop
        iterations = 0
        max_iterations = 10  # safety limit

        while response.stop_reason == "tool_use" and iterations < max_iterations:
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await self._execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })

            # response.content — list of ContentBlock; SDK принимает as-is в messages
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            response = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                messages=messages,
                tools=MORNING_TOOLS,
            )
            iterations += 1

        # Extract text response
        text_blocks = [b.text for b in response.content if b.type == "text"]
        return "\n".join(text_blocks) if text_blocks else "Не удалось сгенерировать отчёт"

    async def _execute_tool(self, name: str, input_data: dict) -> dict:
        """Execute a tool call and return the result."""
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return {"error": f"Unknown tool: {name}"}
        try:
            return await handler(**input_data)
        except Exception as e:
            logger.warning("Tool %s failed: %s", name, e)
            return {"error": str(e)}
```

### `ai/prompts.py`

Добавляется:
- `SYSTEM_PROMPT_V2` — новый system prompt без данных, с инструкциями по tools
- `get_system_prompt_v2()` — форматирует с athlete settings

Остаётся (не удаляется):
- `SYSTEM_PROMPT` — для generate_workout и analyze_week (они остаются на фиксированном промпте)
- `MORNING_REPORT_PROMPT` — как fallback
- `WORKOUT_GENERATION_PROMPT` — без изменений

### `ai/tool_definitions.py` (новый файл)

```python
"""Tool definitions and handlers for Claude tool-use API."""

MORNING_TOOLS = [...]  # tool definitions (name, description, input_schema)
TOOL_HANDLERS = {...}  # name → async handler function
```

### `bot/scheduler.py`

Минимальное изменение — вызов `get_morning_recommendation_v2` вместо `get_morning_recommendation`:

```python
# В save_wellness или daily_metrics_job
if ai_is_new:
    agent = ClaudeAgent()
    try:
        recommendation = await agent.get_morning_recommendation_v2(dt)
    except Exception:
        # Fallback на старый метод
        recommendation = await agent.get_morning_recommendation(
            wellness_row, hrv_flatt, hrv_aie, rhr_row, workouts
        )
```

---

## Fallback стратегия

Старый метод `get_morning_recommendation()` **не удаляется**. Используется как fallback:
- Если tool-use loop превышает max_iterations
- Если Claude API возвращает ошибку при tool-use
- Через конфиг `AI_USE_TOOL_USE=true/false` для A/B тестирования

---

## Gemini

Gemini **не переходит** на tool-use. Текущая роль Gemini — дублирующий утренний отчёт с тем же фиксированным промптом. По плану #21 Gemini перейдёт в роль weekly pattern analyst (не tool-use, а batch-анализ training_log).

`MORNING_REPORT_PROMPT_GEMINI` и `gemini_agent.py` — без изменений.

---

## Оценка стоимости

| Метрика | V1 (фиксированный промпт) | V2 (tool-use) |
|---|---|---|
| API вызовы | 1 | 3-5 (initial + tool rounds) |
| Input tokens | ~2K (промпт) | ~5-8K (system + tools defs + tool results) |
| Output tokens | ~300-500 | ~500-800 (tool calls + final text) |
| Latency | 2-3 sec | 5-10 sec |
| Cost estimate | ~$0.01/day | ~$0.03-0.05/day |

Рост стоимости ~3-5x, но в абсолюте — копейки ($1-1.5/месяц вместо $0.30).

---

## Конфигурация

```env
# .env
AI_USE_TOOL_USE=true    # Enable tool-use for morning analysis (default: true)
```

```python
# config.py
AI_USE_TOOL_USE: bool = True   # Tool-use by default, fallback on errors
```

---

## План реализации

| # | Задача | Файлы |
|---|---|---|
| 1 | Tool definitions + handlers | `ai/tool_definitions.py` (новый) |
| 2 | Tool handlers — обёртки над DB/MCP функциями | `ai/tool_definitions.py` |
| 3 | `get_morning_recommendation_v2()` с tool-use loop | `ai/claude_agent.py` |
| 4 | `SYSTEM_PROMPT_V2` + `get_system_prompt_v2()` | `ai/prompts.py` |
| 5 | Конфиг `AI_USE_TOOL_USE` + fallback логика | `config.py`, `bot/scheduler.py` |
| 6 | Тесты: tool execution, loop termination, fallback | `tests/test_tool_use.py` |
| 7 | A/B сравнение: 1 неделя с логированием обоих вариантов | `bot/scheduler.py` |

### Критерии готовности

- [ ] Claude вызывает 5-8 tools и генерирует рекомендацию
- [ ] Tool-use loop корректно завершается (max_iterations safety)
- [ ] Fallback на V1 при ошибках
- [ ] Конфиг `AI_USE_TOOL_USE` переключает между V1 и V2
- [ ] Стоимость в пределах оценки (~$0.05/день)
- [ ] Качество рекомендаций не хуже V1

---

## Будущее: объединение с Phase 3

Tool-use инфраструктура (tool definitions, handlers, loop) переиспользуется для MCP Phase 3 (free-form Telegram chat). Разница:
- Утренний анализ: автоматический вызов, `SYSTEM_PROMPT_V2` с инструкциями по отчёту
- Free-form chat: по запросу пользователя, system prompt без привязки к утреннему формату
- Tools одни и те же, handlers одни и те же
