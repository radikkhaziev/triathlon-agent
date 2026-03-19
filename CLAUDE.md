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

| Component         | Technology                                 |
| ----------------- | ------------------------------------------ |
| Language          | Python 3.11+                               |
| Garmin Data       | `garminconnect` (cyberjunky)               |
| AI Analysis       | Anthropic Claude API (`claude-sonnet-4-6`) |
| Telegram Bot      | `python-telegram-bot` v21+                 |
| Scheduler         | `APScheduler`                              |
| Database          | SQLite + `SQLAlchemy`                      |
| API Server        | `FastAPI` + `uvicorn`                      |
| Mini App Frontend | HTML + Chart.js + Tailwind CSS             |
| Mini App Hosting  | Vercel or GitHub Pages                     |
| Backend Hosting   | VPS (Ubuntu) or Railway                    |
| Config            | `pydantic-settings` + `.env`               |

---

## Project Structure

```
triathlon-agent/
│
├── CLAUDE.md                    # ← this file
├── .env                         # secrets (never commit!)
├── .env.example                 # environment variable template
├── pyproject.toml
│
├── bot/
│   ├── __init__.py
│   ├── main.py                  # bot entry point
│   ├── handlers.py              # commands: /start /report /status
│   ├── scheduler.py             # morning job at 07:00
│   └── formatter.py             # Telegram message formatting
│
├── data/
│   ├── __init__.py
│   ├── garmin_client.py         # wrapper around garminconnect
│   ├── metrics.py               # CTL/ATL/TSB/hrTSS calculations
│   ├── database.py              # SQLAlchemy models and CRUD
│   └── models.py                # Pydantic data models
│
├── ai/
│   ├── __init__.py
│   ├── claude_agent.py          # Claude API — analysis and recommendations
│   └── prompts.py               # system prompts
│
├── api/
│   ├── __init__.py
│   ├── server.py                # FastAPI application
│   └── routes.py                # endpoints consumed by Mini App
│
├── webapp/                      # Telegram Mini App
│   ├── index.html               # main dashboard page
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js               # core logic
│       └── charts.js            # Chart.js visualizations
│
└── tests/
    ├── test_metrics.py
    └── test_garmin_client.py
```

---

## Environment Variables (.env)

```env
# Garmin
GARMIN_EMAIL=your@email.com
GARMIN_PASSWORD=yourpassword

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# App
API_BASE_URL=https://your-api.railway.app
WEBAPP_URL=https://your-app.vercel.app
DATABASE_URL=sqlite:///./triathlon.db

# Athlete Thresholds
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

Wrapper around `garminconnect`. Must implement:

```python
class GarminClient:
    def get_sleep(self, date: str) -> SleepData
    def get_hrv(self, date: str) -> HRVData
    def get_body_battery(self, start: str, end: str) -> list[BodyBatteryData]
    def get_stress(self, date: str) -> StressData
    def get_resting_hr(self, date: str) -> float
    def get_scheduled_workouts(self, start: str, end: str) -> list[ScheduledWorkout]
    def get_activities(self, start: int, limit: int) -> list[Activity]
    def get_training_readiness(self, date: str) -> TrainingReadinessData
    def get_training_status(self, date: str) -> TrainingStatusData
```

**Important:** Store OAuth tokens in `~/.garminconnect` — do not re-authenticate on every
request. Implement token refresh logic. Respect rate limits: no more than 1 request per second.

---

## Module: data/metrics.py

### TSS (Training Stress Score) by Sport

**Running (hrTSS — heart rate based):**

```python
def calc_hr_tss(duration_sec: float, avg_hr: float,
                resting_hr: float, max_hr: float, lthr: float) -> float:
    """
    Heart Rate TSS calculation.
    Uses the ratio of average HR to lactate threshold HR
    to estimate training stress similar to power-based TSS.
    """
    intensity_factor = (avg_hr - resting_hr) / (lthr - resting_hr)
    tss = (duration_sec / 3600) * intensity_factor ** 2 * 100
    return round(tss, 1)
