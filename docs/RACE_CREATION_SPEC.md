# Race Creation Spec

> Создание будущих гонок (`RACE_A/B/C`) через Telegram-чат: юзер просит бота
> добавить старт, Claude собирает недостающие поля, делает dry-run preview,
> по кнопке «Отправить в Intervals» — push в `intervals.icu/events` +
> локальная запись в `athlete_goals`.

**Related:**

| Spec / code | Связь |
|---|---|
| `bot/prompts.py:SYSTEM_PROMPT_CHAT` | Добавится секция `## Race creation` с правилами ведения диалога |
| `bot/main.py` — `_PREVIEWABLE_TOOLS` + `pending_workout` | Паттерн copy-paste для `pending_race` |
| `mcp_server/tools/ai_workouts.py:suggest_workout` | Референс dry-run / push-flag логики |
| `mcp_server/tools/races.py:tag_race` | Пост-гонка (уже есть); не путать с `suggest_race` для будущих |
| `tasks/actors/athlets.py:actor_sync_athlete_goals` | Читает RACE_* events из Intervals в `athlete_goals` (каждые 30 мин) |
| `data/db/athlete.py:AthleteGoal` | Целевая таблица, `intervals_event_id` — дедуп |
| `data/intervals/dto.py:EventExDTO` | Payload для `POST /athlete/{id}/events` |

---

## 1. Мотивация

Сейчас гонки в `athlete_goals` появляются **только** через синк из Intervals.icu — юзер должен руками создать Event в их UI с категорией RACE_A/B/C, потом подождать до 30 минут пока `scheduler_sync_goals_job` подхватит. Это:

1. **Барьер входа.** Новый атлет заходит в бота, нажимает `/dashboard` → «Race Goal» пусто → не понимает что ему нужно сначала сходить в Intervals.icu.
2. **Нет `ctl_target` в Intervals.** Peak-CTL к race day — наша внутренняя метрика, задать её можно только через Python shell (`cli shell`), пользователю недоступно.
3. **Нет контекста у Claude.** Бот знает даты гонок, но не знает поверхности, погоды, целевого времени, дистанции — всё это могло бы улучшать suggest_workout.

Хотим: юзер пишет «добавь мне Drina Trail 3 мая, RACE_A, трейл 17 км, хочу CTL 55 к старту» → бот собирает недостающее, показывает preview, по кнопке уходит в Intervals + в нашу БД.

---

## 2. Scope

### Phase 1 (MVP) — делаем сейчас

- **MCP tool** `suggest_race` (§4) с `dry_run` flag по образцу `suggest_workout`.
- **Preview/confirm flow** через `context.user_data["pending_race"]` (§5) —
  handler пушит напрямую без повторного inference, как в `workout_push`.
- **Prompt rules** в `SYSTEM_PROMPT_CHAT` (§6) — инструктируем Claude собирать
  обязательные поля перед `suggest_race`.
- **Идемпотентность** по `(user_id, category, event_date)` — повторный запрос на
  ту же дату и приоритет обновляет, а не создаёт дубль (§4.3).
- **Запись в обе стороны**: Intervals.icu event + локальный `athlete_goals` в
  одной транзакции tool'а (§4.4). Не ждём scheduler'а.
- **Settings UI — ручное редактирование CTL-полей** (§10). `ctl_target` и
  `per_sport_targets` хранятся только у нас, синком не перезаписываются — значит
  логично дать inline-edit прямо в карточке «Race Goal» на `/settings`.

### Phase 2 — опционально, по спросу

- `/race` ConversationHandler с явным мастером (сейчас только свободный чат).
- Редактирование **даты/имени/категории** из Settings (сейчас только через чат,
  т.к. требует push в Intervals).
- Удаление: `delete_race_goal(category)` — также убирает event из Intervals.
- Ссылка из `/settings` «Race Goal» → кнопка «Добавить / изменить» → кидает в чат с пре-заполненным промптом.

### Non-goals

- Создание тренировочных планов (не за этот квартал).
- Import из trka.rs и других сайтов — отдельная спека.
- Post-race tagging — покрывается существующим `tag_race`.

---

## 3. User Flow

### 3.1. Happy path (свободный чат)

