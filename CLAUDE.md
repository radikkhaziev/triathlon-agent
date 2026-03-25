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
| AI Analysis       | Anthropic Claude API (`claude-sonnet-4-6`) + Google Gemini (optional) |
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
│   ├── main.py                  # bot entry point (polling + webhook modes)
│   ├── cli.py                   # CLI: shell, backfill, sync-workouts
│   ├── scheduler.py             # periodic jobs (wellness every 10 min, workouts every 1 hr)
│   └── formatter.py             # report summary formatting
│
├── data/
│   ├── intervals_client.py      # Intervals.icu API client (wellness + events + download_fit)
│   ├── metrics.py               # dual HRV, RHR baseline, CTL/ATL/TSB, ESS/Banister, recovery
│   ├── hrv_activity.py          # Level 2: DFA a1 pipeline (FIT → RR → DFA → thresholds → Ra/Da)
│   ├── database.py              # SQLAlchemy async ORM models and CRUD
│   ├── models.py                # Pydantic data models
│   ├── utils.py                 # SPORT_MAP, extract_sport_ctl, extract_sport_ctl_tuple
│   └── openapi-spec.json        # Intervals.icu OpenAPI spec (reference)
│
├── ai/
│   ├── claude_agent.py          # Claude API — morning + weekly analysis
│   ├── gemini_agent.py          # Gemini API — optional second opinion (same prompts)
│   └── prompts.py               # system + report prompts (configurable, shared by both AI)
│
├── api/
│   ├── server.py                # FastAPI application + static mount + Telegram webhook
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
│   │   ├── scheduled_workouts.py # get_scheduled_workouts
│   │   ├── activities.py        # get_activities (with has_hrv_analysis)
│   │   └── activity_hrv.py      # get_activity_hrv, get_thresholds_history, get_readiness_history
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

Seven tables:

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
| `ess_today`, `banister_recovery` | Float, nullable | stress/recovery |
| `recovery_score` | Float, nullable | combined 0-100 |
| `recovery_category` | String, nullable | excellent/good/moderate/low |
| `recovery_recommendation` | String, nullable | zone2_ok/zone1_long/zone1_short/skip |
| `readiness_score` | Integer, nullable | derived from recovery_score |
| `readiness_level` | String, nullable | green/yellow/red |
| `ai_recommendation` | Text, nullable | Claude AI output |
| `ai_recommendation_gemini` | Text, nullable | Gemini AI output (optional, only if GOOGLE_AI_API_KEY set) |

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
| `last_synced_at` | DateTime(tz), nullable | set to `now(UTC)` on every upsert in `save_scheduled_workouts()` |

Synced every 1 hour (at :00, hours 4-23) via scheduler. Upserted by Intervals.icu event ID.

### `activities` — completed activities from Intervals.icu
| Column | Type | Notes |
|---|---|---|
| `id` | String PK | Intervals.icu activity ID (e.g. "i12345") |
| `start_date_local` | String | "YYYY-MM-DD" |
| `type` | String, nullable | sport type: Ride, Run, Swim, VirtualRide, etc. |
| `icu_training_load` | Float, nullable | TSS/hrTSS/ssTSS from Intervals.icu |
| `moving_time` | Integer, nullable | duration in seconds |
| `average_hr` | Float, nullable | average heart rate during activity |
| `last_synced_at` | DateTime(tz), nullable | set to `now(UTC)` on every upsert in `save_activities()` |

Synced every hour at :30 via scheduler. Used for per-sport CTL calculation (EMA τ=42d).
Indexed on `start_date_local` for range queries.

### `activity_hrv` — post-activity DFA alpha 1 analysis (Level 2)
| Column | Type | Notes |
|---|---|---|
| `activity_id` | String PK, FK → activities | |
| `date` | String | "YYYY-MM-DD" |
| `activity_type` | String | "Ride" or "Run" |
| `hrv_quality` | String, nullable | good/moderate/poor |
| `artifact_pct` | Float, nullable | % of corrected RR intervals |
| `rr_count` | Integer, nullable | total RR intervals extracted |
| `dfa_a1_mean` | Float, nullable | mean DFA alpha 1 across activity |
| `dfa_a1_warmup` | Float, nullable | DFA alpha 1 during first 15 min |
| `hrvt1_hr`, `hrvt1_power`, `hrvt1_pace` | nullable | aerobic threshold (a1=0.75) |
| `hrvt2_hr` | Float, nullable | anaerobic threshold HR (a1=0.50) |
| `threshold_r_squared`, `threshold_confidence` | nullable | regression quality |
| `ra_pct`, `pa_today` | Float, nullable | Readiness (Ra) vs baseline |
| `da_pct` | Float, nullable | Durability (Da) first vs second half |
| `processing_status` | String | processed/no_rr_data/low_quality/too_short/error |
| `dfa_timeseries` | JSON, nullable | sampled every 30s for charts |

