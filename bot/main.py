import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

from bot.scheduler import create_scheduler
from config import settings
from data.database import set_bot


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
        set_bot(application.bot)
        scheduler = create_scheduler()
        scheduler.start()
        logging.info("Scheduler started")

        # Startup health check: verify Garmin connection and notify
        from data.garmin_client import GarminClient
        garmin = GarminClient()
        garmin_ok = False
        try:
            if garmin.client and garmin.client.get_full_name():
                garmin_ok = True
        except Exception:
            pass

        chat_id = settings.TELEGRAM_CHAT_ID
        status = "connected" if garmin_ok else "disconnected"
        await application.bot.send_message(
            chat_id=chat_id, text=f"Bot started\nGarmin: {status}"
        )

    app = ApplicationBuilder().token(token).post_init(post_init).build()
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^whoami$"), whoami))

    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    start_bot()