```
User:  добавь Drina Trail 3 мая, трейл 17 км, хочу CTL 55 к старту, приоритет A

Bot (после вызовов `get_athlete_profile`, `suggest_race(dry_run=True)`):
  🏁 Preview: Drina Trail — RACE_A
  📅 Дата: 2026-05-03 (через 13 дней)
  🏃 Вид: TrailRun, 17 km
  🎯 Peak CTL к старту: 55 (сейчас 21 → требует +34 за 13 дней → слишком агрессивно,
      реалистично 28–30)
  📝 Notes: —

  [ ✅ Отправить в Intervals ]  [ ❌ Отмена ]

User tap «✅ Отправить в Intervals»

Bot:
  ✅ Гонка создана в Intervals.icu и в вашем профиле.
  Event ID: 105038593. Цель CTL 55 сохранена в athlete_goals.
```

### 3.2. Edit path (уже есть RACE_A)

```
User:  перенеси мой RACE_A на 10 мая

Bot (вызов `get_races` → `suggest_race(category=RACE_A, date=2026-05-10, dry_run=True)`):
  ♻️ Update preview: Drina Trail — RACE_A
  📅 Было: 2026-05-03 → Станет: 2026-05-10
  (остальные поля без изменений)

  [ ✅ Обновить в Intervals ]  [ ❌ Отмена ]
```

### 3.3. Disambiguation (вопросов больше одного)

```
User:  добавь Ironman 70.3

Bot:
  Нужно ещё пару деталей:
  • Дата старта?
  • Приоритет: A, B или C? (A = основная цель сезона, B = проходная, C = проверка формы)
  • Peak CTL target? (опционально; сейчас 21, реалистично +5…+10 к race day если остаётся 8+ недель)
```

Никаких tool-вызовов пока не хватает required-полей (см. §6).

---

## 4. MCP tool: `suggest_race`

### 4.1. Signature

```python
@mcp.tool()
@sentry_tool
async def suggest_race(
    name: str,
    category: Literal["RACE_A", "RACE_B", "RACE_C"],
    date: str,                       # ISO, "2026-05-03"
    sport: str = "",                 # Run / Ride / Swim / TrailRun / Triathlon
    distance_m: float | None = None,
    description: str = "",           # freeform notes — surface/weather/goal time
    ctl_target: float | None = None, # peak CTL on race day; validated vs current CTL
    dry_run: bool = False,
) -> str:
    """Create or update a future race event in Intervals.icu (RACE_A/B/C)
    and mirror it into athlete_goals. Dry-run returns a preview string; the
    bot replays the exact same input with dry_run=False on user confirmation.
    """
```

### 4.2. Validation

- `category` — только RACE_A/B/C. Для просто «important training» — используй обычный calendar event (не этот tool).
- `date` — парсится как ISO, должна быть **≥ today** (гонки в прошлом размечаются через `tag_race`, не этим).
- `ctl_target` — если задан, компонуем «sanity hint» в preview (`current_ctl → target`, ramp rate, недели до гонки); **не отклоняем**, но показываем риск в rationale. Intervals `ramp_rate > 7 TSS/week` = flag.
- `sport` — если пусто, дефолт подсказываем по `user.primary_sport` из `athlete_settings` (как делает `suggest_workout`).

### 4.3. Идемпотентность

**Уникальность по `intervals_event_id`** — один атлет может иметь **несколько** RACE_A / RACE_B / RACE_C в сезоне (типичный кейс: две A-гонки на год, Ironman 70.3 в сентябре + Oceanlava в октябре; подтверждено реальными данными user 1 на 2026-04-21). Dedupe ключ — `(user_id, intervals_event_id)`, одна строка на event. `category` — не часть уникальности.

**Что делает `suggest_race` при ambiguous запросе** («перенеси мой RACE_A» при двух RACE_A):
- `get_by_category` возвращает **ближайшую предстоящую** гонку (ORDER BY event_date ASC, WHERE event_date >= today). Это совпадает с дефолтным пользовательским ожиданием: «RACE_A» → ближайшая, не далёкая.
- При >1 upcoming — INFO-лог «N upcoming %s rows, picking nearest».
- Для явного targeting'а конкретной гонки из нескольких — Claude должен передать `intervals_event_id` напрямую (Phase 2 расширение signature, см. §2 и §17 open questions). В MVP `suggest_race` принимает `category` → берём ближайшую.

Логика lookup в `suggest_race(dry_run=False)`:

