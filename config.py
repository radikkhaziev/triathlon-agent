from datetime import date

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    TELEGRAM_BOT_TOKEN: SecretStr = SecretStr("")
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_WEBHOOK_URL: str = ""  # base URL, e.g. "https://your-api.example.com"; empty = polling mode

    # Intervals.icu
    INTERVALS_API_KEY: SecretStr = SecretStr("")
    INTERVALS_ATHLETE_ID: str = ""

    # Anthropic
    ANTHROPIC_API_KEY: SecretStr = SecretStr("")

    # Google AI (optional — enables Gemini second opinion in dashboard)
    GOOGLE_AI_API_KEY: SecretStr = SecretStr("")  # empty = Gemini disabled

    # App
    API_BASE_URL: str = "https://your-api.railway.app"
    WEBAPP_URL: str = "https://your-app.vercel.app"
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/triathlon"

    # Athlete thresholds (from HumanGo tests, Nov-Dec 2025 + Mar 2026)
    ATHLETE_LTHR_RUN: int = 153
    ATHLETE_LTHR_BIKE: int = 153
    ATHLETE_MAX_HR: int = 179
    ATHLETE_FTP: float = 233
    ATHLETE_CSS: float = 141  # 2:21/100m from HumanGo, Mar 2026
    ATHLETE_AGE: int = 43

    # Race goal
    GOAL_EVENT_NAME: str = "Ironman 70.3"
    GOAL_EVENT_DATE: date = date(2026, 9, 15)
    GOAL_CTL_TARGET: float = 75
    GOAL_SWIM_CTL_TARGET: float = 15
    GOAL_BIKE_CTL_TARGET: float = 35
    GOAL_RUN_CTL_TARGET: float = 25

    TIMEZONE: str = "Europe/Belgrade"

    # HRV Algorithm
    HRV_ALGORITHM: str = "flatt_esco"  # "flatt_esco" | "ai_endurance"

    # Web Auth (desktop login via one-time code)
    JWT_SECRET: SecretStr = SecretStr("")  # If empty, falls back to TELEGRAM_BOT_TOKEN
    JWT_EXPIRY_DAYS: int = 7  # JWT token lifetime

    # MCP
    MCP_AUTH_TOKEN: SecretStr = SecretStr("")  # Bearer token for remote MCP access

    # GitHub
    GITHUB_TOKEN: SecretStr = SecretStr("")  # PAT for issue creation
    GITHUB_REPO: str = "radikkhaziev/triathlon-agent"

    # AI Workout Generation (Phase 1: Adaptive Training Plan)
    AI_WORKOUT_ENABLED: bool = True  # Enable AI workout generation and MCP tools
    AI_WORKOUT_AUTO_PUSH: bool = True  # Auto-push generated workouts to Intervals.icu in morning cron

    # AI Tool-Use (MCP Phase 2)
    AI_USE_TOOL_USE: bool = True  # Tool-use for morning analysis (vs fixed prompt V1)
    AI_CHAT_ENABLED: bool = True  # Free-form Telegram chat (Phase 3)


settings = Settings()
