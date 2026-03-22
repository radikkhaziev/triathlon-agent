# Triathlon AI Agent — Project Specification for Claude Code

> Read this before taking any action. Architecture, stack, structure, and business logic.

---

## What We're Building

A personal AI agent for a triathlete that:

- Every morning reads sleep and HRV data from Garmin Connect
- Compares it with scheduled workouts from the Garmin calendar (populated by HumanGO training app)
- Calculates training load (CTL/ATL/TSB) across all sports
- Evaluates progress toward a target race (e.g., Ironman 70.3)
- Sends a morning report via Telegram Bot
- Opens a beautiful interactive dashboard via Telegram Mini App

---

## Tech Stack

| Component         | Technology                                     |
| ----------------- | ---------------------------------------------- |
| Language          | Python 3.12+                                   |
| Package Manager   | Poetry                                         |
| Garmin Data       | `garminconnect` (cyberjunky)                   |
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
│   ├── cli.py                   # CLI: echo, backfill, garmin-login, shell
│   ├── scheduler.py             # periodic jobs (daily_metrics_job every 15 min)
│   └── formatter.py             # morning report formatting (MarkdownV2)
│
├── data/
│   ├── garmin_client.py         # wrapper around garminconnect (singleton)
│   ├── metrics.py               # CTL/ATL/TSB/hrTSS + recovery calculations
│   ├── database.py              # SQLAlchemy async ORM models and CRUD
│   └── models.py                # Pydantic data models (20+ models)
│
├── ai/
│   ├── claude_agent.py          # Claude API — morning + weekly analysis
│   └── prompts.py               # system + report prompts (configurable)
│
├── api/
│   ├── server.py                # FastAPI application + static mount
│   └── routes.py                # REST endpoints + Telegram initData auth
│
├── webapp/                      # Telegram Mini App (HTML + Chart.js + Tailwind)
├── migrations/                  # Alembic migrations
├── docs/                        # Design documents (HRV specs)
├── mockups/                     # Dashboard UI mockups
└── tests/
```

---

## Current Implementation Status

| Module                  | Status      | Notes                                                                                 |
| ----------------------- | ----------- | ------------------------------------------------------------------------------------- |
| `data/models.py`        | Done        | 20+ Pydantic models incl. `RecoveryScore`, `RmssdStatus`, `TrendResult`               |
| `data/garmin_client.py` | Done        | 16+ methods, singleton, retry, cooldown                                               |
| `data/metrics.py`       | Done        | TSS (3 sports), CTL/ATL/TSB, readiness, dual-algorithm RMSSD, RHR, ESS, Banister, combined recovery |
| `data/database.py`      | Done        | `DailyMetricsRow` (~30 cols), `ActivityRow`, `ScheduledWorkoutRow`, `TSSHistoryRow`   |
| `ai/prompts.py`         | Done        | System prompt configurable via settings                                               |
| `ai/claude_agent.py`    | Done        | Morning + weekly analysis                                                             |
| `bot/main.py`           | Partial     | `whoami`, `howareyou`, `report` handlers; no /start /status /week /goal /zones /sync  |
| `bot/scheduler.py`      | Done        | Fetches 6 Garmin sources in parallel; recovery pipeline on wake-up; TODO: ESS/Banister, Claude AI |
| `bot/cli.py`            | Done        | echo, backfill, garmin-login, shell                                                   |
| `bot/formatter.py`      | Done        | Full morning report with recovery score header, HRV/RHR breakdown, components         |
| `api/routes.py`         | Skeleton    | Routes defined, CRUD available in database.py                                         |
| `webapp/`               | Scaffold    | HTML/CSS/JS files exist                                                               |
| `bot/handlers.py`       | Not started |                                                                                       |

---

## Environment Variables (.env)

```env
# Garmin
GARMIN_EMAIL=your@email.com
GARMIN_PASSWORD=yourpassword
GARMIN_TOKENS=~/.garminconnect    # path to OAuth token store

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
ATHLETE_RESTING_HR=42         # updated automatically from Garmin
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

# HRV Algorithm: "flatt_esco" (default) | "ai_endurance"
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

- CTL = 42-day EMA of TSS ("fitness"), ATL = 7-day EMA ("fatigue"), TSB = CTL - ATL ("form")
- TSB > +10: under-training | -10..+10: optimal | -10..-25: productive overreach | < -25: overtraining risk

### HRV Recovery — Dual Algorithm

Controlled by `settings.HRV_ALGORITHM`. Minimum 14 days of data required.

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
- `insufficient_data` (< 14 days) → fall back to Garmin readiness

**CV:** < 5% very stable, 5-10% normal, > 10% unreliable (stress/illness/travel)

### Resting HR Analysis

Inverted vs RMSSD: elevated RHR = under-recovered. 30-day window, ±0.5 SD bounds.

### ESS (External Stress Score)

Banister TRIMP-based, normalised so 1 hour at LTHR ≈ 100. Sport-agnostic.