Processed every 5 min via scheduler. Only bike/run activities ≥15 min with chest strap HRM (ANT+).

### `pa_baseline` — Pa baseline for Readiness (Ra) calculation
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | autoincrement |
| `activity_type` | String | "Ride" or "Run" |
| `date` | String | "YYYY-MM-DD" |
| `pa_value` | Float | power (bike) or speed (run) at fixed DFA a1 during warmup |
| `dfa_a1_ref` | Float, nullable | reference DFA a1 level |
| `quality` | String, nullable | good/moderate/poor |

Ra baseline = average Pa over last 14 days (≥3 data points required).

---

## Current Implementation Status

| Module                    | Status      | Notes                                                                     |
| ------------------------- | ----------- | ------------------------------------------------------------------------- |
| `data/models.py`          | Done        | Pydantic models: `Wellness`, `Activity`, `ScheduledWorkout`, `RecoveryScore`, `RmssdStatus`, `RhrStatus`, `TrendResult` |
| `data/intervals_client.py`| Done        | Intervals.icu API client: wellness, activities, events, download_fit      |
| `data/metrics.py`         | Done        | Dual HRV, RHR, recovery score, per-sport CTL, ESS/Banister pipeline      |
| `data/hrv_activity.py`    | Done        | Level 2: DFA a1 pipeline — RR extraction, artifact correction, DFA timeseries, thresholds, Ra/Da |
| `data/database.py`        | Done        | `WellnessRow`, `HrvAnalysisRow`, `RhrAnalysisRow`, `ActivityRow`, `ScheduledWorkoutRow`, `ActivityHrvRow`, `PaBaselineRow` + CRUD |
| `data/utils.py`           | Done        | `SPORT_MAP`, `extract_sport_ctl`, `extract_sport_ctl_tuple`               |
| `ai/prompts.py`           | Done        | System + morning report prompts; includes planned workouts + yesterday DFA |
| `ai/claude_agent.py`      | Done        | Morning AI recommendation (sonnet-4-6); evaluates planned workouts + DFA context |
| `ai/gemini_agent.py`      | Not started | Optional Gemini second opinion; same prompts, `google-genai` SDK; gated by `GOOGLE_AI_API_KEY` |
| `bot/main.py`             | Done        | `/morning`, `whoami` handlers; `build_application()` shared by polling + webhook; no /start /status /week /goal /zones |
| `bot/scheduler.py`        | Done        | Wellness every 10 min; workouts every 1 hr; activities at :30; DFA every 5 min + post-activity TG notification; evening report at 21:00; 5 cron jobs total |
| `bot/cli.py`              | Done        | shell, backfill, sync-workouts, sync-activities, process-fit              |
| `bot/formatter.py`        | Done        | Report summary, post-activity DFA notification, evening report + tomorrow's plan |
| `api/routes.py`           | Done        | `/api/report`, `/api/scheduled-workouts`, `/api/jobs/sync-workouts` + `/health` |
| `api/dashboard_routes.py` | Scaffold    | Mock data endpoints for dashboard visual preview: `/api/dashboard`, `/api/training-load`, `/api/goal`, job stubs |
| `mcp_server/`             | Done        | 12 tools + 3 resources; includes get_activities + Level 2 DFA tools (activity_hrv, thresholds_history, readiness_history) |
| `webapp/index.html`       | Done        | Public landing page                                                        |
| `webapp/report.html`      | Done        | Morning report (single-page, calls `/api/report`)                          |
| `webapp/plan.html`        | Done        | Scheduled workouts by week, sync button, collapsible HumanGo descriptions  |
| `webapp/dashboard.html`   | Scaffold    | Multi-tab dashboard, needs API endpoints                                   |

