# MCP Phase 3 — Free-form Telegram Chat

> Свободный чат с AI-тренером через Telegram. Stateless: каждое сообщение — независимый вызов. Контекст — через tools, не через историю диалога.

---

## Два уровня взаимодействия

| | Telegram Chat (Phase 3) | Claude / Cowork (MCP) |
|---|---|---|
| **Когда** | На ходу, перед тренировкой, быстрый вопрос | За столом, вдумчивая работа |
| **Контекст** | Нет истории — данные через tools | Полный: skills, файлы, длинные сессии |
| **Возможности** | Вопросы по данным, короткие советы, mood/iqos | Workout cards, анализ паттернов, адаптация плана, документация |
| **Ограничения** | Только текст + ссылки, нет файлов, нет skills | Требует десктоп |
| **Стоимость** | ~$0.02-0.05 за диалог | Включено в подписку |

Telegram-чат **не пытается** заменить Claude. Он закрывает сценарий "быстрый вопрос тренеру" — то, для чего открывать десктоп избыточно.

---

## Архитектура

```
Telegram: текстовое сообщение (не команда)
    ↓
MessageHandler(filters.TEXT & ~filters.COMMAND)
    ↓
owner check: update.effective_user.id == TELEGRAM_CHAT_ID
    ↓  нет → игнор (без ответа)
    ↓  да ↓
handle_chat_message()
    ↓
Claude messages.create(
    system = SYSTEM_PROMPT_CHAT,
    messages = [{role: "user", content: message.text}],
    tools = CHAT_TOOLS,             ← те же tools из Phase 2
    max_tokens = 2048,
)
    ↓
Tool-use loop (те же handlers из ai/tool_definitions.py)
    ↓
Текстовый ответ → update.message.reply_text()
```

**Ключевое:** никакой истории сообщений. Каждое сообщение — чистый лист. Claude получает контекст из данных через tool calls, а не из предыдущих сообщений.

---

## Ограничение доступа

Чат доступен **только owner** (TELEGRAM_CHAT_ID). Все остальные сообщения игнорируются без ответа.

```python
async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-form text messages — AI chat via tool-use."""
    # Owner only — silent ignore for others
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
        return

    user_text = update.message.text
    if not user_text or not user_text.strip():
        return

    # Typing indicator while Claude thinks.
    # send_action("typing") автоматически пропадает через 5 сек.
    # При длинном tool-use loop (5-10 сек) переотправляем в _run_tool_use_loop
    # через on_iteration callback, но для MVP достаточно одного раза.
    await update.message.chat.send_action("typing")

    try:
        agent = ClaudeAgent()
        response = await agent.chat(user_text)

        # Telegram Markdown хрупкий: незакрытые *, _ или []() сломают отправку.
        # Пробуем Markdown, fallback на plain text.
        try:
            await update.message.reply_text(response, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(response)
    except Exception as e:
        logger.error("Chat error: %s", e, exc_info=True)
        await update.message.reply_text("Ошибка при обработке. Попробуй ещё раз.")
```

Регистрация handler-а — **последним**, чтобы команды и whoami обрабатывались первыми:

```python
# bot/main.py — build_application()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("morning", morning))
app.add_handler(CommandHandler("web", web_login))
app.add_handler(CommandHandler("stick", stick))
# whoami закомментирован — debug-команда, в продакшене не нужна.
# app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^whoami$"), whoami))

# Phase 3: free-form chat — последний handler, catches all remaining text.
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_message))
```

---

## ClaudeAgent — общий tool-use loop

Tool-use loop дублировался бы в `get_morning_recommendation_v2()` и `chat()`. Выносим в общий приватный метод:

```python
async def _run_tool_use_loop(
    self,
    system: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 4096,
    max_iterations: int = 10,
) -> str:
    """Run Claude API with tool-use loop. Returns final text response.

    Shared between morning analysis (V2) and free-form chat.
    """
    response = await self.client.messages.create(
        model=self.model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        tools=tools,
    )

    iterations = 0
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

        # response.content — list of ContentBlock; SDK принимает as-is
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )
        iterations += 1

    text_blocks = [b.text for b in response.content if b.type == "text"]
    return "\n".join(text_blocks) if text_blocks else ""
```

### Утренний анализ (V2) — упрощается:

