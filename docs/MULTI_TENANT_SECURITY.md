# Multi-Tenant Security Specification

> Threat model, data isolation, auth/authz, API security, secrets management — всё что нужно до перехода на multi-tenant.

**Related issues:**

| Issue | Title                                                          | Status           | Phase                                                                |
| ----- | -------------------------------------------------------------- | ---------------- | -------------------------------------------------------------------- |
| #41   | Design security agent before multi-tenant                      | open, needs-spec | This spec                                                            |
| #15   | Design and implement multi-tenant architecture                 | open             | All phases                                                           |
| #34   | Add multi-user table to store bot users                        | **done**         | Phase 1                                                              |
| #49   | Add unique indexes for multi-tenant data isolation             | **done**         | Phase 1                                                              |
| #48   | Add created_by field to GitHub issues in multi-tenant context  | open, security   | Phase 2                                                              |
| #47   | Define manual onboarding process for new athlete               | open, needs-spec | Phase 5                                                              |
| #37   | Integrate Redis for caching and session storage                | open             | Phase 3 (rate limiting)                                              |
| #3    | Job endpoints: add status tracking for background job failures | open             | Phase 4 (observability)                                              |
| #40   | Integrate Sentry for error monitoring                          | open             | Phase 4 (observability)                                              |
| #50   | Add i18n support for multi-language interface                  | open, needs-spec | Phase 5 (per-user `language` in users table)                         |
| #51   | Switch LLM model for Telegram chat                             | open             | Phase 5 (per-user `preferred_model` in users table, API key routing) |
| #132  | Реализовать web login через Telegram                           | **done**         | Threat T12 (Telegram Login Widget)                                   |

---

## 1. Current State (Single-Tenant)

### Что есть сейчас

| Компонент                | Реализация                                                                                   | Файл                              |
| ------------------------ | -------------------------------------------------------------------------------------------- | --------------------------------- |
| Auth — Telegram initData | HMAC-SHA256 верификация                                                                      | `api/deps.py`                     |
| Auth — JWT (desktop)     | Кастомный HS256 JWT, 7-day expiry                                                            | `api/auth.py`                     |
| Auth — MCP               | Статический Bearer token (`MCP_AUTH_TOKEN`)                                                  | `api/server.py` MCPAuthMiddleware |
| Roles                    | 3 роли: anonymous / viewer / owner                                                           | `api/deps.py`                     |
| Owner detection          | `user.id == TELEGRAM_CHAT_ID` (hardcoded)                                                    | `api/deps.py`, `bot/main.py`      |
| Rate limiting            | Verify-code: 5 attempts / 5 min per IP                                                       | `api/auth.py`                     |
| Bot access               | `/morning` — owner only, остальное — все                                                     | `bot/main.py`                     |
| Secrets                  | `pydantic SecretStr` + `.env`                                                                | `config.py`                       |
| DB                       | **Phase 1 done:** `users` table + `user_id` FK на 13 таблицах, callers hardcoded `user_id=1` | `data/database.py`                |
| Crypto                   | Fernet encryption для per-user secrets                                                       | `data/crypto.py`                  |

### Ключевые уязвимости для multi-tenant (оставшиеся)