---

## Environment Variables (.env)

```env
# Intervals.icu
INTERVALS_API_KEY=your-api-key
INTERVALS_ATHLETE_ID=i12345

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789
TELEGRAM_WEBHOOK_URL=                 # base URL for webhook mode, e.g. "https://bot.example.com"; empty = polling

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Google AI (optional — enables Gemini second opinion in dashboard)
GOOGLE_AI_API_KEY=                    # empty = Gemini disabled

# App
API_BASE_URL=https://your-api.railway.app
WEBAPP_URL=https://your-app.vercel.app
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/triathlon

# Athlete Profile (from HumanGo tests, Nov-Dec 2025 + Mar 2026)
ATHLETE_AGE=43
ATHLETE_LTHR_RUN=153          # lactate threshold HR for running
ATHLETE_LTHR_BIKE=153         # lactate threshold HR for cycling
ATHLETE_MAX_HR=179            # max HR (bike test, Dec 2025)
ATHLETE_FTP=233               # functional threshold power (watts, Dec 2025)
ATHLETE_CSS=141               # critical swim speed (sec per 100m = 2:21, Mar 2026)

# Race Goal
GOAL_EVENT_NAME=Ironman 70.3
GOAL_EVENT_DATE=2026-09-15
GOAL_CTL_TARGET=75
GOAL_SWIM_CTL_TARGET=15
GOAL_BIKE_CTL_TARGET=35
GOAL_RUN_CTL_TARGET=25

TIMEZONE=Europe/Belgrade

# HRV primary algorithm for recovery score: "flatt_esco" (default) | "ai_endurance"
# Note: both algorithms are always computed and stored in hrv_analysis
HRV_ALGORITHM=flatt_esco

# MCP
MCP_AUTH_TOKEN=your-secret-token    # Bearer token for remote MCP access via /mcp endpoint
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

`R(t+1) = R(t) + (100 - R(t)) * (1 - exp(-1/τ)) - k * ESS(t)` — defaults: k=0.1, τ=2.0 (conservative).
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
| **Yesterday DFA** | `yesterday_dfa_summary` (Ra, Da, HRVT1, quality per activity) | `ActivityHrvRow` + `ActivityRow` for yesterday |

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

### Gemini Second Opinion (optional)

Enabled when `GOOGLE_AI_API_KEY` is set in `.env`. Disabled otherwise — no Gemini code runs, no tab in dashboard.

**Architecture:**
- Module: `ai/gemini_agent.py` — same prompt from `prompts.py`, called via `google-genai` SDK
- Model: `gemini-2.5-flash` (or `gemini-2.5-pro`)
- Both AI calls run in parallel via `asyncio.gather` during `save_wellness(run_ai=True)`
- Each call is independent — if one fails, the other still saves
- Result persisted to `wellness.ai_recommendation_gemini`
- Skipped if `ai_recommendation_gemini` is already set (idempotent)

**Display rules:**
- **Telegram morning report**: only Claude recommendation (no change)
- **Dashboard**: two tabs — Claude | Gemini (Gemini tab hidden if `GOOGLE_AI_API_KEY` not configured)
- **`/api/report`**: returns both `ai_recommendation` and `ai_recommendation_gemini` (latter is `null` if disabled)
- **MCP**: `get_recovery` returns both fields

---

## Bot Commands (bot/main.py)

```
/morning  — morning report from DB data + Mini App button
/start    — welcome + quick guide (not yet implemented)
/status   — quick numbers, no AI (not yet implemented)
/week     — weekly training summary (not yet implemented)
/goal     — goal progress breakdown (not yet implemented)
/zones    — current threshold zones (not yet implemented)
/iqos     — increment daily IQOS stick counter (not yet implemented)
```

### `/iqos` — Daily IQOS Counter

Цель: отслеживание количества выкуренных стиков IQOS за день (помощь в отказе от курения).

**Поведение:**
- `/iqos` (без аргументов) — инкремент +1, ответ: `🚬 Сегодня: {count}`
- Счётчик привязан к дате (сбрасывается каждый день)
- Показывается в вечернем отчёте: `🚬 IQOS: {count}` (если count > 0)

**Реализация:**
- Новая таблица `iqos_daily`: `date` (String PK, "YYYY-MM-DD"), `count` (Integer), `updated_at` (DateTime)
- Alembic migration
- Bot handler: `CommandHandler("iqos", iqos_handler)` в `bot/main.py`
- MCP tools: `get_iqos_count(date)`, `set_iqos_count(date, count)` для доступа из Claude Desktop
- Интеграция в `build_evening_message` — строка `🚬 IQOS: {count}` перед блоком "Завтра"

---

## API Endpoints (api/server.py + api/routes.py)

```
GET  /api/report                        — full morning report (grouped JSON)
GET  /api/scheduled-workouts?week_offset=0 — weekly plan (Mon-Sun), 7 days with workouts
GET  /api/activities-week?week_offset=0 — weekly activities (Mon-Sun), 7 days with completed activities
POST /api/jobs/sync-workouts            — trigger scheduled workouts sync (initData auth)
POST /api/jobs/sync-activities          — trigger activities sync (initData auth)
GET  /health                            — healthcheck
POST /telegram/webhook                  — Telegram update receiver (webhook mode only)
POST /mcp                               — MCP server (Streamable HTTP transport, Bearer auth)

