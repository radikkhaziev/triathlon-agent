import logging
import zoneinfo
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from api.auth import generate_code
from bot.formatter import build_morning_message
from bot.scheduler import create_scheduler
from config import settings
from data.database import get_wellness, increment_iqos_stick
from data.intervals_client import IntervalsClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /morning command — build report from current DB data."""
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
        await update.message.reply_text("У вас нет доступа к этому боту.")
        return

    dt = datetime.now(zoneinfo.ZoneInfo(settings.TIMEZONE)).date()
    row = await get_wellness(dt)

    if not row:
        await update.message.reply_text("Нет данных за сегодня. Данные обновляются автоматически каждые 10 минут.")
        return

    summary = build_morning_message(row)
    webapp_url = f"{settings.API_BASE_URL}/report"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть отчёт", web_app=WebAppInfo(url=webapp_url))]])
    await update.message.reply_text(summary, reply_markup=keyboard)


async def web_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /web command — generate one-time login code for desktop browser."""
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
        await update.message.reply_text("У вас нет доступа к этому боту.")
        return

    code = generate_code(str(update.effective_user.id))
    login_url = f"{settings.API_BASE_URL}/login"
    await update.message.reply_text(
        f"🔑 Код: `{code}`\n\nДействует 5 минут. Введите на странице:\n{login_url}",
        parse_mode="Markdown",
    )


async def stick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stick command — increment IQOS stick counter for today."""
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
        await update.message.reply_text("У вас нет доступа к этому боту.")
        return

    dt = datetime.now(zoneinfo.ZoneInfo(settings.TIMEZONE)).date()
    row = await increment_iqos_stick(dt)
    await update.message.reply_text(f"🚬 Стик #{row.count} за {dt.strftime('%d.%m')}")


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


# ---------------------------------------------------------------------------
# App builder (shared between polling and webhook modes)
# ---------------------------------------------------------------------------


async def _post_init(application: Application) -> None:
    scheduler = await create_scheduler(bot=application.bot)
    scheduler.start()
    application.bot_data["scheduler"] = scheduler
    logger.info("Scheduler started")


async def _post_shutdown(application: Application) -> None:
    scheduler = application.bot_data.get("scheduler")
    if scheduler and scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")

    if IntervalsClient._instance is not None and IntervalsClient._instance.is_active:
        await IntervalsClient._instance.close()
        logger.info("IntervalsClient closed")


def build_application() -> Application:
    """Build the Telegram Application with all handlers.

    Used by both polling mode (start_bot) and webhook mode (api/server.py).
    """
    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    builder = ApplicationBuilder().token(token).post_init(_post_init).post_shutdown(_post_shutdown)
    # In webhook mode, we handle updates manually — no need for built-in Updater
    if settings.TELEGRAM_WEBHOOK_URL:
        builder = builder.updater(None)
    app = builder.build()
    app.add_handler(CommandHandler("morning", morning))
    app.add_handler(CommandHandler("web", web_login))
    app.add_handler(CommandHandler("stick", stick))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^whoami$"), whoami))
    return app


# ---------------------------------------------------------------------------
# Polling mode (local development)
# ---------------------------------------------------------------------------


def start_bot() -> None:
    """Start the Telegram bot with polling (for local development)."""
    app = build_application()
    logger.info("Bot started (polling mode)")
    app.run_polling()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    start_bot()
