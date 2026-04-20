# User Context / Memory Spec

> Долговременная память о пользователе для Telegram-бота: бот запоминает факты
> (травмы, предпочтения, рабочий график, семейные обстоятельства) и подмешивает
> их в системный промпт Claude, чтобы советы и диалог учитывали контекст.

**Related:**

| Issue / Spec | Связь |
|---|---|
| `docs/MULTI_TENANT_SECURITY.md` | T1 (tenant data leak) — факты per-user, FK на `users.id` |
| `docs/ADAPTIVE_TRAINING_PLAN.md` | Personal patterns (Phase 3) — отдельный слой, не пересекается |
| `bot/prompts.py` | `get_system_prompt_chat` — точка инъекции фактов/цели |
| `bot/agent.py:76` | `cache_control: ephemeral` — уже есть на system prompt |

---

## 1. Мотивация

Сейчас чат stateless: Claude каждое сообщение начинает «с чистого листа» + reply-context. Если атлет пишет «опять колено болит» — бот не знает, что неделю назад обсуждали эту же жалобу, и начинает диалог заново. Цели из `athlete_goals` подгружаются через MCP resource `athlete://goal`, но остальной контекст (травмы, работа, ограничения по времени, стиль тренировок, семья) нигде не хранится.

`data/db/mood_checkins.py` — не подходит: это структурированные 1–5 шкалы, а нужны свободно-текстовые факты с темой.

---

## 2. Scope

### Phase 1 (MVP) — делаем сейчас

- Таблица `user_facts` + ORM (append-with-cap, N=3 активных на topic — см. §3).
- MCP tools: `save_fact`, `list_facts`, `deactivate_fact`, `get_fact_metrics` (per-user, все атлеты видят свои метрики).
- Инъекция активных фактов + цели в системный промпт с учётом prompt caching (§5, §6).
- Undo-кнопка «🗑 Забудь это» после `save_fact` с TTL (§4).
- TTL фактов через `expires_at` (опционально на факт).
- Observability: метрики fact-writes / undo-rate / cache-hit-rate (§10).

### Phase 2 — отложено (условно, по триггеру из §11.3)

- Async post-chat extractor (actor вычитывает историю диалогов из Redis и предлагает факты батчем).
- Batch-approval UI через `context.user_data["pending_facts"]` (§4 Phase 2).
- Phase 2b: per-item чекбоксы «⚙️ Выборочно».

### Вне scope (никогда или до major-redesign)

- Shared facts между пользователями — multi-tenant isolation (threat T1).
- Top-N фильтрация фактов по keyword'ам сообщения — ломает prompt caching, решено грузить всё (см. §6 tradeoff).
- Персональные паттерны тренировок (`compute_personal_patterns` — отдельный флоу ATP Phase 3).
- Замена `athlete_goals` / `athlete_settings` — структурированные данные остаются отдельно, факты — только прозаические.
- Embedding-based семантический дедуп — дороже, чем выигрыш на десятках фактов.

---

## 3. Data model

### Таблица `user_facts`

```sql
CREATE TABLE user_facts (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic           VARCHAR(64) NOT NULL,   -- "injury", "schedule", "family", "preference", "job", ...
    fact            VARCHAR(300) NOT NULL,  -- hard cap: один факт = одна мысль, не эссе
    source          VARCHAR(16) NOT NULL,   -- "tool" | "extractor" | "user"
    confidence      REAL NOT NULL DEFAULT 1.0,  -- 0..1; extractor пишет <1, tool/user — 1.0
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,            -- NULL = бессрочно
    deactivated_at  TIMESTAMPTZ,            -- NULL = активен
    deactivated_reason VARCHAR(32),         -- "topic_cap" | "user_request" | "expired" | "contradicted"
    superseded_by   INTEGER REFERENCES user_facts(id)  -- для аудита вытеснений по cap
);

CREATE INDEX ix_user_facts_active ON user_facts(user_id, topic, created_at DESC)
    WHERE deactivated_at IS NULL;
```