# Dashboard API (api/dashboard_routes.py) — mock data for visual preview
GET  /api/dashboard                     — today tab: readiness, metrics, AI recommendation
GET  /api/training-load?days=84         — CTL/ATL/TSB + per-sport CTL time series
GET  /api/activities?days=28            — completed activities with sport and TSS
GET  /api/goal                          — race goal progress
GET  /api/weekly-summary                — this week's training summary by sport
GET  /api/scheduled?days=7              — planned workouts for N days (legacy mock)
POST /api/jobs/sync-activities          — trigger activity sync + DFA (stub)
POST /api/jobs/morning-report           — trigger morning report (stub)
POST /api/jobs/sync-wellness            — trigger wellness sync (stub)
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
  "ai_recommendation": "...",
  "ai_recommendation_gemini": "..."
}
```

Security: Telegram `initData` HMAC via `Authorization` header.

---

## Webapp (webapp/)

Multiple standalone pages (dark theme, Inter font, mobile-first):

| Page | Status | Description |
|---|---|---|
| `index.html` | Done | Public landing page — features, how it works, links to dashboard/plan/Telegram |
| `report.html` | Done | Morning report — recovery gauge, HRV/RHR/sleep metrics, AI recommendation. Source: `/api/report` |
| `plan.html` | Done | Scheduled workouts by week (Mon-Sun), prev/next navigation, sync button with `last_synced_at`, collapsible HumanGo descriptions. Source: `/api/scheduled-workouts` |
| `activities.html` | Planned | Completed activities by week, sync button. Source: `/api/activities-week` |
| `dashboard.html` | Scaffold | Multi-tab dashboard (Today, Calendar, Load, Goal). Needs API endpoints |

Telegram Mini App support via `--tg-theme-*` CSS variables (report.html, plan.html). Landing page is standalone (no Telegram SDK).

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
docker compose up -d             # all (db + migrate + api)
docker compose --profile polling up -d  # all + bot in polling mode (local dev)
```

### Running CLI commands in Docker

In production (webhook mode) there is no standalone `bot` container — the bot runs inside `api`. Use `docker compose run` to execute CLI commands:

```bash
docker compose run --rm api python -m bot.cli backfill
docker compose run --rm api python -m bot.cli backfill-details
docker compose run --rm api python -m bot.cli sync-workouts
docker compose run --rm api python -m bot.cli sync-activities
docker compose run --rm api alembic upgrade head
```

`--rm` removes the container after execution. Uses the same image and `.env` as the `api` service.

---

## Key Implementation Notes

- **Intervals.icu API** — official REST API; wellness synced every 10 min (5-23h), scheduled workouts every 1 hr (4-23h), activities at :30 (4-23h), DFA processing every 5 min (5-22h), evening report at 21:00
- **Both HRV algorithms** are always computed and stored; `HRV_ALGORITHM` selects primary for recovery
- **Claude API** once per day (morning report) to minimize costs
- **All timestamps** UTC in DB, local timezone for display
- **HRV algorithm** never changes mid-season without re-baselining
- **Mini App** should degrade gracefully if API unreachable
- **Telegram bot** supports two modes: polling (local dev) and webhook (production) — controlled by `TELEGRAM_WEBHOOK_URL`