1. ~~**Нет `user_id` / `tenant_id`**~~ — **РЕШЕНО:** user_id на всех таблицах, CRUD обновлены. Callers пока hardcoded `user_id=1`
2. **Один набор credentials** — `INTERVALS_API_KEY`, `ANTHROPIC_API_KEY` и т.д. хардкодятся в `.env`. Per-user `api_key_encrypted` готов в users table, но не подключен
3. **Кастомный JWT** — самописный HS256 без стандартных claims (iss, aud), без ротации ключей
4. **In-memory state** — `_pending_codes`, `_verify_attempts` теряются при рестарте (частично решено Redis #37)
5. **MCP auth** — один статический токен на всех, не привязан к пользователю
6. **Bot handler** — проверяет `TELEGRAM_CHAT_ID` в handler-ах, а не middleware
7. **Нет audit log** — нет записи кто что делал

---

## 2. Threat Model

### Actors

| Actor                   | Описание                                   | Текущий доступ                         |
| ----------------------- | ------------------------------------------ | -------------------------------------- |
| **Owner**               | Владелец инстанса (ты)                     | Полный доступ ко всему                 |
| **Viewer**              | Друг с Telegram, share link                | Read-only через webapp                 |
| **Anonymous**           | Случайный человек                          | Только лендинг                         |
| **Attacker — external** | Атакует API endpoints                      | Rate limiting на verify-code           |
| **Attacker — tenant**   | Другой пользователь системы (multi-tenant) | **Новый actor — не существует сейчас** |

### Threats (STRIDE-lite)

#### T1. Tenant Data Leak (Spoofing + Information Disclosure)

- **Что:** Пользователь A видит HRV/wellness/workouts пользователя B
- **Где:** Все CRUD функции в `database.py` — `WellnessRow.get()`, `get_activities()`, etc.
- **Severity:** Critical
- **Mitigation:** Row-level tenant_id filtering (см. раздел 3)

#### T2. Cross-Tenant Intervals.icu Access (Elevation of Privilege)

- **Что:** Пользователь A триггерит sync, который пишет данные пользователя B
- **Где:** `IntervalsClient` использует глобальный `INTERVALS_API_KEY` + `INTERVALS_ATHLETE_ID`
- **Severity:** Critical
- **Mitigation:** Per-tenant credentials store (см. раздел 4)

#### T3. JWT Token Reuse Across Tenants (Spoofing)

- **Что:** JWT содержит только `sub: chat_id`, нет tenant scope
- **Где:** `api/auth.py` — `create_jwt()` / `verify_jwt()`
- **Severity:** High
- **Mitigation:** Добавить `tenant_id` + `scope` claims

#### T4. MCP Token Sharing (Elevation of Privilege)

- **Что:** Один `MCP_AUTH_TOKEN` на всех — кто знает токен, видит всё
- **Где:** `api/server.py` MCPAuthMiddleware
- **Severity:** High
- **Mitigation:** Per-tenant MCP tokens или JWT-based MCP auth

#### T5. Bot Command Injection (Tampering)

- **Что:** Чужой Telegram user отправляет /morning → видит чужие данные
- **Где:** `bot/main.py` — проверка `TELEGRAM_CHAT_ID` только в отдельных handlers
- **Severity:** Medium (сейчас single-tenant, будет Critical в multi-tenant)
- **Mitigation:** Bot middleware для tenant resolution по chat_id

#### T6. Secrets in Environment (Information Disclosure)

- **Что:** Все API ключи в одном `.env`, один утёкший ключ = доступ ко всему
- **Где:** `config.py`
- **Severity:** Medium
- **Mitigation:** Secrets vault (HashiCorp Vault / AWS Secrets Manager) или encrypted per-tenant store в DB

#### T7. No Audit Trail (Repudiation)

- **Что:** Нет записи кто что делал — кто триггернул sync, кто создал workout
- **Где:** Все endpoints
- **Severity:** Medium
- **Mitigation:** Audit log table + structured logging

#### T8. AI Prompt Injection via Shared Context (Tampering)

- **Что:** В multi-tenant AI agent может подмешать данные другого tenant в prompt
- **Где:** `ai/claude_agent.py` — `analyze_morning()`, `chat()`
- **Severity:** High
- **Mitigation:** Strict tenant-scoped data loading, никогда не смешивать tenant data в одном prompt

#### T9. Denial of Service — Resource Exhaustion (Availability)

- **Что:** Один пользователь спамит sync jobs / AI chat / MCP tools, исчерпывая ресурсы для всех
- **Где:** `bot/scheduler.py`, `api/routes.py`, `mcp_server/`
- **Severity:** Medium (single-tenant), High (multi-tenant)
- **Mitigation:** Per-tenant rate limits, job queuing, AI quota enforcement (см. разделы 6.1, 8.3)

#### T10. Background Job Cross-Tenant Pollution (Information Disclosure)

- **Что:** Scheduler job обрабатывает всех пользователей и случайно смешивает данные
- **Где:** `bot/scheduler.py` — cron jobs (sync wellness, workouts, activities, DFA)
- **Severity:** High
- **Mitigation:** Per-tenant job execution с tenant_id context, job isolation (см. раздел 6.5)

#### T11. initData Replay Attack (Spoofing)

- **Что:** Перехваченный Telegram initData переиспользуется спустя дни — нет проверки `auth_date` freshness
- **Где:** `api/deps.py` — `_verify_and_parse_init_data()`
- **Severity:** Medium
- **Mitigation:** Проверять `auth_date` < 15 минут, reject stale initData. 15 мин а не 5 — Mini App может быть открыто до первого API-вызова дольше 5 минут

#### T12. Telegram Login Widget Replay / Forgery (Spoofing)

- **Что:** Подделка или реплей payload от Telegram Login Widget → выпуск JWT на чужого юзера
- **Где:** `api/auth.py:verify_telegram_widget_auth()`, эндпоинт `POST /api/auth/telegram-widget`
- **Severity:** High (auth path, ведёт к выпуску сессионного JWT)
- **Mitigation (реализовано):**
  - HMAC-SHA256 над data-check-string (все поля кроме `hash`, sorted by key, `\n`-разделители), secret_key = `SHA256(bot_token)` — по спеке https://core.telegram.org/widgets/login
  - Constant-time сравнение (`hmac.compare_digest`)
  - Replay window: `auth_date` старше 24ч → reject (лимит самой спеки Telegram)
  - Clock skew: `auth_date` в будущем >60с → reject
  - Пустой `TELEGRAM_BOT_TOKEN` → reject (никакой fallback на статический секрет)
  - **Auto-provisioning с default role `viewer`**: если юзера нет → создаём row в `users` с `chat_id`, `username`, `display_name` из Telegram payload. Это тот же паттерн, что у бота в `/start` (`bot/main.py:start`). Upgrade до `athlete` (с `athlete_id`, `api_key`, и т.д.) остаётся ручным через `cli shell` — никакого auto-promote на основе HMAC-подписи. То есть widget открывает только read-only доступ viewer'а к общим данным
- **Operator setup:** `/setdomain` в `@BotFather` → `bot.endurai.me` (иначе виджет не рендерится). `TELEGRAM_BOT_USERNAME` в `.env` для фронта через `GET /api/auth/telegram-widget-config`
- **Тесты:** `tests/api/test_telegram_widget_auth.py` — 18 кейсов, включая valid/tampered/missing-fields/stale/future/wrong-token/empty-token/null-optional/extra-fields-signed-through

#### T14. Silent Re-Activation of Blocked Users (Tampering / Availability)

- **Что:** Пользователь блокирует бота в Telegram → `users.is_active=False` (см. `bot/main.py:handle_my_chat_member` и 403-fallback в `tasks/tools.py:TelegramTool.send_message`). Но у заблокированного юзера может оставаться открытая вкладка Mini App или старая ссылка с валидным `initData`. Если auth-путь автоматически реактивирует юзера при первом же API-запросе, scheduler снова начнёт дёргать Telegram API, ловить 403, снова выключать — пинг-понг, лишние Sentry-события, лишние Telegram calls.
- **Где:** `data/db/user.py:get_or_create_from_telegram()`, вызывается из `api/deps.py:get_current_user` (initData), `api/routers/auth.py` (Login Widget) и `bot/main.py:start` (`/start` handler)
- **Severity:** Medium (availability degradation + auth policy leak, не ведёт к data disclosure)
- **Mitigation (реализовано):**
  - `get_or_create_from_telegram` ищет юзера через `get_by_chat_id(..., include_inactive=True)` — предотвращает `IntegrityError` на UNIQUE `chat_id` если row существует как `is_active=False`. Но **реактивацию не делает**: возвращает юзера "как есть", auth-код дальше сам решает что с ним.
  - Реактивация (`set_active_by_chat_id(..., True)`) происходит **только** в двух явных сигналах re-engagement:
    1. `bot/main.py:start` — юзер явно пишет `/start`
    2. `bot/main.py:handle_my_chat_member` — Telegram прислал `my_chat_member` с `status=MEMBER`
  - Webapp auth (initData) и Login Widget **не реактивируют** — они читают `is_active` через существующий фильтр в `get_by_chat_id(chat_id)` (без `include_inactive`), т.е. заблокированный юзер для webapp просто "не найден" и получает anonymous flow.
- **Семантика `users.is_active`:** флаг перегружен двумя смыслами — "админ-деактивация через CLI" ∪ "Telegram-канал недоступен". Оба состояния = полная потеря доступа (webapp, MCP, рассылки), потому что auth-запросы (`get_by_mcp_token`, `get_by_chat_id`) фильтруют по `is_active=True`. Это **намеренное решение**: блокировка бота = явный сигнал "не хочу пользоваться сервисом", и даёт юзеру единую kill-switch.
- **Граничный случай:** юзер блокирует бота, но webapp-вкладка открыта. Первый API-запрос вернёт 401 (юзер не найден через `get_by_chat_id`). Frontend должен показать баннер "переподключите бота через /start" (TODO в `webapp/src/auth/`). Пока баннер не реализован — юзер увидит generic "требуется авторизация".

#### T13. RPE Callback Cross-Tenant Write (Tampering)

- **Что:** Подделка `callback_data` вида `rpe:{activity_id}:{value}` → запись RPE на чужую activity
- **Где:** `bot/main.py:handle_rpe_callback()`, register через `CallbackQueryHandler(pattern=r"^rpe:")`
- **Severity:** Low (callback_data приходит от Telegram-клиента, который авторизован как bot chat; forward/share inline-button между юзерами ограничен Telegram'ом; единственный реалистичный вектор — юзер руками собирает callback через API bot token'а, но тогда он уже контролирует свой собственный chat)
- **Mitigation (реализовано):**
  - `@athlete_required` декоратор резолвит `User` из `chat_id`
  - Input validation: `parts = query.data.split(":"); len(parts) != 3` → silent ack; `int(raw_value)` в try/except; `1 <= value <= 10` range check
  - **Atomic CAS UPDATE:** один `UPDATE activities SET rpe = :value WHERE id = :activity_id AND user_id = :user_id AND rpe IS NULL`. Три предиката коллапсируются в одно SQL-выражение — existence, tenant scope и single-shot check. Не нужен preceding `session.get` + Python-level check (устранены race conditions и защита от регресса при рефакторе). Если `rowcount == 0`, пользователь получает `answerCallbackQuery("Уже оценено")` — без раскрытия причины (non-ownership vs already-rated), чтобы не утекала информация о существовании чужих activity IDs.
  - CHECK constraint на БД: `rpe IS NULL OR (rpe BETWEEN 1 AND 10)` — защита at rest даже при обходе handler
  - Single-shot invariant enforced в том же UPDATE через `rpe IS NULL` predicate (см. `docs/RPE_SPEC.md` § Single-shot)
- **RPE не принимается через MCP write tool** — `get_activity_details`, `get_training_log`, `get_workout_compliance`, `get_weekly_summary` read-only. Запись возможна только через Telegram callback. Это защищает от prompt-injection сценария "Claude записал RPE по интерпретации фразы юзера".

#### T15. Intervals.icu OAuth — Account Mismatch via State Race (Tampering)

- **Что:** OAuth callback проверяет `db_user.athlete_id == response.athlete.id` для защиты от подмены аккаунта, но state JWT не snapshot'ит `athlete_id` — только `user_id`. Между инициацией (`POST /auth/init`) и callback'ом (`GET /auth/callback`) `athlete_id` в БД может измениться (shell admin, parallel OAuth, migration), и guard пропустит чужой `athlete.id`.
- **Где:** `api/routers/intervals/oauth.py:_validate_oauth_state` + `intervals_oauth_callback`
- **Severity:** High (возможна cross-tenant data attribution после Phase 2 когда Bearer sync начнёт писать wellness/activities с чужого аккаунта)
- **Реалистичность:** низкая на текущем этапе (2-юзерный dev, владелец shell'а = владелец OAuth). Становится реальной при public launch.
- **Mitigation (Phase 2, follow-up issue):**
  - В `_generate_oauth_state` добавить `athlete_id_snapshot` в payload
  - В callback: reject если `db_user.athlete_id != state_snapshot.athlete_id` с error `oauth_state_stale`
  - Защищает от ВСЕХ race-condition сценариев, не только shell-admin
  - Плюс: добавить `aud='intervals_oauth_callback'` claim для PyJWT audience-level separation от session JWT (defence-in-depth поверх существующего `purpose` guard)

#### T16. Intervals.icu OAuth — Long-Lived Token Without Rotation (Information Disclosure)

- **Что:** Intervals.icu OAuth не выдаёт `refresh_token` и не указывает `expires_in` — access_token живёт "вечно" до явного revoke через Intervals.icu UI. Украденный токен (XSS, DB leak, лог с token) даёт perpetual access к wellness/activities/calendar юзера.
- **Где:** `data/db/user.py:intervals_access_token_encrypted` (Fernet at rest), Intervals.icu token response shape (§T15 cookbook confirmed)
- **Severity:** Medium (impact = read wellness/calendar, не financial)
- **Mitigation:**
  - Fernet encryption at rest (уже реализовано в Phase 1)
  - **Нет rotation endpoint** — юзер может отозвать только через Intervals.icu UI. EndurAI detect'ит revoke через ленивую 401-проверку в `IntervalsClient` (Phase 2 §8)
  - Callback логирует только structure, НЕ full token (`keys=sorted(data.keys())`, `body_len` вместо `body`). Полный token никогда не в логах, breadcrumbs, Sentry stack frames (через `data scrubbing` в `sentry_config.py`)
  - Token exchange через POST body (не URL) — не в access logs Caddy/nginx
  - **Phase 2 TODO:** rate limit `POST /api/intervals/auth/init` (5 req / 5 min per user), иначе злоумышленник с валидной session может flood'ить OAuth initiation (abuse → блок IP со стороны Intervals.icu)

---

## 3. Data Isolation Strategy

### 3.1. Tenant Model

```
users (новая таблица, #34) — реализовано в data/database.py User
├── id: INTEGER (PK, autoincrement)
├── chat_id: VARCHAR (unique)
├── username: VARCHAR (nullable)
├── display_name: VARCHAR (nullable)
├── role: VARCHAR DEFAULT 'viewer'             — owner / coach / athlete / viewer
├── athlete_id: VARCHAR (unique, nullable)
├── api_key_encrypted: TEXT (nullable)         — Fernet-encrypted Intervals.icu API key
├── mcp_token: VARCHAR(64) (unique, nullable)  — MCP Bearer token
├── language: VARCHAR(5) DEFAULT 'ru'          — (#50) per-user language for UI and AI responses
├── preferred_model: VARCHAR(30) DEFAULT NULL   — (#51) per-user LLM model override
├── is_active: BOOLEAN DEFAULT true
├── created_at: TIMESTAMP WITH TZ
└── updated_at: TIMESTAMP WITH TZ
```

**Tenant = User.** Не организация, не команда — один пользователь = один набор данных. Простая модель для начала. Coach/team модель — позже.

**Регистрация:** Telegram bot `/start` автоматически создаёт `User` с `role=viewer`.

### 3.2. Migration — user_id на все таблицы (РЕАЛИЗОВАНО)

> Миграция: `268670b22cd7` (users table) + `f0d2f435b802` (user_id на все таблицы)
> Все существующие данные backfill с `user_id=1` (owner placeholder).

13 таблиц получили `user_id INTEGER NOT NULL REFERENCES users(id)`. `exercise_cards` — общая библиотека, без user_id.

| Таблица              | Было                                            | Стало                                                                                | Индекс                          |
| -------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------- |
| `wellness`           | PK `id` (date string)                           | PK `id` (autoincrement), `date` VARCHAR, `UNIQUE(user_id, date)`                     | `ix_wellness_user_id`           |
| `hrv_analysis`       | PK `(date, algorithm)`, FK → wellness           | PK `(user_id, date, algorithm)`, FK dropped                                          | implicit (leading PK column)    |
| `rhr_analysis`       | PK `date`, FK → wellness                        | PK `(user_id, date)`, FK dropped                                                     | implicit (leading PK column)    |
| `scheduled_workouts` | PK `event_id`                                   | PK `event_id` + `user_id` FK                                                         | `ix_scheduled_workouts_user_id` |
| `activities`         | PK `activity_id`                                | PK `activity_id` + `user_id` FK                                                      | `ix_activities_user_id`         |
| `activity_details`   | PK `activity_id` FK → activities                | Без изменений — tenant scope через JOIN с activities                                 | —                               |
| `activity_hrv`       | PK `activity_id` FK → activities, `date` column | PK без изменений, **`date` column удалена** (JOIN через activities.start_date_local) | —                               |
| `pa_baseline`        | PK autoincrement, `UNIQUE(activity_type, date)` | + `user_id` FK, `UNIQUE(user_id, activity_type, date)`                               | `ix_pa_baseline_user_id`        |
| `ai_workouts`        | PK autoincrement                                | + `user_id` FK                                                                       | `ix_ai_workouts_user_id`        |
| `training_log`       | PK autoincrement                                | + `user_id` FK                                                                       | `ix_training_log_user_id`       |
| `mood_checkins`      | PK autoincrement                                | + `user_id` FK. **Owner-only**                                                       | `ix_mood_checkins_user_id`      |
| `iqos_daily`         | PK `date`                                       | PK `id` (autoincrement), `UNIQUE(user_id, date)`. **Owner-only**                     | `ix_iqos_daily_user_id`         |
| `exercise_cards`     | PK `id`                                         | **Без изменений** — общая библиотека, user_id не нужен                               | —                               |
| `workout_cards`      | PK autoincrement                                | + `user_id` FK                                                                       | `ix_workout_cards_user_id`      |

**Ключевые решения:**

- `activity_details` и `activity_hrv` — user_id не добавлен, tenant scope через JOIN с `activities` (FK → activities.id, а activities уже имеет user_id)
- `activity_hrv.date` удалена — дублировала `activities.start_date_local`, теперь все date-запросы через JOIN
- `wellness` и `iqos_daily` — PK изменён с date-string на autoincrement (multi-tenant: два пользователя, одна дата)
- `hrv_analysis` / `rhr_analysis` — FK к wellness удалены (не использовались в коде, связь логическая по user_id + date)
- `exercise_cards` — общая библиотека, user_id не нужен
- Все CRUD методы обновлены с `user_id` параметром, callers пока hardcoded `user_id=1` с `# TODO` комментариями

### 3.3. Query Isolation Pattern

**Подход: Middleware-level tenant injection, НЕ row-level security PostgreSQL.**

Причина: RLS требует `SET LOCAL` per-connection что плохо работает с async connection pooling. Проще и надёжнее — фильтр на уровне приложения.

```python
# data/database.py — новый паттерн

@asynccontextmanager
async def get_tenant_session(user_id: UUID) -> AsyncGenerator[TenantSession, None]:
    """Yield a session scoped to a specific tenant."""
    factory = get_session_factory()
    session = factory()
    try:
        yield TenantSession(session, user_id)
    finally:
        await session.close()

class TenantSession:
    """Wraps AsyncSession with automatic tenant_id filtering."""

    def __init__(self, session: AsyncSession, user_id: UUID):
        self._session = session
        self.user_id = user_id

    async def get_wellness(self, dt: date) -> WellnessRow | None:
        stmt = select(WellnessRow).where(
            WellnessRow.user_id == self.user_id,
            WellnessRow.date == str(dt),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ... все CRUD методы принудительно фильтруют по user_id
```

**Правило:** Ни один запрос к данным не должен работать без `user_id`. Глобальные CRUD (типа `WellnessRow.get(date)`) — удалить или пометить `@deprecated`.

### 3.4. Migration Strategy (РЕАЛИЗОВАНО)

Выполнено в одной миграции `f0d2f435b802`:

1. [x] Insert owner placeholder (id=1) в users table
2. [x] Добавить `user_id` column (nullable) → backfill=1 → SET NOT NULL → FK + index
3. [x] Обновить unique constraints для multi-tenant
4. [x] Обновить все CRUD методы с `user_id` параметром
5. [x] Обновить все callers с `user_id=1 # TODO`
6. [x] Тесты обновлены и проходят (144/148, 4 pre-existing failures)

---

## 4. Auth & Authorization Redesign

### 4.1. JWT Upgrade

Текущий JWT — минимальный (`sub`, `iat`, `exp`). Для multi-tenant нужно:

```json
{
  "sub": "telegram_chat_id",
  "tenant_id": "uuid",
  "role": "owner|athlete|viewer",
  "scope": ["read", "write", "sync", "admin"],
  "iss": "triathlon-agent",
  "aud": "triathlon-api",
  "iat": 1711900000,
  "exp": 1712504800,
  "jti": "unique-token-id"
}
```

**Изменения:**

- Добавить `tenant_id` (UUID пользователя) — основной идентификатор для data access
- Добавить `role` и `scope` — вместо hardcoded проверки `chat_id == TELEGRAM_CHAT_ID`
- Добавить `jti` — для token revocation (через Redis blacklist)
- **Рассмотреть:** Миграция на `PyJWT` или `python-jose` вместо кастомного кода

### 4.2. Role Matrix (Multi-Tenant)

| Role          | Read own data          | Write own data     | Sync  | AI chat | MCP   | Admin |
| ------------- | ---------------------- | ------------------ | ----- | ------- | ----- | ----- | --------------------------------------------------------------------- |
| **owner**     | +                      | +                  | +     | +       | +     | +     |
| **athlete**   | +                      | + (limited)        | +     | +       | +     | -     |
| ~~**coach**~~ | ~~+ (their athletes)~~ | ~~+ (plans only)~~ | ~~-~~ | ~~+~~   | ~~+~~ | ~~-~~ | _Out of scope — coach→athlete binding не описан. См. "Что НЕ входит"_ |
| **viewer**    | + (shared link)        | -                  | -     | -       | -     | -     |
| **anonymous** | -                      | -                  | -     | -       | -     | -     |

### 4.3. Telegram Bot — Multi-User Resolution

```python
# bot/middleware.py — новый файл

async def resolve_tenant(update: Update) -> User | None:
    """Resolve tenant from Telegram chat_id.

    Returns User object or None if user not registered.
    """
    chat_id = str(update.effective_user.id)
    user = await User.get_by_chat_id(chat_id)

    if not user:
        # Unregistered user — offer onboarding
        return None

    if not user.is_active:
        return None

    return user
```

**Каждый bot handler** получает `user` через middleware, а не проверяет `TELEGRAM_CHAT_ID`:

```python
async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await resolve_tenant(update)
    if not user:
        await update.message.reply_text("Вы не зарегистрированы. /start для начала.")
        return

    # Все данные загружаются через tenant session
    async with get_tenant_session(user.id) as ts:
        wellness = await ts.get_wellness(today)
        ...
```

### 4.4. MCP Auth — Per-Tenant Tokens

**Реализовано:** Per-user `mcp_token` (random 32-byte hex, VARCHAR(64)), хранится plaintext в `users` таблице. Генерируется через `User.generate_mcp_token()`. Lookup через `User.get_by_mcp_token(token)`. Claude Desktop конфигурируется с user-specific токеном.

### 4.5. API Endpoint Auth Flow (Updated)

```
Request → Authorization header
  ├── "Bearer <jwt>" → verify_jwt() → extract tenant_id, role, scope
  ├── Telegram initData → verify_hmac() → lookup user by chat_id → tenant_id, role
  ├── "Bearer <api_key>" → lookup in users table → tenant_id, role
  └── None → anonymous (landing only)

→ Inject tenant_id into request state
→ All data access via get_tenant_session(tenant_id)
```

---

## 5. Secrets Management

### 5.1. Per-Tenant Credentials

Каждый пользователь приносит свои:

| Secret                          | Колонка в `users`                     | Хранение                    | Примечание                                                        |
| ------------------------------- | ------------------------------------- | --------------------------- | ----------------------------------------------------------------- |
| Intervals.icu API key (legacy)  | `api_key_encrypted`                   | Fernet-encrypted            | Legacy path, замещается OAuth для новых юзеров                    |
| Intervals.icu OAuth access_token | `intervals_access_token_encrypted`   | Fernet-encrypted            | Долгоживущий (нет `refresh_token` / `expires_in`), см. §T15       |
| Intervals.icu OAuth scope       | `intervals_oauth_scope`               | Plain (не секрет)           | `ACTIVITY:READ,WELLNESS:READ,CALENDAR:WRITE,SETTINGS:WRITE`        |
| Intervals.icu auth method       | `intervals_auth_method`               | Plain (`api_key`/`oauth`/`none`) | Source of truth для `IntervalsClient.for_user()` dispatch    |
| Intervals.icu athlete ID        | `athlete_id`                          | Plain (unique)              | Не секрет, но per-user                                            |
| Telegram chat ID                | `chat_id`                             | Plain (unique)              | Идентификатор                                                     |
| MCP Bearer token                | `mcp_token`                           | Plain (unique, VARCHAR(64)) | Per-user токен для MCP доступа                                    |

Системные секреты (общие для сервиса):

| Secret                             | Хранение         | Примечание                                            |
| ---------------------------------- | ---------------- | ----------------------------------------------------- |
| `ANTHROPIC_API_KEY`                | `.env` / Vault   | Один на сервис, usage per-tenant tracked              |
| `TELEGRAM_BOT_TOKEN`               | `.env` / Vault   | Один бот на всех                                      |
| `JWT_SECRET`                       | `.env` / Vault   | Подписание session + OAuth state JWT (разделены `purpose` claim) |
| `DATABASE_URL`                     | `.env` / Vault   | Единая БД                                             |
| `GITHUB_TOKEN`                     | `.env` / Vault   | CI/CD, не per-tenant                                  |
| `INTERVALS_OAUTH_CLIENT_ID`        | `.env`           | Публичный client ID от Intervals.icu (plain)          |
| `INTERVALS_OAUTH_CLIENT_SECRET`    | `.env` / Vault   | Секретный (в `SecretStr`), используется только в callback server-to-server |

### 5.2. Encryption at Rest (РЕАЛИЗОВАНО)

Реализовано в `data/crypto.py`:

- `encrypt_field(value)` / `decrypt_field(encrypted)` — Fernet encryption
- `generate_key()` — генерация нового ключа
- `FIELD_ENCRYPTION_KEY` в `config.py` как `SecretStr`
- `User.set_api_key()` / `get_api_key()` — хелперы для Intervals.icu API key (legacy)
- `User.set_oauth_tokens()` / `intervals_access_token` property / `clear_oauth_tokens()` — OAuth access_token dual с тем же ключом (см. §T15)
- Key rotation (`re_encrypt_all`) — TODO, не реализовано. При ротации `FIELD_ENCRYPTION_KEY` нужно перезаписать **оба** поля: `api_key_encrypted` и `intervals_access_token_encrypted`.

### 5.3. New Env Vars

```env
# Security (new for multi-tenant)
FIELD_ENCRYPTION_KEY=...          # Fernet key for encrypting per-user secrets in DB
REGISTRATION_ENABLED=false        # Allow new user registration via bot
REGISTRATION_CODE=                # Invite code: alphanumeric 12+ chars (NOT 6-digit — brute-force risk)
MAX_USERS=10                      # Max registered users (safety limit)
```

---

## 6. API Security Hardening

### 6.1. Rate Limiting (Global)

Сейчас rate limiting только на verify-code. Нужно расширить:

| Endpoint Group     | Limit         | Per       |
| ------------------ | ------------- | --------- |
| `POST /api/auth/*` | 5 req / 5 min | IP        |
| `GET /api/*`       | 60 req / min  | tenant_id |
| `POST /api/jobs/*` | 10 req / min  | tenant_id |
| `POST /mcp`        | 30 req / min  | tenant_id |
| Bot commands       | 20 msg / min  | chat_id   |

**Реализация:** Redis-based sliding window (`data/redis_client.py` уже есть, #37).

### 6.2. Input Validation

Уже частично есть (Pydantic models). Дополнительно:

- **Sanitize date params** — `date` query params проверять на диапазон (не больше ±2 лет)
- **Limit week_offset** — не больше ±52
- **Activity ID validation** — должен принадлежать tenant
- **MCP tool params** — валидация через Pydantic перед выполнением

### 6.3. CORS

Текущий CORS — `allow_origins=["*"]` в dev. Для production:

```python
CORS_ORIGINS = [
    settings.API_BASE_URL,
    # Telegram Mini App работает с нескольких доменов
    "https://web.telegram.org",
    "https://webk.telegram.org",
    "https://weba.telegram.org",
    "https://webz.telegram.org",
]
```

### 6.4. Background Job Tenancy

Scheduler jobs (`bot/scheduler.py`) в multi-tenant должны работать per-tenant:

```python
# bot/scheduler.py — multi-tenant pattern

async def sync_wellness_job():
    """Sync wellness for ALL active users."""
    users = await User.get_active_users()
    for user in users:
        try:
            client = IntervalsClient(
                api_key=decrypt_field(user.intervals_api_key),
                athlete_id=user.intervals_athlete_id,
            )
            async with get_tenant_session(user.id) as ts:
                await ts.sync_wellness(client, today)
        except Exception as e:
            logger.error(f"Wellness sync failed for user={user.id}: {e}")
            # Ошибка одного пользователя не ломает остальных
```

**Правила:**

- Каждый user обрабатывается в отдельном try/except — изоляция ошибок
- IntervalsClient создаётся per-user с user-specific credentials
- Job не кэширует данные между пользователями
- Максимум N concurrent syncs (asyncio.Semaphore) чтобы не перегрузить Intervals.icu API

### 6.5. MCP Tool Tenant Audit

Все 32 MCP tools должны быть проверены на tenant isolation. Классификация:

| Категория                    | Tools                                                                                                                                                                                                                                                                       | Tenant Filter                                                                                       |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| **Read own data (direct)**   | get_wellness, get_hrv_analysis, get_rhr_analysis, get_training_load, get_recovery, get_activities, get_scheduled_workouts, get_wellness_range, get_efficiency_trend, get_training_log, get_personal_patterns, get_threshold_freshness, list_ai_workouts, list_workout_cards, get_weekly_summary | `WHERE user_id = ?` обязателен. `get_training_log` также делает tenant-scoped bulk fetch `activities.rpe` по `actual_activity_id` для RPE-enrichment (см. `docs/RPE_SPEC.md`). `get_weekly_summary.rpe` — null-aware aggregates. |
| **Read own data (via JOIN)** | get_activity_details, get_activity_hrv, get_workout_compliance, get_thresholds_history, get_readiness_history                                                                                                                                                               | Tenant scope через JOIN с activities (user_id на parent table). `get_workout_compliance` содержит `activity.rpe` в `actual` блоке — read-only, **НЕ** принимает RPE как input параметр (T13). |
| **Shared library**           | list_exercise_cards                                                                                                                                                                                                                                                         | Без user_id — общая библиотека                                                                      |
| **Owner-only read**          | get_mood_checkins_tool, get_iqos_sticks                                                                                                                                                                                                                                     | Owner-only. `mood_checkins` и `iqos_daily` — personal tracking, не реплицируется для других атлетов |
| **Write own data**           | suggest_workout, remove_ai_workout, create_ramp_test_tool, create_exercise_card, update_exercise_card, compose_workout, remove_workout_card                                                                                                                                 | `user_id` inject при создании                                                                       |
| **Owner-only write**         | save_mood_checkin_tool                                                                                                                                                                                                                                                      | Owner-only. Mood check-in только для owner                                                          |
| **Compute**                  | get_goal_progress                                                                                                                                                                                                                                                           | Берёт goal из users table, данные через tenant session                                              |
| **External**                 | create_github_issue, get_github_issues                                                                                                                                                                                                                                      | Owner-only в multi-tenant (общий repo)                                                              |

### 6.6. Security Headers

```python
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # CSP вместо deprecated X-XSS-Protection
    # Mini App использует inline scripts — нужен nonce или 'unsafe-inline' на первом этапе
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://telegram.org 'unsafe-inline'; "  # TODO: заменить unsafe-inline на nonce
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' https://api.telegram.org; "
        "img-src 'self' data:; "
        "frame-ancestors 'self' https://web.telegram.org https://webk.telegram.org https://weba.telegram.org"
    )
    return response
```

> **Note:** `X-XSS-Protection` убран — deprecated, современные браузеры его игнорируют. `Content-Security-Policy` заменяет его и даёт более надёжную защиту от XSS.

---

## 7. Audit Log

### 7.1. Audit Events

```
audit_log (новая таблица)
├── id: UUID (PK)
├── timestamp: TIMESTAMP WITH TZ
├── user_id: UUID (nullable — anonymous actions)
├── action: VARCHAR(50) — enum-like
├── resource_type: VARCHAR(30) — "wellness", "workout", "activity", etc.
├── resource_id: VARCHAR(100) — nullable
├── details: JSONB — request params, changes
├── ip_address: VARCHAR(45) — nullable
└── user_agent: VARCHAR(200) — nullable
```

### 7.2. Actions to Log

| Action              | Trigger                            | Priority |
| ------------------- | ---------------------------------- | -------- |
| `user.login`        | JWT issued (verify-code success)   | High     |
| `user.login_failed` | verify-code failure                | High     |
| `sync.wellness`     | Job trigger or manual sync         | Medium   |
| `sync.workouts`     | Job trigger or manual sync         | Medium   |
| `sync.activities`   | Job trigger or manual sync         | Medium   |
| `workout.create`    | AI workout pushed to Intervals.icu | Medium   |
| `workout.delete`    | remove_ai_workout                  | Medium   |
| `mcp.tool_call`     | Any MCP tool invocation            | Low      |
| `bot.command`       | Any bot command                    | Low      |
| `user.register`     | New user created                   | High     |
| `user.deactivate`   | User disabled                      | High     |

---

## 8. AI Security

### 8.1. Prompt Isolation

**Правило:** Один Claude API call = один tenant. Никогда не смешивать данные разных пользователей.

```python
# ai/claude_agent.py — обязательные проверки

async def analyze_morning(user_id: UUID, ...):
    """Morning analysis — all data scoped to user_id."""
    async with get_tenant_session(user_id) as ts:
        wellness = await ts.get_wellness(today)
        hrv = await ts.get_hrv_analysis(today)
        # ... все данные через tenant session

    # System prompt НЕ содержит данные других пользователей
    # Athlete profile берётся из users table, не из .env
```

### 8.1a. Tool-Use Tenant Validation

В tool-use mode (MCP Phase 2) Claude вызывает tools с параметрами. **Критическое правило:**

- **Tool handler ВСЕГДА берёт `tenant_id` из auth context (JWT/session), НИКОГДА из tool arguments**
- Если tool argument содержит `user_id` или `tenant_id` — игнорировать, использовать только из auth
- Все tool handlers в `ai/tool_definitions.py` должны принимать `user_id` как inject-parameter, не как user input

```python
# ai/tool_definitions.py — правильный паттерн

async def handle_get_wellness(params: dict, user_id: UUID) -> dict:
    """user_id injected from auth, NOT from params."""
    date = params.get("date", str(today))
    async with get_tenant_session(user_id) as ts:
        return await ts.get_wellness(date)
```

### 8.1b. Data Minimization в AI Prompts

Не отправлять в Claude API больше данных чем нужно:

- **Убрать PII:** Не включать telegram_username, display_name в system prompt
- **Минимум health data:** Только агрегированные метрики (HRV status, recovery score), не raw значения где возможно
- **Mood notes:** Если содержат чувствительный контент — не включать в morning analysis prompt, только по запросу через chat

### 8.2. AI Cost Tracking

В multi-tenant нужно трекать расход AI per-tenant:

```
ai_usage (новая таблица)
├── id: autoincrement
├── user_id: INTEGER FK
├── timestamp: TIMESTAMP
├── model: VARCHAR — "claude-sonnet-4-6", "gemini-2.5-flash"
├── input_tokens: INT
├── output_tokens: INT
├── cost_usd: DECIMAL(10, 6)
└── context: VARCHAR — "morning_report", "chat", "workout_generation"
```

### 8.3. Per-Tenant AI Limits

| Tier    | Morning reports | Chat messages / day | Workout generations / day |
| ------- | --------------- | ------------------- | ------------------------- |
| Free    | 1 / day         | 5                   | 2                         |
| Premium | 1 / day         | 50                  | 10                        |
| Owner   | Unlimited       | Unlimited           | Unlimited                 |

---

## 9. Implementation Order

### Phase 1 — Users Table + Foundation

> Цель: создать таблицу users, crypto module. Остальное продолжает работать как single-tenant.

1. [x] `data/crypto.py` — Fernet encrypt/decrypt + `generate_key()`, `FIELD_ENCRYPTION_KEY` в config.py
2. [x] Создать таблицу `users` (**#34**) — `User` ORM + Alembic migration (`268670b22cd7`)
3. [x] Bot `/start` — автоматически создаёт `User` с `role=viewer` при первом вызове
4. [ ] ~~`sync_athlete_settings()`~~ убрать из startup (done — thresholds managed in Intervals.icu)

### Phase 1.1 — user_id на все таблицы (РЕАЛИЗОВАНО)

> Одна миграция `f0d2f435b802`. Все 13 таблиц (кроме exercise_cards) получили user_id.

5. [x] **wellness** — autoincrement PK, `user_id` FK + index, `UNIQUE(user_id, date)`, `id` renamed to `date`
6. [x] **hrv_analysis** — composite PK `(user_id, date, algorithm)`, FK к wellness удалён
7. [x] **rhr_analysis** — composite PK `(user_id, date)`, FK к wellness удалён
8. [x] **scheduled_workouts** — `user_id` FK + index
9. [x] **activities** — `user_id` FK + index
10. [x] **activity_details** — без изменений (tenant scope через JOIN с activities)
11. [x] **activity_hrv** — `date` column удалена (JOIN через activities.start_date_local)
12. [x] **pa_baseline** — `user_id` FK + index, `UNIQUE(user_id, activity_type, date)`
13. [x] **ai_workouts** — `user_id` FK + index
14. [x] **training_log** — `user_id` FK + index
15. [x] **mood_checkins** — `user_id` FK + index (owner-only)
16. [x] **iqos_daily** — autoincrement PK, `user_id` FK + index, `UNIQUE(user_id, date)`
17. [x] **exercise_cards** — без изменений (общая библиотека, user_id не нужен)
18. [x] **workout_cards** — `user_id` FK + index
19. [x] Все CRUD методы обновлены с `user_id` параметром
20. [x] Все callers обновлены с `user_id=1 # TODO`
21. [x] Тесты обновлены (conftest создаёт test user, 144/148 pass)

### Phase 1.2 — Дополнительные улучшения (РЕАЛИЗОВАНО)

22. [x] CLI `onboard <user_id> [--days 180]` — полный онбоардинг: wellness → activities → details → workouts
23. [x] `IntervalsClient.for_user(api_key, athlete_id)` — non-singleton factory для per-user API доступа
24. [x] `fill_training_log_actual` — direct type comparison (types normalized at DTO layer)
25. [x] CLI `fix-training-log-actual` — одноразовый пересчёт actual data после фикса матчинга
26. [x] Code review фиксы: race condition в /start, is_active проверка в lookups, Fernet caching, session.get→select, missing user_id filters

### Phase 1.3 — Per-User Scheduler (следующий шаг)

> Заменить hardcoded `user_id=1` на реальный user_id. Без этого user 2+ не получают автоматических обновлений.

27. [ ] Scheduler jobs — per-user loop: get_active_users → для каждого IntervalsClient.for_user → execute job
28. [ ] Перевести API/MCP callers с `user_id=1 # TODO` на реальный user_id из auth
29. [ ] `TenantSession` wrapper — единый интерфейс для tenant-scoped queries
30. [ ] AI tool handlers — tenant_id injection from auth, never from user input (8.1a)

**Ограничение текущего состояния:** scheduler, утренний/вечерний отчёт, sync jobs работают только для user_id=1. User 2+ получает данные только через `python -m bot.cli onboard`.

### Phase 2 — Auth Upgrade

24. [ ] JWT — добавить `tenant_id`, `role`, `scope`, `jti` claims
25. [ ] Рассмотреть миграцию на PyJWT (вместо кастомного HS256)
26. [ ] initData — проверка `auth_date` freshness (< 15 мин). **Не 5 минут** — Mini App может быть открыто дольше до первого API-вызова. 15 минут — компромисс между безопасностью и UX
27. [ ] Bot middleware — `resolve_tenant()` вместо `TELEGRAM_CHAT_ID` проверок
28. [ ] API deps — обновить `get_current_role()` → `get_current_user()` (возвращает User)
29. [ ] MCP auth — per-tenant JWT + API keys для external clients
30. [ ] Token revocation — Redis blacklist для `jti`. **Критично:** при деактивации пользователя — немедленно blacklist все его `jti` (не ждать expiry 7 дней)
31. [ ] Добавить `created_by` (user_id) к GitHub issue creation (**#48**)

### Phase 3 — Security Hardening

32. [ ] Redis rate limiting — per-tenant, per-endpoint (**#37**)
33. [ ] CORS whitelist (убрать `*`)
34. [ ] Security headers middleware (CSP вместо deprecated X-XSS-Protection)
35. [ ] Input validation — date ranges, week_offset limits, activity ownership checks
36. [ ] Аудит всех 32 MCP tools на tenant isolation (чеклист в 6.5)
37. [ ] Fernet key rotation plan — при компрометации `FIELD_ENCRYPTION_KEY`: скрипт `re_encrypt_all_keys(old_key, new_key)` для перешифровки всех `intervals_api_key` в users table. Документировать процедуру в runbook

### Phase 4 — Observability

38. [ ] Audit log table + middleware
39. [ ] AI usage tracking table — `cost_usd: DECIMAL(10, 6)` (не 8,6 — запас для высокого usage)
40. [ ] Structured logging с tenant_id в каждой строке
41. [ ] Health data access logging (отдельный уровень для чувствительных данных)
42. [ ] Job failure tracking (**#3**) + Sentry integration (**#40**)

### Phase 5 — Registration & Multi-User

43. [ ] Определить manual onboarding process (**#47**)
44. [ ] Bot /start → onboarding flow (connect Intervals.icu)
45. [ ] Registration gates — invite code: alphanumeric 12+ chars (не 6-digit numeric!), rate limit 3 attempts / 15 min per IP
46. [ ] Per-tenant AI limits
47. [ ] Data deletion endpoint (right to erasure) — `DELETE /api/account` удаляет все данные пользователя. Минимальный GDPR compliance для health data
48. [ ] Data export endpoint — `GET /api/account/export` → JSON/ZIP со всеми данными пользователя

---

## 10. Testing Checklist

### Cross-Tenant Isolation Tests

```python
# tests/test_security.py

async def test_user_a_cannot_see_user_b_wellness():
    """User A's tenant session must not return User B's data."""

async def test_user_a_cannot_trigger_user_b_sync():
    """Sync jobs must use the requesting user's credentials."""

async def test_jwt_from_user_a_rejected_for_user_b_data():
    """JWT tenant_id must match requested resource."""

async def test_mcp_scoped_to_tenant():
    """MCP tools return only data for the authenticated tenant."""

async def test_bot_handler_scopes_to_chat_id():
    """Bot commands return data only for the messaging user."""

async def test_unregistered_user_gets_onboarding():
    """Unknown chat_id → registration prompt, not data."""

async def test_deactivated_user_blocked():
    """is_active=False → 403 on all endpoints."""

async def test_rate_limit_per_tenant():
    """Exceeding rate limit returns 429."""

async def test_ai_prompt_contains_only_own_data():
    """Claude API call includes only the requesting tenant's data."""
```

---

## 11. Что НЕ входит в эту спеку

- **Team/organization model** — coach manages multiple athletes. Отдельная спека после базового multi-tenant
- **OAuth2 / OIDC** — overkill для Telegram-first приложения. JWT + Telegram initData достаточно
- **PostgreSQL Row-Level Security** — не совместимо с async pooling, используем application-level filtering
- **Zero-trust / mTLS** — не нужно для single-VPS deployment
- **GDPR / data export** — важно, но отдельная задача
- **Penetration testing** — после реализации, не на этапе спеки

---

## 12. Onboarding Flow (#47)

### 12.1. Текущий процесс (полу-автоматический)

Новый атлет подключается вручную owner-ом:

1. User отправляет `/start` боту → `User` создаётся автоматически с `role=viewer`
2. Owner через shell/SQL:
   - Меняет `role` на `athlete` (или `owner` для себя)
   - Прописывает `athlete_id` (Intervals.icu athlete ID)
   - Прописывает `api_key_encrypted` через `user.set_api_key(plaintext)`
   - Генерирует MCP токен через `user.generate_mcp_token()`
3. Owner запускает CLI для заполнения БД нового пользователя:
   ```bash
   # Порядок важен:
   python -m bot.cli backfill [period]        # 1. wellness + recovery pipeline (зависимости для activities)
   python -m bot.cli sync-activities [days]    # 2. completed activities
   python -m bot.cli backfill-details [days]   # 3. extended stats для activities
   python -m bot.cli sync-workouts [days]      # 4. planned workouts
   ```
4. User готов — утренние отчёты, AI chat, MCP tools

> **TODO:** CLI команды пока используют hardcoded `user_id=1`. Для онбоардинга второго пользователя нужна `--user-id` опция или отдельная CLI команда `onboard-user <chat_id>` которая выполнит все шаги автоматически.

### 12.2. Будущий процесс (автоматический, Phase 5)

1. User отправляет `/start` → User создаётся
2. Бот запрашивает Intervals.icu credentials (API key + athlete ID)
3. Credentials валидируются (тестовый API-запрос) и шифруются
4. Автоматический backfill в фоне
5. User готов

**Критические проверки:**

- Валидация Intervals.icu API key — тестовый запрос перед сохранением
- `chat_id` уникален в таблице `users`
- Лимит `MAX_USERS` не превышен
- Если `REGISTRATION_CODE` задан — проверить invite code

**Registration code security:**

- Формат: alphanumeric, минимум 12 символов (36^12 = ~4.7×10^18 комбинаций)
- Rate limit: 3 попытки / 15 мин per IP + per chat_id
- После 10 неудачных попыток — блокировка chat_id на 24 часа
- Не 6-digit numeric! (10^6 = 1M комбинаций, при 5 req/5 min = полный перебор за ~3.5 дня)

---

## 13. Multi-Tenant Shared Resources (#48)

Некоторые ресурсы общие для всех tenants. Правила:

| Resource                        | Ownership                | Multi-tenant rule                                                                                                                          |
| ------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| GitHub issues                   | Общие (один repo)        | Добавить `created_by` (user display name или ID) в body issue. MCP tool `create_github_issue` записывает автора. Owner-only в multi-tenant |
| Exercise cards (shared library) | `user_id = NULL` = общие | User может создавать свои (`user_id` set), или использовать общие. Общие создаёт только owner                                              |
| System prompts                  | Общие                    | Один набор промптов для всех. Athlete-specific данные из `users` table                                                                     |
| Scheduler jobs                  | Per-tenant               | Каждый user обрабатывается отдельно (6.5). Ошибка одного не ломает других                                                                  |
