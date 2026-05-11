# User Context / Memory Spec

> Долговременная память о пользователе для Telegram-бота: бот запоминает факты (травмы, предпочтения, рабочий график, семейные обстоятельства) и подмешивает их в системный промпт Claude, чтобы советы и диалог учитывали контекст.

**Status:** Phase 1 (MVP, tool-based + undo) ✅ shipped (commit `cf624ba`). Phase 2 (async extractor) deferred — gate `tool_facts_per_100_msgs_30d < 3` ∧ `chat_msgs ≥ 100`. Phase 3 (UX polish) — optional.

**Code anchors:**

| Concern | File |
|---|---|
| Migration | `migrations/versions/b8d1c4e7f0a3_add_user_facts.py` |
| ORM + `save_with_cap` | `data/db/user_fact.py` |
| MCP tools | `mcp_server/tools/user_facts.py` (`save_fact` / `list_facts` / `deactivate_fact` / `reactivate_fact` / `get_fact_metrics`) |
| Static prompt + render | `bot/prompts.py:get_static_system_prompt`, `render_athlete_block`, `_facts_block` |
| Two-segment cache | `bot/agent.py:_run_tool_use_loop` (system: list[dict] с двумя `cache_control`) |
| Undo registry | `bot/main.py:_UNDOABLE_TOOLS`, `_extract_pending_undoable`, `fact_undo` callback |
| Tool filter inclusion | `bot/tool_filter.py` (tracking group, ALWAYS_INCLUDE) |

**Related:**

| Issue / Spec | Связь |
|---|---|
| `docs/MULTI_TENANT_SECURITY_SPEC.md` | T1 (tenant data leak) — факты per-user, FK на `users.id` |
| `docs/ADAPTIVE_TRAINING_PLAN_SPEC.md` | Personal patterns (Phase 3) — отдельный слой, не пересекается |

---

## 1. Мотивация

Сейчас чат stateless: Claude каждое сообщение начинает «с чистого листа» + reply-context. Если атлет пишет «опять колено болит» — бот не знает, что неделю назад обсуждали ту же жалобу, и начинает диалог заново. Цели из `athlete_goals` подгружаются через MCP resource `athlete://goal`, но остальной контекст (травмы, работа, ограничения по времени, стиль тренировок, семья) нигде не хранится.

`data/db/mood_checkins.py` — не подходит: это структурированные 1–5 шкалы, а нужны свободно-текстовые факты с темой.

---

## 2. Scope

### Phase 1 (MVP) — ✅ shipped

- Таблица `user_facts` + ORM (append-with-cap, N=3 default / N=5 для `injury` и `health`).
- MCP tools: `save_fact`, `list_facts`, `deactivate_fact`, `reactivate_fact`, `get_fact_metrics`.
- Инъекция активных фактов + цели в системный промпт с двумя cache-control сегментами (§5, §6).
- Undo-кнопка «🗑 Забудь это» / «↩️ Вернуть» после `save_fact` / `deactivate_fact` (TTL 10 мин).
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

Schema живёт в миграции `b8d1c4e7f0a3` + ORM `data/db/user_fact.py:UserFact`. Ключевые колонки и инварианты:

- `topic VARCHAR(64)` — enum-like, свободный список (см. §7). НЕ слот: в одном topic может быть до **N** активных фактов.
- `fact VARCHAR(300)` — прозаический текст до 300 символов, один факт = одна мысль. Пишем от лица атлета: «болит правое колено после забега на 10K 12 апреля», а не «у пользователя травма». Cap валидируется в MCP tool + БД-constraint (double защита).
- `fact_language VARCHAR(5)` — BCP-47 code (`"ru"` / `"en"` / `"sr"` / …). Заполняется из `user.language` на момент save. Не используется для рендера в Phase 1 (см. §11.1), но даёт pivot-опцию для Phase 3 без ручной LLM-миграции задним числом. Nullable — старые extractor-факты без языка не ломают invariants.
- `source VARCHAR(16)` — `tool` (Claude вызвал `save_fact`) / `extractor` (Phase 2) / `user` (Settings UI, Phase 3).
- `expires_at` — опционально. Пример: «жена беременна, срок октябрь» → `expires_at='2026-10-31'`.
- `deactivated_at` / `deactivated_reason` — audit trail вытеснений, физически не удаляем. `reason ∈ {topic_cap, hard_cap, user_request, expired, contradicted}`. Отдельная колонка `superseded_by` НЕ заводилась — cap-chain расследования rare, replacement в том же topic находится через `created_at DESC`.