**Семантика полей:**

- `topic` — enum-like, свободный список (см. §7). **НЕ** слот — в одном topic может быть до **N=3** активных фактов (см. ниже).
- `fact` — прозаический текст до 300 символов, один факт = одна мысль. Пишем от лица атлета: «болит правое колено после забега на 10K 12 апреля», а не «у пользователя травма». Cap валидируется в MCP tool + БД-constraint (double защита).
- `source` — кто записал. `tool` — Claude вызвал `save_fact` в диалоге. `extractor` — async actor Phase 2. `user` — явное редактирование через Settings (пока не планируется).
- `confidence` — нужен для Phase 2 extractor'а. Tool и user пишут 1.0. Ниже 0.7 не инжектируем в промпт (позже, когда будет extractor).
- `expires_at` — факт с TTL. Пример: «жена беременна, срок октябрь» → `expires_at = '2026-10-31'`.
- `deactivated_at` / `superseded_by` — audit trail вытеснений по cap, не удаляем строки физически.

### Конфликт-резолюшен: append-with-cap (не upsert-by-topic)

Изначально план был «один активный факт на topic», но это теряет контекст: «болит ахилл» стирал бы «болит колено», хотя травмы разные. Правильная семантика:

1. **Append** — новый факт всегда добавляется активным.
2. **Cap на topic** — после вставки, если у `(user_id, topic)` стало `>N` активных (N=3) — самый старый по `created_at` помечается `deactivated_at = now()`, `deactivated_reason = 'topic_cap'`, `superseded_by = <new_id>`.
3. **Global soft cap** — если суммарно активных `>50`, tool возвращает warning в response, Claude сам должен звать `deactivate_fact` на устаревшее. Hard cap не ставим.

Для семантических дублей в рамках одного topic («болит колено» + «колено болит уже неделю») — опираемся на модель, которая при явной дублирующей фразе должна звать `deactivate_fact` на старую вместо создания новой. В Phase 2 extractor дедуп через prompt, не embedding.

### Партицийный индекс

`WHERE deactivated_at IS NULL` — активных фактов у атлета будет десятки максимум, индекс компактный. Добавлен `created_at DESC` чтобы cap-вытеснение («найди самый старый в topic») работало index-only без сортировки.

---

## 4. Writers

### Phase 1 — MCP tool `save_fact` (MVP)

Claude сам решает, что сохранить, вызывая tool во время диалога:

```python
@sentry_tool
async def save_fact(topic: str, fact: str, expires_at: str | None = None) -> dict:
    """Save a LASTING trait about the user to long-term memory.

    Lasting trait = something still relevant in 2 weeks.
    Transient state (mood, today's energy, "I'm tired") → use save_mood_checkin.

    Save (lasting):
    - Injuries, chronic conditions, recovery constraints
    - Work schedule, travel plans, family events (pregnancy, newborn, ...)
    - Training preferences (morning person, hates intervals, loves hills)
    - Equipment or environment (new bike, treadmill-only in winter)

    Do NOT save:
    - Transient moods / one-off complaints ("feeling low today") → save_mood_checkin
    - Data already in athlete_settings / athlete_goals (FTP, LTHR, race goals)
    - Anything derivable from wellness / activities data
    - More than one fact per call (split into multiple calls)

    Args:
        topic: Short slot name. Canonical: injury, schedule, family, preference,
               job, equipment, health, travel. Pick the closest; a new topic is
               also fine but be consistent with past ones.
        fact:  Prose, first-person-about-user, includes date if time-bound.
               MAX 300 chars — one fact = one thought, not an essay.
               "right knee hurts after 10K on 2026-04-12"
        expires_at: Optional ISO date; leave null for indefinite facts.

    Before saving a fact that may duplicate an existing one, call list_facts
    first and consider deactivate_fact on the older version instead of adding
    a near-duplicate.

    Returns: {"fact_id": int, "evicted_id": int | None, "warning": str | None}
             warning is set when the user has >50 active facts — then you
             should deactivate stale ones before saving more.
    """
```

