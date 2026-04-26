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

    # Intervals.icu OAuth. Empty `CLIENT_ID` disables the OAuth flow —
    # `POST /api/intervals/auth/init` returns 503.
    INTERVALS_OAUTH_CLIENT_ID: str = ""
    INTERVALS_OAUTH_CLIENT_SECRET: SecretStr = SecretStr("")
    INTERVALS_OAUTH_REDIRECT_URI: str = "https://bot.endurai.me/api/intervals/auth/callback"
    # Shared secret configured in Intervals.icu → Manage App → Webhook Secret.
    # Used (Phase 4) to verify push webhook signatures in `POST /api/intervals/webhook`.
    # Empty = no verification, accept all (Phase 1 debug mode).
    INTERVALS_WEBHOOK_SECRET: SecretStr = SecretStr("")
    # App
    API_BASE_URL: str = "https://bot.endurai.me"  # serves API + webapp + static from one container
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/triathlon"

    TIMEZONE: str = "Europe/Belgrade"

    # HRV Algorithm
    HRV_ALGORITHM: str = "flatt_esco"  # "flatt_esco" | "ai_endurance"

    # Free-form chat daily request cap. 0 = unlimited (disabled gate).
    # Counted via ``ApiUsageDaily.request_count`` which `bot/agent.py` already
    # increments on every Claude call. Applies to ALL roles including owner —
    # cap is global anti-abuse, not a permission tier. Workout/race conversation
    # handlers are NOT gated (bounded by their own state machines).
    CHAT_DAILY_LIMIT: int = Field(default=40, ge=0)
    # Higher cap for donors — recognised when ``User.last_donation_at`` falls
    # within ``CHAT_DONOR_WINDOW_DAYS``. 0 = unlimited for donors.
    # Stop-message for non-donors mentions the donor cap as a soft conversion
    # nudge (separate from the donate-nudge cadence in DONATE_SPEC §11).
    CHAT_DAILY_LIMIT_DONOR: int = Field(default=100, ge=0)
    # Days a donation grants the elevated cap. Mirrors
    # ``DONATE_NUDGE_SUPPRESS_DAYS`` so a single donation buys exactly one
    # week of both nudge silence and the higher cap — keeping the donor
    # contract simple to communicate ("donate → 7 days of perks").
    CHAT_DONOR_WINDOW_DAYS: int = Field(default=7, ge=1)

    # Donate nudge (see docs/DONATE_SPEC.md §11)
    DONATE_NUDGE_EVERY_N: int = Field(default=5, gt=0)  # show nudge on every N-th chat request (must be > 0)
    DONATE_NUDGE_SKIP_OWNER: bool = False  # True = suppress nudge for owner role
    DONATE_NUDGE_MAX_PER_DAY: int = Field(default=2, ge=0)  # cap nudges per day to avoid over-prompting
    DONATE_NUDGE_SUPPRESS_DAYS: int = Field(default=7, ge=0)  # suppress for N days after a recent donation

    # Web Auth (desktop login via one-time code)
    JWT_SECRET: SecretStr = SecretStr("")  # If empty, falls back to TELEGRAM_BOT_TOKEN
    JWT_EXPIRY_DAYS: int = 7  # JWT token lifetime

    # Demo mode: shared password for read-only access to owner's data. Empty = disabled.
    DEMO_PASSWORD: SecretStr = SecretStr("")

    # Strava signature: auto-rename activities with AI-generated promo title/description.
    STRAVA_SIGNATURE_ENABLED: bool = False

    # Video render service (video.endurai.me). Empty URL = feature disabled,
    # bot does not show the "🎬 Video (beta)" button after activities.
    VIDEO_API_URL: str = ""
    VIDEO_API_TOKEN: SecretStr = SecretStr("")

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