```python
async def get_morning_recommendation_v2(self, target_date: date) -> str:
    system = get_system_prompt_v2()
    messages = [{"role": "user", "content": f"Сгенерируй утренний отчёт за {target_date:%Y-%m-%d}"}]
    result = await self._run_tool_use_loop(system, messages, MORNING_TOOLS, max_tokens=4096)
    return result or "Не удалось сгенерировать отчёт"
```

### Chat — 5 строк:

```python
async def chat(self, user_message: str) -> str:
    """Handle a free-form chat message. Stateless: no conversation history."""
    system = get_system_prompt_chat()
    messages = [{"role": "user", "content": user_message}]
    result = await self._run_tool_use_loop(system, messages, CHAT_TOOLS, max_tokens=2048)
    return result or "Не удалось обработать запрос."
```

---

## SYSTEM_PROMPT_CHAT

Отдельный промпт — без привязки к утреннему формату. Компактный, свободный.

```python
SYSTEM_PROMPT_CHAT = """
You are a personal AI triathlon coach available via Telegram chat.
Answer the athlete's question concisely. Use tools to fetch current data when needed.

Athlete profile:
- Experienced triathlete, age {athlete_age}
- Target race: {goal_event} ({goal_date})
- LTHR Run: {lthr_run}, LTHR Bike: {lthr_bike}, FTP: {ftp}W, CSS: {css}s/100m
- Data source: Intervals.icu (Garmin wearable sync)

Important:
- CTL, ATL, TSB come from Intervals.icu (τ_CTL=42d, τ_ATL=7d). NOT TrainingPeaks.
- Use tools to get actual data — don't guess or assume values.
- If the question doesn't require data (e.g. general training advice), answer directly without tools.
- Keep answers short: 2-5 sentences for simple questions, up to 10 for analysis.
- Respond in Russian.
- Format for Telegram: use Markdown (bold, italic), no headers, no long lists.

Available tools give you access to: wellness, HRV, RHR, recovery, training load,
scheduled workouts, activities, goal progress, training log, mood, IQOS data,
threshold freshness, and readiness history.
"""
```

---

## Tools

`CHAT_TOOLS` — копия `MORNING_TOOLS` из Phase 2. 14 инструментов (7 основных + 7 опциональных). Handlers — те же. Код — тот же.

```python
# ai/tool_definitions.py
CHAT_TOOLS = [*MORNING_TOOLS]  # Копия, не alias — чтобы можно было добавлять chat-only tools
```

Если в будущем появятся chat-only tools (например `save_mood_checkin` — записать настроение по запросу из чата), они добавляются в `CHAT_TOOLS` отдельно, не затрагивая `MORNING_TOOLS`.

---

## Что чат умеет (примеры)

| Вопрос | Tools | Ответ |
|---|---|---|
| "Как у меня дела?" | get_recovery, get_hrv_analysis | Краткая сводка: recovery 72, HRV green, можно тренироваться |
| "Стоит ли бежать интервалы?" | get_recovery, get_scheduled_workouts, get_training_load | Да/нет с обоснованием по TSB и recovery |
| "Какой у меня TSB?" | get_training_load | TSB = -8, оптимальная зона |
| "Что было вчера?" | get_activities | Бег 45 мин, Z2, HR avg 142 |
| "Нужен ли рамп-тест?" | get_threshold_freshness | LTHR Run: 45 дней, рекомендую обновить |
| "Сколько стиков за неделю?" | get_iqos_sticks | 47 стиков, ~6.7/день |
| "Как правильно бегать Z2?" | — (без tools) | Общий совет по Z2 тренировкам |

---

## Что чат НЕ умеет

- **Создавать файлы** — workout cards, документы, графики. Для этого Claude/Cowork + MCP.
- **Держать контекст** — каждое сообщение независимо. "А что я спросил 5 минут назад?" — не знает.
- **Модифицировать план** — не может вызвать `suggest_workout` или `remove_ai_workout` (намеренно не включены в CHAT_TOOLS; запись — через Cowork).
- **Показывать графики** — только текст. Может дать ссылку на webapp.

---

## Ограничения