1. **Local first:** `existing_goal = AthleteGoal.get_by_category(user_id, category)` — возвращает ближайшую upcoming по этой категории.
2. Если `existing_goal` есть → `existing_intervals_id = existing_goal.intervals_event_id`.
3. Если `existing_goal` нет → сверить с Intervals через `get_events(oldest=date, newest=date, category=category)` — перестраховка от случая когда локальная запись потерялась, но event в Intervals остался.
4. Итого: `existing_intervals_id is not None` → `update_event` (обновит существующую ближайшую), иначе → `create_event` (создаст **вторую** A/B/C, если на разную дату).

**`actor_sync_athlete_goals`** (`tasks/actors/athlets.py:95`) — итерируется по **всем** events из Intervals в каждой категории, не только `events[0]`. Каждый event с новым `intervals_event_id` триггерит отдельный `_actor_send_goal_notification`.

### 4.4. Атомарная запись

```python
if dry_run:
    return _format_preview(...)

# 0) Lookup существующей записи (см. §4.3)
existing_goal = await AthleteGoal.get_by_category(user_id, category)
existing_intervals_id = existing_goal.intervals_event_id if existing_goal else None

# 1) Push в Intervals
async with IntervalsAsyncClient.for_user(user_id) as client:
    payload = EventExDTO(
        category=category,
        type=sport or None,
        name=name,
        start_date_local=f"{date}T00:00:00",
        description=description or None,
        distance=distance_m,
    )
    if existing_intervals_id:
        result = await client.update_event(existing_intervals_id, payload)
    else:
        result = await client.create_event(payload)

# 2) Локально в athlete_goals
goal = await AthleteGoal.upsert_from_intervals(
    user_id=user_id,
    category=category,
    event_name=name,
    event_date=date_obj,
    intervals_event_id=result.id,
)

# 3) ctl_target — отдельным update (upsert_from_intervals его не трогает,
#    см. data/db/athlete.py:236 "Does NOT overwrite CTL targets")
if ctl_target is not None:
    await AthleteGoal.set_ctl_target(goal.id, ctl_target)

return _format_success(...)
```

**Failure recovery.** Порядок «Intervals → local DB» не транзакционный. Если шаг 1 успел, шаг 2 упал (DB transient error) — event есть в Intervals, нет локально. Компенсирующий `delete_event` **не делаем** — вместо этого полагаемся на idempotency из §4.3:
- Следующий `suggest_race` с теми же параметрами: `get_by_category` вернёт None (локально не записано), но fallback из §4.3 шаг 3 найдёт event в Intervals → `existing_intervals_id` подхватится → `update_event` будет no-op (те же значения) + локальный upsert запишет запись. Консистентность восстановлена без действий юзера.
- Если юзер не retry'ит — orphan event в Intervals, но это не портит данные у нас (`athlete_goals` просто не знает о нём; scheduler-sync подхватит в следующий 30-мин тик).

### 4.5. Return shape

`dry_run=True` → многострочная markdown-совместимая строка (см. §3.1 Preview).

`dry_run=False` → короткое подтверждение с event_id + ссылкой на Intervals:
```
✅ RACE_A создана: Drina Trail — 2026-05-03.
Peak CTL target: 55.
Event: https://intervals.icu/event/105038593
```

Ошибки Intervals (422 duplicate, 401 auth) → `Error pushing: {e}` — bot handler показывает юзеру как есть.

---

## 5. Bot-side state: `pending_race`

Копируем паттерн `pending_workout` из `bot/main.py:654-692`.

### 5.1. Новый элемент `_PREVIEWABLE_TOOLS`

```python
def _suggest_race_is_preview(inp: dict) -> bool:
    return inp.get("dry_run") is True

def _suggest_race_apply_push(inp: dict) -> None:
    inp["dry_run"] = False

_PREVIEWABLE_TOOLS: dict[str, PreviewableTool] = {
    "suggest_workout": PreviewableTool(_suggest_workout_is_preview, _suggest_workout_apply_push),
    "compose_workout": PreviewableTool(_compose_workout_is_preview, _compose_workout_apply_push),
    "suggest_race":    PreviewableTool(_suggest_race_is_preview,    _suggest_race_apply_push),  # NEW
}
```

### 5.2. Extractor

`_extract_pending_workout` переименовать в `_extract_pending_preview` (универсальный — уже скан по `_PREVIEWABLE_TOOLS`, меняется только ключ `context.user_data`). Или параллельный `_extract_pending_race` с тем же reverse-scan, но фильтрующий только `suggest_race`.

Предпочтительно: **один универсальный extractor**, но разные ключи в user_data (`pending_workout` vs `pending_race`) — потому что handler разный (`workout_push` vs `race_push`) и UI кнопок тоже разный. В extractor добавить параметр `tool_filter: set[str]`.

