# Triathlon AI Agent — Project Specification for Claude Code

> This file is the primary instruction set for Claude Code.
> Read this before taking any action. It contains architecture, stack, structure, and business logic.

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

| Component         | Technology                                    |
| ----------------- | --------------------------------------------- |
| Language          | Python 3.12+                                  |
| Package Manager   | Poetry                                        |
| Garmin Data       | `garminconnect` (cyberjunky)                  |
| AI Analysis       | Anthropic Claude API (`claude-sonnet-4-6`)    |
| Telegram Bot      | `python-telegram-bot` v21+                    |
| Scheduler         | `APScheduler`                                 |
| Database          | PostgreSQL 16 + `SQLAlchemy` (async) + Alembic|
| API Server        | `FastAPI` + `uvicorn`                         |
| Mini App Frontend | HTML + Chart.js + Tailwind CSS                |
| Backend Hosting   | Docker Compose on VPS                         |
| Config            | `pydantic-settings` + `.env`                  |

---

## Project Structure

```
triathlon-agent/
│
├── CLAUDE.md                    # ← this file
├── .env                         # secrets (never commit!)
├── .env.example                 # environment variable template
├── pyproject.toml               # Poetry dependencies and tools config
├── poetry.lock
├── Dockerfile
├── docker-compose.yml           # db + migrate + bot services
├── alembic.ini                  # Alembic migration config
├── config.py                    # pydantic-settings (centralized config)
│
├── bot/
│   ├── __init__.py
│   ├── main.py                  # bot entry point (polling + scheduler init)
│   ├── cli.py                   # CLI: echo, backfill, garmin-login, shell
│   └── scheduler.py             # periodic jobs (daily_metrics_job every 15 min)
│
├── data/
│   ├── __init__.py
│   ├── garmin_client.py         # wrapper around garminconnect (singleton)
│   ├── metrics.py               # CTL/ATL/TSB/hrTSS calculations + readiness
│   ├── database.py              # SQLAlchemy async ORM models and CRUD
│   └── models.py                # Pydantic data models (20+ models)
│
├── ai/
│   ├── __init__.py
│   ├── claude_agent.py          # Claude API — morning + weekly analysis
│   └── prompts.py               # system + report prompts (configurable)
│
├── api/
│   ├── __init__.py
│   ├── server.py                # FastAPI application + static mount
│   └── routes.py                # REST endpoints + Telegram initData auth
│
├── webapp/                      # Telegram Mini App
│   ├── index.html
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js
│       └── charts.js
│
├── migrations/                  # Alembic migrations
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 001_initial_schema.py
│
├── docs/                        # Design documents
│   ├── HRV_IMPLEMENTATION_PLAN.md
│   └── HRV_MODULE_SPEC.md
│
├── mockups/                     # Dashboard UI mockups (HTML)
│
└── tests/
    ├── conftest.py
    ├── test_metrics.py
    ├── test_garmin_client.py
    ├── test_garmin_login.py
    └── test_database.py
```

---

## Current Implementation Status

