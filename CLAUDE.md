# Triathlon AI Agent — Project Specification for Claude Code

> Read this before taking any action. Architecture, stack, structure, and business logic.

---

## What We're Building

A personal AI agent for a triathlete that:

- Periodically syncs wellness, HRV, training load, and scheduled workouts from Intervals.icu
- Calculates training load (CTL/ATL/TSB) across all sports
- Runs dual-algorithm HRV analysis (Flatt & Esco + AIEndurance) and RHR baseline tracking
- Evaluates planned workouts against current recovery state
- Evaluates progress toward a target race (e.g., Ironman 70.3)
- Sends a morning report via Telegram Bot (`/morning` command)
- Exposes all data via MCP server for Claude Desktop
- Opens a beautiful interactive dashboard via Telegram Mini App

---

## Tech Stack

| Component         | Technology                                     |
| ----------------- | ---------------------------------------------- |
| Language          | Python 3.12+                                   |
| Package Manager   | Poetry                                         |
| Data Source       | Intervals.icu API                              |
| AI Analysis       | Anthropic Claude API (`claude-sonnet-4-6`)     |
| Telegram Bot      | `python-telegram-bot` v21+                     |
| Scheduler         | `APScheduler`                                  |
| Database          | PostgreSQL 16 + `SQLAlchemy` (async) + Alembic |
| API Server        | `FastAPI` + `uvicorn`                          |
| Mini App Frontend | HTML + Chart.js + Tailwind CSS                 |
| Backend Hosting   | Docker Compose on VPS                          |
| Config            | `pydantic-settings` + `.env`                   |

---

## Project Structure

```
triathlon-agent/
├── CLAUDE.md                    # ← this file
├── .env / .env.example          # secrets / template
├── pyproject.toml / poetry.lock
├── Dockerfile / docker-compose.yml
├── alembic.ini
├── config.py                    # pydantic-settings
│
├── bot/
│   ├── main.py                  # bot entry point (polling + scheduler init)
│   ├── cli.py                   # CLI: shell, backfill, sync-workouts
│   ├── scheduler.py             # periodic jobs (wellness every 15 min, workouts every 1 hr)
│   └── formatter.py             # report summary formatting
│
├── data/
│   ├── intervals_client.py      # Intervals.icu API client (wellness + events)
│   ├── metrics.py               # dual HRV, RHR baseline, CTL/ATL/TSB, recovery
│   ├── database.py              # SQLAlchemy async ORM models and CRUD
│   ├── models.py                # Pydantic data models
│   └── openapi-spec.json        # Intervals.icu OpenAPI spec (reference)
│
├── ai/
│   ├── claude_agent.py          # Claude API — morning + weekly analysis
│   └── prompts.py               # system + report prompts (configurable)
│
├── api/
│   ├── server.py                # FastAPI application + static mount
│   └── routes.py                # REST endpoints + Telegram initData auth
│
├── mcp_server/
│   ├── __init__.py
│   ├── __main__.py              # python -m mcp_server
│   ├── app.py                   # FastMCP instance
│   ├── server.py                # imports all tools + resources
│   ├── tools/
│   │   ├── wellness.py          # get_wellness, get_wellness_range
│   │   ├── hrv.py               # get_hrv_analysis
│   │   ├── rhr.py               # get_rhr_analysis
│   │   ├── training_load.py     # get_training_load
│   │   ├── recovery.py          # get_recovery
│   │   ├── goal.py              # get_goal_progress
│   │   └── scheduled_workouts.py # get_scheduled_workouts
│   └── resources/
│       └── athlete_profile.py   # read-only: thresholds, zones, goal config
│
├── webapp/                      # Telegram Mini App (HTML + Chart.js + Tailwind)
├── migrations/                  # Alembic migrations
├── docs/                        # Design documents (HRV specs)
├── mockups/                     # Dashboard UI mockups
└── tests/
```

---

## Database Schema

Four tables:

