# Multi-Tenant Security Specification

> Threat model, data isolation, auth/authz, secrets — что нужно до и после перехода на multi-tenant. Spec живёт как **reference catalog угроз** (§2 T1-T19) + **deferred punch-lists** для Phase 2/3 (§9).

**Status (2026-05-09):**

- **Phase 1 / 1.1 / 1.2 / 1.3** — ✅ done. Users table + `user_id` на 13 таблицах, per-user MCP tokens, OAuth-scoped credentials, scheduler per-tenant.
- **Phase 2** (JWT upgrade с tenant_id/role/scope claims) — **deferred 2026-05-09**, нет use case. Trigger conditions + punch-list в §9.
- **Phase 3** (security hardening, Redis rate-limit migration) — **deferred 2026-05-09**, traffic profile не оправдывает. Trigger + punch-list в §9.
- **Phase 4** (observability — audit log, AI cost tracking, structured logs) — not started.
- **Phase 5** (registration & multi-user, invite codes, GDPR erasure/export) — not started.

**Related issues:** #41 (security agent), #15 (multi-tenant arch), #34 (users table) ✅, #49 (unique indexes) ✅, #48 (created_by in GH issues) ✅, #47 (onboarding), #37 (Redis caching), #3 (job failure tracking), #40 (Sentry), #50 (i18n), #51 (per-user LLM model), #132 (Telegram Login Widget) ✅.

---

## 1. Current implementation snapshot

| Concern | Code |
|---|---|
| Auth — Telegram initData | `api/deps.py` (HMAC-SHA256, `auth_date <15min` freshness gate) |
| Auth — Telegram Login Widget | `api/auth.py:verify_telegram_widget_auth` (SHA256 over data-check-string, 24h replay window, constant-time compare) |
| Auth — JWT (desktop) | `api/auth.py` (custom HS256, 7-day TTL, `purpose` claim) |
| Auth — MCP | `api/server.py:MCPAuthMiddleware` → `User.get_by_mcp_token` (per-user Bearer) |
| Role guards | `api/deps.py:require_viewer / require_athlete / require_owner` |
| Bot decorators | `bot/decorator.py:@athlete_required / @user_required` (resolve via `chat_id`) |
| Per-user credentials | `users.api_key_encrypted` / `intervals_access_token_encrypted` (Fernet at rest) |
| MCP context | `mcp_server/context.py:get_current_user_id()` (contextvars, never tool parameter) |
| Audit log allowlist | `api/routers/auth.py:_sanitize_last_error` (whitelist: `EMPTY_INTERVALS`, `watchdog_exhausted`, `OAuth revoked during backfill`; everything else → `"internal"`) |

Schema: 36 tables, 13 with `user_id` FK + index (migrations `268670b22cd7` users + `f0d2f435b802` user_id backfill). `exercise_cards` остаётся общей библиотекой без `user_id`. ORM `@dual` decorator + `@with_session` гарантируют user-scoped queries.

---

## 2. Threat Model (STRIDE-lite)

### Actors

| Actor | Описание | Текущий доступ |
|---|---|---|
| **Owner** | Владелец инстанса | Полный доступ |
| **Viewer** | Друг через share-link | Read-only webapp |
| **Anonymous** | Случайный | Только лендинг |
| **Attacker — external** | Атакует API endpoints | Rate limiting на verify-code/демо |
| **Attacker — tenant** | Другой пользователь системы | **Новый actor — multi-tenant** |

### Threats

#### T1. Tenant Data Leak (Spoofing + Information Disclosure)

- **Что:** User A видит HRV/wellness/workouts user B.
- **Где:** все CRUD в `data/db/*`.
- **Severity:** Critical.
- **Mitigation:** row-level `user_id` filtering. Реализовано — все CRUD методы требуют `user_id` параметр.

#### T2. Cross-Tenant Intervals.icu Access (Elevation of Privilege)

