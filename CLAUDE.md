# Triathlon AI Agent — Project Specification

> Architecture, stack, structure, and business logic.

---

## What We're Building

Personal AI agent for a triathlete: syncs wellness/HRV/training from Intervals.icu, evaluates recovery and planned workouts, sends morning reports via Telegram Bot, exposes data via MCP server, and provides an interactive dashboard via Telegram Mini App.

---

## Tech Stack

| Component         | Technology                                                            |
| ----------------- | --------------------------------------------------------------------- |
| Language          | Python 3.12+                                                          |
| Package Manager   | Poetry                                                                |
| Data Source       | Intervals.icu API                                                     |
| AI Analysis       | Anthropic Claude API (`claude-sonnet-4-6`)                            |
| Telegram Bot      | `python-telegram-bot` v21+                                            |
| Scheduler         | `APScheduler`                                                         |
| Database          | PostgreSQL 16 + `SQLAlchemy` (async) + Alembic                        |
| API Server        | `FastAPI` + `uvicorn`                                                 |
| Mini App Frontend | React 18 + TypeScript + Vite + Tailwind CSS + Chart.js                |
| Backend Hosting   | Docker Compose on VPS                                                 |
| Error Monitoring  | Sentry (`sentry-sdk[fastapi,dramatiq]`)                               |
| Config            | `pydantic-settings` + `.env`                                          |

---

## Project Structure

```
triathlon-agent/
├── config.py / sentry_config.py / cli.py
├── bot/          # Telegram bot: main.py (handlers), agent.py (ClaudeAgent), tools.py (MCPClient), prompts.py, scheduler.py
├── tasks/        # Dramatiq actors: broker.py, actors/ (wellness, activities, training_log, reports, workout)
├── data/         # Domain: metrics.py, hrv_activity.py, workout_adapter.py, ramp_tests.py, crypto.py
│   ├── intervals/  # Intervals.icu client + DTOs
│   ├── garmin/     # Garmin GDPR parser + importer
│   └── db/         # SQLAlchemy ORM (@dual sync/async), all models, decorators
├── api/          # FastAPI: server.py, auth.py, deps.py, routers/ (wellness, activities, workouts, jobs, auth)
├── mcp_server/   # 49 MCP tools + 3 resources, context.py (user_id contextvars), sentry.py
├── webapp/       # React 18 SPA (Vite + TypeScript + Tailwind)
├── migrations/ / templates/ / static/ / locale/ / docs/ / tests/
```

---

## Database Schema

29 tables. Full column specs in `data/db/`. Key tables:

**Core:** `users` (multi-tenant, chat_id, role, api_key_encrypted, mcp_token, is_active, last_donation_at, + Intervals.icu OAuth: `intervals_access_token_encrypted` / `intervals_oauth_scope` / `intervals_auth_method` — `"api_key"` | `"oauth"` | `"none"` — see `docs/INTERVALS_OAUTH_SPEC.md`), `athlete_settings` (per-sport thresholds), `athlete_goals` (race goals + CTL targets), `wellness` (daily Intervals.icu data + recovery score + AI recommendations).

**Analysis:** `hrv_analysis` (dual-algorithm baselines), `rhr_analysis` (RHR baselines, inverted), `activity_details` (zones, intervals, EF, decoupling), `activity_hrv` (DFA a1, Ra/Da), `pa_baseline` (14d rolling), `fitness_projection` (CTL/ATL/rampRate decay curve from `FITNESS_UPDATED` webhook, dates can be future).

**Training:** `scheduled_workouts`, `activities` (incl. `is_race`/`sub_type`/`rpe` — Borg CR-10 1-10 with `CHECK` constraint, see `docs/RPE_SPEC.md`), `ai_workouts`, `training_log` (pre/actual/post + compliance + `race_id` FK), `exercise_cards`, `workout_cards`, `races` (name, distance, finish/goal time, placement, surface/weather, RPE, notes, race-day CTL/ATL/TSB/HRV/recovery snapshot). See `docs/RACE_TAGGING.md`.

**Tracking:** `mood_checkins` (1-5 scales), `iqos_daily`, `api_usage_daily`, `star_transactions` (Telegram Stars donation ledger, `UNIQUE(charge_id)` for webhook idempotency, `refunded_at` nullable — see `docs/DONATE_SPEC.md`).