### `wellness` — daily data from Intervals.icu
| Column | Type | Notes |
|---|---|---|
| `id` | String PK | "YYYY-MM-DD" |
| `ctl`, `atl`, `ramp_rate` | Float | training load from Intervals.icu |
| `ctl_load`, `atl_load` | Float | absolute load values |
| `sport_info` | JSON, nullable | per-sport breakdown |
| `weight`, `body_fat`, `vo2max` | Float, nullable | body metrics |
| `resting_hr` | Integer, nullable | resting heart rate |
| `hrv` | Float, nullable | RMSSD from wearable |
| `sleep_secs`, `sleep_score`, `sleep_quality` | nullable | sleep data |
| `steps` | Integer, nullable | daily steps |
| `ess_today`, `banister_recovery` | Float, nullable | stress/recovery (TODO) |
| `recovery_score` | Float, nullable | combined 0-100 |
| `recovery_category` | String, nullable | excellent/good/moderate/low |
| `recovery_recommendation` | String, nullable | zone2_ok/zone1_long/zone1_short/skip |
| `readiness_score` | Integer, nullable | derived from recovery_score |
| `readiness_level` | String, nullable | green/yellow/red |
| `ai_recommendation` | Text, nullable | Claude AI output |

### `hrv_analysis` — dual-algorithm HRV baselines
| Column | Type | Notes |
|---|---|---|
| `date` | String PK, FK → wellness | |
| `algorithm` | String PK | "flatt_esco" or "ai_endurance" |
| `status` | String | green/yellow/red/insufficient_data |
| `rmssd_7d`, `rmssd_sd_7d` | Float | 7-day baseline |
| `rmssd_60d`, `rmssd_sd_60d` | Float | 60-day baseline |
| `lower_bound`, `upper_bound` | Float | decision bounds |
| `cv_7d` | Float | coefficient of variation % |
| `swc` | Float | smallest worthwhile change |
| `days_available` | Integer | data points used |
| `trend_direction`, `trend_slope`, `trend_r_squared` | nullable | 7d trend |

Both algorithms are **always computed** on every save. `settings.HRV_ALGORITHM` selects which one feeds the recovery score.

### `rhr_analysis` — resting HR baselines
| Column | Type | Notes |
|---|---|---|
| `date` | String PK, FK → wellness | |
| `status` | String | green/yellow/red (inverted: high RHR = red) |
| `rhr_today` | Float | today's value |
| `rhr_7d`, `rhr_sd_7d` | Float | 7-day baseline |
| `rhr_30d`, `rhr_sd_30d` | Float | 30-day baseline (used for bounds) |
| `rhr_60d`, `rhr_sd_60d` | Float | 60-day baseline (context) |
| `lower_bound`, `upper_bound` | Float | ±0.5 SD of 30d |
| `cv_7d` | Float | coefficient of variation % |
| `days_available` | Integer | data points used |
| `trend_direction`, `trend_slope`, `trend_r_squared` | nullable | 7d trend |

### `scheduled_workouts` — planned workouts from Intervals.icu calendar
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | Intervals.icu event ID |
| `start_date_local` | String | "YYYY-MM-DD" |
| `end_date_local` | String, nullable | end date for multi-day events |
| `name` | String, nullable | workout name (e.g. "CYCLING:Endurance w/ 2min tempo") |
| `category` | String | WORKOUT / RACE_A / RACE_B / RACE_C / NOTE |
| `type` | String, nullable | sport type: Ride, Run, Swim, WeightTraining |
| `description` | Text, nullable | full workout structure (intervals, zones, power targets from HumanGo) |
| `moving_time` | Integer, nullable | planned duration in seconds |
| `distance` | Float, nullable | planned distance in km |
| `workout_doc` | JSON, nullable | native Intervals.icu workout format |
| `updated` | DateTime(tz), nullable | last update timestamp |

Synced every hour via scheduler. Upserted by Intervals.icu event ID.

---

## Current Implementation Status

