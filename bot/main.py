import logging

from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler

from bot.handlers import (
    callback_handler,
    goal_handler,
    report_handler,
    settings_handler,
    start_handler,
    status_handler,
    sync_handler,
    week_handler,
    zones_handler,
)
from bot.scheduler import create_scheduler
from config import settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from data.database import init_db

    init_db()

    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("report", report_handler))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("week", week_handler))
    app.add_handler(CommandHandler("goal", goal_handler))
    app.add_handler(CommandHandler("zones", zones_handler))
    app.add_handler(CommandHandler("settings", settings_handler))
    app.add_handler(CommandHandler("sync", sync_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))

    scheduler = create_scheduler()
    scheduler.start()

    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