### Telegram Bot — Polling vs Webhook

| | Polling (default) | Webhook |
|---|---|---|
| When | `TELEGRAM_WEBHOOK_URL` empty | `TELEGRAM_WEBHOOK_URL` set |
| Entry point | `bot/main.py` → `start_bot()` → `run_polling()` | `api/server.py` lifespan → `build_application()` |
| How it runs | Standalone process | Embedded in FastAPI server |
| Updates | Bot polls Telegram API | Telegram POSTs to `/telegram/webhook` |
| Auth | — | `X-Telegram-Bot-Api-Secret-Token` header (SHA256 of bot token, first 32 hex chars) |
| Updater | Built-in PTB Updater | Disabled (`.updater(None)`), manual `process_update()` |
| Use case | Local development | Production (VPS with HTTPS) |

Webhook lifecycle in `api/server.py` lifespan:
- **Startup**: `initialize()` → `post_init()` (starts scheduler) → `start()` → `set_webhook()`
- **Shutdown**: `delete_webhook()` → `stop()` → `shutdown()` → `post_shutdown()` (closes IntervalsClient)

---

## Documentation (docs/)

Detailed design documents and implementation plans:

| Document | Description |
|---|---|
| `docs/HRV_MODULE_SPEC.md` | HRV module architecture — Level 1 (RMSSD recovery, done) + Level 2 (DFA alpha 1, deferred) |
| `docs/HRV_IMPLEMENTATION_PLAN.md` | Level 1 implementation steps — all completed |
| `docs/ESS_BANISTER_PLAN.md` | ESS/Banister pipeline — implemented |
| `docs/DFA_ALPHA1_PLAN.md` | Level 2: DFA alpha 1 — post-activity HRV pipeline (FIT → RR → DFA → thresholds → Ra/Da) — implemented |
| `docs/PROCESS_FIT_JOB.md` | process_fit_job pipeline docs — steps, quality testing (ANT+ vs BLE), hardware config |
| `docs/MCP_INTEGRATION_PLAN.md` | MCP roadmap — Phase 1 (done), Phase 2-3 (future) |
| `docs/PROGRESS_TRACKING_PLAN.md` | Progress tracking — Efficiency Factor (bike/run) + swim pace/SWOLF trends |
| `docs/SCHEDULED_WORKOUTS_PAGE.md` | Scheduled workouts dashboard page — architecture doc (implemented) |
| `docs/ACTIVITIES_PAGE.md` | Activities dashboard page — architecture doc |
| `docs/ACTIVITY_DETAILS_PHASE1.md` | Activity Details Phase 1 — fetch from Intervals.icu API & store in DB |
| `docs/ACTIVITY_DETAILS_PHASE2.md` | Activity Details Phase 2 — web display (inline + full page) + MCP tool |

---

## Next Steps (Priority Order)

1. ~~**ESS/Banister pipeline**~~ — Done. `average_hr` added, ESS/Banister integrated into recovery pipeline.
2. ~~**DFA Alpha 1 pipeline (Level 2)**~~ — Done. Post-activity HRV: FIT→RR→DFA a1→thresholds→Ra/Da. Cron every 5 min, 3 MCP tools.
3. ~~**Post-activity Telegram notification**~~ — Done. DFA summary sent after FIT processing (Ra, Da, thresholds).
4. ~~**Evening report**~~ — Done. Daily summary at 21:00 via Telegram (activities, recovery, DFA).
5. ~~**Morning prompt + DFA context**~~ — Done. Yesterday's DFA data added to MORNING_REPORT_PROMPT.
6. **Activity Details** — расширенная статистика per activity (HR, power, pace, splits). Новая таблица `activity_details`, MCP tool, Intervals.icu API + FIT parsing.
7. ~~**Scheduled Workouts page**~~ — Done. `plan.html` with weekly view, sync button, collapsible HumanGo descriptions. API: `/api/scheduled-workouts`, `/api/jobs/sync-workouts`.
8. **Web Dashboard** — full dashboard с вкладками: Today, Calendar (activities + plan), Load, Goal. Manual job triggers. Вертикальные срезы: API + frontend за один раз.
9. **Implement bot commands** — /start /status /week /goal /zones /iqos
10. **Web App auth model** — определить что видит: анонимный пользователь (прямой URL), неизвестный пользователь Telegram, авторизованный владелец. Авторизация с десктоп-браузера без Telegram (например, token-based login).
11. **MCP Phase 2** — replace `claude_agent.py` fixed prompt with MCP tool-use (Claude picks which data to query)
12. **MCP Phase 3** — free-form Telegram chat — user asks any question, Claude queries tools as needed