| Module                    | Status      | Notes                                                                     |
| ------------------------- | ----------- | ------------------------------------------------------------------------- |
| `data/models.py`          | Done        | Pydantic models: `Wellness`, `ScheduledWorkout`, `RecoveryScore`, `RmssdStatus`, `RhrStatus`, `TrendResult` |
| `data/intervals_client.py`| Done        | Intervals.icu API client: wellness + events (scheduled workouts)          |
| `data/metrics.py`         | Done        | Dual HRV (Flatt & Esco + AIEndurance), RHR 7d/30d/60d, recovery score    |
| `data/database.py`        | Done        | `WellnessRow`, `HrvAnalysisRow`, `RhrAnalysisRow`, `ScheduledWorkoutRow` + CRUD |
| `ai/prompts.py`           | Done        | System + morning report prompts; includes planned workout block            |
| `ai/claude_agent.py`      | Done        | Morning AI recommendation (sonnet-4-6); evaluates planned workouts        |
| `bot/main.py`             | Partial     | `/morning`, `whoami` handlers; no /start /status /week /goal /zones       |
| `bot/scheduler.py`        | Done        | Wellness every 15 min (7-23h); workouts sync every 1 hr (4-23h)          |
| `bot/cli.py`              | Done        | shell, backfill, sync-workouts                                            |
| `bot/formatter.py`        | Done        | Report summary with recovery score                                        |
| `api/routes.py`           | Done        | `/api/report` with grouped JSON (recovery, hrv, rhr, sleep, training_load, body, stress) |
| `mcp_server/`             | Done        | 7 tools + 3 resources; read-only access to all athlete data               |
| `webapp/`                 | Scaffold    | HTML/CSS/JS files exist, needs update for new API structure                |

---

## Environment Variables (.env)

```env
# Intervals.icu
INTERVALS_API_KEY=your-api-key
INTERVALS_ATHLETE_ID=i12345

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# App
API_BASE_URL=https://your-api.railway.app
WEBAPP_URL=https://your-app.vercel.app
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/triathlon

# Athlete Profile
ATHLETE_AGE=43
ATHLETE_LTHR_RUN=158          # lactate threshold HR for running
ATHLETE_LTHR_BIKE=152
ATHLETE_MAX_HR=182
ATHLETE_RESTING_HR=42         # updated automatically from Intervals.icu
ATHLETE_FTP=245               # functional threshold power (watts)
ATHLETE_CSS=98                # critical swim speed (sec per 100m)

# Race Goal
GOAL_EVENT_NAME=Ironman 70.3
GOAL_EVENT_DATE=2026-09-15
GOAL_CTL_TARGET=75
GOAL_SWIM_CTL_TARGET=15
GOAL_BIKE_CTL_TARGET=35
GOAL_RUN_CTL_TARGET=25

# Scheduler
MORNING_REPORT_HOUR=7
MORNING_REPORT_MINUTE=0
TIMEZONE=Europe/Belgrade

# HRV primary algorithm for recovery score: "flatt_esco" (default) | "ai_endurance"
# Note: both algorithms are always computed and stored in hrv_analysis
HRV_ALGORITHM=flatt_esco
```

---

## Business Rules & Thresholds

> Full implementations are in `data/metrics.py`. This section documents the **design decisions** only.

### TSS by Sport

- **Running**: hrTSS (heart rate based) — `IF = (avg_hr - resting_hr) / (lthr - resting_hr)`
- **Cycling**: power-based TSS — `IF = normalized_power / ftp`
- **Swimming**: ssTSS — `IF = css_per_100m / pace_per_100m`

### CTL / ATL / TSB

**All CTL/ATL/TSB/ramp rate values come directly from the Intervals.icu API.** We do NOT recalculate them — Intervals.icu applies its own impulse-response model (τ_CTL=42d, τ_ATL=7d) and sport-specific TSS formulas. This is important because TrainingPeaks PMC uses different normalization coefficients, so the same athlete's TSB can differ by 5-15 points between platforms. All thresholds in this project are calibrated for Intervals.icu values.

- CTL = 42-day EMA of TSS ("fitness"), ATL = 7-day EMA ("fatigue"), TSB = CTL - ATL ("form")
- TSB > +10: under-training | -10..+10: optimal | -10..-25: productive overreach | < -25: overtraining risk

### HRV Recovery — Dual Algorithm

Both algorithms are **always computed** and stored in `hrv_analysis`. `settings.HRV_ALGORITHM` selects which feeds the recovery score. Minimum 14 days of data required.

| | Flatt & Esco (default) | AIEndurance |
|---|---|---|
| Compares | today vs 7d mean | 7d mean vs 60d mean |
| Bounds | asymmetric −1/+0.5 SD | symmetric ±0.5 SD |
| Response speed | fast (1-2 days) | slow (3-4 days) |
| Best for | acute changes, illness, travel | chronic fatigue accumulation |
| Data needed | 14 days min | 60 days for reliable bounds |

**Status interpretation:**
- `green` (above upper_bound) → train at full load
- `yellow` (between bounds) → train as planned, monitor
- `red` (below lower_bound) → reduce intensity or rest
- `insufficient_data` (< 14 days) → use readiness fallback

