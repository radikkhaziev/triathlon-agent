import logging
import zoneinfo
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from bot.formatter import build_report_summary
from bot.scheduler import create_scheduler
from config import settings
from data.database import get_wellness
from data.models import RecoveryScore, Wellness


async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /morning command — build report from current DB data."""
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
        await update.message.reply_text("У вас нет доступа к этому боту.")
        return

    dt = datetime.now(zoneinfo.ZoneInfo(settings.TIMEZONE)).date()
    row = await get_wellness(dt)

    if not row:
        await update.message.reply_text("Нет данных за сегодня. Данные обновляются автоматически каждые 15 минут.")
        return

    recovery = None
    if row.recovery_score is not None:
        recovery = RecoveryScore(
            score=row.recovery_score,
            category=row.recovery_category or "moderate",
            recommendation=row.recovery_recommendation or "zone1_long",
        )

    wellness = Wellness(sleep_score=row.sleep_score, sleep_secs=row.sleep_secs)

    summary = build_report_summary(recovery=recovery, sleep_data=wellness)
    webapp_url = f"{settings.API_BASE_URL}/report.html"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть отчёт", web_app=WebAppInfo(url=webapp_url))]])
    await update.message.reply_text(summary, reply_markup=keyboard)


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
        scheduler = await create_scheduler()
        scheduler.start()
        logging.info("Scheduler started")

    async def post_shutdown(application):
        from data.intervals_client import IntervalsClient

        client = IntervalsClient()
        if client._initialized:
            await client.close()
            logging.info("IntervalsClient closed")

    app = ApplicationBuilder().token(token).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("morning", morning))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^whoami$"), whoami))

    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    start_bot()
