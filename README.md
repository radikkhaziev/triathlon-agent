# Triathlon AI Agent

Personal AI agent for triathlon training. Reads Garmin data every morning, calculates training load, and sends AI-powered recommendations via Telegram.

## Features

- **Morning Reports** — automated daily analysis of sleep, HRV, and training readiness
- **Training Load Tracking** — CTL/ATL/TSB across swim, bike, and run
- **AI Recommendations** — Claude-powered analysis with specific workout adjustments
- **Telegram Bot** — commands for reports, status, goals, and HR zones
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
alembic upgrade head
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

## Database Migrations

Проект использует Alembic для управления схемой БД (PostgreSQL).

```bash
# Применить все миграции
alembic upgrade head

# Откатить последнюю миграцию
alembic downgrade -1

# Создать новую миграцию (после изменения моделей в data/database.py)
alembic revision --autogenerate -m "описание изменений"

# Посмотреть текущую версию
alembic current

# Посмотреть историю миграций
alembic history
```

## CLI Commands

```bash
# Send a message to Telegram chat
python -m bot.cli echo "Hello from CLI"

# Backfill daily metrics (default: last 180 days)
python -m bot.cli backfill

# Backfill a specific quarter
python -m bot.cli backfill 2025Q3

# Backfill a specific month
python -m bot.cli backfill 2025-03

# Backfill a date range
python -m bot.cli backfill 2025-01-01:2025-03-31

# Backfill a single day
python -m bot.cli backfill 2025-09-01

# Full Garmin credential login (use when refresh token is expired)
python -m bot.cli garmin-login

# Interactive Python shell with app context
python -m bot.cli shell
```

Via Docker:

```bash
docker compose exec bot python -m bot.cli echo "Hello"
docker compose exec bot python -m bot.cli backfill 2025Q4
docker compose exec bot python -m bot.cli garmin-login
```

## Garmin Token Management

If the bot gets a 429 (Too Many Requests) from Garmin Connect, it enters a 2-hour cooldown automatically. To manually re-authenticate after the cooldown:

```bash
docker compose exec bot python -m bot.cli garmin-login
```

## Running Tests

```bash
pytest
```

## Project Structure

```
bot/           Telegram bot (main, scheduler, CLI)
data/          Garmin client, metrics calculations, database ORM
ai/            Claude API integration and prompts
api/           FastAPI server for the Mini App
webapp/        Telegram Mini App (HTML + Chart.js)
migrations/    Alembic database migrations
docs/          Design documents (HRV spec)
mockups/       Dashboard UI mockups
tests/         Unit tests
config.py      Centralized settings (pydantic-settings)
```

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable             | Description                          |
| -------------------- | ------------------------------------ |
| `GARMIN_EMAIL`       | Garmin Connect email                 |
| `GARMIN_PASSWORD`    | Garmin Connect password              |
| `GARMIN_TOKENS`      | Path to OAuth token store (default: ~/.garminconnect) |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token              |
| `TELEGRAM_CHAT_ID`   | Your Telegram chat ID              |
| `ANTHROPIC_API_KEY`  | Anthropic API key                   |
| `DATABASE_URL`       | PostgreSQL connection string        |
| `ATHLETE_AGE`        | Athlete age for AI prompt           |
| `GOAL_EVENT_NAME`    | Target race name                    |
| `GOAL_EVENT_DATE`    | Target race date (YYYY-MM-DD)       |

## License

MIT