**SWC (Smallest Worthwhile Change):** 0.5 × SD_60d. Verdict: within noise / significant improvement / significant decline.

**CV:** < 5% very stable, 5-10% normal, > 10% unreliable (stress/illness/travel)

### Resting HR Analysis

Stored in `rhr_analysis` table. Baselines computed at 3 windows:
- **7-day** — short-term state + CV + trend
- **30-day** — primary bounds (±0.5 SD), status classification
- **60-day** — long-term context

Inverted vs RMSSD: elevated RHR = under-recovered (red), low RHR = well-recovered (green).

### ESS (External Stress Score)

Banister TRIMP-based, normalised so 1 hour at LTHR ≈ 100. Sport-agnostic.

### Banister Recovery Model

`R(t+1) = R(t) * exp(-1/τ) + k * ESS(t)` — defaults: k=0.1, τ=2.0 (conservative).
Re-calibrate every 4-6 weeks via `scipy.optimize.minimize` against actual RMSSD.

### Combined Recovery Score (0-100)

**Weights:**
- RMSSD status 35% | Banister R(t) 25% | RHR status 20% | Sleep 20%

**Status → score:** green=100, yellow=65, red=20, insufficient_data=50

**Modifiers:** late sleep (>23:00) −10, CV>15% −5, RMSSD declining → flag only

**Categories:** excellent >85, good 70-85, moderate 40-70, low <40

**Recommendations:** excellent/good → zone2_ok, moderate → zone1_long, low → zone1_short, red RMSSD → skip (overrides)

**Readiness:** derived from recovery — excellent/good → green, moderate → yellow, low → red.

### Trend Analysis

Linear regression on rolling window. Per-metric thresholds in `TREND_THRESHOLDS` dict.
Directions: rising_fast/rising/stable/declining/declining_fast. Show only if r² ≥ 0.3.

### HR Zones (% of LTHR)

```
Run:  Z1 0-72%, Z2 72-82%, Z3 82-87%, Z4 87-92%, Z5 92-100%
Bike: Z1 0-68%, Z2 68-83%, Z3 83-94%, Z4 94-105%, Z5 105-120%
```

---

## Morning Report Format (bot/formatter.py)

Template structure (data from `WellnessRow`):

```
{emoji} {category_text}
Readiness: {score}/100
Rec: {recommendation_text}
Sleep: {sleep_score}/100
```

**Display mappings:**
- Categories: excellent→"ОТЛИЧНОЕ ВОССТАНОВЛЕНИЕ", good→"ГОТОВ К НАГРУЗКЕ", moderate→"УМЕРЕННАЯ НАГРУЗКА", low→"РЕКОМЕНДОВАН ОТДЫХ"
- Recommendations: zone2_ok→"тренировка Z2 — полный объём", zone1_long→"только аэробная база, Z1-Z2", zone1_short→"лёгкая активность, 30-45 мин", skip→"отдых — не тренироваться"

---

## AI Recommendation — Morning Report (ai/claude_agent.py + ai/prompts.py)

### When It Runs

- **Only for current date** — `run_ai=True` passed by scheduler when `dt == date.today()`
- **NOT during backfill** — backfill calls `save_wellness` with `run_ai=False` (default)
- Called at step 5 of recovery pipeline, after HRV/RHR/recovery are computed
- Result persisted to `wellness.ai_recommendation`, returned via `/api/report`
- Skipped if `ai_recommendation` is already set (idempotent)

### Data Contract — What Claude Receives

The `MORNING_REPORT_PROMPT` template in `ai/prompts.py` assembles:

| Block | Fields | Source |
|---|---|---|
| **Recovery** | `recovery_score`, `recovery_category`, `recovery_recommendation` | `WellnessRow` |
| **Sleep** | `sleep_score`, `sleep_duration` | `WellnessRow` |
| **HRV** | `hrv_today`, `hrv_7d`, `hrv_delta%`, both algorithm statuses, `cv_7d`, `swc_verdict` | `WellnessRow` + `HrvAnalysisRow` (both) |
| **RHR** | `rhr_today`, `rhr_30d`, `rhr_delta`, `rhr_status` | `RhrAnalysisRow` |
| **Training Load** | `ctl`, `atl`, `tsb`, `ramp_rate` | `WellnessRow` (from Intervals.icu) |
| **Per-Sport CTL** | `ctl_swim`, `ctl_bike`, `ctl_run` + targets from settings | `WellnessRow.sport_info` JSON → `_extract_sport_ctl()` |
| **Race Goal** | `goal_event`, `weeks_remaining`, `goal_pct`, `swim/bike/run_pct` | Calculated from settings + current CTL |
| **Planned Workouts** | `planned_workouts` (formatted text: type, name, duration, description with intervals) | `ScheduledWorkoutRow` for today |

