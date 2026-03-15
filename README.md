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

## Running Tests

```bash
pytest
```

## Project Structure

```
bot/           Telegram bot (handlers, scheduler, formatter)
data/          Garmin client, metrics calculations, database
ai/            Claude API integration and prompts
api/           FastAPI server for the Mini App
webapp/        Telegram Mini App (HTML + Chart.js)
tests/         Unit tests
```

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Description |
|---|---|
| `GARMIN_EMAIL` | Garmin Connect email |
| `GARMIN_PASSWORD` | Garmin Connect password |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `DATABASE_URL` | PostgreSQL connection string |
| `GOAL_EVENT_NAME` | Target race name |
| `GOAL_EVENT_DATE` | Target race date (YYYY-MM-DD) |

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/report` | Generate morning report |
| `/status` | Quick status (numbers only) |
| `/week` | Weekly training summary |
| `/goal` | Goal progress breakdown |
| `/zones` | HR threshold zones |
| `/sync` | Manually sync Garmin data |
| `/settings` | Current settings |

## License

MIT