**Garmin (9 tables):** `garmin_sleep`, `garmin_daily_summary`, `garmin_training_readiness`, `garmin_health_status`, `garmin_training_load`, `garmin_fitness_metrics`, `garmin_race_predictions`, `garmin_bio_metrics`, `garmin_abnormal_hr_events`.

---

## Implementation Status

All core modules done. Multi-tenant Phase 1.3 complete (per-user MCP auth, contextvars, scheduler). Intervals.icu OAuth Phase 1 complete, Phase 2 complete (Bearer auth in `IntervalsClient`, viewer→athlete promotion + mcp_token generation in callback, auto-dispatch sync actors for new users). Webhook research done (10/10 event types documented in `docs/INTERVALS_WEBHOOKS_RESEARCH.md`). Pending: OAuth disconnect endpoint, lazy 401 handling, webhook dispatchers, personal patterns cron, MT Phase 2 (JWT upgrade).

**Key patterns:** ORM uses `@dual` (auto sync/async dispatch), `@with_session`/`@with_sync_session`. `AthleteSettings.get_thresholds()` + `AthleteGoal.get_goal_dto()`. MCP tools use `get_current_user_id()` from contextvars. Sentry with `@sentry_tool` for MCP. Bot decorators: `@athlete_required` (needs `athlete_id`), `@user_required` (any active user — for `/lang`, `/silent`, `/donate`). API DTOs in `api/dto.py`.

**Webapp pages:** Today, Landing, Login, Wellness, Plan, Activities, Activity, Dashboard, Settings. Bottom tabs. `/report` → `/wellness`. Global auth gate in `App.tsx`: users without `athlete_id` see `<OnboardingPrompt />` on all data routes until OAuth onboarding completes. PWA manifest + favicon/icons (SVG + ICO + apple-touch + android-chrome).

---

## Environment Variables (.env)

See `.env.example` for full list. Key vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` (for Login Widget), `TELEGRAM_WEBHOOK_URL` (empty=polling), `ANTHROPIC_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `API_BASE_URL` (single URL for API + webapp + static + CORS origin), `INTERVALS_API_KEY`/`INTERVALS_ATHLETE_ID` (legacy owner, being replaced by per-user OAuth), `INTERVALS_OAUTH_CLIENT_ID`/`INTERVALS_OAUTH_CLIENT_SECRET`/`INTERVALS_OAUTH_REDIRECT_URI` (per-user OAuth), `INTERVALS_WEBHOOK_SECRET` (shared secret for webhook verification), `INTERVALS_WEBHOOK_MONITORING` (opt-in Sentry sampling, default false), `TIMEZONE=Europe/Belgrade`, `HRV_ALGORITHM=flatt_esco`, `MCP_AUTH_TOKEN`, `FIELD_ENCRYPTION_KEY` (Fernet), `DEMO_PASSWORD` (shared password for read-only demo access, empty=disabled), `SENTRY_DSN` (empty=disabled).

**Telegram Login Widget setup** (one-time, for web login): in `@BotFather` run `/setdomain` → choose your bot → enter `bot.endurai.me` (no protocol, no path). Widget will only render on that domain. Set `TELEGRAM_BOT_USERNAME` in `.env` to the bot username (without `@`). See `api/auth.py:verify_telegram_widget_auth` for the HMAC-SHA256 verification logic (`docs/MULTI_TENANT_SECURITY.md` threat T3 scope).

---

## Business Rules & Thresholds

> Full implementations in `data/metrics.py`.

**CTL/ATL/TSB** — All values from Intervals.icu API (τ_CTL=42d, τ_ATL=7d). NOT recalculated. Thresholds calibrated for Intervals.icu, not TrainingPeaks.
TSB zones: >+10 under-training | -10..+10 optimal | -10..-25 productive overreach | <-25 overtraining risk.

**HRV — Dual Algorithm** (both always computed, `HRV_ALGORITHM` selects primary for recovery):

- Flatt & Esco: today vs 7d mean, asymmetric bounds (−1/+0.5 SD), fast response
- AIEndurance: 7d mean vs 60d mean, symmetric ±0.5 SD bounds, chronic fatigue detection
- Status: green (full load) / yellow (monitor) / red (reduce) / insufficient_data (<14 days)

**RHR** — Inverted vs HRV: elevated RHR = red. Bounds: ±0.5 SD of 30d mean.