### System Prompt — Persona & Rules

Defined in `SYSTEM_PROMPT` (`ai/prompts.py`). Key constraints:

1. Persona: personal AI triathlon coach
2. Athlete profile: age (`ATHLETE_AGE`), target race (`GOAL_EVENT_NAME`)
3. Be specific — numbers, zones, durations
4. HRV >15% below baseline → reduce intensity
5. TSB < −25 → rest/recovery day
6. Max 250 words, language: Russian

### Expected Output — 4 Sections

```
1. Оценка готовности (🟢/🟡/🔴) + обоснование с цифрами
2. Оценка запланированной тренировки — подходит ли она текущему состоянию? Если нет — корректировка. Если тренировок нет — предложение своей.
3. Наблюдение о тренде нагрузки (CTL/ATL/TSB/ramp rate)
4. Заметка о прогрессе к цели
```

### Decision Logic — Workout Suggestion Rules

| Condition | Allowed Training |
|---|---|
| Recovery = `excellent` + TSB > 0 | Any intensity, key workout (Z3-Z4, intervals) |
| Recovery = `good`, TSB −10..+10 | Z2 full volume |
| Recovery = `moderate` or sleep < 50 | Z1-Z2 only, 45-60 min |
| Recovery = `low` or RMSSD = `red` | Rest or Z1 ≤30 min |
| TSB < −25 | Z1-Z2 cap, flag overreaching |
| HRV delta < −15% | Z1-Z2 max |
| Ramp rate > 7 TSS/week | Flag risk, low-stress session |

### Implementation Notes

- Model: `claude-sonnet-4-6`, max_tokens=1024
- Single API call per day to minimize costs
- On failure: logs exception, `ai_recommendation` stays `None`
- Prompt receives pre-interpreted deltas, not raw HRV bounds

---

## Bot Commands (bot/main.py)

```
/morning  — morning report from DB data + Mini App button
/start    — welcome + quick guide (not yet implemented)
/status   — quick numbers, no AI (not yet implemented)
/week     — weekly training summary (not yet implemented)
/goal     — goal progress breakdown (not yet implemented)
/zones    — current threshold zones (not yet implemented)
```

---

## API Endpoints (api/routes.py)

```
GET /api/report         — full morning report (grouped JSON)
GET /health             — healthcheck
```

**`/api/report` response structure:**
```json
{
  "date": "2026-03-23",
  "has_data": true,
  "recovery": { "score", "category", "emoji", "title", "recommendation", "readiness_score", "readiness_level" },
  "hrv": {
    "primary_algorithm": "flatt_esco",
    "flatt_esco": { "status", "today", "mean_7d", "sd_7d", "mean_60d", "sd_60d", "delta_pct", "lower_bound", "upper_bound", "swc", "swc_verdict", "cv_7d", "cv_verdict", "days_available", "trend" },
    "ai_endurance": { ... same fields ... }
  },
  "rhr": { "status", "today", "mean_7d", "sd_7d", "mean_30d", "sd_30d", "mean_60d", "sd_60d", "delta_30d", "lower_bound", "upper_bound", "cv_7d", "cv_verdict", "days_available", "trend" },
  "sleep": { "score", "quality", "duration", "duration_secs" },
  "training_load": { "ctl", "atl", "tsb", "ramp_rate" },
  "body": { "weight", "body_fat", "vo2max", "steps" },
  "stress": { "ess_today", "banister_recovery" },
  "ai_recommendation": "..."
}
```

Security: Telegram `initData` HMAC via `Authorization` header.

---

## Webapp Dashboard (webapp/)

Tabs: Today (readiness gauge + metrics + workout + AI), Load (CTL/ATL/TSB + TSS charts), Goal (CTL progress bars + countdown), Week (planned vs actual table).
Telegram theme via `--tg-theme-*` CSS variables.