| Module | Status | Notes |
|--------|--------|-------|
| `data/models.py` | Done | 20+ Pydantic models |
| `data/garmin_client.py` | Done | 16+ methods, singleton, retry, cooldown |
| `data/metrics.py` | Done | TSS (3 sports), CTL/ATL/TSB, readiness score |
| `data/database.py` | Partial | Only `DailyMetricsRow` with sleep fields; CTL/ATL/TSB columns commented out; CRUD for activities/workouts/tss_history not implemented |
| `ai/prompts.py` | Done | System prompt configurable via settings |
| `ai/claude_agent.py` | Done | Morning + weekly analysis |
| `bot/main.py` | Partial | Only `whoami` handler; no /start /report /status etc. |
| `bot/scheduler.py` | Partial | Polls sleep every 15 min; no full morning report pipeline |
| `bot/cli.py` | Done | echo, backfill, garmin-login, shell |
| `api/routes.py` | Skeleton | Routes defined but depend on missing CRUD functions in database.py |
| `webapp/` | Scaffold | HTML/CSS/JS files exist |
| `bot/formatter.py` | Not started | |
| `bot/handlers.py` | Not started | |

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
```

---

## Module: data/garmin_client.py

Singleton wrapper around `garminconnect` with retry logic, rate limiting (1 req/sec),
and 2-hour cooldown after 429 errors.

Implemented methods:

```python
class GarminClient:
    # Core data
    def get_sleep(self, date: str) -> SleepData
    def get_hrv(self, date: str) -> HRVData
    def get_body_battery(self, start: str, end: str) -> list[BodyBatteryData]
    def get_stress(self, date: str) -> StressData
    def get_resting_hr(self, date: str) -> float
    def get_scheduled_workouts(self, start: str, end: str) -> list[ScheduledWorkout]
    def get_activities(self, start: int, limit: int) -> list[Activity]
    def get_activities_by_date(self, start: str, end: str) -> list[Activity]
    def get_training_readiness(self, date: str) -> TrainingReadinessData
    def get_training_status(self, date: str) -> TrainingStatusData

    # Extended data
    def get_heart_rates(self, date: str) -> HeartRateData
    def get_stats(self, date: str) -> DailyStats
    def get_body_composition(self, start: str, end: str) -> list[BodyCompositionData]
    def get_respiration(self, date: str) -> RespirationData
    def get_spo2(self, date: str) -> SpO2Data
    def get_max_metrics(self, date: str) -> MaxMetricsData
    def get_race_predictions(self) -> list[RacePrediction]
    def get_endurance_score(self, date: str) -> EnduranceScoreData
    def get_lactate_threshold(self) -> LactateThresholdData
    def get_cycling_ftp(self) -> CyclingFTPData
```

**Important:** OAuth tokens stored in `~/.garminconnect` (configurable via `GARMIN_TOKENS`).
Soft login on init (loads tokens without refresh). Full login fallback on auth failure.

---

## Module: data/metrics.py

### TSS (Training Stress Score) by Sport

**Running (hrTSS — heart rate based):**

```python
def calc_hr_tss(duration_sec, avg_hr, resting_hr, max_hr, lthr) -> float:
    intensity_factor = (avg_hr - resting_hr) / (lthr - resting_hr)
    tss = (duration_sec / 3600) * intensity_factor ** 2 * 100
```

**Cycling (power-based TSS):**

```python
def calc_power_tss(duration_sec, normalized_power, ftp) -> float:
    intensity_factor = normalized_power / ftp
    tss = (duration_sec * normalized_power * intensity_factor) / (ftp * 3600) * 100
```

**Swimming (ssTSS — swim-specific):**

```python
def calc_swim_tss(distance_m, duration_sec, css_per_100m) -> float:
    pace_per_100m = (duration_sec / distance_m) * 100
    intensity_factor = css_per_100m / pace_per_100m
    tss = (duration_sec / 3600) * intensity_factor ** 2 * 100
```

### CTL / ATL / TSB Calculation

```python
def update_ctl_atl(tss_history: list[float], ctl_days=42, atl_days=7) -> (CTL, ATL, TSB):
    # EMA-based fitness/fatigue/form model
    # TSB > +10     -> under-training
    # TSB -10..+10  -> optimal zone
    # TSB -10..-25  -> productive overreach
    # TSB < -25     -> overtraining risk
```

### Readiness Score

```python
def calculate_readiness(hrv, sleep, body_battery, resting_hr, resting_hr_baseline) -> (score, level):
    # Composite 0-100 score from:
    # - HRV delta from baseline (35%)
    # - Sleep score (30%)
    # - Body Battery (20%)
    # - Resting HR deviation (15%)
    # Returns ReadinessLevel: GREEN (>=80) / YELLOW (>=60) / RED (<60)
```

### Heart Rate Zones (% of LTHR)

```python
HR_ZONES = {
    "run": {1: (0.00, 0.72), 2: (0.72, 0.82), 3: (0.82, 0.87), 4: (0.87, 0.92), 5: (0.92, 1.00)},
    "bike": {1: (0.00, 0.68), 2: (0.68, 0.83), 3: (0.83, 0.94), 4: (0.94, 1.05), 5: (1.05, 1.20)},
}
```

---

## Module: data/models.py

Core models (all Pydantic BaseModel):

```
SportType          — SWIM, BIKE, RUN, STRENGTH, OTHER
ReadinessLevel     — GREEN, YELLOW, RED

