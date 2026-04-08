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

    # App
    API_BASE_URL: str = "https://your-api.railway.app"
    WEBAPP_URL: str = "https://your-app.vercel.app"
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/triathlon"

    # Athlete thresholds (from HumanGo tests, Nov-Dec 2025 + Mar 2026)
    ATHLETE_LTHR_RUN: int = 153  # bpm, from HumanGo ramp test
    ATHLETE_LTHR_BIKE: int = 153  # bpm, from HumanGo ramp test
    ATHLETE_MAX_HR: int = 179  # bpm
    ATHLETE_FTP: float = 233  # watts, from HumanGo ramp test, Mar 2026
    ATHLETE_CSS: float = 141  # 2:21/100m from HumanGo, Mar 2026
    ATHLETE_THRESHOLD_PACE_RUN: float = 295  # sec/km, 4:55/km
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

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # MCP
    MCP_AUTH_TOKEN: SecretStr = SecretStr("")  # Bearer token for remote MCP access
    MCP_BASE_URL: str = "http://api:8000"  # Internal MCP URL for Docker; override in .env for local dev

    # GitHub
    GITHUB_TOKEN: SecretStr = SecretStr("")  # PAT for issue creation
    GITHUB_REPO: str = "radikkhaziev/triathlon-agent"

    # Sentry
    SENTRY_DSN: str = ""  # empty = Sentry disabled
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    SENTRY_RELEASE: str = ""

    # Multi-tenant security
    FIELD_ENCRYPTION_KEY: SecretStr = SecretStr("")  # Fernet key for encrypting per-user secrets in DB


settings = Settings()