---

## MCP Integration — Phase 1 (Done)

> Full plan: `docs/MCP_INTEGRATION_PLAN.md`

### Overview

MCP server exposes athlete data as read-only tools. Parallel access channel for Claude Desktop and future integrations — does NOT change the existing pipeline.

Run standalone: `python -m mcp_server`
Production: mounted at `/mcp` in FastAPI (Streamable HTTP transport), protected by Bearer token (`MCP_AUTH_TOKEN`). Auth middleware (`MCPAuthMiddleware`) validates tokens on all `/mcp*` paths.

### Tools (12)

| Tool | Description |
|---|---|
| `get_wellness(date)` | All wellness fields for a day |
| `get_wellness_range(from, to)` | Multi-day wellness for trends |
| `get_activities(date?, days_back?)` | Completed activities with TSS, duration, has_hrv_analysis flag |
| `get_hrv_analysis(date, algorithm?)` | HRV status + baselines + SWC + CV + trend |
| `get_rhr_analysis(date)` | RHR status + 7d/30d/60d baselines + trend |
| `get_training_load(date)` | CTL/ATL/TSB/ramp_rate + per-sport CTL (Intervals.icu) |
| `get_recovery(date)` | Recovery score, category, recommendation |
| `get_goal_progress()` | Race goal, weeks remaining, per-sport CTL vs target % |
| `get_scheduled_workouts(date?, days_ahead?)` | Planned workouts from Intervals.icu calendar with full description |
| `get_activity_hrv(activity_id)` | DFA a1 analysis: quality, thresholds (HRVT1/HRVT2), Ra, Da |
| `get_thresholds_history(sport?, days_back?)` | HRVT1/HRVT2 trend over time (fitness progression) |
| `get_readiness_history(sport?, days_back?)` | Readiness (Ra) trend — warmup power/pace vs baseline |

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

## Activity Details (#6)

Расширенная статистика per activity — HR, power, pace, zones, intervals, efficiency metrics.
Двухфазная реализация: Phase 1 — fetch & store, Phase 2 — web + MCP display.

### Источники данных

1. **Intervals.icu API** (`GET /api/v1/activity/{id}`) — основной источник. Все метрики уже посчитаны: NP, IF, VI, EF, decoupling, зоны HR/power/pace, trimp
2. **Intervals.icu API** (`GET /api/v1/activity/{id}/intervals`) — per-interval breakdown: watts, HR, speed, cadence, decoupling per interval
3. **FIT file** — уже парсим для DFA. Дополнительно на этом этапе НЕ используем. Может понадобиться позже для SWOLF (плавание), per-second streams

> Полная спека Phase 1: `docs/ACTIVITY_DETAILS_PHASE1.md`

### Новая таблица `activity_details`

| Column | Type | Notes |
|---|---|---|
| `activity_id` | String PK, FK → activities | |
| `max_hr` | Integer, nullable | max heart rate |
| `avg_power` | Integer, nullable | average power watts (bike) |
| `normalized_power` | Integer, nullable | NP watts (bike) |
| `avg_speed` | Float, nullable | m/s |
| `max_speed` | Float, nullable | m/s |
| `pace` | Float, nullable | sec/km (run) |
| `gap` | Float, nullable | grade adjusted pace sec/km (run) |
| `distance` | Float, nullable | meters |
| `elevation_gain` | Float, nullable | meters |
| `avg_cadence` | Float, nullable | rpm (bike) or spm (run) |
| `avg_stride` | Float, nullable | meters (run) |
| `calories` | Integer, nullable | kcal |
| `intensity_factor` | Float, nullable | IF = NP/FTP (from Intervals.icu) |
| `variability_index` | Float, nullable | VI = NP/avg power |
| `efficiency_factor` | Float, nullable | EF from Intervals.icu |
| `power_hr` | Float, nullable | power:HR ratio |
| `decoupling` | Float, nullable | aerobic decoupling % (<5% = good aerobic base) |
| `trimp` | Float, nullable | training impulse |
| `hr_zones` | JSON, nullable | array of seconds per HR zone |
| `power_zones` | JSON, nullable | array of seconds per power zone (bike) |
| `pace_zones` | JSON, nullable | array of seconds per pace zone (run/swim) |
| `intervals` | JSON, nullable | per-interval breakdown from Intervals.icu |

