# Triathlon AI Agent

Personal AI agent for triathlon training. Syncs data from Intervals.icu, runs dual-algorithm HRV analysis, calculates training load, processes post-activity DFA alpha 1, and sends AI-powered recommendations via Telegram.

## Features

- **Morning Reports** — automated daily analysis of sleep, HRV, and training readiness with AI recommendation
- **Dual HRV Analysis** — Flatt & Esco (acute) + AIEndurance (chronic) algorithms, both always computed
- **RHR Baseline Tracking** — 7d/30d/60d resting heart rate baselines with trend analysis
- **DFA Alpha 1** — post-activity HRV analysis: HRVT1/HRVT2 thresholds, Readiness (Ra), Durability (Da) from FIT files
- **Training Load Tracking** — CTL/ATL/TSB from Intervals.icu, per-sport CTL for swim/bike/run
- **AI Recommendations** — Claude AI daily analysis with workout suggestions; optional Gemini second opinion
- **ESS/Banister Recovery Model** — stress score + recovery model calibrated against HRV
- **Evening Digest** — daily summary at 21:00 with activities, DFA analysis, tomorrow's plan
- **Telegram Bot** — `/morning` command with Mini App
- **Web Dashboard** — morning report, training plan, activities (dark theme, mobile-first)
- **Goal Tracking** — progress toward target race (e.g., Ironman 70.3)
- **MCP Server** — 12 tools + 3 resources for Claude Desktop integration

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/your-username/triathlon-agent.git
cd triathlon-agent
cp .env.example .env
# Edit .env with your credentials
```

### 2. Install dependencies

```bash
poetry install
```

### 3. Start PostgreSQL

```bash
docker compose up -d db
```

### 4. Run migrations

```bash
poetry run alembic upgrade head
```

### 5. Run the bot (polling mode, local dev)

```bash
python -m bot.main
```

### 6. Run the API server (separate terminal)

```bash
uvicorn api.server:app --reload
```

## Docker

```bash
docker compose up -d                    # db + migrate + api (webhook mode)
docker compose --profile polling up -d  # db + migrate + api + bot (polling mode)
docker compose up -d db                 # PostgreSQL only
```

### Running CLI commands in Docker

In production (webhook mode), use `docker compose run` for CLI commands:

```bash
docker compose run --rm api python -m bot.cli backfill
docker compose run --rm api python -m bot.cli backfill-details
docker compose run --rm api python -m bot.cli sync-workouts
docker compose run --rm api python -m bot.cli sync-activities
docker compose run --rm api alembic upgrade head
```

## Database

Seven tables: `wellness`, `hrv_analysis`, `rhr_analysis`, `scheduled_workouts`, `activities`, `activity_hrv`, `pa_baseline`.

### Migrations

```bash
poetry run alembic upgrade head                      # apply all
poetry run alembic downgrade -1                      # rollback last
poetry run alembic revision --autogenerate -m "msg"  # generate new
```

## CLI Commands

```bash
python -m bot.cli backfill                           # wellness, last 180 days
python -m bot.cli backfill 2026-03-01                # single day
python -m bot.cli backfill 2026-01-01:2026-03-23     # date range
python -m bot.cli backfill 2026Q1                    # quarter
python -m bot.cli backfill 2026-03                   # month
python -m bot.cli sync-workouts                      # scheduled workouts, 14 days ahead
python -m bot.cli sync-workouts 30                   # 30 days ahead
python -m bot.cli sync-activities                    # completed activities
python -m bot.cli backfill-details                   # activity details from Intervals.icu API
python -m bot.cli shell                              # interactive Python shell
```

## Web Pages

| Page | URL | Description |
|---|---|---|
| Landing | `/` | Public page — features, links to dashboard/plan/Telegram |
| Login | `/login` | Desktop auth — 6-digit code from `/web` bot command |
| Morning Report | `/report` | Recovery gauge, HRV/RHR/sleep metrics, AI recommendation |
| Wellness | `/wellness` | Day-by-day wellness navigation with all metrics |
| Training Plan | `/plan` | Scheduled workouts by week, sync button, HumanGo descriptions |
| Activities | `/activities` | Completed activities by week, inline detail expansion |
| Activity | `/activity/:id` | Full activity details — zones, intervals, DFA alpha 1 |
| Dashboard | `/dashboard` | Tabbed dashboard — Today/Load/Goal/Week |

## API Endpoints

```
GET  /api/report                        — morning report (grouped JSON)
GET  /api/scheduled-workouts?week_offset=0 — weekly plan (Mon-Sun)
GET  /api/activities-week?week_offset=0 — weekly activities (Mon-Sun)
POST /api/jobs/sync-workouts            — trigger plan sync (initData auth)
POST /api/jobs/sync-activities          — trigger activities sync (initData auth)
GET  /health                            — healthcheck
POST /telegram/webhook                  — Telegram updates (webhook mode)
POST /mcp                               — MCP server (Streamable HTTP, Bearer auth)
```

## MCP Server

12 tools for Claude Desktop: wellness, HRV analysis, RHR analysis, training load, recovery, goal progress, scheduled workouts, activities, activity HRV (DFA alpha 1), thresholds history, readiness history.

Run standalone: `python -m mcp_server`

Production: mounted at `/mcp` in FastAPI, protected by Bearer token (`MCP_AUTH_TOKEN`).

## Project Structure

```
bot/           Telegram bot (main, scheduler, formatter, CLI)
data/          Intervals.icu client, metrics, HRV activity (DFA), database ORM
ai/            Claude API + Gemini (optional), prompts
api/           FastAPI server, routes, dashboard routes
mcp_server/    MCP tools and resources
webapp/        Web pages (HTML + Chart.js + Tailwind)
migrations/    Alembic database migrations
docs/          Design documents and implementation plans
tests/         Unit tests
config.py      Centralized settings (pydantic-settings)
```

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Description |
|---|---|
| `INTERVALS_API_KEY` | Intervals.icu API key |
| `INTERVALS_ATHLETE_ID` | Intervals.icu athlete ID |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |
| `TELEGRAM_WEBHOOK_URL` | Webhook base URL (empty = polling mode) |
| `ANTHROPIC_API_KEY` | Anthropic API key (Claude) |
| `GOOGLE_AI_API_KEY` | Google AI API key (Gemini, optional) |
| `DATABASE_URL` | PostgreSQL connection string |
| `HRV_ALGORITHM` | Primary HRV algorithm: `flatt_esco` or `ai_endurance` |
| `MCP_AUTH_TOKEN` | Bearer token for MCP server |
| `GOAL_EVENT_NAME` | Target race name |
| `GOAL_EVENT_DATE` | Target race date (YYYY-MM-DD) |

## License

MIT