**Semantics:** append-with-cap (см. §3):

1. Валидация: `len(fact) <= 300`, `topic` не пустой. Иначе — tool error (Claude увидит, перепишет).
2. Insert новый факт → `source='tool'`, `confidence=1.0`.
3. Если после вставки у `(user_id, topic)` активных `>3` — найти самый старый по `created_at`, пометить `deactivated_reason='topic_cap'`, `superseded_by=<new_id>`. Вернуть его id как `evicted_id`.
4. Если total active `>50` — добавить `warning` в response.

### Phase 1 — MCP tools `list_facts` / `deactivate_fact`

```python
async def list_facts(include_inactive: bool = False) -> list[dict]
async def deactivate_fact(fact_id: int, reason: str = "user_request") -> dict
```

Нужны чтобы Claude мог ответить «что ты обо мне помнишь?» и явно забыть факт по просьбе атлета.

### Phase 1 — Undo-кнопка после `save_fact` (переиспользование workout-паттерна)

Полный preview-confirm на каждое сохранение ломает разговорный UX (каждое сообщение превращается в анкету). Вместо этого — **save-then-undo**, переиспользуя механику `/workout`:

1. `save_fact` коммитит сразу в tool-use-loop (`source='tool'`, `confidence=1.0`).
2. Handler чата (`handle_chat_message` в `bot/main.py`) просит `agent.chat(...)` с `tool_calls_filter={"save_fact", "deactivate_fact"}` и получает `ChatResult.tool_calls`.
3. Если среди tool_calls есть `save_fact` — handler читает `fact_id` из tool_result (возвращается tool'ом), кладёт в `context.user_data["last_saved_fact_id"] = N` и **добавляет к ответному сообщению** inline-кнопку «🗑 Забудь это».
4. Callback `fact_undo`: `pop("last_saved_fact_id")` + прямой `MCPClient.call_tool("deactivate_fact", {"fact_id": N, "reason": "user_request"})` без повторной Claude-инференции.

**Почему не полный preview-confirm:** запись факта — внутреннее состояние, soft-delete одним тапом. В отличие от `/workout`, где push в Intervals.icu — side effect в чужую систему, и prompt-injection на state-mutating шаге критичен. Здесь — низкие ставки, и UX-стоимость полного preview не оправдана.

**Consume-on-read:** `pop` чтобы повторный тап со старого сообщения не вызвал `deactivate_fact` повторно на уже деактивированный id. Ответ MCP на второй вызов даст ошибку, но лучше не доводить.

**TTL на кнопку.** Inline «🗑 Забудь» не должна висеть на старом сообщении вечно — через неделю юзер случайно тапнет и потеряет факт, который Claude уже вспоминал в других контекстах. Две меры:

1. **При следующем chat-сообщении** — перед отправкой нового ответа `handle_chat_message` читает `context.user_data.pop("last_undo_message_id", None)`; если есть — `bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)` на предыдущее. Клавиатура исчезает, `last_saved_fact_id` тоже очищается. Это основной путь.
2. **Тайм-аут 10 минут** (fallback) — при отправке сообщения с undo-кнопкой регистрируем `context.job_queue.run_once(_expire_undo_button, when=600, data={...})`. Job делает то же `edit_message_reply_markup(None)`. Покрывает случай «юзер ушёл и не написал до утра».

В обеих мерах сама запись в БД не меняется — факт остаётся активным, просто кнопка отмены исчезает. Отменить через `/forget` или Settings (Phase 3) по-прежнему можно.

**Registry:** расширять `_PREVIEWABLE_TOOLS` не нужно — у этого флоу нет preview-фазы, только пост-коммит-undo. Логика отдельная: `_UNDOABLE_TOOLS: dict[str, UndoableTool(extract_entity_id, undo_tool_name)]` в `bot/main.py`:

```python
_UNDOABLE_TOOLS = {
    "save_fact": UndoableTool(
        extract_id=lambda result: result.get("fact_id"),
        undo_tool="deactivate_fact",
        undo_args=lambda fid: {"fact_id": fid, "reason": "user_request"},
    ),
}
```

Готовая почва для расширения: если появятся другие «committed with undo» tool'ы (например, `schedule_workout` без preview), регистрируем их сюда.

**Edge case — `save_fact` внутри `/workout` flow.** Хэндлеры `workout_sport_chosen` / `workout_dialog_text` вызывают `agent.chat(..., tool_calls_filter={"suggest_workout", "compose_workout"})` — узкий filter, чтобы не хранить deep-copy чужих tool_calls. Если Claude решит внутри этого диалога вызвать `save_fact` («запомни, что тренируюсь утром»), факт **запишется в БД** (server-side MCP работает всегда), но undo-кнопка **не появится** — `save_fact` не попадёт в `ChatResult.tool_calls` из-за фильтра.

Решение для MVP: **silent save** — приемлемо, т.к. факт можно deactivate'ить из обычного чата («забудь что я тренируюсь утром») или `list_facts` + `deactivate_fact`. Альтернатива — union фильтра `{"suggest_workout", "compose_workout", "save_fact"}` в workout-хэндлерах + показ undo-кнопки после основного preview. Сделаем если пользователи начнут жаловаться.

### Phase 2 — async post-chat extractor с batch-approval

1. `ClaudeAgent.chat()` после ответа пушит в Redis stream `user_facts_stream:{user_id}` кортеж `(user_msg, assistant_msg)`.
2. Dramatiq actor `actor_extract_user_facts` раз в N часов читает stream, запускает Claude с промптом «верни JSON массив фактов-кандидатов», но **не пишет в БД сразу** — отправляет пользователю **одно** Telegram-сообщение с превью:

   ```
   Я подметил в недавних разговорах:
   1. [injury] болит правое колено после 10K
   2. [family] жена беременна, срок октябрь
   3. [preference] не любишь длинные интервалы

   Сохранить?
   [✅ Все]  [⚙️ Выборочно]  [❌ Отбросить]
   ```

3. Драфт живёт в `context.user_data["pending_facts"] = [...]` **ровно так же**, как `pending_workout` в `/workout`. Batch approval — прямой `MCPClient.call_tool("save_fact", ...)` в цикле, без повторной Claude-инференции.
4. «⚙️ Выборочно» — вторая клавиатура с per-item чекбоксами (можно начать без этой кнопки в Phase 2a, добавить в 2b).
5. Consume-on-read: `pop("pending_facts")` при любом финальном действии.

**Timing.** Cron в локальной TZ юзера (`users.timezone` или fallback `TIMEZONE=Europe/Belgrade`), окно `hour=18..19` — в это время атлет обычно free + evening report уже ушёл, можно спокойно показать предложение. Не в 3 утра. Scheduler читает per-user TZ как это делает `tasks/scheduler.py` для morning report.

**Concurrent pending.** Если при запуске cron видим что `user_data["pending_facts"]` уже непустой (предыдущий батч без ответа) — **skip** текущий запуск, не затирая старый. Юзер отреагирует → флаг очистится → следующий cron подхватит. Объединять батчи не стоит: смешанные свежие + вчерашние кандидаты ломают UX «я подметил в недавних разговорах». Если pending висит >48h — автоматически dismiss через job_queue (см. TTL на undo в §4), чтобы не блокировать пайплайн навечно.

**Почему здесь preview-confirm оправдан** (в отличие от Phase 1 `save_fact`):
- Extractor может галлюцинировать — пользователь **должен** увидеть что сохраняется.
- Batch-операция: один тап сохраняет 3 факта, а не три отдельных undo-кнопки.
- Нет разговорного контекста в момент извлечения — юзер не писал только что, прерывать нечего, сообщение приходит само.

**Риски extractor'а:** false positives (модель запомнит ерунду), стоимость (отдельный Claude-pass на каждого активного юзера). Phase 2 включаем только если Phase 1 tool-based подход покажет пропуски (Claude забывает звать `save_fact` — см. §11.3 trigger + §10 метрики).

**Ссылка на референс-реализацию:** `bot/main.py:641-689` (`_PREVIEWABLE_TOOLS`, `_extract_pending_workout`, `_apply_push_flag`) и `bot/main.py:828` (`workout_push`) — паттерн один-в-один, только вместо `dry_run` флага — список id'ов фактов для батч-записи.

---

## 5. Reader — инъекция в промпт

### Где

`bot/prompts.py:get_system_prompt_chat` — единственная точка сборки system prompt для чат-флоу.

### Как

```
[ static_prompt  ─ cache_control #1 ]    ← вечный кэш, один раз на сессию
[ dynamic_tail   ─ cache_control #2 ]    ← tухнет при save_fact / goal update
     │
     ├── athlete profile (sports, TZ)
     ├── goal block       (из athlete://goal)
     └── facts block      (активные user_facts)
```

Два `cache_control` маркера — см. §6 для полного объяснения. Статика держится горячей между сохранениями фактов.

**Формат facts-блока:**

```
## Что я помню о тебе
- [injury] болит правое колено после 10K 2026-04-12
- [family] жена беременна, срок октябрь 2026
- [schedule] работаю с 10 до 19, тренируюсь утром до работы или в 20:30
- [preference] не люблю длинные интервалы, предпочитаю пороговые 2х20 мин
```

**Правила:**

- Сортировка по `topic` → внутри по `created_at` DESC. Стабильный порядок → кэш не ломается.
- Инжектим **все активные** факты (не top-N). На десятках фактов это ≪10% промпта, а стабильность кэша важнее.
- Если у атлета ноль активных фактов — блок не рендерим вообще (экономим токены + не нужен negative prompt «ты ещё ничего не знаешь»).

### Morning report

Для `tasks/actors/reports.py` (morning report через `MCPTool`) — **пока не инжектим**. Morning report — это аналитика данных, а не диалог; факты релевантнее чату. Расширим если придёт feedback от атлетов.

---

## 6. Prompt caching — стратегия

Сейчас `bot/agent.py:76` ставит **один** `cache_control` в конец system prompt:

```python
cached_system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
```

**Почему одного маркера мало.** Anthropic prompt cache работает как **префиксный хэш**: хэш считается от начала до маркера. Если в конце блока меняется хоть один символ (добавили факт → блок `## Что я помню о тебе` переписался) — хэш префикса до маркера другой → **cache miss на весь system prompt**, включая статику. Текущая схема работает пока facts/goal не меняются — но как только сделаем `save_fact` в диалоге, следующее сообщение читает всё с нуля.

**Как надо.** Разбиваем system prompt на **два** cacheable сегмента (лимит — 4, запас есть):

```python
static_prompt = get_static_system_prompt()           # ~весь текущий SYSTEM_PROMPT_CHAT
dynamic_tail  = render_athlete_block(user)           # goal + facts + профиль

cached_system = [
    {"type": "text", "text": static_prompt, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": dynamic_tail,  "cache_control": {"type": "ephemeral"}},
]
```

**Поведение:**

- `static_prompt` хэшируется до маркера #1 → кэш **вечный** относительно изменений facts (пока не правим сам шаблон промпта).
- `dynamic_tail` — свой кэш, тухнет только при `save_fact` / `deactivate_fact` / смене `goal`.
- `save_fact` инвалидирует ровно хвост; статика (3–5к токенов) остаётся горячей.

**Эффект:**

- TTL ephemeral 5 мин → при активном диалоге кэш попадает на каждой tool-use-loop итерации и в следующих сообщениях.
- Cache read ≈10% от input tokens. На чате с tool-use (3–5 итераций) + частыми сохранениями фактов экономия **кратная** — без двух маркеров весь system prompt инвалидировался бы на каждом save.

**Порядок внутри `dynamic_tail`:** athlete profile (редко) → goal (реже) → facts (чаще). Но после второго маркера порядок значения не имеет — всё одним хэшем. Оставляем читабельный порядок.

**Что делать в `agent.py`:** поменять строку 76 с одного blob'а на два элемента. Весь остальной tool-use-loop не трогаем. В `bot/prompts.py` разделить `SYSTEM_PROMPT_CHAT` на `get_static_system_prompt()` (константа) и `render_athlete_block(user)` (динамика).

---

## 7. Topic taxonomy

Свободный список, но с «каноническими» значениями для consistency. В docstring `save_fact` перечислен канон, Claude будет стремиться выбирать оттуда:

| topic | примеры |
|---|---|
| `injury` | «правое колено болит после 10K 2026-04-12» |
| `health` | «астма, ингалятор до тренировки», «аллергия на пыльцу берёзы, апрель-май» |
| `schedule` | «работа 10–19, тренируюсь до работы или в 20:30» |
| `family` | «жена беременна, срок октябрь 2026», «дочь Ева 2 года» |
| `preference` | «не люблю длинные интервалы», «люблю горы, ненавижу treadmill» |
| `equipment` | «новый power meter Favero 2026-03», «ездит Canyon Aeroad» |
| `travel` | «в Сербии до декабря 2026, потом переезд» |
| `job` | «senior engineer в Makai Labs, удалёнка» |

Новый `topic` от Claude допустим — tool не валидирует enum. Если через полгода увидим, что Claude постоянно плодит дубли (`food_preference` vs `diet` vs `nutrition`) — пропишем строгий enum в validator.

---

## 8. Multi-tenant isolation

- `user_facts.user_id` — NOT NULL FK на `users(id) ON DELETE CASCADE`.
- Все ORM-методы через `@dual` + `@with_session`, user_id — первый аргумент после `cls`.
- MCP tools используют `get_current_user_id()` из `mcp_server.context` — атлет **не может** передать чужой `user_id` через параметр tool'а.
- `list_facts` возвращает только факты текущего `user_id` (WHERE clause).
- Как и всё в проекте, threat T1 из `MULTI_TENANT_SECURITY.md` покрывается row-level tenant filtering.

**Аудит:** `save_fact` и `deactivate_fact` работают через обычный Sentry wrapper (`@sentry_tool`). Отдельный audit-log пока не заводим (см. MT-spec T7 — будет в Phase 4).

---

## 9. Phases

### Phase 1 — MVP (tool-based + undo-кнопка)

- [ ] Alembic миграция `user_facts` + индекс по активным (с `created_at DESC` для cap-eviction).
- [ ] `data/db/user_fact.py` — ORM модель + `save_with_cap(user_id, topic, fact, ...)` метод (append-with-cap, N=3 per topic).
- [ ] MCP tools: `save_fact` (с 300-char validator + delineation docstring), `list_facts`, `deactivate_fact` в `mcp_server/tools/`.
- [ ] `bot/prompts.py` — рендер блока facts + goal в system prompt.
- [ ] `bot/main.py` — `_UNDOABLE_TOOLS` registry + `handle_chat_message` читает `tool_calls_filter={"save_fact"}` из `ChatResult`, стэш `last_saved_fact_id` + `last_undo_message_id` в `user_data`, inline «🗑 Забудь это».
- [ ] Callback `fact_undo` — `pop("last_saved_fact_id")` + прямой MCP `deactivate_fact`, без Claude.
- [ ] TTL на undo-кнопку: `edit_message_reply_markup(None)` при следующем сообщении + `job_queue.run_once(_expire_undo_button, when=600)` fallback.
- [ ] `get_fact_metrics()` MCP tool — per-user, scoped через `get_current_user_id()` как все остальные tool'ы. См. §10.
- [ ] Unit-тест append-with-cap (4-й факт в topic → 1-й помечен `topic_cap`, не `superseded`).
- [ ] Unit-тест валидатор: `fact` >300 символов → tool error.
- [ ] Smoke-тест на owner: подкинуть факт в диалоге, тапнуть undo, убедиться что факт деактивирован; затем сохранить новый факт и проверить что на следующий день он в промпте; проверить что мусорная болтовня («устал сегодня») идёт в `save_mood_checkin`, а не в `save_fact`.

**Acceptance:** через неделю использования у owner'а есть ≥5 активных фактов, diff-проверка системного промпта показывает блок «Что я помню о тебе», Claude в диалоге цитирует хотя бы один факт без повторного ввода атлетом, undo-кнопка работает на 100% сохранений, `undo_tap_rate` <30% (иначе Claude слишком жадный — переписать docstring), `cache_hit_rate_chat` не упал после релиза.

### Phase 2 — Async extractor с batch-approval (условный)

Запускаем только если Phase 1 покажет, что Claude систематически забывает звать `save_fact` (см. §11.3 trigger + §10 метрики).

- [ ] Redis stream `user_facts_stream:{user_id}` — писатель в `ClaudeAgent.chat()`, LTRIM до последних 50 пар.
- [ ] Dramatiq actor `actor_extract_user_facts` — раз в 24ч на активного юзера.
- [ ] Extractor-промпт + JSON-schema ответа (`[{topic, fact, expires_at?, confidence}]`).
- [ ] Telegram-сообщение с превью + inline «✅ Все / ❌ Отбросить» (минимум для 2a).
- [ ] `context.user_data["pending_facts"]` — тот же паттерн, что `pending_workout` в `/workout`.
- [ ] Callback-хэндлеры `facts_approve_all` / `facts_dismiss` — прямой MCP batch-write без Claude.
- [ ] Дедуп: пропускать кандидата если активный факт с тем же `topic` имеет `confidence >= new.confidence`.
- [ ] Phase 2b (опционально): «⚙️ Выборочно» с per-item чекбоксами.

### Phase 3 — UX polish (опционально)

- Webapp `/settings` — страница «Мои факты», список + кнопка «Забыть».
- Bot command `/forget <id>` или `/memory` для просмотра.

---

## 10. Observability

Без метрик не ответим на главный вопрос: **работает ли Phase 1 достаточно хорошо, чтобы не запускать Phase 2**. Минимальный набор — собираем с первого дня Phase 1, смотрим раз в неделю.

### Метрики

| Метрика | Источник | Зачем |
|---|---|---|
| `facts_written_per_user_per_week` | count `user_facts` WHERE `source='tool'`, `created_at >= now()-7d` | Базовая активность записи. Цель: ≥1 факт/неделя на активного юзера. |
| `undo_tap_rate` | (count `deactivated_reason='user_request'` within 10min of `created_at`) / `facts_written` | Если >30% — Claude слишком жадный, нужно ужесточать docstring. |
| `topic_distribution` | `SELECT topic, count(*) FROM user_facts WHERE deactivated_at IS NULL GROUP BY topic` | Ловим разрастание синонимов (`food_preference` vs `nutrition`). >3 near-synonyms → пропишем enum. |
| `cap_evictions_per_week` | count `deactivated_reason='topic_cap'` | Высокий rate на одном topic → увеличить N с 3 до 5, или это спам от Claude. |
| `fact_citation_rate` ⚠ best-effort | heuristic: count сообщений, где в ответе Claude встречается substring любого активного факта / total chat messages | Proxy для «факты реально используются». **Подводные камни:** Claude парафразит («болит колено» → «травма ноги») — substring match промахнётся. Метрика low-signal, **не используется** для gating-решений (вроде «выключить facts-блок»), только как лёгкий sanity check. Альтернатива (структурный `cited_fact_ids` в ответе) меняет chat-protocol — откладываем. |
| `cache_hit_rate_chat` | `api_usage_daily.cache_read_tokens / input_tokens` по чат-вызовам | Хотим ≥70% на активных диалогах. Падение после релиза = facts-блок ломает кэш (порядок блоков кривой). |
| `tool_facts_per_100_msgs` | `facts_written / chat_messages * 100` за 30 дней, **только если `chat_messages >= 100`** | **Триггер Phase 2**: если ratio <3 при достаточной выборке — Claude не зовёт tool, пора включать extractor (см. §11.3). |

### Где хранить

Существующая таблица `api_usage_daily` подходит для `cache_hit_rate_chat` напрямую (уже есть cache-token колонки). Остальное — аггрегируем on-demand через SQL из `user_facts`, отдельная таблица не нужна.

### Где смотреть

MCP tool `get_fact_metrics()` — **per-user**, как все остальные tool'ы: `get_current_user_id()` из contextvars, атлет получает только свои метрики. Возвращает JSON со списком из таблицы выше. Атлет спрашивает в чате: «что ты запомнил за неделю?» — Claude зовёт `get_fact_metrics` + `list_facts` и рендерит сводку.

Все метрики (включая «триггер Phase 2») считаются per-user, не глобально: у одного атлета может быть `tool_facts_per_100_msgs = 5`, у другого `= 1` — extractor включим избирательно для тех, у кого тул-based подход не сработал. Dashboard-виджет в webapp — Phase 3, не раньше.

### Алерты

Не ставим. На одном активном юзере (owner) статистика шумная, alert-based decisions не оправданы. Пересматриваем после ≥2 активных атлетов.

---

## 11. Open questions

1. **Язык фактов.** Факт сохраняется на том языке, на котором атлет его произнёс (без нормализации). **При смене `/lang` переводы НЕ делаем** — ни в момент смены языка, ни при инъекции в промпт. Claude многоязычный и корректно цитирует русский факт в английском ответе. Это экономит лишние Claude-вызовы и защищает от дрейфа смысла при авто-переводе («болит ахилл» ≠ «Achilles tendon pain» в коннотации). Если через полгода увидим, что факты на родном языке мешают — добавим колонку `fact_language` + ручную миграцию по запросу юзера, не автомат.
2. **Ограничение количества.** Решено: per-topic cap N=3 (append-with-cap, §3) + global soft warning на >50. Hard cap не ставим.
3. **Phase 2 trigger.** Минимум данных для надёжного ratio: `total_chat_msgs_30d >= 100`. Если выполнено **И** `tool_facts_per_100_msgs < 3` — включаем extractor для этого юзера. При `msgs < 100` не триггерим вообще: ratio на малой выборке шумный (у тихого юзера 0/10 даст нулевой ratio, но проблемы нет — просто мало данных).
4. **Morning report inject.** Решено: Phase 1 не инжектим. Revisit после ≥2 недель данных.
5. **Sensitive topics injection.** Факты `topic=health`/`family` могут быть чувствительными. Инжектим их наравне с остальными (иначе теряется смысл памяти), но не логируем тело факта в Sentry breadcrumbs — только `topic` + `fact_id`. См. §12.

---

## 12. Security review checklist

Перед merge PR Phase 1 прогнать `/security-review` на:

- `data/db/user_fact.py` — tenant filtering в каждом `@classmethod`.
- `mcp_server/tools/save_fact.py` и смежные — использование `get_current_user_id()`, без user_id в параметрах.
- `bot/prompts.py` — факты текущего пользователя подтягиваются по его `user_id`, не кэшируются between-users.
- Sentry data scrubbing: `fact` body не попадает в breadcrumbs / event extra (см. §11.5), только `topic` и `fact_id`.
- Unit-тест: пользователь A не видит факты пользователя B через MCP.