Partial index `ix_user_facts_active(user_id, topic, created_at DESC) WHERE deactivated_at IS NULL` — активных фактов десятки максимум, индекс компактный + cap-вытеснение работает index-only.

### Конфликт-резолюшен: append-with-cap (не upsert-by-topic)

Изначально план был «один активный факт на topic», но это теряло контекст: «болит ахилл» стирал бы «болит колено», хотя травмы разные. Правильная семантика:

1. **Append** — новый факт всегда добавляется активным.
2. **Per-topic cap** — после вставки, если у `(user_id, topic)` стало `>N` активных — самый старый по `created_at` помечается `deactivated_at=now()`, `deactivated_reason='topic_cap'`. `N` зависит от topic:

   ```python
   TOPIC_CAPS = {
       "injury": 5,   # triathlete carries multiple chronic issues at once
       "health": 5,   # asthma + allergies + meds — not mutually exclusive
       # default:
       "*": 3,        # one topic = one slot, most categories don't need more
   }
   ```

   Плоский N=3 выбивал бы валидные медицинские факты у атлетов с несколькими одновременными травмами. Dict централизует тюнинг — меняется одним местом, без миграции.

3. **Global hard cap (= 200)** — safety net против model drift. Если суммарно активных `>200`, `save_fact` **автоматически** деактивирует самые старые до порога с `deactivated_reason='hard_cap'`, **после** per-topic cap. Не полагаемся на Claude'у понимать warning-строку в tool response.
4. **Global soft warning (>50)** — в tool response приходит `warning` string, Claude обычно реагирует и сам зовёт `deactivate_fact` на устаревшее. Дополнение к hard cap, не замена.

Для семантических дублей в рамках одного topic («болит колено» + «колено болит уже неделю») — опираемся на модель: при явной дублирующей фразе должна звать `deactivate_fact` на старую вместо создания новой. В Phase 2 extractor дедуп через prompt, не embedding.

### Race на append-with-cap

Два параллельных `save_fact` на один `(user_id, topic)` (один юзер, несколько MCP-клиентов — бот + webapp Phase 3 + extractor) могут оба увидеть активных=N, оба вставить, оба деактивировать «самого старого» — выбьют 2 факта вместо 1.

Фикс: весь append-with-cap делается в одной транзакции с `SELECT id FROM user_facts WHERE user_id = ? AND topic = ? AND deactivated_at IS NULL FOR UPDATE` перед INSERT. Блокирует параллельных writer'ов в пределах одной ORM-транзакции. PG advisory lock тоже сработал бы, но `FOR UPDATE` идёт через существующий SQLAlchemy workflow без отдельного lock-release protocol.

---

## 4. Writers

### Phase 1 — MCP tool `save_fact` (✅ shipped)

Claude сам решает, что сохранить, вызывая tool во время диалога. Сигнатура: `save_fact(topic: str, fact: str, expires_at: str | None = None) -> dict`. Полный docstring живёт в `mcp_server/tools/user_facts.py` — задаёт критерии «save vs save_mood_checkin», topic canon, формат `fact` (first-person, ≤300 chars), guidance вызывать `list_facts` перед save при подозрении на duplicate. Returns `{"fact_id": int, "evicted_id": int | None, "warning": str | None}`.