---

## CLI (bot/cli.py)

```bash
python -m bot.cli shell                             # interactive Python shell
python -m bot.cli backfill                           # backfill wellness, last 180 days
python -m bot.cli backfill 2026-03-01                # single day
python -m bot.cli backfill 2026-01-01:2026-03-23     # date range
python -m bot.cli backfill 2026Q1                    # quarter
python -m bot.cli backfill 2026-03                   # month
python -m bot.cli sync-workouts                      # sync scheduled workouts, 14 days ahead
python -m bot.cli sync-workouts 30                   # sync scheduled workouts, 30 days ahead
```

Backfill fetches data from Intervals.icu day by day with 3s pause between requests.

Sync-workouts fetches planned workouts from Intervals.icu calendar and upserts into `scheduled_workouts` table. Also runs automatically every hour via scheduler.

---

## Migrations (Alembic)

```bash
poetry run alembic upgrade head                     # apply all migrations
poetry run alembic revision --autogenerate -m "msg"  # generate new migration
poetry run alembic downgrade -1                      # rollback last migration
```

---

## Docker

```bash
docker compose up -d db          # PostgreSQL only
docker compose up -d             # all (db + migrate + bot)
```

---

## Key Implementation Notes

- **Intervals.icu API** — official REST API; wellness synced every 15 min (7-23h), scheduled workouts every 1 hr (4-23h)
- **Both HRV algorithms** are always computed and stored; `HRV_ALGORITHM` selects primary for recovery
- **Claude API** once per day (morning report) to minimize costs
- **All timestamps** UTC in DB, local timezone for display
- **HRV algorithm** never changes mid-season without re-baselining
- **Mini App** should degrade gracefully if API unreachable

---

## Next Steps (Priority Order)

1. **Update webapp** — adapt report.html to new grouped API response structure
2. **Implement bot commands** — /start /status /week /goal /zones
3. **ESS/Banister pipeline** — sync activities → ESS per activity → Banister → persist
4. **Additional API endpoints** — /api/dashboard, /api/training-load, /api/goal, /api/weekly-summary
5. **MCP Phase 2** — replace `claude_agent.py` fixed prompt with MCP tool-use (Claude picks which data to query)
6. **MCP Phase 3** — free-form Telegram chat — user asks any question, Claude queries tools as needed

---

## MCP Integration — Phase 1 (Done)

> Full plan: `docs/MCP_INTEGRATION_PLAN.md`

### Overview

MCP server exposes athlete data as read-only tools. Parallel access channel for Claude Desktop and future integrations — does NOT change the existing pipeline.

Run: `python -m mcp_server`

### Tools (7)

| Tool | Description |
|---|---|
| `get_wellness(date)` | All wellness fields for a day |
| `get_wellness_range(from, to)` | Multi-day wellness for trends |
| `get_hrv_analysis(date, algorithm?)` | HRV status + baselines + SWC + CV + trend |
| `get_rhr_analysis(date)` | RHR status + 7d/30d/60d baselines + trend |
| `get_training_load(date)` | CTL/ATL/TSB/ramp_rate + per-sport CTL (Intervals.icu) |
| `get_recovery(date)` | Recovery score, category, recommendation |
| `get_goal_progress()` | Race goal, weeks remaining, per-sport CTL vs target % |
| `get_scheduled_workouts(date?, days_ahead?)` | Planned workouts from Intervals.icu calendar with full description |

### Resources (3)

| Resource | Description |
|---|---|
| `athlete://profile` | Static athlete profile: thresholds, HR zones |
| `athlete://goal` | Race goal config: event, targets |
| `athlete://thresholds` | Business rules: TSB zones, ramp rate, HRV/RHR interpretation |

### Key constraint

All tools must document in docstrings that CTL/ATL/TSB come from Intervals.icu and thresholds are calibrated for its model, not TrainingPeaks.

### Phase 2-3 (future)

- Phase 2: Replace `claude_agent.py` fixed prompt with MCP tool-use (Claude picks which data to query)
- Phase 3: Free-form Telegram chat — user asks any question, Claude queries tools as needed

---

## Contributing

- Follow existing module structure
- Add Pydantic models for new data types in `data/models.py`
- Write tests for all metric calculations (must be deterministic)
- Keep Claude API prompt modular — add sections to `prompts.py`
- Document new env vars in `.env.example`