```

**Cycling (power-based TSS):**

```python
def calc_power_tss(duration_sec: float, normalized_power: float, ftp: float) -> float:
    """
    Standard TSS formula used by TrainingPeaks.
    Requires a power meter on the bike.
    Falls back to hrTSS if power data is unavailable.
    """
    intensity_factor = normalized_power / ftp
    tss = (duration_sec * normalized_power * intensity_factor) / (ftp * 3600) * 100
    return round(tss, 1)
```

**Swimming (ssTSS — swim-specific):**

```python
def calc_swim_tss(distance_m: float, duration_sec: float, css_per_100m: float) -> float:
    """
    Swim-Specific TSS based on Critical Swim Speed (CSS).
    CSS is the anaerobic threshold pace for swimming (sec per 100m).
    Faster than CSS = above threshold.
    """
    if distance_m == 0:
        return 0.0
    pace_per_100m = (duration_sec / distance_m) * 100
    intensity_factor = css_per_100m / pace_per_100m
    tss = (duration_sec / 3600) * intensity_factor ** 2 * 100
    return round(tss, 1)
```

### CTL / ATL / TSB Calculation

```python
def update_ctl_atl(tss_history: list[float],
                   ctl_days: int = 42,
                   atl_days: int = 7) -> tuple[float, float, float]:
    """
    Fitness / Fatigue / Form model (Performance Manager Chart).

    CTL (Chronic Training Load)   = 42-day EMA of TSS -> "fitness"
    ATL (Acute Training Load)     = 7-day EMA of TSS  -> "fatigue"
    TSB (Training Stress Balance) = CTL - ATL         -> "form"

    TSB Interpretation:
        TSB > +10     -> under-training, fitness declining
        TSB -10..+10  -> optimal zone, good form
        TSB -10..-25  -> productive overreach
        TSB < -25     -> overtraining risk, injury/illness risk
    """
    ctl_k = 2 / (ctl_days + 1)
    atl_k = 2 / (atl_days + 1)

    ctl, atl = 0.0, 0.0
    for tss in tss_history:
        ctl = tss * ctl_k + ctl * (1 - ctl_k)
        atl = tss * atl_k + atl * (1 - atl_k)

    tsb = ctl - atl
    return round(ctl, 1), round(atl, 1), round(tsb, 1)
```

### Heart Rate Zones (% of LTHR)

```python
HR_ZONES = {
    "run": {
        1: (0.00, 0.72),    # Recovery
        2: (0.72, 0.82),    # Aerobic base
        3: (0.82, 0.87),    # Tempo
        4: (0.87, 0.92),    # Sub-threshold
        5: (0.92, 1.00),    # VO2max
    },
    "bike": {
        1: (0.00, 0.68),
        2: (0.68, 0.83),
        3: (0.83, 0.94),
        4: (0.94, 1.05),
        5: (1.05, 1.20),
    }
}
```

---

## Module: data/models.py

```python
from pydantic import BaseModel
from datetime import date, datetime
from enum import Enum

class SportType(str, Enum):
    SWIM = "swimming"
    BIKE = "cycling"
    RUN = "running"
    STRENGTH = "strength_training"
    OTHER = "other"

class ReadinessLevel(str, Enum):
    GREEN = "green"    # score >= 80 -> train as planned
    YELLOW = "yellow"  # score 60-79 -> reduce intensity
    RED = "red"        # score < 60  -> rest or easy only

class SleepData(BaseModel):
    date: date
    sleep_score: int               # 0-100
    duration_seconds: int
    deep_sleep_seconds: int
    rem_sleep_seconds: int
    awake_seconds: int
    avg_overnight_hrv: float | None
    avg_stress: float | None

class HRVData(BaseModel):
    date: date
    hrv_weekly_avg: float          # 7-day baseline
    hrv_last_night: float          # last night's HRV
    hrv_5min_high: float | None
    status: str                    # "Balanced" | "Low" | "Unbalanced"