**Семантика** — append-with-cap (см. §3), всё в одной транзакции с `SELECT … FOR UPDATE`:

1. Валидация: `len(fact) <= 300`, `topic` не пустой. Иначе — tool error (Claude увидит, перепишет).
2. Lock активных фактов `(user_id, topic)` через `SELECT … FOR UPDATE`.
3. Insert новый факт → `source='tool'`, `fact_language=user.language`.
4. Если после вставки у `(user_id, topic)` активных `>TOPIC_CAPS[topic]` — деактивировать самые старые по `created_at` с `reason='topic_cap'`. Вернуть их ids как `evicted_ids` (plural — теоретически >1 при extractor batch write'е).
5. Global hard cap: если total active всё ещё `>200` — деактивировать глобально самые старые до 200, `reason='hard_cap'`.
6. Если total active `>50` (но ещё не hard cap) — добавить `warning` в response.
7. Commit транзакции.

`save_fact` мапит `ValueError` в `{"error": ...}` чтобы Claude видел и переписал.

### Phase 1 — `list_facts` / `deactivate_fact` / `reactivate_fact` (✅ shipped)

```python
async def list_facts(include_inactive: bool = False) -> list[dict]
async def deactivate_fact(fact_id: int, reason: str = "user_request") -> dict
async def reactivate_fact(fact_id: int) -> dict   # not in docstrings, only via undo
```

Нужны чтобы Claude мог ответить «что ты обо мне помнишь?» и явно забыть факт по просьбе атлета. `deactivate_fact` — reversible через тот же undo registry; защита от галлюцинации модели, которая может деактивировать валидный факт по неверному толкованию фразы.

`reactivate_fact` — thin MCP tool, `UPDATE … SET deactivated_at=NULL, deactivated_reason=NULL WHERE id=? AND user_id=?` (tenant-guard обязателен). НЕ торчит в docstring'ах для модели — вызывается только из undo callback'а.

### Phase 1 — Undo-кнопка после `save_fact` / `deactivate_fact` (✅ shipped)

Полный preview-confirm на каждое сохранение ломает разговорный UX (каждое сообщение превращается в анкету). Вместо этого — **save-then-undo**, симметрично для обеих мутаций:

1. Tool коммитит сразу в tool-use-loop. `save_fact` пишет `source='tool'`, `deactivate_fact` ставит `deactivated_at=now()`.
2. `handle_chat_message` зовёт `agent.chat(..., tool_calls_filter={"save_fact", "deactivate_fact"})` и получает `ChatResult.tool_calls`.
3. Если среди tool_calls есть любой из них — handler читает `fact_id` из `tool_call.result`, кладёт в `context.user_data` и **добавляет к ответному сообщению** inline-кнопку:
   - `save_fact` → «🗑 Забудь это» (undo = `deactivate_fact`)
   - `deactivate_fact` → «↩️ Вернуть» (undo = `reactivate_fact`)
4. Callback `fact_undo`: pop stash из `user_data` + прямой `MCPClient.call_tool(undo_tool, undo_args)` без повторной Claude-инференции.

**Симметричная защита для `deactivate_fact`:** если Claude галлюцинирует и деактивирует валидный факт, пользователь узнает об этом дни спустя. Асимметрия (save защищён, deactivate нет) создаёт silent data-loss. Один registry покрывает оба случая.

**Не полный preview-confirm:** запись/снятие факта — внутреннее состояние, soft-delete одним тапом. В отличие от `/workout`, где push в Intervals — side effect в чужую систему и prompt-injection критичен. Здесь низкие ставки, UX-стоимость полного preview не оправдана.

**Consume-on-read:** `pop` чтобы повторный тап со старого сообщения не вызвал undo повторно на уже применённый id.

**TTL на кнопку.** Inline undo не должна висеть на старом сообщении вечно — через неделю юзер случайно тапнет и потеряет факт. Две меры:

1. **При следующем chat-сообщении** — `handle_chat_message` читает `context.user_data.pop("last_undo_message_id", None)`; если есть — `bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)` на предыдущее. Клавиатура исчезает, stash тоже очищается. Основной путь.
2. **Тайм-аут 10 минут** (fallback) — `context.job_queue.run_once(_expire_undo_button, when=600, ...)`. Job делает то же `edit_message_reply_markup(None)`. Покрывает «юзер ушёл и не написал до утра». Job проверяет `user_data[_LAST_UNDO_MSG_ID_KEY] == message_id` перед очисткой stash (защита от ротации при раннем save-then-save).

В обеих мерах сама запись в БД не меняется — undo только закрывается. Явная отмена через Settings (Phase 3) по-прежнему возможна.

**Registry:** `_UNDOABLE_TOOLS: dict[str, UndoableTool]` в `bot/main.py` — отдельная структура от `_PREVIEWABLE_TOOLS` (preview-фазы нет, только пост-коммит-undo). Готовая почва для расширения: если появятся другие «committed with undo» tool'ы — регистрируются сюда.

**Edge case — `save_fact` внутри `/workout` flow.** Workout-handler'ы (`workout_sport_chosen` / `workout_dialog_text`) расширены до `tool_calls_filter = set(_PREVIEWABLE_TOOLS) | _UNDOABLE_TOOL_NAMES` — стоимость один лишний deep-copy `save_fact` input'а (≤300 char + topic), приемлемо. Handler рисует две группы кнопок если обе секции заполнены: основной `[✅ Отправить в Intervals] [❌ Отмена]` + undo от fact (3-я строка).

### Phase 2 — async post-chat extractor с batch-approval

> Не реализовано. Дизайн ниже описывает целевой флоу для триггера из §11.3.

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
4. «⚙️ Выборочно» — вторая клавиатура с per-item чекбоксами (можно начать без неё в Phase 2a, добавить в 2b).
5. Consume-on-read: `pop("pending_facts")` при любом финальном действии.

**Timing.** Cron в локальной TZ юзера (`users.timezone` или fallback `TIMEZONE=Europe/Belgrade`), окно `hour=18..19` — атлет обычно free + evening report уже ушёл. Не в 3 утра. Scheduler читает per-user TZ как `tasks/scheduler.py` для morning report.

**Concurrent pending.** Если cron видит, что `user_data["pending_facts"]` уже непустой (предыдущий батч без ответа) — **skip** текущий запуск. Юзер отреагирует → флаг очистится → следующий cron подхватит. Объединять батчи не стоит: смешанные свежие + вчерашние кандидаты ломают UX «я подметил в недавних разговорах». Если pending висит >48h — автоматически dismiss через job_queue (см. TTL на undo в §4), чтобы не блокировать пайплайн.

**Persistence caveat.** PTB `context.user_data` — in-memory, не переживает рестарт бота. `context.job_queue.run_once(...)` тоже in-memory. При деплое:
- `pending_facts` очистятся автоматически — это ок (данных в БД нет, только драфт).
- 48h dismiss-таймер потеряется — pending мог бы зависнуть, но cron (раз в 24ч) сам упрётся в skip; для уборки достаточно `app.post_init = lambda app: app.bot_data.clear_stale_pending_facts()` сканит `user_data` всех юзеров и выкидывает батчи старше 48h. Необязательно для MVP.

`last_mutated_fact_id` (undo для Phase 1) тоже in-memory и теряется при рестарте — приемлемо, undo-окно TTL 10 мин, факт в БД в коммитнутом состоянии.

**Почему здесь preview-confirm оправдан** (в отличие от Phase 1 `save_fact`):
- Extractor может галлюцинировать — пользователь **должен** увидеть, что сохраняется.
- Batch-операция: один тап сохраняет 3 факта, а не три отдельных undo-кнопки.
- Нет разговорного контекста в момент извлечения — юзер не писал только что, прерывать нечего, сообщение приходит само.

**Риски extractor'а:** false positives, стоимость (отдельный Claude-pass на каждого активного юзера). Phase 2 включаем только если Phase 1 tool-based подход покажет пропуски (см. §11.3 trigger + §10 метрики).

**Референс-реализация:** `bot/main.py:_PREVIEWABLE_TOOLS`, `_extract_pending_workout`, `_apply_push_flag`, `workout_push` — паттерн один-в-один, только вместо `dry_run` флага — список id'ов фактов для батч-записи.

---

## 5. Reader — инъекция в промпт

### Где

`bot/prompts.py` разбит на два строительных блока:

- `get_static_system_prompt() -> str` — публичный аксессор приватной `_STATIC_PROMPT_CHAT` (cache segment #1).
- `render_athlete_block(user_id, language, *, include_facts=True) -> str` — динамический хвост: athlete profile + goal + (опционально) facts. **Единственный reader** активных фактов в codebase'е.

`get_system_prompt_chat(user)` остаётся public API бота и склеивает их. Morning report / evening report / любой другой код, которому в будущем захочется подмешать факты, импортирует `render_athlete_block` напрямую — не дублирует SQL в `tasks/actors/reports.py`.

### Как

```
[ static_prompt                       ─ cache_control #1 ]  ← вечный кэш
[ render_athlete_block(user, ...)     ─ cache_control #2 ]  ← тухнет при save_fact / goal update
     │
     ├── athlete profile (sports, TZ)
     ├── goal block       (из athlete://goal)
     └── facts block      (активные user_facts)   ← only if include_facts
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

(EN-локализованный заголовок — `## What I remember about you`, выбирается по `user.language`.)

**Правила:**

- Сортировка по `topic` → внутри по `created_at` DESC. Стабильный порядок → кэш не ломается.
- Инжектим **все активные** факты (не top-N). На десятках фактов это ≪10% промпта, а стабильность кэша важнее.
- Если у атлета ноль активных фактов — блок не рендерим вообще (экономим токены + не нужен negative prompt «ты ещё ничего не знаешь»).

### Morning report

Для `tasks/actors/reports.py` (morning report через `MCPTool`) — **Phase 1 не инжектим** (`include_facts=False`). Morning report — это аналитика данных, а не диалог; факты релевантнее чату. Но `render_athlete_block` уже единая точка, опт-ин тривиален — один флаг в вызове, без рефакторинга SQL. Расширим если придёт feedback от атлетов.

---

## 6. Prompt caching — стратегия

`bot/agent.py` ставит **два** `cache_control: ephemeral` маркера на system prompt:

```python
static_prompt = get_static_system_prompt()           # _STATIC_PROMPT_CHAT (~780 tok)
dynamic_tail  = render_athlete_block(user)           # goal + facts + профиль (~240 tok)

cached_system = [
    {"type": "text", "text": static_prompt, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": dynamic_tail,  "cache_control": {"type": "ephemeral"}},
]
```

**Почему одного маркера мало.** Anthropic prompt cache работает как **префиксный хэш**: хэш считается от начала до маркера. Если в конце блока меняется хоть один символ (добавили факт → блок `## Что я помню о тебе` переписался) — хэш префикса до маркера другой → **cache miss на весь system prompt**, включая статику. Текущая схема работает пока facts/goal не меняются — но как только делаем `save_fact` в диалоге, следующее сообщение читало бы всё с нуля.

**Поведение с двумя маркерами:**

- `static_prompt` хэшируется до маркера #1 → кэш **вечный** относительно изменений facts (пока не правим сам шаблон промпта).
- `dynamic_tail` — свой кэш, тухнет только при `save_fact` / `deactivate_fact` / смене `goal`.
- `save_fact` инвалидирует ровно хвост (~240 tok); статика (~780 tok) остаётся горячей.

**Эффект:**

- TTL ephemeral 5 мин → при активном диалоге кэш попадает на каждой tool-use-loop итерации и в следующих сообщениях.
- Cache read ≈10% от input tokens. На чате с tool-use (3–5 итераций) + частыми сохранениями фактов экономия **кратная** — без двух маркеров весь system prompt инвалидировался бы на каждом save.

**Порядок внутри `dynamic_tail`:** athlete profile (редко) → goal (реже) → facts (чаще). После второго маркера порядок значения не имеет — всё одним хэшем. Оставляем читабельный порядок.

**Лимит — 4 cache_control маркера на запрос**, у нас 2 — запас есть на будущие сегменты.

---

## 7. Topic taxonomy

Свободный список, но с «каноническими» значениями для consistency. В docstring `save_fact` перечислен канон, Claude стремится выбирать оттуда:

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
- ORM-методы через `@dual` + `@with_session`, `user_id` — первый аргумент после `cls`.
- MCP tools используют `get_current_user_id()` из `mcp_server.context` — атлет НЕ может передать чужой `user_id` через параметр tool'а.
- `list_facts` / `reactivate_fact` возвращают/мутируют только факты текущего `user_id`.
- Threat T1 из `MULTI_TENANT_SECURITY_SPEC.md` покрывается row-level tenant filtering.

`save_fact` и `deactivate_fact` работают через обычный Sentry wrapper (`@sentry_tool`). Отдельный audit-log пока не заводим — будет в MT-spec Phase 4.

---

## 9. Phases

### Phase 1 — MVP (tool-based + undo) — ✅ shipped (commit `cf624ba`)

См. code anchors в шапке. 28 acceptance items закрыты unit-тестами (`tests/data/test_user_facts.py`, `tests/bot/test_undo_button.py`, `tests/bot/test_personal_patterns_block.py`). Coverage:
- Append-with-cap (per-topic `injury=5` / `health=5` / `default=3`, hard-cap 200)
- Concurrent two-writer race (`asyncio.gather`) → cap honored, eviction reasons correct
- Cross-tenant guard на `reactivate_fact`
- Boundary: 300 chars accepted, 301 rejected; пустые `topic`/`fact` rejected
- Renderer возвращает только свои факты, no-facts → блок не рендерится, заголовок локализован
- Undo TTL: next-message ротация + 10-мин fallback job

[ ] **Smoke-тест на owner** (требует живой деплой): подкинуть факт в диалоге → тапнуть undo → факт деактивирован; сохранить новый → на следующий день он в промпте; Claude деактивирует по ошибке → тап «↩️ Вернуть» возвращает; мусорная болтовня «устал сегодня» идёт в `save_mood_checkin_tool`, не `save_fact`.

**Acceptance:** через неделю использования у owner'а есть ≥5 активных фактов; diff системного промпта показывает блок «Что я помню о тебе»; Claude в диалоге цитирует хотя бы один факт без повторного ввода; undo-кнопка работает на 100% мутаций; `undo_tap_rate` <30% (иначе Claude слишком жадный — переписать docstring); `cache_hit_rate_chat` не упал после релиза.

### Phase 2 — Async extractor с batch-approval (условный)

Запускаем только если Phase 1 покажет, что Claude систематически забывает звать `save_fact` (см. §11.3 trigger + §10 метрики).

- [ ] Alembic миграция: добавить колонку `confidence REAL NOT NULL DEFAULT 1.0` (отложили из Phase 1). Значения <1.0 пишет только extractor.
- [ ] Redis stream `user_facts_stream:{user_id}` — писатель в `ClaudeAgent.chat()`, LTRIM до последних 50 пар.
- [ ] Dramatiq actor `actor_extract_user_facts` — раз в 24ч на активного юзера.
- [ ] Extractor-промпт + JSON-schema ответа (`[{topic, fact, expires_at?, confidence}]`).
- [ ] Telegram-сообщение с превью + inline «✅ Все / ❌ Отбросить» (минимум для 2a).
- [ ] `context.user_data["pending_facts"]` — тот же паттерн, что `pending_workout` в `/workout`.
- [ ] Callback-хэндлеры `facts_approve_all` / `facts_dismiss` — прямой MCP batch-write без Claude.
- [ ] Дедуп: пропускать кандидата если активный факт с тем же `topic` имеет `confidence >= new.confidence`.
- [ ] `clear_stale_pending_facts` bot-init handler — очищать pending_facts старше 48h на старте после рестарта (§4).
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

1. **Язык фактов.** Факт сохраняется на том языке, на котором атлет его произнёс (без нормализации). **При смене `/lang` переводы НЕ делаем** — ни в момент смены языка, ни при инъекции в промпт. Claude многоязычный и корректно цитирует русский факт в английском ответе. Это экономит лишние Claude-вызовы и защищает от дрейфа смысла при авто-переводе («болит ахилл» ≠ «Achilles tendon pain» в коннотации). **Колонка `fact_language` заводится сразу** (§3) — заполняется `user.language` на момент save, не используется для рендера в Phase 1, но держит открытой опцию нормализации/перевода/группировки в Settings UI (Phase 3) без ручной LLM-миграции существующих фактов.
2. **Ограничение количества.** Решено: per-topic cap через dict `TOPIC_CAPS` (injury=5, health=5, default=3) + global hard cap 200 (автоматический trimming) + soft warning на >50 (§3).
3. **Phase 2 trigger.** Минимум данных для надёжного ratio: `total_chat_msgs_30d >= 100`. Если выполнено **И** `tool_facts_per_100_msgs < 3` — включаем extractor для этого юзера. При `msgs < 100` не триггерим вообще: ratio на малой выборке шумный (у тихого юзера 0/10 даст нулевой ratio, но проблемы нет — просто мало данных).
4. **Morning report inject.** Решено: Phase 1 не инжектим (`render_athlete_block(user, include_facts=False)`). Revisit после ≥2 недель данных.
5. **Sensitive topics injection.** Факты `topic=health`/`family` могут быть чувствительными. Инжектим их наравне с остальными (иначе теряется смысл памяти), но не логируем тело факта в Sentry breadcrumbs — только `topic` + `fact_id`. См. §12.
6. **Anthropic retention.** Факты из блока «Что я помню о тебе» уходят как часть system prompt'а в Anthropic API. Они **retain'ятся** согласно zero-retention policy (по умолчанию 30 дней на abuse monitoring, zero-retention для Tier 3 / enterprise agreements). Для `health`/`family` это означает: чувствительный текст на стороне провайдера до 30 дней. Trade-off осознанный — self-hosted LLM сравнимого качества стоит 10×+ и недоступен в проектом бюджете. Документируем в `docs/MULTI_TENANT_SECURITY_SPEC.md` при первой multi-tenant enrollment (issue T6).

---

## 12. Security review checklist

Перед merge PR Phase 1 прогнать `/security-review` на:

- `data/db/user_fact.py` — tenant filtering в каждом `@classmethod`, в том числе в `reactivate_fact` (особая точка — обходит обычную «save→active» семантику, легко случайно забыть tenant guard).
- `mcp_server/tools/user_facts.py` — использование `get_current_user_id()`, без `user_id` в параметрах.
- `bot/prompts.py:render_athlete_block` — факты текущего пользователя подтягиваются по его `user_id`, не кэшируются between-users; единая точка reader'а (§5), chance на кросс-tenant leak сосредоточен в одной функции.
- Sentry data scrubbing: `fact` body не попадает в breadcrumbs / event extra (см. §11.5), только `topic` и `fact_id`.
- Unit-тест: пользователь A не видит факты пользователя B через MCP (`list_facts`, `reactivate_fact`).
- Anthropic retention (§11.6) — убедиться что `health` / `family` факты документированы в `MULTI_TENANT_SECURITY_SPEC.md` как acknowledged 30-day vendor retention (не surprise для аудита).
