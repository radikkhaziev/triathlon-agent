# Triathlon AI Agent — Project Specification

> Architecture, stack, structure, and business logic.

---

## What We're Building

Personal AI agent for a triathlete: syncs wellness/HRV/training from Intervals.icu, evaluates recovery and planned workouts, sends morning reports via Telegram Bot, exposes data via MCP server, and provides an interactive dashboard via Telegram Mini App.

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12+ |
| Package Manager | Poetry |
| Data Source | Intervals.icu API |
| AI Analysis | Anthropic Claude API (`claude-sonnet-4-6`) + Google Gemini (optional) |
| Telegram Bot | `python-telegram-bot` v21+ |
| Scheduler | `APScheduler` |
| Database | PostgreSQL 16 + `SQLAlchemy` (async) + Alembic |
| API Server | `FastAPI` + `uvicorn` |
| Mini App Frontend | React 18 + TypeScript + Vite + Tailwind CSS + Chart.js |
| Backend Hosting | Docker Compose on VPS |
| Config | `pydantic-settings` + `.env` |

---

## Project Structure

```
triathlon-agent/
├── CLAUDE.md
├── .env / .env.example
├── pyproject.toml / poetry.lock
├── Dockerfile / docker-compose.yml
├── alembic.ini
├── config.py                    # pydantic-settings
├── bot/
│   ├── main.py                  # bot entry (polling + webhook)
│   ├── cli.py                   # shell, backfill, sync-workouts, sync-activities
│   ├── scheduler.py             # 5 cron jobs
│   └── formatter.py             # report formatting
├── data/
│   ├── intervals_client.py      # Intervals.icu API client
│   ├── metrics.py               # dual HRV, RHR, recovery, per-sport CTL, ESS/Banister
│   ├── hrv_activity.py          # DFA a1 pipeline (FIT → RR → DFA → thresholds → Ra/Da)
│   ├── database.py              # SQLAlchemy ORM + CRUD
│   ├── models.py                # Pydantic data models
│   └── utils.py                 # SPORT_MAP, extract_sport_ctl
├── ai/
│   ├── claude_agent.py          # Claude API — morning analysis
│   ├── gemini_agent.py          # Gemini — optional second opinion
│   └── prompts.py               # system + report prompts
├── api/
│   ├── server.py                # FastAPI + static + webhook
│   ├── routes.py                # REST endpoints + auth
│   └── auth.py                  # one-time codes + JWT
├── mcp_server/                  # FastMCP: 14 tools + 3 resources
│   ├── tools/                   # wellness, hrv, rhr, training_load, recovery, goal, activities, activity_hrv, scheduled_workouts, mood
│   └── resources/               # athlete profile, goal, thresholds
├── webapp/                      # React SPA (Vite + TypeScript + Tailwind)
│   ├── index.html               # Vite entry
│   ├── package.json / tsconfig.json / vite.config.ts
│   └── src/
│       ├── main.tsx / App.tsx
│       ├── api/                 # apiClient + TypeScript types
│       ├── auth/                # AuthProvider, useAuth, Telegram SDK
│       ├── components/          # Layout, MetricCard, Gauge, TabSwitcher, WeekNav
│       ├── pages/               # Landing, Login, Report, Wellness, Plan, Activities, Activity, Dashboard
│       ├── hooks/               # useApi, useWeekNav, useDayNav
│       └── styles/              # Tailwind + --tg-theme-* CSS vars
├── migrations/
├── docs/
└── tests/
```

---

## Database Schema

Eight tables. Full column specs in `data/database.py`.

| Table | PK | Purpose |
|---|---|---|
| `wellness` | date string | Daily Intervals.icu data: CTL/ATL, HRV, sleep, body metrics, recovery score, AI recommendations |
| `hrv_analysis` | (date, algorithm) | Dual-algorithm HRV baselines: flatt_esco + ai_endurance. Status, bounds, CV, SWC, trend |
| `rhr_analysis` | date | RHR baselines: 7d/30d/60d means, bounds (±0.5 SD of 30d), trend. Inverted: high RHR = red |
| `scheduled_workouts` | event ID | Planned workouts from Intervals.icu calendar. Synced hourly |
| `activities` | activity ID | Completed activities. Synced hourly at :30 |
| `activity_hrv` | activity_id FK | Post-activity DFA a1: quality, thresholds (HRVT1/HRVT2), Ra, Da. Processed every 5 min |
| `pa_baseline` | autoincrement | Pa values for Readiness (Ra) calculation. 14-day rolling baseline |
| `mood_checkins` | autoincrement | Emotional state: energy/mood/anxiety/social (1-5) + note. Via MCP only |

