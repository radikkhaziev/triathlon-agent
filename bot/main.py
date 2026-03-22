import logging
import time
import zoneinfo
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from bot.formatter import build_report_summary
from bot.scheduler import _fetch_garmin_data, create_scheduler
from config import settings
from data.database import get_daily_metrics, save_daily_metrics
from data.garmin_client import GarminClient
from data.models import RecoveryScore


def _format_token_ttl(seconds: int) -> str:
    if seconds <= 0:
        return "expired"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins or not parts:
        parts.append(f"{mins}m")
    return " ".join(parts)


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'report' message — fetch Garmin data, persist, and send morning report."""
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
        await update.message.reply_text("У вас нет доступа к этому боту.")
        return

    await update.message.reply_text("⏳ Собираю данные...")

    garmin = GarminClient()
    dt = datetime.now(zoneinfo.ZoneInfo(settings.TIMEZONE)).date()
    data = await _fetch_garmin_data(garmin, dt)

    # Persist to DB (runs recovery pipeline if wake-up detected)
    await save_daily_metrics(
        dt,
        sleep_data=data["sleep"],
        hrv_data=data["hrv"],
        body_battery_morning=data["body_battery_morning"],
        resting_hr=data["resting_hr"],
        readiness=data["readiness"],
        workouts=data["workouts"],
    )

    # Read persisted row for the report
    row = await get_daily_metrics(dt)

    recovery = None
    if row and row.recovery_score is not None:
        recovery = RecoveryScore(
            score=row.recovery_score,
            category=row.recovery_category or "moderate",
            recommendation=row.recovery_recommendation or "zone1_long",
            # flags/components not persisted in DB yet — defaults from model
        )

    sleep_data = data["sleep"]
    summary = build_report_summary(recovery=recovery, sleep_data=sleep_data)
    webapp_url = f"{settings.API_BASE_URL}/report.html"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть отчёт", web_app=WebAppInfo(url=webapp_url))]])
    await update.message.reply_text(summary, reply_markup=keyboard)


async def howareyou(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
        await update.message.reply_text("У вас нет доступа к этому боту.")
        return

    gc = GarminClient()

    # Auth status
    if gc.profile:
        display_name = gc.profile.get("displayName") or gc.profile.get("userName") or "?"
        auth = f"Authenticated as {display_name}"
    else:
        auth = "Not authenticated"

    # Cooldown status
    now = time.monotonic()
    if now < gc._login_cooldown_until:
        remaining = int(gc._login_cooldown_until - now)
        mins, secs = divmod(remaining, 60)
        cooldown = f"Active — {mins}m {secs}s remaining"
    else:
        cooldown = "None"

    # Last request
    if gc._last_request_time > 0:
        elapsed = int(now - gc._last_request_time)
        last_req = f"{elapsed}s ago"
    else:
        last_req = "No requests yet"

    # Token expiration
    token_info = "No token"
    oauth2 = gc.client.garth.oauth2_token
    if oauth2:
        now_ts = int(time.time())
        access_left = oauth2.expires_at - now_ts
        refresh_left = oauth2.refresh_token_expires_at - now_ts
        token_info = f"Access: {_format_token_ttl(access_left)}\n" f"Refresh: {_format_token_ttl(refresh_left)}"

    lines = [
        "*Garmin Client Status*",
        f"Email: `{gc.email}`",
        f"Auth: {auth}",
        f"Cooldown: {cooldown}",
        f"Last request: {last_req}",
        f"Token store: `{settings.GARMIN_TOKENS}`",
        f"\n*OAuth2 Token*\n{token_info}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    lines = [
        "*Who Am I*",
        f"ID: `{user.id}`",
        f"First name: {user.first_name or '—'}",
        f"Last name: {user.last_name or '—'}",
        f"Username: @{user.username}" if user.username else "Username: —",
        f"Language: {user.language_code or '—'}",
        f"Is bot: {user.is_bot}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def start_bot() -> None:
    """Start the Telegram bot with polling."""
    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    async def post_init(application):
        scheduler = await create_scheduler(bot=application.bot)
        scheduler.start()
        logging.info("Scheduler started")

    app = ApplicationBuilder().token(token).post_init(post_init).build()
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^report$"), report))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^whoami$"), whoami))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^howareyou$"), howareyou))

    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    start_bot()
