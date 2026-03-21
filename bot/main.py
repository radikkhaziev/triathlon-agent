import logging
import time

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from bot.scheduler import create_scheduler
from config import settings
from data.garmin_client import GarminClient


async def howareyou(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
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

    lines = [
        "*Garmin Client Status*",
        f"Email: `{gc.email}`",
        f"Auth: {auth}",
        f"Cooldown: {cooldown}",
        f"Last request: {last_req}",
        f"Token store: `{settings.GARMIN_TOKENS}`",
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
