import logging

from telegram.ext import ApplicationBuilder

from bot.scheduler import create_scheduler
from config import settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    async def post_init(application):
        scheduler = create_scheduler()
        scheduler.start()
        logging.info("Scheduler started")

    app = ApplicationBuilder().token(token).post_init(post_init).build()

    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