**Recovery Score (0-100)** — Weights: RMSSD 35%, Banister 25%, RHR 20%, Sleep 20%.
Categories: excellent >85, good 70-85, moderate 40-70, low <40.
Recommendations: zone2_ok / zone1_long / zone1_short / skip.

**Cardiac Drift (Decoupling)** — Pa:Hr from Intervals.icu, not recalculated.
Filter: `is_valid_for_decoupling()` — VI <= 1.10, >70% Z1+Z2, bike >= 60min / run >= 45min, swim excluded.
Traffic light: green (<5%) / yellow (5-10%) / red (>10%). Uses abs() for negative drift.
Trend: last-5 median via `get_efficiency_trend(strict_filter=True)`. Theory: `docs/knowledge/decoupling.md`.

**HR Zones** — synced from Intervals.icu sport-settings (source of truth). `get_zones` MCP tool returns boundaries from DB; fallback to calculated zones if not yet synced. Zone count varies per user (typically 5-7 zones). Fallback defaults:
Run (7-zone): Z1 0-84%, Z2 85-89%, Z3 90-94%, Z4 95-99%, Z5 100-103%, Z6 103-106%, Z7 106%+
Bike (5-zone): Z1 0-68%, Z2 68-83%, Z3 83-94%, Z4 94-105%, Z5 105-120%

---

## AI Architecture

### MCP as Unified Data Layer

All AI tool calls go through MCP server via HTTP — no direct DB access from AI code.

```
Telegram text → ClaudeAgent (bot/agent.py)
  → MCPClient.list_tools() → HTTP /mcp tools/list (Streamable HTTP, SSE)
  → Claude API (claude-sonnet-4-6, tools from MCP)
  → tool_use? → MCPClient.call_tool() → HTTP /mcp tools/call
  → final text → Telegram
```

**ClaudeAgent** (`bot/agent.py`): thin async client. No business logic. Per-user `mcp_token` passed to `MCPClient` per call.

**MCPClient** (`bot/tools.py`): async MCP Streamable HTTP client. Tool list cached at class level. Session per-instance (per-token).

**MCPTool** (`tasks/tools.py`): sync MCP client for dramatiq actors (morning report generation).

### Morning Report (via Dramatiq)

Generated by `actor_compose_user_morning_report` → `MCPTool.generate_morning_report_via_mcp()` → sync Claude API + MCP tool loop → saves `ai_recommendation` to wellness row.

### Telegram Chat

Stateless. Each message: `agent.chat(text, mcp_token=user.mcp_token)` → Claude + MCP tools → response. Reply context included when replying to a message.

**Distance-based workouts:** `WorkoutStep` supports `distance` (meters) as alternative to `duration` (seconds). Mutually exclusive. `target: "PACE"` set for Swim/Run.

**Intensity target mandate:** `PlannedWorkoutDTO._check_steps_have_targets` rejects any terminal (non-repeat-group) step without `hr` / `power` / `pace`. Garmin/Wahoo watches only alert on the target corridor when a numeric target is present, so text-only steps (`"Z2" label + duration`) are forbidden. **Exception:** sport `Other` (yoga, stretching, mobility) skips this validation — watches don't need intensity targets for these activities. Per-sport convention: Run → `hr` with `%lthr` units, Ride → `power` with `%ftp`, Swim → `pace` with `%pace`. Use `value` (low) + `end` (high) for a corridor. The `suggest_workout` MCP tool docstring and `SYSTEM_PROMPT_CHAT` (workout-generation section) both enforce this contract — the validator is the backstop if the model forgets.

**Strava source filter:** Intervals.icu returns 422 `Cannot read Strava activities via the API` for `source == STRAVA` activities (licensing). `actor_fetch_user_activities` drops them **before** `Activity.save_bulk` so they never enter the DB or trigger downstream pipelines. `ActivityDTO.source` carries `GARMIN_CONNECT` / `OAUTH_CLIENT` / `STRAVA` / etc. from Intervals.icu.

---

## Bot Commands (bot/main.py)

Commands use `@athlete_required` (needs `athlete_id`) or `@user_required` (any active user) decorator — resolves `User` from Telegram `chat_id`.