class Activity(BaseModel):
    activity_id: int
    sport: SportType
    start_time: datetime
    duration_seconds: int
    distance_meters: float | None
    avg_hr: float | None
    max_hr: float | None
    avg_power: float | None
    normalized_power: float | None
    tss: float | None              # calculated by metrics.py

class ScheduledWorkout(BaseModel):
    scheduled_date: date
    workout_name: str
    sport: SportType
    description: str | None
    planned_duration_seconds: int | None
    planned_tss: float | None

class DailyMetrics(BaseModel):
    date: date
    readiness_score: int           # 0-100, composite calculation
    readiness_level: ReadinessLevel
    hrv_delta_pct: float           # % deviation from 7-day baseline
    sleep_score: int
    body_battery_morning: int
    resting_hr: float
    ctl: float
    atl: float
    tsb: float
    ctl_swim: float
    ctl_bike: float
    ctl_run: float

class GoalProgress(BaseModel):
    event_name: str
    event_date: date
    weeks_remaining: int
    overall_pct: float             # weighted average of 3 sports
    swim_pct: float                # ctl_swim / GOAL_SWIM_CTL_TARGET * 100
    bike_pct: float
    run_pct: float
    on_track: bool
```

---

## Module: ai/prompts.py

### System Prompt

```python
SYSTEM_PROMPT = """
You are a personal AI triathlon coach. Your role is to analyze an athlete's
physiological data and provide specific, actionable training recommendations.

Athlete profile:
- Experienced triathlete, age 43
- Target race: Ironman 70.3
- Uses Garmin device for all monitoring

Response rules:
1. Be specific — mention numbers, zones, durations
2. Always consider training load history when making recommendations
3. If HRV is more than 15% below baseline -> recommend reducing intensity
4. If TSB < -25 -> recommend a rest or recovery day
5. Keep recommendations under 250 words
6. Use emoji sparingly for readability
7. Respond in the same language the prompt is written in
"""

