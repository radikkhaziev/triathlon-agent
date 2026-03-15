from datetime import date

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Garmin
    GARMIN_EMAIL: str = ""
    GARMIN_PASSWORD: SecretStr = SecretStr("")

    # Telegram
    TELEGRAM_BOT_TOKEN: SecretStr = SecretStr("")
    TELEGRAM_CHAT_ID: str = ""

    # Anthropic
    ANTHROPIC_API_KEY: SecretStr = SecretStr("")

    # App
    API_BASE_URL: str = "https://your-api.railway.app"
    WEBAPP_URL: str = "https://your-app.vercel.app"
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/triathlon"
    )

    # Athlete thresholds
    ATHLETE_LTHR_RUN: int = 158
    ATHLETE_LTHR_BIKE: int = 152
    ATHLETE_MAX_HR: int = 182
    ATHLETE_RESTING_HR: float = 42
    ATHLETE_FTP: float = 245
    ATHLETE_CSS: float = 98
    ATHLETE_AGE: int = 43

    # Race goal
    GOAL_EVENT_NAME: str = "Ironman 70.3"
    GOAL_EVENT_DATE: date = date(2026, 9, 15)
    GOAL_CTL_TARGET: float = 75
    GOAL_SWIM_CTL_TARGET: float = 15
    GOAL_BIKE_CTL_TARGET: float = 35
    GOAL_RUN_CTL_TARGET: float = 25

    # Scheduler
    MORNING_REPORT_HOUR: int = 7
    MORNING_REPORT_MINUTE: int = 0
    TIMEZONE: str = "Europe/Belgrade"


settings = Settings()