| Ограничение | Значение | Комментарий |
|---|---|---|
| max_tokens | 2048 | Хватит для ответа, меньше чем morning (4096) |
| max_iterations | 10 | Safety limit для tool-use loop |
| Rate limit | Нет (owner only) | Один пользователь — нет смысла в rate limit |
| Стоимость | ~$0.02-0.05 за сообщение | 1-5 API calls с tools |
| Latency | 3-10 сек | Tool-use loop + Claude thinking |
| Контекст | Нет | Stateless, данные через tools |

---

## Конфигурация

Новых env vars не нужно. Chat использует существующие:

```env
ANTHROPIC_API_KEY=...            # уже есть
TELEGRAM_BOT_TOKEN=...           # уже есть
TELEGRAM_CHAT_ID=...             # owner check
```

Опциональный kill switch (если нужно отключить чат, оставив утренний отчёт):

```env
AI_CHAT_ENABLED=true             # Enable free-form Telegram chat (default: true)
```

```python
# config.py
AI_CHAT_ENABLED: bool = True
```

```python
# В handle_chat_message, первая строка:
if not settings.AI_CHAT_ENABLED:
    return
```

---

## Изменения в коде

| Файл | Изменение |
|---|---|
| `ai/claude_agent.py` | Вынести `_run_tool_use_loop()`, упростить `get_morning_recommendation_v2()`, добавить `chat()` |
| `ai/prompts.py` | Добавить `SYSTEM_PROMPT_CHAT` + `get_system_prompt_chat()` |
| `ai/tool_definitions.py` | Добавить `CHAT_TOOLS = [*MORNING_TOOLS]` (копия, не alias) |
| `bot/main.py` | Добавить `handle_chat_message()` + `MessageHandler` (последним) |
| `config.py` | Добавить `AI_CHAT_ENABLED: bool = True` |

Без новых таблиц. Без новых зависимостей. Без миграций.

---

## План реализации

| # | Задача | Файлы | Статус |
|---|---|---|---|
| 1 | Вынести `_run_tool_use_loop()`, упростить `get_morning_recommendation_v2()` | `ai/claude_agent.py` | Done |
| 2 | `SYSTEM_PROMPT_CHAT` + `get_system_prompt_chat()` | `ai/prompts.py` | Done |
| 3 | `CHAT_TOOLS = [*MORNING_TOOLS]` | `ai/tool_definitions.py` | Done |
| 4 | `ClaudeAgent.chat()` (5 строк, использует `_run_tool_use_loop`) | `ai/claude_agent.py` | Done |
| 5 | `handle_chat_message()` + handler registration (последним) | `bot/main.py` | Done |
| 6 | `AI_CHAT_ENABLED` config | `config.py` | Done |
| 7 | Тесты: chat response, owner check, kill switch, tool-use, Markdown fallback | `tests/test_chat.py` (13 тестов) | Done |

### Зависимости

Phase 3 **зависит от Phase 2** — реиспользует:
- `MORNING_TOOLS` / `TOOL_HANDLERS` из `ai/tool_definitions.py`
- `_execute_tool()` и `_run_tool_use_loop()` из `ClaudeAgent`

Шаг 1 — рефакторинг Phase 2 (вынос loop). Шаги 2-6 — новый код Phase 3. ~80 строк нового кода.

### Критерии готовности

- [x] Текстовое сообщение от owner → Claude ответ с tool-use
- [x] Сообщение от чужого user → молчание (без ответа, без ошибки)
- [x] Команды (/morning, /stick) продолжают работать как раньше
- [x] `AI_CHAT_ENABLED=false` → чат отключён, команды работают
- [x] Ответы: Markdown с fallback на plain text при ошибке парсинга
- [x] Tool-use loop завершается корректно (max_iterations)

---

## Оценка стоимости

При 10-15 сообщениях в день: ~$0.30-0.75/день, ~$10-20/месяц.
При 3-5 сообщениях в день (реалистичный сценарий): ~$0.10-0.25/день, ~$3-7/месяц.

Это поверх утреннего отчёта ($0.03-0.05/день).

---

## Будущее

- **save_mood_checkin в CHAT_TOOLS** — Claude замечает эмоциональный контекст в сообщении → предлагает записать mood → пользователь подтверждает. Требует добавить tool + handler.
- **Ссылки на webapp** — Claude включает deep links: "Подробнее: {webapp_url}/wellness" или "{webapp_url}/activity/{id}".
- **Telegram formatting** — explore MarkdownV2 для лучшего форматирования (escaped chars).