MORNING_REPORT_PROMPT = """
Analyze today's training readiness and provide recommendations.

Date: {date}

LAST NIGHT SLEEP:
- Sleep score: {sleep_score}/100
- Duration: {sleep_duration}
- Last night HRV: {hrv_last} (7-day baseline: {hrv_baseline}, delta: {hrv_delta:+.0f}%)
- Resting HR: {resting_hr} bpm (baseline: {resting_hr_baseline} bpm)
- Body Battery (morning): {body_battery}/100
- Yesterday stress score: {stress_score}/100

TRAINING LOAD:
- CTL (fitness): {ctl:.1f}
- ATL (fatigue): {atl:.1f}
- TSB (form): {tsb:+.1f}
- Swimming CTL: {ctl_swim:.1f}
- Cycling CTL: {ctl_bike:.1f}
- Running CTL: {ctl_run:.1f}

TODAY'S PLAN (from Garmin/HumanGO calendar):
{workout_today}

RACE GOAL ({goal_event}, {weeks_remaining} weeks away):
- Overall readiness: {goal_pct:.0f}%
- Swim: {swim_pct:.0f}% | Bike: {bike_pct:.0f}% | Run: {run_pct:.0f}%

Please provide:
1. Readiness assessment (Green / Yellow / Red) with brief reasoning
2. Specific workout recommendation for today (adjust planned workout if needed)
3. One observation about the current training load trend
4. One short note on goal progression
"""
```

---

## Module: bot/formatter.py

```python
def format_morning_message(metrics: DailyMetrics,
                            workout: ScheduledWorkout | None,
                            goal: GoalProgress,
                            ai_text: str) -> str:

    level_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    level = level_emoji[metrics.readiness_level]

    hrv_arrow = "↓" if metrics.hrv_delta_pct < -5 else "↑" if metrics.hrv_delta_pct > 5 else "→"

    sport_emoji = {
        "swimming": "🏊", "cycling": "🚴",
        "running": "🏃", "strength_training": "💪", "other": "🏋"
    }

    workout_text = "Rest day / no workout scheduled"
    if workout:
        emoji = sport_emoji.get(workout.sport, "🏋")
        workout_text = f"{emoji} {workout.workout_name}"

    def progress_bar(pct: float, width: int = 8) -> str:
        filled = int((pct / 100) * width)
        return "█" * filled + "░" * (width - filled)

    return f"""
🌅 *Good morning! Report for {metrics.date.strftime('%B %d, %Y')}*

━━━ READINESS ━━━
{level} *{metrics.readiness_score}/100*

HRV `{metrics.hrv_delta_pct:+.0f}%` {hrv_arrow}  Sleep `{metrics.sleep_score}/100`
Battery `{metrics.body_battery_morning}/100`  RHR `{metrics.resting_hr:.0f} bpm`

━━━ TODAY'S PLAN ━━━
{workout_text}

━━━ TRAINING LOAD ━━━
CTL `{metrics.ctl:.0f}` · ATL `{metrics.atl:.0f}` · TSB `{metrics.tsb:+.0f}`

━━━ GOAL: {goal.event_name} ({goal.weeks_remaining} weeks) ━━━
🏊 `{progress_bar(goal.swim_pct)}` {goal.swim_pct:.0f}%
🚴 `{progress_bar(goal.bike_pct)}` {goal.bike_pct:.0f}%
🏃 `{progress_bar(goal.run_pct)}` {goal.run_pct:.0f}%

━━━ AI RECOMMENDATION ━━━
{ai_text}
"""
```

---

## Module: bot/handlers.py

Bot commands to implement:

```
/start      — welcome message + quick guide
/report     — trigger a manual report right now
/status     — quick status (numbers only, no AI call)
/week       — weekly training summary
/goal       — detailed goal progress breakdown
/zones      — show current threshold zones
/settings   — configure report time, goal, zones
/sync       — manually trigger Garmin data sync
```

Button layout under every morning report:

```python
keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("📊 Open Dashboard", web_app=WebAppInfo(url=WEBAPP_URL))],
    [
        InlineKeyboardButton("📅 Week Plan", callback_data="week_plan"),
        InlineKeyboardButton("📈 Load Chart", callback_data="load_chart"),
    ]
])
```

---

## Module: api/routes.py (FastAPI)

```
GET  /api/dashboard          -> data for Mini App main page
GET  /api/training-load      -> CTL/ATL/TSB history for N days
GET  /api/activities         -> recent activities list
GET  /api/goal               -> goal progress details
GET  /api/weekly-summary     -> weekly breakdown by sport
GET  /api/scheduled          -> upcoming scheduled workouts
GET  /health                 -> healthcheck endpoint
```

**Security:** Validate Telegram `initData` HMAC signature on every request.

```python
import hmac, hashlib

