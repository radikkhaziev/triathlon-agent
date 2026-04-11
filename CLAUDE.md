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
├── CLAUDE.md
├── .env / .env.example
├── pyproject.toml / poetry.lock
├── Dockerfile / docker-compose.yml
├── alembic.ini
├── config.py                        # pydantic-settings
├── sentry_config.py                 # Sentry SDK init, data scrubbing, traces sampler
├── cli.py                           # shell, sync-settings, sync-wellness, sync-activities, sync-training-log, import-garmin
├── bot/
│   ├── main.py                      # bot entry (polling + webhook), handlers, ClaudeAgent instance
│   ├── agent.py                     # ClaudeAgent — thin async client over MCP (tool-use loop)
│   ├── tools.py                     # MCPClient — async MCP Streamable HTTP client (list_tools, call_tool)
│   ├── prompts.py                   # system prompts for chat + morning analysis
│   ├── scheduler.py                 # APScheduler cron jobs → dramatiq group dispatch
│   ├── decorator.py                 # @athlete_required — resolves User from Telegram update
│   └── formatter.py                 # report formatting (re-exports from tasks.formatter)
├── tasks/
│   ├── broker.py                    # Dramatiq RedisBroker configuration
│   ├── middleware.py                # Pydantic auto-serialization for actor kwargs
│   ├── worker.py                    # dramatiq worker entry point
│   ├── tools.py                     # TelegramTool (sync HTTP) + MCPTool (sync, for morning report)
│   ├── formatter.py                 # Message builders: morning, evening, post-activity
│   ├── utils.py                     # RampTrainingSuggestion, detect_compliance
│   ├── dto.py                       # DateDTO, ORMDTO, FitProcessingResultDTO, ThresholdsDTO
│   └── actors/
│       ├── common.py                # Shared: CATEGORY_TO_READINESS, actor_after_activity_update, sport CTL enrichment
│       ├── wellness.py              # Wellness sync + RHR/HRV/recovery pipelines
│       ├── activities.py            # Activity sync + FIT processing + DFA a1
│       ├── training_log.py          # Training log lifecycle: PRE+ACTUAL (same day), POST (next day)
│       ├── reports.py               # Morning/evening report composition + workout adaptation
│       ├── workout.py               # actor_push_workout → Intervals.icu + DB + Telegram
│       └── athlets.py               # Athlete settings sync from Intervals.icu
├── data/
│   ├── dto.py                       # Domain DTOs: TrendResult, RmssdStatus, RhrStatus, RecoveryScore
│   ├── metrics.py                   # HRV, RHR, recovery, per-sport CTL, ESS/Banister calculations
│   ├── hrv_activity.py              # DFA a1 pipeline (FIT → RR → DFA → thresholds → Ra/Da)
│   ├── workout_adapter.py           # HumanGo parser + adaptation engine
│   ├── ramp_tests.py                # Ramp test protocol generation
│   ├── utils.py                     # normalize_sport, is_bike/run/swim, CANONICAL_TYPES, extract_sport_ctl
│   ├── crypto.py                    # Fernet encryption for per-user secrets
│   ├── redis_client.py              # Redis init/close
│   ├── github.py                    # GitHub issue creation
│   ├── intervals/
│   │   ├── client.py                # IntervalsAsyncClient + IntervalsSyncClient (per-user factory)
│   │   └── dto.py                   # Intervals.icu API DTOs: Wellness, Activity, Workout, EventEx
│   ├── garmin/
│   │   ├── parser.py                # GarminExportParser — chunked JSON discovery + period filter
│   │   ├── dto.py                   # Pydantic DTOs for sleep, daily, readiness, health
│   │   └── importer.py              # Bulk upsert to DB (ON CONFLICT DO NOTHING/UPDATE)
│   └── db/
│       ├── common.py                # Engine, session factories, @dual DualMethod descriptor
│       ├── decorator.py             # @with_session, @with_sync_session, @dual
│       ├── dto.py                   # DB DTOs: UserDTO, WellnessPostDTO, AthleteThresholdsDTO
│       ├── user.py                  # User ORM + get_threshold_freshness, detect_threshold_drift
│       ├── athlete.py               # AthleteSettings, AthleteGoal ORM (get_thresholds, get_goal_dto)
│       ├── wellness.py              # Wellness ORM + HRV/RHR history
│       ├── activity.py              # Activity, ActivityHrv, ActivityDetail ORM
│       ├── hrv.py                   # HrvAnalysis, RhrAnalysis, PaBaseline ORM
│       ├── workout.py               # ScheduledWorkout, AiWorkout, TrainingLog, ExerciseCard, WorkoutCard
│       ├── tracking.py              # MoodCheckin, IqosDaily, ApiUsageDaily
│       └── garmin.py                # 9 Garmin ORM models (sleep, daily, readiness, health, load, fitness, race, bio, abnormal_hr)
├── api/
│   ├── server.py                    # FastAPI + MCPAuthMiddleware (per-user token) + webhook
│   ├── deps.py                      # Auth dependencies: get_current_user, require_viewer/athlete/owner
│   ├── auth.py                      # One-time codes + JWT
│   ├── routes.py                    # Router aggregation
│   └── routers/
│       ├── wellness.py              # /api/report, /api/wellness-day
│       ├── activities.py            # /api/activities-week, /api/activity/{id}/details, /api/progress
│       ├── workouts.py              # /api/scheduled-workouts
│       ├── jobs.py                  # /api/jobs/sync-* → dramatiq actors (require_athlete)
│       ├── auth.py                  # /api/auth/verify-code, /api/auth/me
│       └── system.py                # /health
├── mcp_server/
│   ├── app.py                       # FastMCP instance
│   ├── server.py                    # MCP server with all tools + resources imported
│   ├── context.py                   # contextvars: set/get_current_user_id (from MCPAuthMiddleware)
│   ├── sentry.py                    # @sentry_tool decorator (spans + error capture for MCP tools)
│   ├── tools/                       # 43 tools (all use get_current_user_id() for tenant isolation)
│   └── resources/                   # athlete profile, goal, thresholds
├── webapp/                          # React SPA (Vite + TypeScript + Tailwind)
├── templates/                       # Jinja2 templates for exercise/workout cards
├── static/                          # Generated HTML files (exercises, workouts, uploads)
├── migrations/
├── docs/
│   └── knowledge/                   # Training methodology & theory
└── tests/
```

---

## Database Schema

Twenty-five tables. Full column specs in `data/db/`.

| Table                 | PK                         | Purpose                                                                                         |
| --------------------- | -------------------------- | ----------------------------------------------------------------------------------------------- |
| `users`               | autoincrement              | Multi-tenant: chat_id, role, athlete_id, api_key_encrypted, mcp_token                          |
| `athlete_settings`    | (user_id, sport_type)      | Per-user per-sport thresholds from Intervals.icu (LTHR, FTP, zones)                             |
| `athlete_goals`       | (user_id)                  | Per-user race goal (event, date, CTL targets)                                                   |
| `wellness`            | autoincrement              | Daily Intervals.icu data: CTL/ATL, HRV, sleep, body metrics, recovery score, AI recommendations |
| `hrv_analysis`        | (user_id, date, algorithm) | Dual-algorithm HRV baselines: flatt_esco + ai_endurance. Status, bounds, CV, SWC, trend         |
| `rhr_analysis`        | (user_id, date)            | RHR baselines: 7d/30d/60d means, bounds (±0.5 SD of 30d), trend. Inverted: high RHR = red       |
| `scheduled_workouts`  | event ID                   | Planned workouts from Intervals.icu calendar. Synced hourly                                     |
| `activities`          | activity ID                | Completed activities. Synced hourly at :30                                                      |
| `activity_details`    | activity_id FK             | Extended stats: HR/power/pace zones, zone times, intervals, EF, decoupling, pool_length         |
| `activity_hrv`        | activity_id FK             | Post-activity DFA a1: quality, thresholds (HRVT1/HRVT2), Ra, Da. Processed every 5 min          |
| `pa_baseline`         | autoincrement              | Pa values for Readiness (Ra) calculation. 14-day rolling baseline                               |
| `ai_workouts`         | autoincrement              | AI-generated/adapted workouts pushed to Intervals.icu. External ID for dedup                    |
| `training_log`        | autoincrement              | Training log: pre-context, actual, post-outcome. Compliance detection + personal patterns       |
| `mood_checkins`       | autoincrement              | Emotional state: energy/mood/anxiety/social (1-5) + note. Via MCP only                          |
| `iqos_daily`          | autoincrement              | Daily IQOS stick counter. Incremented via /stick bot command. Queried via MCP                   |
| `exercise_cards`      | id string                  | Exercise library: animation HTML/CSS, metadata, steps, focus (shared, no user_id)               |
| `workout_cards`       | autoincrement              | Composed workouts from exercise cards with custom sets/reps. Sport type (Swim/Ride/Run/Other)   |
| `api_usage_daily`     | (user_id, date)            | Daily API token usage: input/output/cache tokens, request count. Atomic upsert                  |
| `garmin_sleep`        | (user_id, calendar_date)   | Garmin sleep phases (deep/light/REM), 7 scores, respiration, stress. GDPR export                |
| `garmin_daily_summary`| (user_id, calendar_date)   | Garmin UDS: steps, calories, stress breakdown, body battery, RHR                                |
| `garmin_training_readiness` | (user_id, date, ctx) | Garmin Training Readiness: score, level, factor breakdown (HRV/sleep/ACWR/stress)               |
| `garmin_health_status`| (user_id, calendar_date)   | Garmin Health Status: HRV/HR/SpO2/skin temp/respiration with baselines                          |
| `garmin_training_load`| (user_id, calendar_date)   | Garmin ACWR: acute/chronic load, ratio, status                                                  |
| `garmin_fitness_metrics`| (user_id, calendar_date) | Combined VO2max (run/bike) + Endurance Score + Max MET. Sparse (~1/week)                        |
| `garmin_race_predictions`| (user_id, calendar_date)| Race time predictions: 5K/10K/half/marathon (seconds)                                           |
| `garmin_bio_metrics`  | (user_id, calendar_date)   | Weight, height, lactate threshold HR/speed. Sparse (~1/week)                                    |
| `garmin_abnormal_hr_events`| (user_id, timestamp)  | Abnormal HR events: high HR value + threshold                                                   |

---

## Current Implementation Status

| Module                 | Status            | Notes                                                                                                                                  |
| ---------------------- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `data/*`               | Done              | Intervals.icu client (per-user factory), metrics pipeline, DFA a1, ORM (dual sync/async), crypto (Fernet)                              |
| `data/db/`             | Done              | SQLAlchemy ORM with `@dual` DualMethod (auto-dispatches sync/async by context), `@with_session`/`@with_sync_session` decorators. `AthleteSettings.get_thresholds()` + `AthleteGoal.get_goal_dto()` (no AthleteConfig wrapper) |
| Multi-tenant           | Phase 1.3 done    | `users` table, `user_id` FK on all tables, per-user MCP auth (token → contextvars), per-user scheduler, API auth returns User object   |
| `bot/*`                | Done              | ClaudeAgent (thin MCP client), MCPClient (Streamable HTTP), per-user mcp_token, @athlete_required decorator                           |
| `tasks/*`              | Done              | Dramatiq actors: wellness/RHR/HRV/recovery pipelines, FIT processing, training log lifecycle, workout push                             |
| `api/*`                | Done              | REST endpoints, auth (User-based, not role string), require_viewer/athlete/owner, jobs → dramatiq direct dispatch                      |
| `mcp_server/`          | Done              | 43 tools + 3 resources. All tools use `get_current_user_id()` from contextvars. Per-user Bearer token auth                             |
| `webapp/` (React SPA)  | Done              | React 18 + TypeScript + Vite + Tailwind. Bottom tabs, Today hub, light theme                                                           |
| Adaptive Training Plan | Phase 4 done      | Write API, HumanGo adaptation, training log (pre/actual/post via actors), ramp tests + threshold drift                                 |
| Sentry                 | Done              | Error monitoring, performance tracing, data scrubbing (incl. stackframe vars), user context, `@sentry_tool` for MCP                   |
| Garmin Import          | Phase 2 done      | 9 tables, parser, importer, CLI `import-garmin`, 6 MCP tools, morning report enrichment                                                |

**Webapp pages:** Today (hub), Landing, Login, Wellness, Plan, Activities, Activity, Dashboard, Settings. Bottom tabs navigation. `/report` redirects to `/wellness`.

---

## Environment Variables (.env)

```env
TELEGRAM_BOT_TOKEN=...            # Telegram
TELEGRAM_CHAT_ID=123456789        # Owner chat ID (legacy, used in some places)
TELEGRAM_WEBHOOK_URL=             # empty = polling mode
ANTHROPIC_API_KEY=sk-ant-...
API_BASE_URL=https://...
WEBAPP_URL=https://...
DATABASE_URL=postgresql+asyncpg://...
REDIS_URL=redis://localhost:6379/0

# Per-user credentials (legacy, for owner user_id=1; new users use DB)
INTERVALS_API_KEY=...             # Intervals.icu
INTERVALS_ATHLETE_ID=i12345
ATHLETE_MAX_HR=179

TIMEZONE=Europe/Belgrade
HRV_ALGORITHM=flatt_esco          # or "ai_endurance"
JWT_SECRET=                       # if empty, uses TELEGRAM_BOT_TOKEN
JWT_EXPIRY_DAYS=7
MCP_AUTH_TOKEN=...                # Owner MCP token (per-user tokens in DB)
FIELD_ENCRYPTION_KEY=...          # Fernet key for per-user secrets

# Sentry (empty DSN = disabled)
SENTRY_DSN=https://...@o0.ingest.sentry.io/0
SENTRY_ENVIRONMENT=production     # production / development / staging
SENTRY_TRACES_SAMPLE_RATE=0.1    # 10% of transactions
SENTRY_RELEASE=                   # optional, auto-detect from git

```

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

**HR Zones (% LTHR):**
Run: Z1 0-72%, Z2 72-82%, Z3 82-87%, Z4 87-92%, Z5 92-100%
Bike: Z1 0-68%, Z2 68-83%, Z3 83-94%, Z4 94-105%, Z5 105-120%

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

---

## Bot Commands (bot/main.py)

All commands use `@athlete_required` decorator — resolves `User` from Telegram `chat_id`.

```
/start      — welcome message + create User in DB + Mini App button
/morning    — trigger morning report via dramatiq actor
/dashboard  — dashboard link (Mini App)
/web        — one-time code for desktop login (5 min TTL)
/stick      — increment IQOS stick counter for today (owner only)
/silent     — toggle silent mode (suppress Telegram notifications)
/whoami     — show current user info (chat_id, role)
<text>      — free-form AI chat (stateless, tool-use via MCP, per-user token)
<photo>     — AI chat with vision (base64 image + caption)
<reply>     — reply context included as "[В ответ на: ...]"
```

**Callback handlers:** `ramp_test:{sport}` — create ramp test, `update_zones` — update HR zones.

---

## API Endpoints

```
GET  /api/report                        — full morning report (today)
GET  /api/wellness-day?date=YYYY-MM-DD  — wellness for any date (navigable)
GET  /api/scheduled-workouts?week_offset=0 — weekly plan (Mon-Sun)
GET  /api/activities-week?week_offset=0 — weekly activities
GET  /api/activity/{id}/details         — full activity stats + zones + DFA
GET  /api/progress?sport=bike&days=90   — aerobic efficiency trend (EF/SWOLF/pace)
POST /api/auth/verify-code              — verify one-time code → JWT
GET  /api/auth/me                       — auth status
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

**Auth:** Two methods in `Authorization` header — Telegram initData (HMAC-SHA256) or `Bearer <jwt>`. Resolves to `User` object via `get_current_user()`. Dependencies: `require_viewer` (any authenticated user), `require_athlete` (active + athlete_id), `require_owner`. Viewers without `athlete_id` see owner data (read-only) via `get_data_user_id(user)`.

---

## Webapp (webapp/) — React SPA

> Full migration plan: `docs/REACT_MIGRATION_PLAN.md`

React 18 + TypeScript + Vite SPA. Light theme, Inter font, mobile-first. Telegram Mini App compatible.

**Stack:** React 18 + TypeScript, Vite 6, React Router v7, Tailwind CSS v3 (JIT), Chart.js v4, React Context (no Redux).

### Pages

| Route           | Component       | API Source                                |
| --------------- | --------------- | ----------------------------------------- | -------------------------------- |
| `/`             | Today / Landing | `/api/report` + `/api/scheduled-workouts` | Auth → Today hub, anon → Landing |
| `/login`        | Login           | `POST /api/auth/verify-code`              | Desktop auth                     |
| `/wellness`     | Wellness        | `GET /api/wellness-day`                   | Full day analytics with DayNav   |
| `/plan`         | Plan            | `GET /api/scheduled-workouts`             | Weekly plan with WeekNav         |
| `/activities`   | Activities      | `GET /api/activities-week`                | Weekly activities with WeekNav   |
| `/activity/:id` | Activity        | `GET /api/activity/{id}/details`          | Detail page, bottom tabs hidden  |
| `/dashboard`    | Dashboard       | Multiple endpoints                        | 3 tabs: Load, Goal, Week         |
| `/settings`     | Settings        | —                                         | Read-only profile + logout       |
| `/report`       | redirect        | —                                         | Redirects to `/wellness`         |

### Navigation

Bottom tabs: Today, Plan, Activities, Wellness, More (→ Dashboard, Settings). Hidden on `/activity/:id` and `/login`.

### Shared Components

Layout (with BottomTabs), MetricCard, Gauge, TabSwitcher, WeekNav, DayNav, ZoneChart, ZoneBar, SportCtlBars, AiRecommendation, SyncButton, StatusBadge, LoadingSpinner, ErrorMessage.

### Auth

Centralized `AuthProvider` (React Context): Telegram initData → JWT fallback → anonymous.
`useAuth()` hook: `{ role, isAuthenticated, authHeader, logout }`.
`apiClient.ts` attaches auth + handles 401 → redirect.

Desktop auth: `/web` bot command → 6-digit code → `/login` → JWT (7-day expiry).

### Telegram Mini App

SDK via `<script>` in index.html. Theme: CSS vars `--tg-theme-*` with dark fallbacks. Lifecycle: `tg.ready()` + `tg.expand()`.

### Build

Dev: `cd webapp && npm run dev` (Vite :5173, proxies /api → :8000).
Production: Docker multi-stage — Node 20 builds SPA → Python 3.12 serves `webapp/dist/` with SPA fallback.

---

## CLI (cli.py)

```bash
python -m cli shell                                              # interactive Python shell with app context
python -m cli sync-settings <user_id>                            # sync athlete settings & goals from Intervals.icu
python -m cli sync-wellness <user_id> [period]                   # force re-sync wellness + HRV/RHR/recovery day by day
python -m cli sync-activities <user_id> [period] [--force]       # force re-sync activities day by day
python -m cli sync-training-log <user_id> [period]               # recalculate training log from existing activities
python -m cli import-garmin <user_id> <source> [--types] [--period] [--force] [--dry-run]  # import Garmin GDPR export
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

### Step 5 (optional): Set CTL targets for goals

```python
from data.db import AthleteGoal
from data.db.common import get_sync_session

with get_sync_session() as s:
    goal = s.query(AthleteGoal).filter_by(user_id=2, category="RACE_A").first()
    goal.ctl_target = 75
    goal.per_sport_targets = {"swim": 15, "ride": 35, "run": 25}
    s.commit()
```

### Onboarding нового пользователя

```bash
# 1. User отправляет /start боту → UserRow создаётся с role=viewer
# 2. Owner через shell: меняет role, прописывает athlete_id, api_key, mcp_token
# 3. Запуск onboard:
python -m bot.cli onboard <user_id> --days 180
```

Команда `onboard` выполняет последовательно: sync wellness → sync activities → sync details → sync workouts. Использует per-user Intervals.icu credentials из таблицы `users`.

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

- **Intervals.icu API** — wellness every 10 min (4-8h) then every 30 min (9-22h), workouts hourly at :00 (4-23h), activities every 10 min (4-23h), DFA every 5 min (5-22h), evening report at 19:00
- **Both HRV algorithms** always computed; `HRV_ALGORITHM` selects primary
- **Claude API** once per day to minimize costs (morning report). Chat uses per-request calls
- **All timestamps** UTC in DB, local timezone for display
- **Telegram bot** — polling (local dev, `TELEGRAM_WEBHOOK_URL` empty) or webhook (production)
- **Frontend** — React SPA via Vite; dev proxies /api to FastAPI; production serves from webapp/dist/
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

## MCP Server (43 tools + 3 resources)

Run: `python -m mcp_server`. Production: mounted at `/mcp` (Streamable HTTP, per-user Bearer auth via `User.mcp_token`).

**Auth:** `MCPAuthMiddleware` resolves user by `User.get_by_mcp_token(token)` → sets `user_id` in `contextvars`. All tools call `get_current_user_id()` — user cannot manipulate `user_id` via tool parameters.

**Tools:** get_wellness, get_wellness_range, get_activities, get_activity_details, get_hrv_analysis, get_rhr_analysis, get_training_load, get_recovery, get_goal_progress, get_scheduled_workouts, get_activity_hrv, get_thresholds_history, get_readiness_history, suggest_workout, remove_ai_workout, list_ai_workouts, get_training_log, get_personal_patterns, get_threshold_freshness, create_ramp_test_tool, save_mood_checkin_tool, get_mood_checkins_tool, get_iqos_sticks, create_exercise_card, update_exercise_card, list_exercise_cards, compose_workout, remove_workout_card, list_workout_cards, get_efficiency_trend, create_github_issue, get_github_issues, get_animation_guidelines, get_api_usage, get_garmin_sleep, get_garmin_readiness, get_garmin_daily_metrics, get_garmin_race_predictions, get_garmin_vo2max_trend, get_garmin_abnormal_hr_events, get_zones, get_weight_trend, get_workout_compliance.

**Resources:** `athlete://profile`, `athlete://goal`, `athlete://thresholds`.

**Key constraint:** All tools document that CTL/ATL/TSB come from Intervals.icu, not TrainingPeaks.

---

## Mood Tracking

Via MCP only (no Telegram command). Claude notices emotional context → proposes check-in → user confirms → `save_mood_checkin`. Scales 1-5: energy, mood, anxiety, social + free text note. Multiple check-ins per day OK. No stored summaries — Claude generates on demand.

---

## IQOS Stick Tracking

Telegram command `/stick` increments daily counter (one row per date in `iqos_daily` table). Uses PostgreSQL `ON CONFLICT DO UPDATE` for atomic upsert. Bot replies with current count for today (e.g. "🚬 Стик #5 за 27.03").

MCP tool `get_iqos_sticks(target_date, days_back)`: `days_back=0` returns single-day count, `days_back>0` returns range with totals, daily breakdown, and average per day. Useful for trend analysis and correlating with training/recovery data.

---

## Activity Details (#6 — Done)

Extended per-activity stats (HR, power, pace, zones, intervals, efficiency). Table `activity_details` + `activity_hrv`. Sync job fetches details for new activities. React page `/activity/:id` with zones, intervals, DFA a1. MCP tool `get_activity_details`. Full spec: `docs/ACTIVITY_DETAILS_PHASE1.md`, `docs/ACTIVITY_DETAILS_PHASE2.md`.

---

## Web Dashboard (#9 — Done)

Three tabs: Load (CTL/ATL/TSB charts), Goal (per-sport progress), Week (weekly summary). Manual job triggers (sync workouts, sync activities). Implemented as React components. Full spec: `docs/WEB_DASHBOARD.md`.

---

## Documentation (docs/)

| Document                       | Description                                                                         |
| ------------------------------ | ----------------------------------------------------------------------------------- |
| `REACT_MIGRATION_PLAN.md`      | React migration — stack, structure, migration order, Docker                         |
| `WEBAPP_RESTRUCTURE.md`        | Webapp restructure — bottom tabs, Today hub, merged Wellness, Settings              |
| `WEB_DASHBOARD.md`             | Web Dashboard — 3 tabs: Load, Goal, Week                                            |
| `WEB_AUTH_MODEL.md`            | Auth: 3 roles, Telegram initData, JWT                                               |
| `HRV_MODULE_SPEC.md`           | HRV architecture — Level 1 (RMSSD) + Level 2 (DFA a1)                               |
| `HRV_IMPLEMENTATION_PLAN.md`   | Level 1 implementation steps                                                        |
| `DFA_ALPHA1_PLAN.md`           | DFA a1 pipeline — FIT → RR → thresholds → Ra/Da                                     |
| `PROCESS_FIT_JOB.md`           | FIT processing pipeline + quality testing                                           |
| `ESS_BANISTER_PLAN.md`         | ESS/Banister pipeline                                                               |
| `MCP_INTEGRATION_PLAN.md`      | MCP roadmap — Phase 1-3 (all done)                                                  |
| `ACTIVITY_DETAILS_PHASE1.md`   | Activity Details — fetch & store                                                    |
| `ACTIVITY_DETAILS_PHASE2.md`   | Activity Details — web + MCP display                                                |
| `SCHEDULED_WORKOUTS_PAGE.md`   | Workouts page architecture                                                          |
| `ACTIVITIES_PAGE.md`           | Activities page architecture                                                        |
| `ADAPTIVE_TRAINING_PLAN.md`    | Adaptive Training Plan — 4 phases: Write API, adaptation, training log, ramp tests  |
| `GEMINI_ROLE_SPEC.md`          | ~~Gemini role~~ — removed, dependencies dropped                                    |
| `PROGRESS_TRACKING_PLAN.md`    | EF + SWOLF + pace trends. MCP tool + API done. Webapp chart pending                 |
| `MOOD_TRACKING.md`             | Mood tracking via MCP — scales, workflow                                            |
| `WORKOUT_CARDS.md`             | Workout Cards — exercise library + workout composition from cards                   |
| `MCP_PHASE2.md`                | MCP Phase 2 — tool-use для утреннего анализа, tool definitions, fallback            |
| `MCP_PHASE3.md`                | MCP Phase 3 — free-form Telegram chat, stateless, owner-only, two-tier architecture |
| `ACTUAL_MAX_ZONE_TIME_SPEC.md` | Спека заполнения actual_max_zone_time — реализовано                                 |
| `TODO_WORKOUT_DISTANCE.md`     | Distance-based workouts — реализовано, Этап 0 (API верификация) pending             |
| `intervals_icu_openapi.json`   | Intervals.icu OpenAPI 3.0 spec (official, full API reference)                       |

---

## Next Steps (Priority Order)

1. ~~ESS/Banister~~ — Done
2. ~~DFA Alpha 1~~ — Done
3. ~~Post-activity notification~~ — Done
4. ~~Evening report~~ — Done
5. ~~Morning prompt + DFA~~ — Done
6. ~~Activity Details~~ — Done (table, API, MCP tool, React page, sync job, CLI sync-activities)
7. ~~Scheduled Workouts page~~ — Done
8. ~~React Migration~~ — Done (React 18 + TypeScript + Vite + Tailwind)
9. ~~Web Dashboard~~ — Done (3 tabs: Load, Goal, Week)
10. ~~Bot commands~~ — Done (/start with description + webapp link)
11. ~~Web Auth~~ — Done
12. ~~Mood Tracking~~ — Done
13. ~~IQOS Tracking~~ — Done (/stick command + MCP tool)
14. ~~Adaptive Training Plan Phase 1~~ — Done (Write API, AI workout generation, MCP tools, `ai_workouts` table)
15. ~~Webapp Restructure~~ — Done (Bottom tabs, Today hub, merge Report→Wellness, Settings stub)
16. ~~Adaptive Training Plan Phase 2~~ — Done (HumanGo parser, adaptation rules, clamp engine, scheduler integration, 33 unit tests)
17. ~~Adaptive Training Plan Phase 3~~ — Done (training_log table, pre/actual/post lifecycle, compliance detection, MCP tools, 10 tests)
18. ~~Adaptive Training Plan Phase 4~~ — Done (Ramp protocols, threshold freshness check, drift detection, MCP tools, compact morning message, 15 tests)
19. ~~MCP Phase 2~~ — Done (Tool-use for morning analysis via MCP HTTP)
20. ~~MCP Phase 3~~ — Done (Free-form Telegram chat: stateless, per-user MCP token, tool-use via MCP)
21. ~~Gemini Role Spec~~ — Removed (Gemini dependencies dropped)
22. ~~Workout Cards~~ — Done (Exercise library with HTML cards + SVG animations, Jinja templates, 6 MCP tools incl. guidelines)
23. **ATP Phase 3 доделка** — `compute_personal_patterns()` еженедельный cron + prompt enrichment. Ждёт 30+ записей в training_log (~30 дней после деплоя)
24. ~~Multi-Tenant Phase 1~~ — Done (users table, user_id on 13 tables, crypto, onboarding CLI)
25. ~~Multi-Tenant Phase 1.3~~ — Done (per-user MCP auth, contextvars user_id, API auth returns User, per-user scheduler via dramatiq, jobs dispatch per-user actors)
26. ~~Sentry Integration~~ — Done (error monitoring, performance tracing, data scrubbing, user context, `@sentry_tool`, 19 tests)
27. **Multi-Tenant Phase 2** — JWT upgrade (tenant_id, role, scope claims), bot middleware (resolve_tenant), initData freshness check. See `docs/MULTI_TENANT_SECURITY.md`

---

## Contributing

- Follow existing module structure
- DTOs: `data/dto.py` (metrics), `data/db/dto.py` (DB), `data/intervals/dto.py` (API), `tasks/dto.py` (processing)
- ORM methods: use `@with_session` (async), `@with_sync_session` (sync), or `@dual` (both). `user_id` always first param after `cls`
- New MCP tools: add to `mcp_server/tools/`, use `get_current_user_id()` from `mcp_server.context`, never accept `user_id` as tool parameter
- New data tools: add only to MCP, not to `TOOL_HANDLERS` (deprecated)
- Write deterministic tests for metric calculations
- Keep prompts in `bot/prompts.py`
- Document new env vars in `.env.example`
- When closing GitHub issues, follow the workflow in `~/.claude/skills/github-workflow/SKILL.md` — add a closing comment with "What was done" + "How to verify" before closing