---

## Current Implementation Status

| Module | Status | Notes |
|---|---|---|
| `data/*` | Done | Models, Intervals.icu client, metrics pipeline, DFA a1, database ORM |
| `ai/*` | Done | Claude + Gemini (optional) morning reports, shared prompts |
| `bot/*` | Done | /morning, /web, scheduler (5 jobs), CLI, formatter |
| `api/*` | Done | REST endpoints, dashboard_routes (scaffold), auth |
| `mcp_server/` | Done | 14 tools + 3 resources |
| `webapp/` (React SPA) | Migration | See `docs/REACT_MIGRATION_PLAN.md` |

**Webapp pages status:** All pending React migration — Landing, Login, Report, Plan, Activities, Activity, Wellness, Dashboard.

---

## Environment Variables (.env)

```env
INTERVALS_API_KEY=...             # Intervals.icu
INTERVALS_ATHLETE_ID=i12345
TELEGRAM_BOT_TOKEN=...            # Telegram
TELEGRAM_CHAT_ID=123456789
TELEGRAM_WEBHOOK_URL=             # empty = polling mode
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_AI_API_KEY=                # empty = Gemini disabled
API_BASE_URL=https://...
WEBAPP_URL=https://...
DATABASE_URL=postgresql+asyncpg://...

# Athlete Profile
ATHLETE_AGE=43
ATHLETE_LTHR_RUN=153
ATHLETE_LTHR_BIKE=153
ATHLETE_MAX_HR=179
ATHLETE_FTP=233                   # watts
ATHLETE_CSS=141                   # sec per 100m

# Race Goal
GOAL_EVENT_NAME=Ironman 70.3
GOAL_EVENT_DATE=2026-09-15
GOAL_CTL_TARGET=75
GOAL_SWIM_CTL_TARGET=15
GOAL_BIKE_CTL_TARGET=35
GOAL_RUN_CTL_TARGET=25

TIMEZONE=Europe/Belgrade
HRV_ALGORITHM=flatt_esco          # or "ai_endurance"
JWT_SECRET=                       # if empty, uses TELEGRAM_BOT_TOKEN
JWT_EXPIRY_DAYS=7
MCP_AUTH_TOKEN=...                # Bearer token for /mcp endpoint
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

**HR Zones (% LTHR):**
Run: Z1 0-72%, Z2 72-82%, Z3 82-87%, Z4 87-92%, Z5 92-100%
Bike: Z1 0-68%, Z2 68-83%, Z3 83-94%, Z4 94-105%, Z5 105-120%

---

## AI Recommendation (ai/claude_agent.py + ai/prompts.py)

Runs once daily for current date (not backfill). Model: `claude-sonnet-4-6`, max_tokens=1024.

**Input data:** recovery score/category, sleep, HRV (both algorithms), RHR, CTL/ATL/TSB, per-sport CTL, race goal progress, today's planned workouts, yesterday's DFA summary.

**Output (4 sections, Russian, max 250 words):**
1. Readiness assessment (green/yellow/red) with numbers
2. Planned workout evaluation — adjust if needed, suggest if none
3. Training load trend observation
4. Race goal progress note

**Workout suggestion rules:** Recovery excellent+TSB>0 → any intensity; good → Z2; moderate/sleep<50 → Z1-Z2 45-60min; low/red RMSSD → rest/Z1≤30min; TSB<-25 → Z1-Z2 cap; HRV delta<-15% → Z1-Z2 max.

**Gemini** (optional, gated by `GOOGLE_AI_API_KEY`): `gemini-2.5-flash`, parallel call via `asyncio.gather`. Result in `ai_recommendation_gemini`. Shown as second tab in webapp, not in Telegram.

---

## Bot Commands (bot/main.py)

```
/morning  — morning report + Mini App button
/web      — one-time code for desktop login (5 min TTL)
/start, /status, /week, /goal, /zones — not yet implemented
/iqos     — daily IQOS counter (not yet implemented, needs iqos_daily table)
```

---

## API Endpoints

```
GET  /api/report                        — full morning report (today)
GET  /api/wellness-day?date=YYYY-MM-DD  — wellness for any date (navigable)
GET  /api/scheduled-workouts?week_offset=0 — weekly plan (Mon-Sun)
GET  /api/activities-week?week_offset=0 — weekly activities
GET  /api/activity/{id}/details         — full activity stats + zones + DFA
POST /api/auth/verify-code              — verify one-time code → JWT
GET  /api/auth/me                       — auth status
POST /api/jobs/sync-workouts            — trigger sync (owner auth)
POST /api/jobs/sync-activities          — trigger sync (owner auth)
GET  /health
POST /telegram/webhook                  — webhook mode only
POST /mcp                               — MCP (Streamable HTTP, Bearer auth)
```

**Dashboard API** (scaffold, mock data): `/api/dashboard`, `/api/training-load`, `/api/goal`, `/api/weekly-summary`, job trigger stubs.

**Auth:** Two methods in `Authorization` header — Telegram initData (HMAC-SHA256) or `Bearer <jwt>`. Resolves to: owner / viewer / anonymous.

---

## Webapp (webapp/) — React SPA

> Full migration plan: `docs/REACT_MIGRATION_PLAN.md`

React 18 + TypeScript + Vite SPA. Dark theme, Inter font, mobile-first. Telegram Mini App compatible.

**Stack:** React 18 + TypeScript, Vite 6, React Router v7, Tailwind CSS v3 (JIT), Chart.js v4 + react-chartjs-2, React Context (no Redux).

### Pages

| Route | Component | API Source |
|---|---|---|
| `/` | Landing | — |
| `/login` | Login | `POST /api/auth/verify-code` |
| `/report` | Report | `GET /api/report` |
| `/wellness` | Wellness | `GET /api/wellness-day` |
| `/plan` | Plan | `GET /api/scheduled-workouts` |
| `/activities` | Activities | `GET /api/activities-week` |
| `/activity/:id` | Activity | `GET /api/activity/{id}/details` |
| `/dashboard` | Dashboard | Multiple endpoints |

### Shared Components

Layout, MetricCard, Gauge (canvas), TabSwitcher, WeekNav, DayNav, WorkoutCard, ActivityCard, ZoneChart (Chart.js), StatusBadge, LoadingSpinner, ErrorMessage.

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

## CLI (bot/cli.py)

```bash
python -m bot.cli shell
python -m bot.cli backfill [date|range|quarter|month]  # default: last 180 days
python -m bot.cli sync-workouts [days_ahead]            # default: 14
python -m bot.cli sync-activities
python -m bot.cli process-fit
```

---

## Docker

```bash
docker compose up -d db                  # PostgreSQL only
docker compose up -d                     # all (includes React build)
docker compose --profile polling up -d   # + bot polling mode
docker compose run --rm api python -m bot.cli backfill  # CLI in Docker
```

Multi-stage build: Node 20 → React SPA, Python 3.12 → serves built assets. No Node in final image.

---

## Key Implementation Notes

- **Intervals.icu API** — wellness every 10 min (5-23h), workouts hourly (4-23h), activities at :30 (4-23h), DFA every 5 min (5-22h), evening report at 21:00
- **Both HRV algorithms** always computed; `HRV_ALGORITHM` selects primary
- **Claude API** once per day to minimize costs
- **All timestamps** UTC in DB, local timezone for display
- **Telegram bot** — polling (local dev, `TELEGRAM_WEBHOOK_URL` empty) or webhook (production)
- **Frontend** — React SPA via Vite; dev proxies /api to FastAPI; production serves from webapp/dist/

### Telegram Bot — Webhook Lifecycle

Startup: `initialize()` → `post_init()` (scheduler) → `start()` → `set_webhook()`.
Shutdown: `delete_webhook()` → `stop()` → `shutdown()` → `post_shutdown()`.
Auth: `X-Telegram-Bot-Api-Secret-Token` header (SHA256 of bot token, first 32 hex).

---

## MCP Server (14 tools + 3 resources)

Run: `python -m mcp_server`. Production: mounted at `/mcp` (Streamable HTTP, Bearer auth via `MCP_AUTH_TOKEN`).

**Tools:** get_wellness, get_wellness_range, get_activities, get_hrv_analysis, get_rhr_analysis, get_training_load, get_recovery, get_goal_progress, get_scheduled_workouts, get_activity_hrv, get_thresholds_history, get_readiness_history, save_mood_checkin_tool, get_mood_checkins_tool.

**Resources:** `athlete://profile`, `athlete://goal`, `athlete://thresholds`.