- **Что:** User A триггерит sync, который пишет данные user B.
- **Где:** `IntervalsClient` использует per-user credentials.
- **Severity:** Critical.
- **Mitigation:** `IntervalsClient.for_user(user)` non-singleton factory, читает per-user Fernet-encrypted токен. Реализовано (Phase 1.2).

#### T3. JWT Token Reuse Across Tenants (Spoofing)

- **Что:** JWT содержит только `sub: chat_id`, нет tenant scope.
- **Где:** `api/auth.py:create_jwt / verify_jwt`.
- **Severity:** High (теоретически).
- **Mitigation (current):** `sub=chat_id` достаточно — DB lookup по `chat_id` даёт user_id; нет шеринга токенов между chat_id. Phase 2 audit (§9) откладывает claim-расширение до появления ban/logout flow.

#### T4. MCP Token Sharing (Elevation of Privilege)

- **Что:** Один MCP токен на всех.
- **Где:** ранее `MCP_AUTH_TOKEN`.
- **Severity:** High.
- **Mitigation:** Per-user `mcp_token` (random 32-byte hex, `users.mcp_token UNIQUE`). Lookup через `User.get_by_mcp_token`. Реализовано.

#### T5. Bot Command Injection (Tampering)

- **Что:** Чужой Telegram user отправляет `/dashboard` → видит чужие данные.
- **Где:** `bot/main.py` handlers.
- **Severity:** Medium → Critical в multi-tenant.
- **Mitigation:** `@athlete_required` / `@user_required` декораторы резолвят `User` через `chat_id` + `is_active` filter (`bot/decorator.py:48-101`). Реализовано.

#### T6. Secrets in Environment (Information Disclosure)

- **Что:** Все ключи в одном `.env`, один утёкший = доступ ко всему.
- **Severity:** Medium.
- **Mitigation:** Per-user `api_key_encrypted` / `intervals_access_token_encrypted` (Fernet). Globals (`ANTHROPIC_API_KEY`, `INTERVALS_API_KEY` legacy) остаются. Vault — out of scope.

#### T7. No Audit Trail (Repudiation)

- **Что:** Нет записи кто что делал.
- **Severity:** Medium.
- **Mitigation:** Phase 4 (not started). Текущий paliative: `logger.info` на write endpoints (`PATCH /api/athlete/goal/{id}`, `PATCH /api/athlete/profile`).

#### T8. AI Prompt Injection via Shared Context (Tampering)

- **Что:** Multi-tenant AI agent подмешивает данные другого tenant в prompt.
- **Где:** `bot/agent.py` `chat()`, `tasks/tools.py` `generate_morning_report`.
- **Severity:** High.
- **Mitigation:** `render_athlete_block(user)` строго scoped. MCP tools — `get_current_user_id()` из contextvars (не параметр). Реализовано.

#### T9. Denial of Service — Resource Exhaustion (Availability)

- **Что:** Один user спамит sync / AI chat / MCP, исчерпывая ресурсы.
- **Severity:** Medium → High в multi-tenant.
- **Mitigation:** 5 точечных in-process rate limiters (см. §9 Phase 3 punch-list). Migration to Redis pending Phase 3 trigger.

#### T10. Background Job Cross-Tenant Pollution (Information Disclosure)

- **Что:** Scheduler job обрабатывает всех пользователей и смешивает данные.
- **Где:** `bot/scheduler.py` cron jobs.
- **Severity:** High.
- **Mitigation:** Per-user dispatch loops (`actor_user_wellness(user_id)`, `actor_fetch_user_activities(user_id, ...)`). Каждый job tenant-scoped. Реализовано (Phase 1.3).

#### T11. initData Replay Attack (Spoofing)

- **Что:** Перехваченный initData переиспользуется спустя дни.
- **Где:** `api/deps.py:_verify_and_parse_init_data`.
- **Severity:** Medium.
- **Mitigation:** `auth_date < 15 min` (`api/deps.py:99-106`). 15 мин а не 5 — Mini App может быть открыто до первого API-вызова дольше 5. Реализовано.