### Banister Recovery Model

`R(t+1) = R(t) * exp(-1/τ) + k * ESS(t)` — defaults: k=0.1, τ=2.0 (conservative).
Re-calibrate every 4-6 weeks via `scipy.optimize.minimize` against actual RMSSD.

### Combined Recovery Score (0-100)

**Weights:**
- RMSSD status 35% | Banister R(t) 25% | RHR status 15% | Sleep 15% | Body Battery 10%

**Status → score:** green=100, yellow=65, red=20, insufficient_data=50

**Modifiers:** late sleep (>23:00) −10, CV>15% −5, RMSSD declining → flag only

**Categories:** excellent >85, good 70-85, moderate 40-70, low <40

**Recommendations:** excellent/good → zone2_ok, moderate → zone1_long, low → zone1_short, red RMSSD → skip (overrides)

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

Template structure (all data from `DailyMetricsRow`):

```
🌅 Доброе утро! {weekday}, {date}

━━━━━━━━━━━━━━━━━━━━
{emoji} {category_text}
Готовность: {score}/100
Рекомендация: {recommendation_text}
━━━━━━━━━━━━━━━━━━━━

🫀 HRV (RMSSD) — change from baseline, today/7d/60d values, SWC verdict, CV stability
💓 Пульс покоя — today vs 30d norm, deviation
😴 Сон — duration, sleep start time
⚡ Нагрузка — ESS yesterday, Banister R(t)
📊 Вклад в оценку — all 5 components × weights = contributions, modifiers, total
```

**Display mappings:**
- Categories: excellent→"ОТЛИЧНОЕ ВОССТАНОВЛЕНИЕ", good→"ГОТОВ К НАГРУЗКЕ", moderate→"УМЕРЕННАЯ НАГРУЗКА", low→"РЕКОМЕНДОВАН ОТДЫХ"
- Recommendations: zone2_ok→"тренировка Z2 — полный объём", zone1_long→"только аэробная база, Z1-Z2", zone1_short→"лёгкая активность, 30-45 мин", skip→"отдых — не тренироваться"

---

## Bot Commands (bot/handlers.py — NOT YET IMPLEMENTED)

```
/start    — welcome + quick guide
/report   — trigger manual report
/status   — quick numbers, no AI
/week     — weekly training summary
/goal     — goal progress breakdown
/zones    — current threshold zones
/settings — current settings
/sync     — manual Garmin sync
```

---

## API Endpoints (api/routes.py)

```
GET /api/dashboard      — Mini App main page data
GET /api/training-load  — CTL/ATL/TSB history
GET /api/activities     — recent activities
GET /api/goal           — goal progress
GET /api/weekly-summary — weekly breakdown by sport
GET /api/scheduled      — upcoming workouts
GET /health             — healthcheck
```

Security: Telegram `initData` HMAC via `Authorization` header.

---

## Webapp Dashboard (webapp/)

Tabs: Today (readiness gauge + metrics + workout + AI), Load (CTL/ATL/TSB + TSS charts), Goal (CTL progress bars + countdown), Week (planned vs actual table).
Telegram theme via `--tg-theme-*` CSS variables.

---

## CLI (bot/cli.py)

```bash
python -m bot.cli echo "message"
python -m bot.cli backfill                          # last 180 days
python -m bot.cli backfill 2025Q3                   # quarter
python -m bot.cli backfill 2025-03                  # month
python -m bot.cli backfill 2025-01-01:2025-03-31    # range
python -m bot.cli garmin-login                      # full credential login
python -m bot.cli shell                             # interactive Python shell
```

---

## Docker

```bash
docker compose up -d db          # PostgreSQL only
docker compose up -d             # all (db + migrate + bot)
docker compose exec bot python -m bot.cli backfill 2025Q4
```

---

## Key Implementation Notes

- **Garmin API is unofficial** — max 1 req/sec, 2h cooldown after 429, cache in PostgreSQL
- **OAuth tokens** in `~/.garminconnect` (via `GARMIN_TOKENS`), never in `.env`
- **Claude API** once per day (morning report) to minimize costs
- **All timestamps** UTC in DB, local timezone for display
- **HRV algorithm** never changes mid-season without re-baselining
- **Mini App** should degrade gracefully if API unreachable

---

## Next Steps (Priority Order)

1. **Implement `bot/handlers.py`** — /start /status /week /goal /zones /sync
2. **ESS/Banister pipeline** — sync activities → ESS per activity → Banister → persist
3. **Claude AI integration** — `analyze_morning()` in wake-up pipeline → persist `ai_recommendation`
4. **Wire up API endpoints** with real DB data
5. **Finalize Mini App dashboard** — recovery gauge, HRV band chart, Banister trend

---

## Contributing

- Follow existing module structure
- Add Pydantic models for new data types in `data/models.py`
- Write tests for all metric calculations (must be deterministic)
- Keep Claude API prompt modular — add sections to `prompts.py`
- Document new env vars in `.env.example`