**Key constraint:** All tools document that CTL/ATL/TSB come from Intervals.icu, not TrainingPeaks.

---

## Mood Tracking

Via MCP only (no Telegram command). Claude notices emotional context → proposes check-in → user confirms → `save_mood_checkin`. Scales 1-5: energy, mood, anxiety, social + free text note. Multiple check-ins per day OK. No stored summaries — Claude generates on demand.

---

## Activity Details (#6 — Future)

Extended per-activity stats (HR, power, pace, zones, intervals, efficiency). New table `activity_details`. Two phases: fetch & store, then web + MCP display. Full spec: `docs/ACTIVITY_DETAILS_PHASE1.md`, `docs/ACTIVITY_DETAILS_PHASE2.md`.

---

## Web Dashboard (#9 — Future, post-React migration)

Four tabs: Today (recovery + AI), Calendar (activities + plan), Load (CTL/ATL/TSB charts), Goal (per-sport progress). Manual job triggers. Implemented as React components. Full spec: `docs/WEB_DASHBOARD.md` (when created).

---

## Documentation (docs/)

| Document | Description |
|---|---|
| `REACT_MIGRATION_PLAN.md` | React migration — stack, structure, migration order, Docker |
| `HRV_MODULE_SPEC.md` | HRV architecture — Level 1 (RMSSD) + Level 2 (DFA a1) |
| `DFA_ALPHA1_PLAN.md` | DFA a1 pipeline — FIT → RR → thresholds → Ra/Da |
| `PROCESS_FIT_JOB.md` | FIT processing pipeline + quality testing |
| `ESS_BANISTER_PLAN.md` | ESS/Banister pipeline |
| `MCP_INTEGRATION_PLAN.md` | MCP roadmap — Phase 1 (done), Phase 2-3 (future) |
| `ACTIVITY_DETAILS_PHASE1.md` | Activity Details — fetch & store |
| `ACTIVITY_DETAILS_PHASE2.md` | Activity Details — web + MCP display |
| `WEB_AUTH_MODEL.md` | Auth: 3 roles, Telegram initData, JWT |
| `SCHEDULED_WORKOUTS_PAGE.md` | Workouts page architecture |
| `ACTIVITIES_PAGE.md` | Activities page architecture |
| `PROGRESS_TRACKING_PLAN.md` | EF + swim pace trends |
| `HRV_IMPLEMENTATION_PLAN.md` | Level 1 implementation steps |

---

## Next Steps (Priority Order)

1. ~~ESS/Banister~~ — Done
2. ~~DFA Alpha 1~~ — Done
3. ~~Post-activity notification~~ — Done
4. ~~Evening report~~ — Done
5. ~~Morning prompt + DFA~~ — Done
6. **Activity Details** — new table, MCP tool, Intervals.icu API
7. ~~Scheduled Workouts page~~ — Done
8. **React Migration** — webapp/ → React SPA. Prerequisite for Dashboard. See `docs/REACT_MIGRATION_PLAN.md`
9. **Web Dashboard** — Today/Calendar/Load/Goal tabs. React components post-migration
10. **Bot commands** — /start /status /week /goal /zones /iqos
11. ~~Web Auth~~ — Done
12. ~~Mood Tracking~~ — Done
13. **MCP Phase 2** — replace fixed prompt with tool-use
14. **MCP Phase 3** — free-form Telegram chat

---

## Contributing

- Follow existing module structure
- Add Pydantic models in `data/models.py`
- Write deterministic tests for metric calculations
- Keep prompts modular in `prompts.py`
- Document new env vars in `.env.example`