#### T12. Telegram Login Widget Replay / Forgery (Spoofing)

- **Что:** Подделка или реплей Login Widget payload → выпуск JWT на чужого юзера.
- **Где:** `api/auth.py:verify_telegram_widget_auth`, `POST /api/auth/telegram-widget`.
- **Severity:** High (auth path).
- **Mitigation:**
  - HMAC-SHA256 над data-check-string (поля кроме `hash`, sorted by key, `\n`-разделители), `secret_key = SHA256(bot_token)` — спека https://core.telegram.org/widgets/login.
  - `hmac.compare_digest` constant-time.
  - Replay window: `auth_date` старше 24ч → reject.
  - Clock skew: `auth_date` в будущем >60s → reject.
  - Empty `TELEGRAM_BOT_TOKEN` → reject (никакой fallback).
  - **Auto-provisioning с default role `viewer`** — read-only access, upgrade до `athlete` ручной через CLI.
- **Operator setup:** `/setdomain` в `@BotFather` → `bot.endurai.me`; `TELEGRAM_BOT_USERNAME` в `.env`.
- **Tests:** `tests/api/test_telegram_widget_auth.py` (18 кейсов).

#### T13. RPE Callback Cross-Tenant Write (Tampering)

- **Что:** Подделка `callback_data` `rpe:{activity_id}:{value}` → запись RPE на чужую activity.
- **Где:** `bot/main.py:handle_rpe_callback`.
- **Severity:** Low (callback_data приходит от Telegram-клиента, авторизованного как bot chat; forward/share inline-button между юзерами Telegram'ом ограничен).
- **Mitigation:**
  - `@athlete_required` резолвит `User` из `chat_id`.
  - Input validation: parts length, int parse в try/except, `1 ≤ value ≤ 10`.
  - **Atomic CAS UPDATE:** `UPDATE activities SET rpe=:value WHERE id=:activity_id AND user_id=:user_id AND rpe IS NULL`. Три предиката в одном SQL — existence, tenant scope, single-shot. `rowcount==0` → `answerCallbackQuery("Уже оценено")` без раскрытия причины.
  - CHECK constraint `rpe IS NULL OR (rpe BETWEEN 1 AND 10)` на БД.
- **RPE не пишется через MCP** — only Telegram callback. Защита от prompt-injection «Claude записал RPE по фразе юзера».

#### T14. Silent Re-Activation of Blocked Users (Tampering / Availability)

- **Что:** Юзер блокирует бота → `is_active=False`. Открытая webapp-вкладка может авто-реактивировать → пинг-понг scheduler ↔ Telegram 403.
- **Где:** `data/db/user.py:get_or_create_from_telegram`.
- **Severity:** Medium (availability + auth policy leak, не data disclosure).
- **Mitigation:**
  - `get_or_create_from_telegram` ищет через `include_inactive=True` (предотвращает `IntegrityError` на UNIQUE), но **реактивацию не делает**.
  - Реактивация (`set_active_by_chat_id`) **только** в `bot/main.py:start` или `handle_my_chat_member` MEMBER-transition.
  - Webapp/initData/Login Widget читают через `get_by_chat_id` без `include_inactive` → блокированный юзер «не найден», anonymous flow.
- **Семантика `is_active`:** перегружена — «admin-deactivation» ∪ «Telegram-канал недоступен». Намеренное решение: блокировка бота = единый kill-switch.
- **Edge:** webapp-вкладка после блокировки → 401 → `apiFetch` clear JWT + редирект на `/login` + `<BotChatBanner/>` deep-link на бота.

#### T15. Intervals.icu OAuth — Account Mismatch via State Race (Tampering)

- **Что:** OAuth callback проверяет `db_user.athlete_id == response.athlete.id`, но state JWT не snapshot'ит `athlete_id` — между init и callback `athlete_id` в БД может измениться (shell admin, parallel OAuth, migration) → guard пропустит чужой `athlete.id`.
- **Где:** `api/routers/intervals/oauth.py:_validate_oauth_state` + `intervals_oauth_callback`.
- **Severity:** High (cross-tenant data attribution после Phase 2 когда Bearer sync начнёт писать данные).
- **Реалистичность:** низкая (2-юзерный dev). Реальна при public launch.
- **Mitigation (Phase 2 follow-up):** `athlete_id_snapshot` в state payload, reject если `db_user.athlete_id != state_snapshot.athlete_id` → `oauth_state_stale`. Плюс `aud='intervals_oauth_callback'` claim для defence-in-depth.

#### T16. Intervals.icu OAuth — Long-Lived Token Without Rotation (Information Disclosure)

- **Что:** Intervals.icu не выдаёт `refresh_token` / `expires_in` — access_token живёт «вечно» до явного revoke через Intervals UI. Украденный токен = perpetual read access.
- **Где:** `users.intervals_access_token_encrypted`.
- **Severity:** Medium (impact = read wellness/calendar, не financial).
- **Mitigation:**
  - Fernet encryption at rest.
  - **Нет rotation endpoint** — revoke только через Intervals UI. EndurAI detect'ит через lazy 401 в `IntervalsClient`.
  - Callback логирует только structure (`keys=sorted(...)`, `body_len`) — never full token. Sentry data scrubbing.
  - Token exchange через POST body (не URL) — не в access logs.
- **Phase 2 TODO:** rate limit `POST /api/intervals/auth/init` (5 req / 5 min per user) против OAuth-init flood.

#### T17. User Memory — Vendor-Side Retention of Prose PII (Information Disclosure)

- **Что:** `user_facts` хранит свободно-текстовые свойства (астма, аллергии, беременность, семья). `render_athlete_block` инжектит в system prompt → уходит в Anthropic API → retention до 30 дней (default ToS).
- **Где:** `bot/prompts.py:render_athlete_block` + `data/db/user_fact.py:UserFact`.
- **Severity:** Low-Medium.
- **Mitigation:**
  - **Local:** Sentry scrubber (`sentry_config.py:SENSITIVE_KEYS` включает `"fact"`).
  - **Vendor retention:** trade-off задокументирован в `USER_CONTEXT_SPEC` §11.6. Self-hosted LLM сравнимого качества — 10×+. Enterprise contract с zero-retention — opt-in на первом non-owner athlete.
  - **User control:** inline «🗑 Забудь это» + Phase 3 webapp UI.
  - **Scope limit:** `save_fact` docstring запрещает транзиентные moods и данные из `athlete_settings`/`athlete_goals`.
  - **Phase 2 (async extractor) gated** per-user через `get_fact_metrics.tool_facts_per_100_msgs_30d < 3` ∧ `chat_msgs ≥ 100`.

#### T18. Login Widget Signup Without Bot Chat (Availability / Operational Hygiene)

- **Что:** Login Widget создаёт `users` row из подписанного payload, но без `/start` Telegram-боты не могут писать первыми → 400 `chat not found` бесконечно (fan-out actors + утечка bot-token в URL в Sentry GH-integration).
- **Где:** `api/routers/auth.py:auth_telegram_widget` → `User.get_or_create_from_telegram`.
- **Severity:** Medium (op noise + secret leakage в issue tracker; не cross-tenant).
- **Mitigation:**
  - **Колонка `users.bot_chat_initialized`** (migration `t0a1b2c3d4e5`, default False для новых).
  - **Set `True`** только в `bot/main.py:start` или `handle_my_chat_member` MEMBER-transition.
  - **OAuth-init gate** → 412 `bot_chat_not_initialized` **до** rate-limit'а.
  - **Defensive `_suppress`** в `TelegramTool` — `send_*` no-op при `bot_chat_initialized=False`.
  - **Self-healing**: 400 `chat not found / user is deactivated / peer_id_invalid` → `set_bot_chat_initialized(chat_id, False)`. Под guard'ом `str(self.user.chat_id) == chat_id` — защита от cross-tenant flag wipe.
  - **Frontend banner** (`<BotChatBanner/>` + `<OnboardingPrompt/>`) на всех путях.
  - **Sentry scrubbing**: regex `bot\d+:[A-Za-z0-9_-]{30,}` редактирует TG bot-токены в httpx URLs до Sentry capture.
- **Семантика vs `is_active`:**
  - `is_active=False` = явный opt-out из сервиса (фильтруется в `get_by_chat_id`/`get_by_mcp_token`).
  - `bot_chat_initialized=False` = TG-канал недоступен, но webapp/MCP работают.

#### T19. Achievement Webhook — Cross-Tenant Write via Tampered `activity.id` (Tampering)

- **Что:** `ACTIVITY_ACHIEVEMENTS` webhook резолвит tenant через `athlete_id`, но `activity_id` берёт из payload без проверки ownership. Атакующий с `INTERVALS_WEBHOOK_SECRET` (single shared secret) может подсунуть `{athlete_id: i_victim_A, activity: {id: i_user_B_activity, icu_achievements: [...]}}`.
- **Где:** `api/routers/intervals/webhook.py:_dispatch_achievements`.
- **Severity:** High (cross-tenant data attribution; зависит от сохранности `INTERVALS_WEBHOOK_SECRET`).
- **Mitigation:**
  - **Tenant existence guard:** `Activity.exists_for_user(user_id=user.id, activity_id=str(activity_id))` перед `save_bulk`.
  - **Telegram notification остаётся за guard'ом** — `TelegramTool(user=user_dto)` + `_suppress`-gate (T18) tenant-scoped.
  - **Idempotency:** `UNIQUE(user_id, activity_id, achievement_id)` + `ON CONFLICT DO NOTHING`.
- **Not covered:** per-tenant webhook signing — Phase 2 follow-up.

---

## 3. Data Isolation Strategy (current state)

**Tenant = User.** Один пользователь = один набор данных. Coach/team/organization model — out of scope.

**Pattern:** middleware-level `user_id` filtering, **not** PostgreSQL RLS (RLS требует `SET LOCAL` per-connection — плохо с async pooling). Все CRUD методы требуют `user_id` параметр. ORM `@dual` + `@with_session` гарантируют consistent scoping. Schema details в `data/db/` (single source of truth); историческая migration list — `migrations/versions/268670b22cd7_*` + `f0d2f435b802_*`.

**Запрещено:** глобальные CRUD без `user_id` (типа `WellnessRow.get(date)`). Audit catches их через test fixtures.

---

## 4. Phase 2 — Auth Upgrade (deferred 2026-05-09)

**Audit verdict:** текущий threat profile не оправдывает работу. Что есть vs spec:

| # | Item | Status | Reasoning |
|---|---|---|---|
| 23 | JWT claims (`tenant_id`/`role`/`scope`/`jti`/`iss`/`aud`) | DEFER | `api/auth.py:create_jwt` несёт `{sub, iat, exp, purpose}`. `role`/`tenant_id` в payload **минус** для безопасности: stale-данные до 7d TTL. `tenant_id` ускоряет lookup на ~1ms — не bottleneck. `iss`/`aud` cargo cult для single-service |
| 24 | PyJWT migration | DEFER | Кастомный HS256 рабочий, протестирован. Не блокер |
| 25 | initData freshness < 15 min | DONE | `api/deps.py:99-106`, widget flow `api/auth.py:138-140` |
| 26 | Bot middleware `resolve_tenant()` | DONE (different shape) | `@athlete_required` / `@user_required` в `bot/decorator.py:48-101` функционально эквивалентны; отдельный middleware-файл = churn |
| 27 | API deps `get_current_user()` | DONE | `api/deps.py:17-83` + role guards `:111-152` |
| 28 | MCP per-tenant auth | DONE (different shape) | `api/server.py:35-83` `MCPAuthMiddleware` → `User.get_by_mcp_token`. Plain Bearer + DB lookup проще JWT для одной аудитории — токен сам = secret |
| 29 | Token revocation (Redis blacklist для `jti`) | DEFER | Нет use case: single user = self-data, нет ban/logout, утечка JWT ≠ cross-tenant breach. 7d TTL автоматически. Появится ban — `users.token_epoch INT` (5 строк, проще blacklist'а) |
| 30 | `created_by` на GitHub issue (#48) | DONE | `mcp_server/tools/github.py:122` пишет `_Reported via MCP by user_id={user_id}_` в body — внешний resource, отдельной колонки не надо |

### Phase 2 trigger conditions

Реактивируем при появлении **любого** из:

- Ban / soft-delete flow (`User.deactivate()` начинают вызывать в проде).
- Утечка JWT в логах / Sentry / GitHub.
- Multi-audience MCP (внешние клиенты = `external` audience claim становится осмысленным).
- Юридический mandate logout flow (GDPR Art. 17, при включении регистрации).

### Phase 2 punch-list (при триггере)

- **Item 29 (revocation):** колонка `users.token_epoch INT NOT NULL DEFAULT extract(epoch from now())`, проверка `payload['iat'] >= user.token_epoch` в `verify_jwt`. На deactivate / ban / forced-logout — `UPDATE users SET token_epoch=now()`. Один запрос, без Redis-blacklist.
- **Item 23:** добавить только то, что реально используется в endpoint guards. На текущей архитектуре — ничего.

---

## 5. Phase 3 — Security Hardening (deferred 2026-05-09)

**Audit:** мало пользователей, мало запросов, нет наблюдаемого abuse. Точка старта зафиксирована.

| # | Item | Status | План |
|---|---|---|---|
| 32 | Redis rate limiting (#37) | DEFER | Redis client `data/redis_client.py:14-47` (get/init/close, без RL helpers). 5 in-process лимитеров защищают конкретные векторы — мигрировать на Redis при multi-worker uvicorn / restart-loss |
| 33 | CORS whitelist | DONE (partial) | `api/server.py:129-135` уже `allow_origins=[settings.API_BASE_URL]`, не `*`. `https://{web,webk,weba,webz}.telegram.org` — cosmetic, WebView не шлёт Origin |
| 34 | Security headers middleware | TODO | `api/server.py` не выставляет `X-Frame-Options` / CSP / HSTS / `X-Content-Type-Options`. Триггер: production-grade hardening / security audit |
| 35 | Input validation (date ±2y, week_offset ±52, activity ownership) | TODO | Pydantic типы есть, range/ownership проверки точечные. Per-endpoint при добавлении новых API |
| 36 | MCP tool tenant isolation audit | TODO | 59 tools. Read-only review через `security-reviewer` subagent. Триггер: до открытия публичной регистрации |
| 37 | Fernet key rotation plan | DEFER | Ключ не компрометирован. `re_encrypt_all` script — gold-plating |

### Phase 3 punch-list (item 32 — Redis migration)

5 in-process rate limiters готовы к Redis-миграции:

| File:line | Key | Limit | Назначение |
|---|---|---|---|
| `api/routers/auth.py:37-44` | `_demo_attempts[ip]` | 5 / 5 min | Demo password brute-force shield |
| `api/routers/auth.py:38` | `_mcp_config_last_access[user.id]` | 1 / 60 s | MCP token disclosure anti-spam |
| `api/routers/auth.py:64` | `_retry_backfill_last_success[user.id]` | 1 / 1 h | Bootstrap backfill retry button |
| `api/routers/intervals/oauth.py:99` | OAuth init | (см. файл) | Anti-flood OAuth initiation |
| `mcp_server/tools/github.py:38-56` | sliding-window per user.id | 5 / 24 h | GitHub issue creation cap |

**Реализация при триггере:**

1. `data/redis_client.py` — sliding-window helper через ZADD/ZREMRANGEBYSCORE: `check_and_record_rate_limit(key, limit, window_sec) → retry_after | None`. Fail-open при Redis-down.
2. Мигрировать 5 callsite'ов — ключи `rl:demo:{ip}`, `rl:mcp_token:{user_id}`, `rl:backfill:{user_id}`, `rl:oauth_init:{user_id}`, `rl:gh_issue:{user_id}`. 429 + `Retry-After` header.
3. Удалить in-process dicts + lazy-prune'ы.
4. Тесты: round-trip + Redis-down fallback + regression на каждом callsite.

**Skip from spec §6.1:** generic global limits (60 GET/min, 30 MCP/min, 20 bot msg/min) — не реализуем. Для текущего профиля ложноположительные 429 > выгода.

### Phase 3 trigger conditions

- Multi-worker uvicorn (in-process dicts перестают шариться).
- Frequent restarts под нагрузкой (counters resets → demo brute-force).
- Открытие регистрации (item 36 MCP audit становится обязательным до).
- Любой security audit / production hardening.

---

## 6. Phase 4 — Observability (not started)

- Audit log table + middleware.
- AI usage tracking — `cost_usd DECIMAL(10, 6)` (запас для высокого usage).
- Structured logging с `tenant_id` в каждой строке.
- Health data access — отдельный log level для чувствительных endpoints.
- Job failure tracking (#3) + Sentry (#40 ✅ done).

---

## 7. Phase 5 — Registration & Multi-User (not started)

- Manual onboarding process definition (#47) — текущий полу-автомат описан в `docs/OPERATIONS.md`.
- Bot `/start` → onboarding flow (connect Intervals.icu — реализован для OAuth, см. `docs/OAUTH_BOOTSTRAP_SYNC_SPEC.md`).
- Registration gates: invite code alphanumeric **12+ chars** (`36^12 ≈ 4.7×10^18`), rate limit 3 attempts / 15 min per IP + per chat_id, 10 неудач → 24h block. **Не 6-digit numeric** (`10^6 = 1M` комбинаций, при 5 req/5 min = полный перебор за ~3.5 дня).
- Per-tenant AI limits.
- Data deletion endpoint (right to erasure) — `DELETE /api/account` удаляет все данные (GDPR minimum).
- Data export endpoint — `GET /api/account/export` → JSON/ZIP всех данных пользователя.

---

## 8. Multi-Tenant Shared Resources (#48)

Некоторые ресурсы общие для всех tenants:

| Resource | Ownership | Rule |
|---|---|---|
| GitHub issues | Общие (один repo, public) | `create_github_issue` available любому athlete с валидным `mcp_token`. Attribution в body — **только** `user_id=N` (Telegram `@username` и Intervals `athlete_id` НЕ публикуются — public repo, PII linkage avoidance). Per-user sliding-window cap 5 issues / 24h (in-process до multi-worker). `title ≤ 200`, `body ≤ 8000` cap |
| Exercise cards (shared library) | `user_id = NULL` = общие | User создаёт свои (`user_id` set) или использует общие. Общие создаёт только owner |
| System prompts | Общие | Один набор промптов для всех. Athlete-specific данные из `users` table через `render_athlete_block` |
| Scheduler jobs | Per-tenant | Каждый user обрабатывается отдельно. Ошибка одного не ломает других (Phase 1.3) |

---

## 9. Out of scope (explicitly)

- **Team / organization model** — coach manages multiple athletes. Отдельная спека после базового multi-tenant.
- **OAuth2 / OIDC** — overkill для Telegram-first. JWT + initData достаточно.
- **PostgreSQL Row-Level Security** — не совместимо с async pooling.
- **Zero-trust / mTLS** — не нужно для single-VPS deployment.
- **Penetration testing** — после реализации, не на этапе спеки.