def verify_telegram_init_data(init_data: str, bot_token: str) -> bool:
    """
    Verify that the request comes from a legitimate Telegram Mini App session.
    Docs: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    ...
```

---

## Webapp: index.html — Dashboard Structure

```
Tabs: [Today] [Load] [Goal] [Week]

Tab "Today":
  - Circular gauge: readiness score (0-100)
  - 4 metric cards: HRV delta | Sleep score | Body Battery | RHR
  - Today's workout block (name + description from Garmin calendar)
  - AI recommendation text

Tab "Load":
  - Line chart: CTL / ATL / TSB over 12 weeks (3 lines, color-coded)
  - Bar chart: daily TSS for last 4 weeks (color by sport)

Tab "Goal":
  - Progress bars: swim / bike / run (% of target CTL)
  - Countdown: weeks to race day
  - CTL by sport trend chart

Tab "Week":
  - Table: current week workouts (planned vs actual)
  - TSS summary: planned vs completed
  - Weekly volume by sport (km / hours)
```

Telegram theme integration (automatic dark/light mode):

```css
:root {
  --bg: var(--tg-theme-bg-color, #ffffff);
  --text: var(--tg-theme-text-color, #000000);
  --hint: var(--tg-theme-hint-color, #999999);
  --button: var(--tg-theme-button-color, #2481cc);
  --button-text: var(--tg-theme-button-text-color, #ffffff);
  --secondary-bg: var(--tg-theme-secondary-bg-color, #f0f0f0);
}
```

Required Telegram SDK (must be first script in `<head>`):

```html
<script src="https://telegram.org/js/telegram-web-app.js"></script>
```

---

## Readiness Score Calculation

Composite score weighted from 4 physiological signals:

```python
def calculate_readiness(hrv: HRVData,
                         sleep: SleepData,
                         body_battery: int,
                         resting_hr: float,
                         resting_hr_baseline: float) -> tuple[int, ReadinessLevel]:
    score = 100

    # HRV component (weight: 35%)
    hrv_delta = (hrv.hrv_last_night - hrv.hrv_weekly_avg) / hrv.hrv_weekly_avg
    if hrv_delta < -0.20:    score -= 35
    elif hrv_delta < -0.10:  score -= 20
    elif hrv_delta < -0.05:  score -= 10
    elif hrv_delta > +0.10:  score += 5   # bonus for good recovery

    # Sleep component (weight: 30%)
    if sleep.sleep_score < 50:    score -= 30
    elif sleep.sleep_score < 65:  score -= 15
    elif sleep.sleep_score < 75:  score -= 7

    # Body Battery component (weight: 20%)
    if body_battery < 30:    score -= 20
    elif body_battery < 50:  score -= 10
    elif body_battery < 65:  score -= 5

    # Resting HR component (weight: 15%)
    hr_delta = resting_hr - resting_hr_baseline
    if hr_delta > 7:    score -= 15
    elif hr_delta > 4:  score -= 8
    elif hr_delta > 2:  score -= 3

    score = max(0, min(100, score))

    if score >= 80:    level = ReadinessLevel.GREEN
    elif score >= 60:  level = ReadinessLevel.YELLOW
    else:              level = ReadinessLevel.RED

    return score, level
```

---

## Database Schema (SQLite)

```sql
CREATE TABLE daily_metrics (
    date             TEXT PRIMARY KEY,
    sleep_score      INTEGER,
    sleep_duration   INTEGER,
    hrv_last         REAL,
    hrv_baseline     REAL,
    body_battery     INTEGER,
    resting_hr       REAL,
    stress_score     INTEGER,
    readiness_score  INTEGER,
    readiness_level  TEXT,
    ctl              REAL,
    atl              REAL,
    tsb              REAL,
    ctl_swim         REAL,
    ctl_bike         REAL,
    ctl_run          REAL,
    ai_recommendation TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE activities (
    activity_id   INTEGER PRIMARY KEY,
    date          TEXT,
    sport         TEXT,
    duration_sec  INTEGER,
    distance_m    REAL,
    avg_hr        REAL,
    max_hr        REAL,
    avg_power     REAL,
    norm_power    REAL,
    tss           REAL,
    synced_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE scheduled_workouts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scheduled_date TEXT,
    sport          TEXT,
    workout_name   TEXT,
    description    TEXT,
    planned_tss    REAL,
    source         TEXT DEFAULT 'garmin'
);

CREATE TABLE tss_history (
    date  TEXT,
    sport TEXT,
    tss   REAL,
    PRIMARY KEY (date, sport)
);
```

---

## Scheduler Setup

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os

scheduler = AsyncIOScheduler(timezone=os.getenv("TIMEZONE", "UTC"))

# Morning report
scheduler.add_job(
    morning_report_job,
    trigger="cron",
    hour=int(os.getenv("MORNING_REPORT_HOUR", 7)),
    minute=int(os.getenv("MORNING_REPORT_MINUTE", 0)),
    id="morning_report",
    replace_existing=True,
)

# Garmin data sync (runs 30 min before report to ensure fresh data)
scheduler.add_job(
    garmin_sync_job,
    trigger="cron",
    hour=int(os.getenv("MORNING_REPORT_HOUR", 7)),
    minute=max(0, int(os.getenv("MORNING_REPORT_MINUTE", 0)) - 30),
    id="garmin_sync",
    replace_existing=True,
)
```

---

## requirements.txt

```
# Garmin
garminconnect>=0.2.38

# AI
anthropic>=0.40.0

# Telegram
python-telegram-bot>=21.0

# Scheduling
apscheduler>=3.10.0

# API
fastapi>=0.115.0
uvicorn>=0.30.0

# Database
sqlalchemy>=2.0.0

# Config & validation
pydantic>=2.7.0
pydantic-settings>=2.3.0

# HTTP
httpx>=0.27.0

# Utils
python-dotenv>=1.0.0
```

---

## Recommended Development Order

Build in this sequence to enable testing at each step:

1. `data/models.py` — Pydantic models (no dependencies)
2. `data/database.py` — SQLite + SQLAlchemy setup
3. `data/metrics.py` — TSS/CTL/ATL/TSB calculations + unit tests
4. `data/garmin_client.py` — Garmin connection, test data fetch
5. `ai/prompts.py` + `ai/claude_agent.py` — Claude API integration
6. `bot/formatter.py` — message formatting
7. `bot/handlers.py` + `bot/main.py` — basic working bot
8. `bot/scheduler.py` — automated morning job
9. `api/server.py` + `api/routes.py` — FastAPI data endpoints
10. `webapp/index.html` — Mini App dashboard with Chart.js
11. Deploy — backend to Railway/VPS, frontend to Vercel

---

## How to Use with Claude Code

```bash
# Install Claude Code (if not installed)
npm install -g @anthropic/claude-code

# Navigate to project folder
cd triathlon-agent

# Start Claude Code — it will automatically read CLAUDE.md
claude
```

### Example prompts inside Claude Code

```
# Bootstrap the project
> Create the full project structure as defined in CLAUDE.md

# Build individual modules
> Implement data/models.py with all Pydantic models from the spec
> Implement data/metrics.py with TSS, CTL/ATL/TSB formulas and full test coverage
> Implement data/garmin_client.py with token caching and all required methods
> Implement ai/claude_agent.py that calls Claude API with the morning report prompt
> Build the FastAPI server with all endpoints from the spec
> Create the Telegram Mini App dashboard in webapp/index.html using Chart.js
> Write a Dockerfile and docker-compose.yml for local development
> Generate a README.md with setup and contribution guide
```

### Useful Claude Code flags

```bash
claude                               # interactive mode (recommended)
claude "implement data/metrics.py"   # single task, then exit
claude --continue                    # resume previous session
claude --model claude-opus-4-6       # use Opus for complex architecture tasks
```

---

## Key Implementation Notes

- **Garmin API is unofficial** — max 1 request/second, cache everything in SQLite
- **OAuth tokens** live in `~/.garminconnect`, never in `.env`
- **Claude API** is called once per day (morning report) to minimize token costs
- **Mini App** should gracefully degrade if API is unreachable (show cached data)
- **All timestamps** stored as UTC in DB, converted to local timezone for display
- **Garmin data sync** runs 30 minutes before the morning report
- When deploying, ensure `.env.example` is complete and `.env` is in `.gitignore`

---

## Contributing

This project is open source. When adding features:

- Follow the existing module structure
- Add Pydantic models for any new data types
- Write tests for all metric calculations (they must be deterministic)
- Keep the Claude API prompt modular — add new sections to `prompts.py`
- Document any new environment variables in `.env.example`
- The athlete profile in `SYSTEM_PROMPT` should be configurable via `.env`, not hardcoded

---

_Last updated: February 2026_