```
/start      — welcome + create User in DB. Branches on `athlete_id`: new users get "🔗 Подключить Intervals.icu" WebApp button → /settings onboarding. Existing athletes get the generic dashboard entry.
/morning    — trigger morning report via dramatiq actor
/dashboard  — dashboard link (Mini App)
/workout    — interactive workout generation: sport picker → dry-run preview → "Отправить в Intervals" button
/web        — one-time code for desktop login (5 min TTL)
/stick      — increment IQOS stick counter for today (owner only)
/health     — server diagnostics: system stats, DB/Redis/queues, per-athlete Intervals.icu token check (OAuth/api_key), daily token usage per user, Anthropic (owner only)
/lang       — set language: /lang ru or /lang en (@user_required — works for viewers too)
/silent     — toggle silent mode (@user_required — works for viewers too)
/whoami     — show current user info (chat_id, role)
/donate     — voluntary support via Telegram Stars (XTR), 3 tiers (50/200/500)
<text>      — free-form AI chat (stateless, tool-use via MCP, per-user token)
<photo>     — AI chat with vision (base64 image + caption)
<reply>     — reply context included as "[В ответ на: ...]"
```

**Callback handlers:** `ramp_test:{sport}` — create ramp test, `update_zones` — update HR zones, `workout:{sport}` / `workout_push` / `workout_cancel` — `/workout` ConversationHandler states, `rpe:{activity_id}:{value}` — single-shot RPE rating from post-activity notification (see `docs/RPE_SPEC.md`).

**`/workout` two-phase flow:** generation calls `suggest_workout` (or `compose_workout` for fitness) with `dry_run=True` / `push_to_intervals=False`. `bot/agent.py:chat()` returns `ChatResult(text, tool_calls, nudge_boundary, request_count)` — `tool_calls` holds every tool_use block Claude emitted (deep-copied), filtered via the `tool_calls_filter` param to `set(_PREVIEWABLE_TOOLS.keys())` to avoid copying unrelated large inputs. The handler stashes the last previewable call in `context.user_data["pending_workout"]`. On "✅ Отправить в Intervals" tap, `workout_push` pops the draft, flips the preview flag, and calls `MCPClient.call_tool` directly **without** re-invoking Claude — so what lands in Intervals.icu is bit-for-bit identical to the preview. Prevents prompt-injection on the state-mutating step and saves one inference round per push. See `bot/main.py:_PREVIEWABLE_TOOLS` for the flag-name mapping.

**Donate nudge:** after every N-th chat request (default N=5), free-form handlers (`handle_chat_message`, `handle_photo_message`) append a nudge as a **separate** Telegram message via `bot/donate_nudge.py:get_nudge_text()`. Policy lives in `should_show_nudge(user, nudge_boundary, request_count)` — agent only reports the raw `nudge_boundary` signal, all suppression rules (owner opt-out, recent donation, daily cap) apply in the handler. `/workout` handlers deliberately skip the nudge (rating limit counted, but not shown — see `DONATE_SPEC.md` §11.6). Suppression after a donation: `User.last_donation_at` is set in `successful_payment_callback` via `User.mark_donation`, and `should_show_nudge` skips for `DONATE_NUDGE_SUPPRESS_DAYS` (default 7 days).

---

## API Endpoints

```
GET  /api/report                        — full morning report (today)
GET  /api/wellness-day?date=YYYY-MM-DD  — wellness for any date (navigable)
GET  /api/scheduled-workouts?week_offset=0 — weekly plan (Mon-Sun)
GET  /api/activities-week?week_offset=0 — weekly activities
GET  /api/activity/{id}/details         — full activity stats + zones + DFA
GET  /api/progress?sport=bike&days=90   — aerobic efficiency trend (EF/SWOLF/pace)
GET  /api/fitness-projection            — CTL/ATL/rampRate decay curve (from FITNESS_UPDATED webhook)
POST /api/auth/verify-code              — verify one-time code → JWT
POST /api/auth/demo                     — demo password → JWT with role=demo (read-only owner data)
GET  /api/auth/me                       — auth status + language + intervals connection + profile/goal
GET  /api/auth/mcp-config                — per-user MCP config (rate-limited, audit-logged)
PUT  /api/auth/language                 — update user language (ru/en)
POST /api/intervals/auth/init            — initiate OAuth (authenticated XHR) → {authorize_url}
GET  /api/intervals/auth/callback        — OAuth callback: code → token → DB → redirect
POST /api/intervals/webhook              — Intervals.icu push webhooks: secret verification, DTO parsing, Sentry monitoring. 10/10 event types researched (see docs/INTERVALS_WEBHOOKS_RESEARCH.md)
POST /api/jobs/sync-wellness            — dispatch dramatiq actor (require_athlete)
POST /api/jobs/sync-workouts            — dispatch dramatiq actor (require_athlete)
POST /api/jobs/sync-activities          — dispatch dramatiq actor (require_athlete)
GET  /health
POST /telegram/webhook                  — webhook mode only
POST /mcp                               — MCP (Streamable HTTP, Bearer auth)
GET  /static/exercises/{id}.html        — generated exercise card HTML (StaticFiles)
GET  /static/workouts/{date}-{slug}.html — generated workout HTML (StaticFiles)
```