SleepData          — date, score, duration, start, end, stress_avg, hrv_avg, heart_rate_avg
HRVData            — date, hrv_weekly_avg, hrv_last_night, hrv_5min_high, status
BodyBatteryData    — date, start_value, end_value, charged, drained
StressData         — date, avg_stress, max_stress, stress_duration_seconds, rest_duration_seconds
TrainingReadinessData — date, score, level, hrv_status, sleep_score, recovery_time_hours
TrainingStatusData — date, training_status, vo2_max_run, vo2_max_bike, load_focus
Activity           — activity_id, sport, start_time, duration_seconds, distance_meters, avg_hr, max_hr, avg_power, normalized_power, tss
ScheduledWorkout   — scheduled_date, workout_name, sport, description, planned_duration_seconds, planned_tss
HeartRateData      — date, resting_hr, max_hr, min_hr, avg_hr
DailyStats         — date, total_steps, total_distance_meters, active_calories, total_calories, intensity_minutes, floors_climbed
BodyCompositionData— date, weight_kg, bmi, body_fat_pct, muscle_mass_kg, bone_mass_kg, body_water_pct
RespirationData    — date, avg_breathing_rate, lowest_breathing_rate, highest_breathing_rate
SpO2Data           — date, avg_spo2, lowest_spo2
MaxMetricsData     — date, vo2_max_run, vo2_max_bike
RacePrediction     — distance_name, predicted_time_seconds
EnduranceScoreData — date, overall_score, rating
LactateThresholdData — heart_rate, speed
CyclingFTPData     — ftp, ftp_date
DailyMetrics       — date, readiness_score, readiness_level, hrv_delta_pct, sleep_score, body_battery_morning, resting_hr, ctl/atl/tsb, ctl_swim/bike/run
GoalProgress       — event_name, event_date, weeks_remaining, overall_pct, swim/bike/run_pct, on_track
```

---

## Module: ai/prompts.py

System prompt is configurable via `settings.ATHLETE_AGE` and `settings.GOAL_EVENT_NAME`.
`get_system_prompt()` renders the template at call time.

Morning report prompt collects: sleep, HRV, body battery, stress, training load (CTL/ATL/TSB by sport), today's workout plan, and goal progress.

---

## Module: bot/formatter.py (NOT YET IMPLEMENTED)

Should format the morning report Telegram message with readiness gauge, metrics cards,
workout plan, training load, goal progress bars, and AI recommendation text.

---

## Module: bot/handlers.py (NOT YET IMPLEMENTED)

Bot commands to implement:

```
/start      — welcome message + quick guide
/report     — trigger a manual report right now
/status     — quick status (numbers only, no AI call)
/week       — weekly training summary
/goal       — detailed goal progress breakdown
/zones      — show current threshold zones
/settings   — current settings display
/sync       — manually trigger Garmin data sync
```

Currently only `whoami` text handler exists in `bot/main.py`.

---

## Module: api/routes.py (FastAPI)

Defined endpoints (require CRUD functions in database.py to be implemented):

```
GET  /api/dashboard          -> data for Mini App main page
GET  /api/training-load      -> CTL/ATL/TSB history for N days
GET  /api/activities         -> recent activities list
GET  /api/goal               -> goal progress details
GET  /api/weekly-summary     -> weekly breakdown by sport
GET  /api/scheduled          -> upcoming scheduled workouts
GET  /health                 -> healthcheck endpoint
```

**Security:** Validates Telegram `initData` HMAC signature via `Authorization` header.

---

## Webapp: index.html — Dashboard Structure

```
Tabs: [Today] [Load] [Goal] [Week]

Tab "Today":
  - Circular gauge: readiness score (0-100)
  - 4 metric cards: HRV delta | Sleep score | Body Battery | RHR
  - Today's workout block
  - AI recommendation text

Tab "Load":
  - Line chart: CTL / ATL / TSB over 12 weeks
  - Bar chart: daily TSS for last 4 weeks (color by sport)

Tab "Goal":
  - Progress bars: swim / bike / run (% of target CTL)
  - Countdown: weeks to race day

Tab "Week":
  - Table: current week workouts (planned vs actual)
  - TSS summary: planned vs completed
