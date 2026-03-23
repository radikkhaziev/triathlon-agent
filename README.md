# Triathlon AI Agent

Personal AI agent for triathlon training. Syncs data from Intervals.icu, runs dual-algorithm HRV analysis, calculates training load, and sends AI-powered recommendations via Telegram.

## Features

- **Morning Reports** — automated daily analysis of sleep, HRV, and training readiness
- **Dual HRV Analysis** — Flatt & Esco (acute) + AIEndurance (chronic) algorithms, both stored
- **RHR Baseline Tracking** — 7d/30d/60d resting heart rate baselines with trend analysis
- **Training Load Tracking** — CTL/ATL/TSB from Intervals.icu
- **AI Recommendations** — Claude-powered daily analysis with workout suggestions
- **Telegram Bot** — `/morning` command with Mini App button
- **Mini App Dashboard** — interactive charts and progress tracking via Telegram Web App
- **Goal Tracking** — progress toward target race (e.g., Ironman 70.3)

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

### 5. Run the bot

```bash
python -m bot.main
```

### 6. Run the API server (separate terminal)

```bash
uvicorn api.server:app --reload
```

## Docker (all services)

```bash
docker compose up -d
```

## Database

Three tables: `wellness` (daily data from Intervals.icu), `hrv_analysis` (dual-algorithm HRV baselines), `rhr_analysis` (resting HR baselines).

### Migrations

```bash
poetry run alembic upgrade head                      # apply all
poetry run alembic downgrade -1                      # rollback last
poetry run alembic revision --autogenerate -m "msg"  # generate new
```

## CLI Commands

```bash
# Backfill wellness data from Intervals.icu (default: last 180 days)
python -m bot.cli backfill

# Backfill a specific period
python -m bot.cli backfill 2025Q3                    # quarter
python -m bot.cli backfill 2025-03                   # month
python -m bot.cli backfill 2025-01-01:2025-03-31     # date range
python -m bot.cli backfill 2025-09-01                # single day

# Interactive Python shell with app context
python -m bot.cli shell
```

Via Docker:

```bash
docker compose exec bot python -m bot.cli backfill 2025Q4
docker compose exec bot python -m bot.cli shell
```

## Running Tests

```bash
pytest
```

## Project Structure

```
bot/           Telegram bot (main, scheduler, CLI)
data/          Intervals.icu client, metrics calculations, database ORM
ai/            Claude API integration and prompts
api/           FastAPI server for the Mini App
webapp/        Telegram Mini App (HTML + Chart.js)
migrations/    Alembic database migrations
tests/         Unit tests
config.py      Centralized settings (pydantic-settings)
```

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable              | Description                              |
| --------------------- | ---------------------------------------- |
| `INTERVALS_API_KEY`   | Intervals.icu API key                    |
| `INTERVALS_ATHLETE_ID`| Intervals.icu athlete ID                 |
| `TELEGRAM_BOT_TOKEN`  | Telegram Bot API token                   |
| `TELEGRAM_CHAT_ID`    | Your Telegram chat ID                    |
| `ANTHROPIC_API_KEY`   | Anthropic API key                        |
| `DATABASE_URL`        | PostgreSQL connection string             |
| `HRV_ALGORITHM`       | Primary HRV algorithm for recovery score |
| `GOAL_EVENT_NAME`     | Target race name                         |
| `GOAL_EVENT_DATE`     | Target race date (YYYY-MM-DD)            |

## License

MIT