**Dashboard API** (scaffold, mock data): `/api/dashboard`, `/api/training-load`, `/api/goal`, `/api/weekly-summary`, job trigger stubs.

**Auth:** Two methods in `Authorization` header — Telegram initData (HMAC-SHA256, 15-min freshness) or `Bearer <jwt>`. Demo mode: `POST /api/auth/demo` with `DEMO_PASSWORD` → JWT with `purpose=demo` claim, resolved to owner's User with virtual `role="demo"` (read-only, mutation endpoints blocked via `require_athlete`). Resolves to `User` object via `get_current_user()`. Dependencies: `require_viewer` (any authenticated user), `require_athlete` (active + athlete_id, blocks demo), `require_owner`. `get_data_user_id(user)` always returns `user.id`. API DTOs centralized in `api/dto.py`.

---

## Webapp (webapp/) — React SPA

React 18 + TypeScript + Vite 6 + React Router v7 + Tailwind CSS v3 + Chart.js v4 + React Context. Light theme, Inter font, mobile-first, Telegram Mini App compatible.

**Routes:** `/` (Today/Landing), `/wellness`, `/plan`, `/activities`, `/activity/:id`, `/dashboard` (3 tabs), `/settings`, `/login`. Bottom tabs navigation.

**Auth:** `AuthProvider` (React Context): Telegram initData → JWT fallback → anonymous. `useAuth()` hook. Desktop: `/web` → 6-digit code → JWT. **Global auth gate** in `App.tsx`: fetches `/api/auth/me` on login, checks `intervals.athlete_id`. If missing → all data routes render `<OnboardingPrompt />` (issue #185). Settings and Login always accessible for OAuth onboarding.

**i18n:** `react-i18next` with `ru.json` / `en.json`. Backend sends localized strings for wellness verdicts (`_cv_verdict`, `_swc_verdict`, `_format_sleep_duration`) and recovery categories (`get_category_display`, `get_recommendation_text`) based on `user.language`. Frontend `StatusBadge` uses i18n keys.

**Build:** Dev: `cd webapp && npm run dev` (:5173, proxies /api → :8000). Prod: Docker multi-stage Node 20 → Python 3.12.

---

## CLI (cli.py)

```bash
python -m cli shell                                              # interactive Python shell with app context
python -m cli sync-settings <user_id>                            # sync athlete settings & goals from Intervals.icu
python -m cli sync-wellness <user_id> [period]                   # force re-sync wellness + HRV/RHR/recovery day by day
python -m cli broadcast-migration [--dry-run]                    # notify active athletes about bot migration (one-time)
python -m cli sync-activities <user_id> [period] [--force]       # force re-sync activities day by day
python -m cli sync-training-log <user_id> [period]               # recalculate training log from existing activities
python -m cli import-garmin <user_id> <source> [--types] [--period] [--force] [--dry-run]  # import Garmin GDPR export
python -m cli backfill-races <user_id> [period]                  # create Race records for historical race activities
```

### Period formats for `sync-wellness` and `sync-activities`

| Format                    | Example                  | Result                         |
| ------------------------- | ------------------------ | ------------------------------ |
| (none)                    | `sync-activities 2`      | Last 180 days                  |
| Quarter                   | `sync-activities 2 2025Q4`| 2025-10-01 → 2025-12-31      |
| Month                     | `sync-activities 2 2025-11`| 2025-11-01 → 2025-11-30     |
| Date range                | `sync-activities 2 2025-01-01:2025-03-31` | Explicit range |

All sync commands dispatch dramatiq tasks with 20s delay between days. Requires a running worker (`dramatiq tasks.actors`) and Redis.

---

## Database Migrations (Alembic)

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration (auto-detect model changes)
alembic revision --autogenerate -m "description"

# Show current revision
alembic current

# Show migration history
alembic history

# In Docker
docker compose run --rm api alembic upgrade head
```

Migrations run automatically on deploy via the `migrate` service in `docker-compose.yml`.

---

## Onboarding a New User

### Step 1: User sends /start to the bot

The bot creates a `User` row with `role=viewer`, no `athlete_id`.

### Step 2: Owner configures user credentials via shell

```bash
python -m cli shell
```

```python
from data.db import User
from data.db.common import get_sync_session

with get_sync_session() as s:
    user = s.get(User, 2)  # user_id
    user.role = "athlete"
    user.athlete_id = "i543070"       # Intervals.icu athlete ID
    user.set_api_key("your-api-key")  # encrypted in DB via Fernet
    user.mcp_token = "generated-token"
    user.age = 30
    user.primary_sport = "triathlon"   # triathlon / run / ride / swim / fitness
    s.commit()
```

### Step 3: Sync athlete settings from Intervals.icu

```bash
python -m cli sync-settings 2
```

Pulls sport-specific thresholds (LTHR, FTP, max HR, threshold pace) and race goals (RACE_A/B/C events) from Intervals.icu into `athlete_settings` and `athlete_goals` tables.

### Step 4: Sync historical data

```bash
python -m cli sync-wellness 2               # 1. wellness + training log POST
python -m cli sync-activities 2             # 2. activities + training log PRE/ACTUAL
```

For each day: fetches wellness data (HRV, CTL, sleep) and activities from Intervals.icu, computes HRV/RHR baselines, Banister/ESS, recovery scores, and syncs activity details.

### Step 5 (optional): Set CTL targets for goals via shell

### Quick onboard (alternative to Steps 3-4)

```bash
python -m bot.cli onboard <user_id> --days 180
```

Runs sequentially: sync wellness → sync activities → sync details → sync workouts.

---

## Docker

```bash
docker compose up -d db                  # PostgreSQL only
docker compose up -d                     # all (includes React build, bot via webhook in api)
docker compose run --rm api python -m cli sync-settings 2   # CLI in Docker
docker compose run --rm api python -m cli sync-wellness 2   # CLI in Docker
docker compose run --rm api python -m cli sync-activities 2     # CLI in Docker
```

Multi-stage build: Node 20 → React SPA, Python 3.12 → serves built assets. No Node in final image.

---

## Key Implementation Notes

- **Intervals.icu API** — wellness every 10 min (4-8h) then every 30 min (9-22h), workouts hourly at :00 (4-23h), activities every 10 min (4-23h), DFA every 5 min (5-22h), evening report at 19:00, weekly report Sunday 18:00
- **Both HRV algorithms** always computed; `HRV_ALGORITHM` selects primary
- **Claude API** once per day to minimize costs (morning report). Chat uses per-request calls. Prompt caching (`cache_control: ephemeral`) on system prompt. Tool filtering: 6 groups, keyword-based, core+tracking always included (~75% token reduction for simple messages)
- **All timestamps** UTC in DB, local timezone for display
- **Telegram bot** — polling (local dev, `TELEGRAM_WEBHOOK_URL` empty) or webhook (production)
- **Frontend** — React SPA via Vite; dev proxies /api to FastAPI; production serves from webapp/dist/
- **i18n** — Backend: gettext (contextvars `_()`, `locale/` .po/.mo). Frontend: react-i18next (`webapp/src/i18n/` .json). User.language field, `"Respond in {response_language}"` in Claude prompts
- **Task queue** — Dramatiq + Redis. Scheduler dispatches groups per-user. Jobs endpoints dispatch directly. Actor time limits (30 min for FIT processing). `--force` flag for re-processing unchanged data
- **ORM** — `@dual` decorator creates `DualMethod` descriptor: auto-dispatches sync/async by detecting event loop. One method name works in both contexts: `Activity.get_for_date()` (sync) and `await Activity.get_for_date()` (async)
- **DTOs** — organized by domain: `data/dto.py` (metrics), `data/db/dto.py` (DB models), `data/intervals/dto.py` (API), `tasks/dto.py` (processing)
- **Sentry** — single init via `sentry_config.py`, called from `tasks/broker.py` (workers), `api/server.py` (API), `bot/main.py` (polling). Empty `SENTRY_DSN` = disabled. Data scrubbing: request headers/body, breadcrumbs, stackframe local vars. `@sentry_tool` decorator for MCP tools with spans. Intervals.icu client has spans + retry breadcrumbs

### Telegram Bot — Webhook Lifecycle

Startup: `initialize()` → `post_init()` (scheduler + Redis) → `start()` → `set_webhook()`.
Shutdown: `delete_webhook()` → `stop()` → `shutdown()` → `post_shutdown()`.
Auth: `X-Telegram-Bot-Api-Secret-Token` header (SHA256 of bot token, first 32 hex).

### Multi-Tenant Data Flow

```
User sends /morning → @athlete_required resolves User from chat_id
  → actor_compose_user_morning_report.send(user=UserDTO)
  → Dramatiq actor (sync) → MCPTool (sync HTTP to /mcp)
  → MCPAuthMiddleware → User.get_by_mcp_token → set_current_user_id
  → MCP tools → get_current_user_id() → user-scoped queries
```

---

## MCP Server (49 tools + 3 resources)

Run: `python -m mcp_server`. Production: mounted at `/mcp` (Streamable HTTP, per-user Bearer auth via `User.mcp_token`).

**Auth:** `MCPAuthMiddleware` resolves user by `User.get_by_mcp_token(token)` → sets `user_id` in `contextvars`. All tools call `get_current_user_id()` — user cannot manipulate `user_id` via tool parameters.

**49 tools** covering: wellness, HRV/RHR analysis, activities, training load/recovery, workouts (suggest/adapt/remove), training log, exercise/workout cards, mood/IQOS tracking, Garmin data (6 tools), efficiency trends, goal progress, zones, races (`get_races`/`tag_race`/`update_race`), GitHub issues, API usage. **3 resources:** `athlete://profile`, `athlete://goal`, `athlete://thresholds`.

**Key constraint:** CTL/ATL/TSB come from Intervals.icu, not TrainingPeaks.

---

## Mood & IQOS Tracking

**Mood:** Via MCP only. Claude notices emotional context → `save_mood_checkin`. Scales 1-5: energy, mood, anxiety, social + note.
**IQOS:** `/stick` command increments daily counter. MCP tool `get_iqos_sticks(target_date, days_back)` for trends.

---

## Intervals.icu Auth — Dual Mode (Phase 1 of OAuth migration)

Per-user Intervals.icu credentials support **two** authentication methods, tracked by `users.intervals_auth_method`:

| method | Credential storage | Who uses it |
|---|---|---|
| `"api_key"` | `users.api_key_encrypted` (Fernet) | Legacy — existing athletes, owner |
| `"oauth"` | `users.intervals_access_token_encrypted` (Fernet) + `intervals_oauth_scope` | New/migrated users via OAuth flow |
| `"none"` | — | Revoked OAuth with no api_key fallback (user must reconnect) |

**OAuth flow** (`api/routers/intervals/oauth.py`): frontend XHR `POST /api/intervals/auth/init` (auth header attached by `apiFetch`) → signed JWT state (`purpose='intervals_oauth'`, 15-min TTL) → returns `{authorize_url}` → `window.location.assign(authorize_url)` → `intervals.icu/oauth/authorize` → consent → `GET /api/intervals/auth/callback?code=&state=` (validates state, no auth header needed) → server-side POST to `intervals.icu/api/oauth/token` → response has `{access_token, token_type: "Bearer", scope, athlete: {id, name}}` (**no** refresh_token, **no** expires_in) → `User.set_oauth_tokens()` → 302 redirect to `/settings?connected=intervals`. Why init is POST and not GET: a full-page `<a href>` doesn't send the Authorization header from localStorage, so a GET endpoint with `require_viewer` would 401. POST+XHR+JSON sidesteps that.

**Scopes:** `ACTIVITY:WRITE,WELLNESS:READ,CALENDAR:WRITE,SETTINGS:WRITE` — `:WRITE` implies `:READ` per Intervals.icu docs, and listing the same area twice produces `"Duplicate scope"` error. `ACTIVITY:WRITE` for rename/update, `SETTINGS:WRITE` for `actor_update_zones` (ramp-test LTHR push).

**Phase 2 complete:** `IntervalsClient` (`data/intervals/client.py`) now supports dual auth — `_resolve_credentials(user)` reads `User.intervals_auth_method` and picks Bearer (`access_token`) or Basic (`api_key`). Constructor is keyword-only (`*, athlete_id, api_key=None, access_token=None`) to prevent positional arg swap. Both `for_user()` factories (async + sync) delegate to `_resolve_credentials`. Empty `athlete_id` → `LookupError` at resolve time. Verified end-to-end on real Intervals.icu API.

**Webhook receiver** (`POST /api/intervals/webhook`): verifies `body.secret` via `hmac.compare_digest`, resolves tenant by `athlete_id`, parses records into typed DTOs for drift detection, forwards metadata-only samples to Sentry (opt-in via `INTERVALS_WEBHOOK_MONITORING`). 5 delivery patterns documented: `records[]`, `activity`, `sportSettings[]`, top-level fields, empty notification. See `docs/INTERVALS_WEBHOOKS_RESEARCH.md` for full payload samples (10/10 event types researched).

**Onboarding routing:** `bot/main.py:start` branches on `user.athlete_id` — new users get "🔗 Подключить Intervals.icu" WebApp button → `/settings`. `webapp/src/pages/Login.tsx:routeAfterLogin` sends users without `athlete_id` to `/settings`. Global auth gate in `App.tsx` blocks all data routes for unauthenticated users or users without `athlete_id` (issue #185 fix).

---

---

## Documentation

Specs and plans in `docs/`. Key: `ADAPTIVE_TRAINING_PLAN.md`, `MULTI_TENANT_SECURITY.md`, `INTERVALS_WEBHOOKS_RESEARCH.md` (webhook payload samples for all 10 event types), `DONATE_SPEC.md`, `BOT_MIGRATION_SPEC.md`, `intervals_icu_openapi.json` (API ref), `knowledge/` (training methodology).

---

## Next Steps

1. **Webhook dispatchers** — `WELLNESS_UPDATED` ✓, `CALENDAR_UPDATED` ✓, `SPORT_SETTINGS_UPDATED` ✓, `FITNESS_UPDATED` ✓, `APP_SCOPE_CHANGED` ✓, `ACTIVITY_ACHIEVEMENTS` ✓ done. Remaining: `ACTIVITY_UPLOADED` → activities sync, `ACTIVITY_UPDATED` → update in DB, `ACTIVITY_ANALYZED` → re-fetch details. See `docs/INTERVALS_WEBHOOKS_RESEARCH.md`.
2. **OAuth remaining** — disconnect endpoint (`POST /api/intervals/auth/disconnect`), lazy 401 handling (catch 401 → clear tokens → Telegram notify)
3. **ATP Phase 3 доделка** — `compute_personal_patterns()` еженедельный cron + prompt enrichment. Ждёт 30+ записей в training_log
4. **Multi-Tenant Phase 2** — JWT upgrade (tenant_id, role, scope claims), bot middleware (resolve_tenant). See `docs/MULTI_TENANT_SECURITY.md`

---

## Contributing

- Follow existing module structure
- DTOs: `api/dto.py` (API request/response), `data/dto.py` (metrics), `data/db/dto.py` (DB), `data/intervals/dto.py` (Intervals.icu API), `tasks/dto.py` (processing)
- ORM methods: use `@with_session` (async), `@with_sync_session` (sync), or `@dual` (both). `user_id` always first param after `cls`
- New MCP tools: add to `mcp_server/tools/`, use `get_current_user_id()` from `mcp_server.context`, never accept `user_id` as tool parameter
- New data tools: add only to MCP, not to `TOOL_HANDLERS` (deprecated)
- Write deterministic tests for metric calculations
- Keep prompts in `bot/prompts.py`
- i18n: wrap user-facing bot strings in `_()` from `bot.i18n`. Add translations to `locale/en/LC_MESSAGES/messages.po`, run `pybabel compile -d locale`. Webapp: add keys to `webapp/src/i18n/ru.json` + `en.json`
- Document new env vars in `.env.example`
- When closing GitHub issues, follow the workflow in `~/.claude/skills/github-workflow/SKILL.md` — add a closing comment with "What was done" + "How to verify" before closing