### 5.3. Confirm handler `race_push`

Точная копия `workout_push` (`bot/main.py:830`) с двумя отличиями:
- Pop из `"pending_race"`.
- Кнопка `callback_data="race_push"` (регистрируется в `ConversationHandler.fallbacks` или как standalone `CallbackQueryHandler` — см. §5.5).

### 5.4. Ключи в `user_data`

| Ключ | Тип | Живёт | Кто ставит | Кто снимает |
|---|---|---|---|---|
| `pending_race` | `dict \| None` | до confirm / cancel / новой генерации | `handle_chat_message`, `handle_photo_message` после `agent.chat(...)` | `race_push` (pop), `/cancel`, следующий chat без `suggest_race` |

Такой же TTL-by-replacement, как `pending_workout` — каждый новый turn вытесняет старый черновик.

### 5.5. Интеграция в `handle_chat_message`

Сейчас `handle_chat_message` вызывает `agent.chat(..., tool_calls_filter=set())` — фильтр пустой, т.е. tool_calls **не** сохраняются в результат. Надо поменять на `set(_PREVIEWABLE_TOOLS.keys())` чтобы ловить `suggest_race` в свободном чате (сейчас там только workout'ы через `/workout`, но race — именно free-form).

После `agent.chat`:
```python
context.user_data["pending_race"] = _extract_pending_preview(
    result.tool_calls, tool_filter={"suggest_race"}
)
if context.user_data["pending_race"]:
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить в Intervals", callback_data="race_push")],
        [InlineKeyboardButton("❌ Отмена", callback_data="race_cancel")],
    ])
```

**Ограничение:** если в одном turn'е Claude сгенерил и workout, и race (маловероятно, но возможно) — сохраняем оба `pending_*` и показываем **две** группы кнопок. Extractor работает per-tool.

---

## 6. Prompt rules (`SYSTEM_PROMPT_CHAT`)

Добавить секцию **после** `## Workout generation`:

```
## Race creation

If the athlete wants to add a future race (triggers: «добавь гонку», «создай старт»,
«запиши Ironman», «race A на X мая»), use `suggest_race`. Required fields the athlete
must provide (or you must confirm via clarifying questions, not via tool-guessing):

  • name (e.g. "Drina Trail", "Ironman 70.3 Hvar")
  • category: A / B / C (priority). If ambiguous, ask — don't default.
  • date (ISO). Relative phrasings like «через 2 недели» must be resolved to a concrete
    date using today's date from the system prompt.

Optional but high-value — ASK ONCE if plausible from context:

  • sport (Run / Ride / Swim / TrailRun / Triathlon) — usually derivable from race name.
  • distance_m — surface the distance if the athlete said it out loud.
  • ctl_target — peak CTL on race day. If the athlete names a number, pass it through.
    If not, don't invent one. Mention the current CTL and realistic ramp-rate (~5 TSS/week)
    in the preview so the athlete can decide.
  • description — surface (trail/asphalt), weather expectations, goal time — in 1-2 sentences.

Flow — always 2-step:
  1. Call `suggest_race(..., dry_run=True)` to produce a preview the user will see.
  2. The bot shows a "Send to Intervals" button. If the user confirms, the bot pushes
     the same payload with dry_run=False WITHOUT asking you again. Do NOT call
     `suggest_race(dry_run=False)` yourself — the confirm button is the athlete's consent.

Do NOT call `tag_race` for future races — it's for marking past activities as races.

If the athlete asks to DELETE a future race («удали RACE_A», «отмени гонку»), explain
that deletion is not yet automated in the bot — they can remove the event from the
Intervals.icu calendar directly; our local `athlete_goals` record will be cleared on
the next 30-min sync. Do NOT try to work around this with `suggest_race` — there's no
"archive" category.
```

### 6.1. Prompt caching

Раздел небольшой (~25 строк) и **статический** — не зависит от per-user данных. Значит уходит в **первый** `cache_control` сегмент (static_prompt) из двухсегментной схемы, описанной в [`USER_CONTEXT_SPEC.md` §6](USER_CONTEXT_SPEC.md). Тот сегмент кэшируется «вечно» (до правки самого шаблона), так что добавление race-правил не требует дополнительных маркеров и не влияет на инвалидацию dynamic_tail при `save_fact` / смене goal.

**Зависимость по merge-порядку:**
- Если `USER_CONTEXT_SPEC` мёржится первым — `get_static_system_prompt()` уже существует, race-секция добавляется внутрь этой константы.
- Если наоборот — race-секция сначала живёт в монолитном `SYSTEM_PROMPT_CHAT`, при мердже user-context-spec'а переезжает в static-часть вместе со всем остальным текстом. Нулевая переделка, переименование переменной.

---

## 7. Data model

### 7.1. Без миграций

`athlete_goals` уже содержит всё что нужно (`category`, `event_name`, `event_date`, `sport_type`, `ctl_target`, `intervals_event_id`). `upsert_from_intervals` — точка записи. Нужны два новых хелпера:

```python
# data/db/athlete.py
@classmethod
@dual
def get_by_category(
    cls, user_id: int, category: str, *, session: Session
) -> AthleteGoal | None:
    """Return the nearest-upcoming active goal for (user_id, category), or None.

    `(user_id, category)` is NOT unique — athletes may have two A-races per
    season. We return the soonest upcoming race as the default "which one does
    suggest_race target on bare command" — see §4.3 for the disambiguation
    policy.

    Filters: is_active=True, event_date >= today. Ordered by event_date ASC.
    """
    today = date.today()
    rows = session.execute(
        select(cls).where(
            cls.user_id == user_id,
            cls.category == category,
            cls.is_active.is_(True),
            cls.event_date >= today,
        ).order_by(cls.event_date.asc())
    ).scalars().all()
    return rows[0] if rows else None


@classmethod
@dual
def set_ctl_target(cls, goal_id: int, ctl_target: float, *, session: Session) -> None:
    session.execute(
        update(cls).where(cls.id == goal_id).values(ctl_target=ctl_target)
    )
    session.commit()
```

`update_local_fields` (для Settings UI PATCH) определяется в §10.3 ниже. Итого новых ORM-методов: **три** (`get_by_category`, `set_ctl_target`, `update_local_fields`).

(`upsert_from_intervals` явно **не** трогает ctl_target чтобы синк не стирал руками-поставленную цель — см. docstring на 236. Мы следуем тому же контракту: ctl_target задаётся отдельным шагом.)

### 7.2. Intervals.icu event payload

Существующий `EventExDTO` покрывает всё. Вызов:

```python
EventExDTO(
    category="RACE_A",          # RACE_A/B/C — see docs/intervals_icu_openapi.json
    type="TrailRun",            # или Run/Ride/Swim/Triathlon
    name="Drina Trail",
    start_date_local="2026-05-03T00:00:00",
    description="17 km trail, target 2h30m",
    distance=17000.0,           # meters; Intervals.icu принимает в метрах для событий
)
```

Поле `ctl_target` в Intervals **не существует** — там нет такой концепции на events. Хранится только у нас.

---

## 8. Edge cases

| Сценарий | Поведение |
|---|---|
| Юзер просит RACE_A, у него уже есть одна RACE_A (рядом по времени) | `get_by_category` возвращает её → `update_event` по existing `intervals_event_id` (§4.3). Preview явно показывает «было X, станет Y». |
| Юзер просит RACE_A, у него уже есть **несколько** RACE_A (реальный кейс user 1: Ironman 70.3 сен + Oceanlava окт) | `get_by_category` возвращает **ближайшую upcoming** (§4.3). Preview показывает «обновляю X (не Y)» — юзер видит target. Если нужна дальняя — уточняет с `name` или явным `intervals_event_id` (Phase 2 signature extension). INFO-лог про неоднозначность в actor'е. |
| Юзер создаёт **вторую** RACE_A на **новую** дату (напр. «добавь Ironman Nice 2027-06-20» при существующей Ironman 70.3 2026-09-15) | Локально `get_by_category` **не** вернёт Nice (далеко, но upcoming ближе 70.3) → fallback `get_events(category, newest=date)` не найдёт по дате → `create_event` создаст новый. В БД появится вторая RACE_A строка. **Это фича, не баг.** |
| Дата в прошлом | Tool отклоняет: `Error: race date {date} is in the past — use tag_race to log past races`. |
| CTL target нереалистичный (ramp > 10 TSS/week) | Preview показывает yellow warning, не блокирует. |
| OAuth нет / 401 от Intervals | `Error pushing: unauthorized — reconnect Intervals in /settings`. |
| Юзер два раза жмёт «Отправить» | `context.user_data.pop("pending_race", None)` в handler'е = consume-on-read (как в `workout_push:844`) — второй tap даёт «Не нашёл черновик». |
| `suggest_race` вызвана без preview (Claude проигнорировал правило §6) | Не блокируем: инференс сделал `dry_run=False` напрямую → event реально создастся. Это bypass confirm-flow, но юзер всё равно в явном диалоге, не в фоне. Метрика `race_skipped_preview` для мониторинга (§11). |

---

## 9. Multi-tenant / security

- `suggest_race` читает `user_id` из `get_current_user_id()` (contextvars, MCP middleware). **Нельзя** принимать `user_id` параметром — см. `docs/MULTI_TENANT_SECURITY.md` T1.
- `IntervalsAsyncClient.for_user(user_id)` использует per-user `access_token_encrypted` / `api_key_encrypted` — push идёт в собственный Intervals аккаунт атлета.
- Preview-текст содержит `event_name` / `description` от атлета → безопасно для Markdown (а значит и для HTML через `markdown_to_telegram_html`), но handler'у на стороне бота ничего экранировать не нужно: сам tool возвращает plain text, и `workout_push` / `race_push` шлют без `parse_mode` (`bot/main.py:881-884` — уже запротоколировано).

---

## 10. Settings UI — manual CTL edit

**Зачем отдельный путь помимо чата.** CTL target и per-sport CTL split — это
«локальный overlay» над Intervals (см. §7). Синк их не трогает, а юзер может
захотеть подправить число без запуска диалога с Claude («скорректировал план —
теперь peak CTL 60, а не 55»). Также если гонка была создана **до** появления
чат-флоу (через `cli sync-settings`), её `ctl_target=None`, и до сих пор
единственный способ выставить CTL был `python -m cli shell`.

### 10.1. Что редактируется

| Поле | Тип | Источник | Редактируется из Settings? |
|---|---|---|---|
| `event_name` | string | Intervals | ❌ — только через чат (пушит в Intervals) |
| `event_date` | date | Intervals | ❌ — только через чат |
| `category` | RACE_A/B/C | Intervals | ❌ — только через чат |
| `ctl_target` | float | **локально** | ✅ **да** |
| `per_sport_targets` | `{swim,ride,run}` | **локально** | ✅ **да** |

Только local-only поля. Всё что синхронизируется с Intervals — менять нельзя
напрямую из UI, иначе следующий sync перезапишет (а писать обратно в Intervals
здесь — это full-blown edit-race flow, он в Phase 2).

### 10.2. API endpoint

```
PATCH /api/athlete/goal/{goal_id}
body:   { "ctl_target"?: number | null, "per_sport_targets"?: {"swim"?: number, "ride"?: number, "run"?: number} | null }
auth:   require_athlete (не demo)
response: { "goal_id": int, "ctl_target": number | null, "per_sport_targets": {...} | null }
```

- `require_athlete` блокирует demo-юзеров (`api/deps.py` — существующая зависимость).
- Проверка ownership: `goal.user_id == current_user.id` — 404 если чужая цель
  (не 403, чтобы не подтверждать факт существования чужого goal_id; см.
  `docs/MULTI_TENANT_SECURITY.md` T1).
- `null` явно очищает поле. Пустой body → 400.
- Никакого push в Intervals. Никакого re-sync. Меняются только две колонки.

### 10.3. ORM

Дополнить хелперы из §7.1:

```python
# data/db/athlete.py
@classmethod
@dual
def update_local_fields(
    cls,
    goal_id: int,
    *,
    user_id: int,             # ownership check
    ctl_target: float | None = _UNSET,
    per_sport_targets: dict | None = _UNSET,
    session: Session,
) -> AthleteGoal | None:
    """Patch the local-only overlay fields. Skip attributes passed as _UNSET so
    the caller can clear a field to None without accidentally clearing others.
    Returns None if goal not found or owned by another user.
    """
```

Sentinel `_UNSET` — классический способ отличить «не передано» от «передано None» (тот же приём в `sqlalchemy.ext.Mutable` и pydantic). Без него PATCH превращается в PUT.

### 10.4. Frontend (`webapp/src/pages/Settings.tsx`)

Сейчас карточка «Race Goal» (`Settings.tsx:272-285`) — read-only `<Row>`s.
Меняем `ctl_target` и `per_sport_targets.*` на inline-редактируемый input:

- Клик на значение → `<input type="number" min=0 max=200 step=1>`.
- Debounce 500ms → `PATCH /api/athlete/goal/{goal_id}` с изменённым полем.
- Optimistic update локального state, rollback если 4xx/5xx.
- На ошибке — toast «Не удалось сохранить CTL», значение возвращается.
- Пустое значение submit → PATCH с `null` → поле очищается.

UX-деталь: `ctl_target` и `per_sport_targets.swim/ride/run` логически связаны
(сумма split должна быть ≤ общий target). Валидацию **не** делаем в MVP — юзер
сам знает что пишет, а проверка только раздражает. Если войдёт в привычку — в
Phase 2 подсвечивать несогласованность warning'ом.

### 10.5. i18n

Новые строки:
- `webapp/src/i18n/ru.json` + `en.json`:
  - `settings.goal.ctl_edit_hint` — "нажмите чтобы изменить" / "click to edit"
  - `settings.goal.save_failed` — ошибка сохранения.

### 10.6. Тесты

**Backend** (`tests/api/test_athlete_goal.py`, новый):
- PATCH с валидным body → 200 + обновлённая строка.
- PATCH с `"ctl_target": null` → поле реально становится None.
- PATCH чужой goal_id → 404.
- PATCH без body / с пустым body → 400.
- PATCH от demo-юзера → 403 (через `require_athlete`).

**Frontend** (`webapp/src/pages/__tests__/Settings.test.tsx` — если есть тест-инфра;
иначе — ручной smoke):
- Клик по CTL → появляется input с текущим значением.
- Успешный PATCH → значение обновляется inplace.
- Failed PATCH → показан toast, значение возвращается.

### 10.7. Sync-устойчивость

Финальный sanity check: `actor_sync_athlete_goals` крутится каждые 30 мин
(`bot/scheduler.py:70-73`). После PATCH от UI юзер закрывает вкладку, в следующий
синк вызывается `AthleteGoal.upsert_from_intervals(...)`. Текущая реализация на
`data/db/athlete.py:245-251` обновляет только `event_name`/`event_date`/`category`/`synced_at` —
`ctl_target` и `per_sport_targets` остаются нетронутыми. UI-правка переживает
синк. Регресс-тест это фиксирует (`tests/tasks/test_athlete_actors.py` или
новый) — **обязательно добавить**, это load-bearing инвариант.

---

## 11. Observability

Метрики через Sentry breadcrumbs (как `@sentry_tool` на других MCP tool'ах):
- `race_created` — category, sport, days_to_race, ctl_target.
- `race_updated` — diff полей.
- `race_skipped_preview` — dry_run=False без предшествующего dry_run=True (indicator промпт-drift).
- `race_push_no_draft` — handler вызван, а `pending_race` пуст (indicator двойного tap).
- `race_push_intervals_ok_db_fail` — Intervals succeeded, local upsert fell through. Спарсенный из exception chain внутри tool'а. Важно для мониторинга recovery-пути из §4.4.

Лог в Sentry не фильтруем по `INTERVALS_WEBHOOK_MONITORING` — это write-path, критично.

---

## 12. Testing

### Unit (`tests/mcp/test_races.py`)

- `suggest_race(dry_run=True)` → возвращает preview-строку, **не** делает HTTP-вызовов.
- `suggest_race(dry_run=False)` c mock `IntervalsAsyncClient` → вызов `create_event` с правильным `EventExDTO`.
- Идемпотентность — **ключ `(user_id, category)`** (см. §4.3):
  - Повторный вызов с тем же `category` и **той же** датой → `update_event`, не `create_event`.
  - Повторный вызов с тем же `category` и **новой** датой (перенос RACE_A) → тоже `update_event`, существующая запись в `athlete_goals` обновляется (не создаётся вторая RACE_A).
- Recovery (§4.4 Failure recovery): первый вызов mock'ает Intervals OK + DB failure на upsert; второй вызов (retry) — `get_by_category` возвращает None, но Intervals fallback находит event → `update_event` no-op + DB upsert проходит.
- `ctl_target` задан → `AthleteGoal.set_ctl_target` вызван; не задан → не вызван.
- Past date → возвращает error без HTTP-вызова.

### Bot integration (`tests/bot/test_race_flow.py`)

- `handle_chat_message` + mock agent возвращает `tool_calls=[{"name":"suggest_race","input":{..., "dry_run": True}}]` → `pending_race` попадает в `user_data`, кнопка «Отправить в Intervals» рендерится.
- `race_push` callback → `pending_race` pop'ается, MCP tool вызывается с `dry_run=False`.
- Повторный tap «Отправить» → «Не нашёл черновик», без второго MCP-вызова.
- `/cancel` чистит `pending_race`.

### E2E (manual, за owner user_id=1)

1. В чате: «добавь RACE_B на 2026-10-12 марафон 42 км».
2. Проверить preview.
3. Tap «Отправить».
4. Проверить Intervals.icu calendar — event появился с `category=RACE_B`, `type=Run`.
5. `poetry run python -c "from data.db import AthleteGoal; [print(g.__dict__) for g in AthleteGoal.get_all(1)]"` — запись с `ctl_target=None` (не задавали).
6. В чате: «поставь CTL target 85 на RACE_B».
7. Должен сработать через `update_race_goal` (Phase 2) или через повторный `suggest_race` с `ctl_target=85` (MVP).
8. В `/settings` на карточке Race Goal — кликнуть по `ctl_target`, ввести 70, проверить что бэкенд принял и значение пережило следующий sync-тик.

---

## 13. Implementation order

1. **MCP tool** `mcp_server/tools/race_creation.py` — `suggest_race(dry_run=...)`. Регистрация в `mcp_server/tools/__init__.py`.
2. **ORM хелперы** в `data/db/athlete.py`: `get_by_category`, `set_ctl_target`, `update_local_fields` (см. §7.1, §10.3).
3. **Prompt** — добавить секцию `## Race creation` в `bot/prompts.py:SYSTEM_PROMPT_CHAT`. Согласовать с `USER_CONTEXT_SPEC §6` (static-сегмент двухмаркерного кэша) — см. §6.1.
4. **Bot handler**:
   - Расширить `_PREVIEWABLE_TOOLS` новым `suggest_race`.
   - Универсализировать `_extract_pending_preview(tool_calls, tool_filter)`.
   - Новый `race_push` / `race_cancel` callback handler (copy-paste из `workout_push`).
   - Поменять `handle_chat_message` / `handle_photo_message`: `tool_calls_filter=set(_PREVIEWABLE_TOOLS.keys())`; после `agent.chat` вытащить `pending_race`.
   - Зарегистрировать `CallbackQueryHandler(race_push, pattern=r"^race_push$")` в `setup_handlers`.
5. **Tests** — unit + bot integration (включая recovery-кейс из §4.4).
6. **Docs** — обновить `CLAUDE.md` секцию «Bot Commands» (упомянуть свободно-чатовый race creation) и `BUSINESS_RULES.md` (race priority semantics).
7. **Settings UI edit** (§10) — backend PATCH endpoint + inline inputs на `/settings`.

---

## 14. Open questions

- **Должны ли мы требовать `goal_time_sec` на preview?** Пока хранится внутри `description` (freeform). Если станет полезным для Claude при `suggest_workout` — вынесем в отдельный параметр в Phase 2.
- **`disciplines` для триатлона.** §4.1 signature не содержит `disciplines` — поле **не** выставляется наружу, а **автогенерится внутри tool'а** из `sport`: `Triathlon` → `["Swim","Ride","Run"]`, `Duathlon` → `["Run","Ride","Run"]`, иначе — не заполняется. Claude этот параметр не видит и не спрашивает. Если будущие нестандарты потребуют override — добавим параметр и обновим §4.1.
- ~~**Ошибка push, локальная запись не создалась** — нужен ли compensating delete?~~ Решено в §4.4: нет, consistency восстанавливается idempotency-retry'ем.
- **Targeting конкретной гонки при multiple same-category (Phase 2).** Сейчас `get_by_category` возвращает ближайшую upcoming. Для явного targeting'а дальней — Claude должен уметь передать `intervals_event_id` или `name` как disambiguator. Варианты: (a) расширить `suggest_race` signature новым опциональным `event_id: int | None = None`; (b) добавить `suggest_race_update(event_id, ...)` отдельный tool; (c) оставить как есть, юзер перетаскивает дальнюю руками в Intervals UI. **Предлагаю (a):** если `event_id` задан — используем его напрямую, иначе текущий лукап по category. Решать когда реально понадобится (у user 1 две RACE_A — пока обходим через name matching в prompt'е).
- ~~**«Одна RACE_A на юзера» как инвариант»**~~ Отменено. БД не enforce'ит (нет UNIQUE constraint на `(user_id, category)`), реальные атлеты имеют 2+ гонок той же priority за сезон, `actor_sync_athlete_goals` после bugfix 2026-04-21 корректно пишет все events. Смотри §4.3 и §8 edge cases.