### Заполнение (Phase 1)

- При `sync_activities_job` — запрашивать detail для **новых** активностей (без записи в `activity_details`). Пауза 1 сек между запросами
- Backfill CLI: `python -m bot.cli backfill-details [days]`
- НЕ запрашивать для всех при каждом sync — только для новых

### MCP Tool + Web (Phase 2)

- MCP: `get_activity_details(activity_id)` — объединяет `activity_details` + `activity_hrv` в один ответ
- Web: клик по активности на `activities.html` раскрывает детальную статистику, зоны, интервалы

---

## Web Dashboard (#7)

Полноценный дашборд с управлением, не только просмотр.

### Архитектура

Single-page app: `dashboard.html` + `app.js` + `charts.js` + `style.css`.
Telegram Mini App (через WebAppInfo) или standalone (прямой URL).
Стек: HTML + Chart.js + Tailwind CSS (CDN). Без фреймворков.

### Вкладки

**Today** — утренний отчёт
- Recovery gauge + score
- HRV/RHR/Sleep метрики
- CTL/ATL/TSB
- AI рекомендация
- Источник: `GET /api/report` (уже есть)

**Calendar** — активности и план по дням
- Календарь-сетка с иконками спорта
- Клик по дню → список активностей + запланированные тренировки
- Клик по активности → детальная статистика (HR, power, pace, laps) из `get_activity_details`
- Источник: `GET /api/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD`

**Load** — графики тренировочной нагрузки
- CTL/ATL/TSB line chart (12 недель)
- Daily TSS stacked bar chart по видам спорта
- Ramp rate indicator
- Источник: `GET /api/training-load?days=84`

**Goal** — прогресс к Ironman 70.3
- Countdown (weeks remaining)
- Per-sport CTL progress bars vs targets
- CTL trend chart per sport
- Источник: `GET /api/goal`

### Manual Job Triggers

Кнопки в UI для ручного запуска джобов (без ожидания cron):

| Кнопка | API endpoint | Что делает |
|---|---|---|
| 🔄 Синхронизировать план | `POST /api/jobs/sync-workouts` | `scheduled_workouts_job()` |
| 🔄 Загрузить активности | `POST /api/jobs/sync-activities` | `sync_activities_job()` + `process_fit_job()` |
| 📊 Утренний отчёт | `POST /api/jobs/morning-report` | `daily_metrics_job(run_ai=True)` |
| 🔄 Обновить wellness | `POST /api/jobs/sync-wellness` | `daily_metrics_job()` |

**Безопасность:** Job endpoints защищены Telegram initData (как `/api/report`) — только авторизованный пользователь.

**Ответ:** `202 Accepted` + job запускается async. Опционально: WebSocket/SSE для статуса выполнения (v2).

### API Endpoints (новые)

```
GET  /api/calendar?from=&to=       — активности + planned workouts по дням
GET  /api/training-load?days=84    — CTL/ATL/TSB/TSS timeseries
GET  /api/goal                     — race goal progress
GET  /api/activity/{id}/details    — full activity stats + laps
POST /api/jobs/sync-workouts       — trigger plan sync
POST /api/jobs/sync-activities     — trigger activity sync + DFA
POST /api/jobs/morning-report      — trigger morning report
POST /api/jobs/sync-wellness       — trigger wellness sync
```

### Порядок реализации (вертикальные срезы)

1. **Today tab** — адаптировать `app.js` под `/api/report` (минимум работы)
2. **Job triggers** — POST endpoints + кнопки в UI (максимальная польза сразу)
3. **Load tab** — `/api/training-load` + Chart.js графики
4. **Goal tab** — `/api/goal` + progress bars
5. **Calendar tab** — `/api/calendar` + activity details drill-down (самый объёмный)

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
