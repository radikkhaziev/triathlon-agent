from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    TELEGRAM_BOT_TOKEN: SecretStr = SecretStr("")
    TELEGRAM_BOT_USERNAME: str = ""  # without @, used by Telegram Login Widget
    TELEGRAM_CHAT_ID: str = ""  # Owner chat ID for service notifications
    TELEGRAM_WEBHOOK_URL: str = ""  # empty = polling mode

    # Anthropic
    ANTHROPIC_API_KEY: SecretStr = SecretStr("")

    # Intervals.icu OAuth (see docs/INTERVALS_OAUTH_SPEC.md)
    # Empty `CLIENT_ID` disables the OAuth flow — `/api/intervals/auth/connect`
    # returns 503. Register the app by emailing david@intervals.icu (see
    # docs/email-draft-intervals-oauth.md).
    INTERVALS_OAUTH_CLIENT_ID: str = ""
    INTERVALS_OAUTH_CLIENT_SECRET: SecretStr = SecretStr("")
    INTERVALS_OAUTH_REDIRECT_URI: str = "https://bot.endurai.me/api/intervals/auth/callback"

    # App
    API_BASE_URL: str = "https://bot.endurai.me"  # serves API + webapp + static from one container
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/triathlon"

    TIMEZONE: str = "Europe/Belgrade"

    # HRV Algorithm
    HRV_ALGORITHM: str = "flatt_esco"  # "flatt_esco" | "ai_endurance"

    # Donate nudge (see docs/DONATE_SPEC.md §11)
    DONATE_NUDGE_EVERY_N: int = Field(default=5, gt=0)  # show nudge on every N-th chat request (must be > 0)
    DONATE_NUDGE_SKIP_OWNER: bool = False  # True = suppress nudge for owner role
    DONATE_NUDGE_MAX_PER_DAY: int = Field(default=2, ge=0)  # cap nudges per day to avoid over-prompting
    DONATE_NUDGE_SUPPRESS_DAYS: int = Field(default=7, ge=0)  # suppress for N days after a recent donation

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