```

Telegram theme integration via CSS variables (`--tg-theme-*`).

Required Telegram SDK:

```html
<script src="https://telegram.org/js/telegram-web-app.js"></script>
```

---

## Database (PostgreSQL + Alembic)

Current ORM model in `data/database.py`:

```python
class DailyMetricsRow(Base):
    __tablename__ = "daily_metrics"
    date: str (PK)
    created_at: datetime
    sleep_score, sleep_duration, sleep_start, sleep_end
    sleep_stress_avg, sleep_hrv_avg, sleep_heart_rate_avg
    # TODO: hrv_last, hrv_baseline, body_battery, resting_hr, stress_score
    # TODO: readiness_score, readiness_level
    # TODO: ctl, atl, tsb, ctl_swim, ctl_bike, ctl_run
    # TODO: ai_recommendation
```

Tables still needed as ORM models:
- `activities` — synced Garmin activities with TSS
- `scheduled_workouts` — planned workouts from Garmin calendar
- `tss_history` — daily TSS by sport for CTL/ATL calculation

Migrations managed via Alembic (`alembic upgrade head`).

---

## Scheduler (bot/scheduler.py)

Current implementation:
- `daily_metrics_job` runs every 15 minutes from 5:00 to 20:00
- Fetches sleep data from Garmin and saves to DB
- Sends "Пробуждение зафиксировано" when new sleep_end matches today

Planned:
- Full morning report pipeline (sync all data -> calculate metrics -> AI analysis -> send report)
- Configurable report time via `MORNING_REPORT_HOUR` / `MORNING_REPORT_MINUTE`

---

## CLI (bot/cli.py)

```bash
python -m bot.cli echo "message"           # send Telegram message
python -m bot.cli backfill                  # backfill last 180 days
python -m bot.cli backfill 2025Q3           # backfill quarter
python -m bot.cli backfill 2025-03          # backfill month
python -m bot.cli backfill 2025-01-01:2025-03-31  # backfill range
python -m bot.cli backfill 2025-09-01       # backfill single day
python -m bot.cli garmin-login              # full Garmin credential login
python -m bot.cli shell                     # interactive Python shell
```

---

## Docker

```bash
docker compose up -d db          # start PostgreSQL only
docker compose up -d             # start all (db + migrate + bot)
docker compose exec bot python -m bot.cli backfill 2025Q4
```

API service is defined but commented out in `docker-compose.yml`.

---

## Key Implementation Notes

- **Garmin API is unofficial** — max 1 request/second, 2h cooldown after 429, cache in PostgreSQL
- **OAuth tokens** live in `~/.garminconnect` (configurable via `GARMIN_TOKENS`), never in `.env`
- **Claude API** called once per day (morning report) to minimize token costs
- **Mini App** should gracefully degrade if API is unreachable (show cached data)
- **All timestamps** stored as UTC in DB, converted to local timezone for display
- When deploying, ensure `.env.example` is complete and `.env` is in `.gitignore`

---

## Next Steps (Priority Order)

1. Expand `DailyMetricsRow` — uncomment and add missing columns + Alembic migration
2. Add `ActivityRow`, `ScheduledWorkoutRow`, `TSSHistoryRow` ORM models + migration
3. Implement CRUD functions in `database.py` (needed by `api/routes.py`)
4. Build full morning report pipeline in `scheduler.py` (sync all data -> metrics -> AI -> Telegram)
5. Implement `bot/formatter.py` — Telegram message formatting
6. Implement `bot/handlers.py` — /start /report /status /week /goal /zones /sync commands
7. Wire up API endpoints with real data
8. Finalize Mini App dashboard

---

## Contributing

When adding features:

- Follow the existing module structure
- Add Pydantic models for any new data types in `data/models.py`
- Write tests for all metric calculations (they must be deterministic)
- Keep the Claude API prompt modular — add new sections to `prompts.py`
- Document any new environment variables in `.env.example`
- Create Alembic migrations for any schema changes: `alembic revision --autogenerate -m "description"`
- The athlete profile in `SYSTEM_PROMPT` is configurable via `.env` (ATHLETE_AGE, GOAL_EVENT_NAME)

---

_Last updated: March 2026_
