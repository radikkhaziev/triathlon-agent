import logging
import zoneinfo
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from api.auth import generate_code
from bot.formatter import build_morning_message
from bot.scheduler import create_scheduler
from config import settings
from data.database import IqosDailyRow, WellnessRow
from data.intervals_client import IntervalsClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command — welcome message with bot description."""
    webapp_url = settings.API_BASE_URL
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Открыть приложение", web_app=WebAppInfo(url=webapp_url))],
        ]
    )
    await update.message.reply_text(
        "Triathlon AI Coach — персональный тренер на основе данных.\n\n"
        "Что умеет бот:\n"
        "• Утренний анализ готовности (HRV, recovery, sleep)\n"
        "• AI-рекомендации по тренировкам\n"
        "• Адаптация плана под текущее состояние\n"
        "• Отслеживание прогресса к гонке\n\n",
        reply_markup=keyboard,
    )


async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /dashboard command — alias for /morning."""
    webapp_url = settings.API_BASE_URL
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть", web_app=WebAppInfo(url=webapp_url))]])
    await update.message.reply_text("Web Dashboard", reply_markup=keyboard)


async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /morning command — build report from current DB data."""
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
        await update.message.reply_text("Нет доступа.")
        return

    dt = datetime.now(zoneinfo.ZoneInfo(settings.TIMEZONE)).date()
    row = await WellnessRow.get(dt)

    if not row:
        await update.message.reply_text("Нет данных за сегодня. Данные обновляются автоматически каждые 10 минут.")
        return

    summary = build_morning_message(row)
    webapp_url = settings.API_BASE_URL
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть отчёт", web_app=WebAppInfo(url=webapp_url))]])
    await update.message.reply_text(summary, reply_markup=keyboard)


async def web_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /web command — generate one-time login code for desktop browser."""
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
        await update.message.reply_text("Нет доступа.")
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
        await update.message.reply_text("Нет доступа.")
        return

    dt = datetime.now(zoneinfo.ZoneInfo(settings.TIMEZONE)).date()
    row = await IqosDailyRow.increment(dt)
    await update.message.reply_text(f"🚬 Стик #{row.count} за {dt.strftime('%d.%m')}")


async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-form text messages — AI chat via tool-use (Phase 3)."""
    if not settings.AI_CHAT_ENABLED:
        return

    # Owner only — silent ignore for others
    if str(update.effective_user.id) != settings.TELEGRAM_CHAT_ID:
        return

    user_text = update.message.text
    if not user_text or not user_text.strip():
        return

    await update.message.chat.send_action("typing")

    try:
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent()
        response = await agent.chat(user_text)

        # Telegram Markdown is fragile — fallback to plain text on parse error
        try:
            await update.message.reply_text(response, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(response)
    except Exception as e:
        logger.error("Chat error: %s", e, exc_info=True)
        await update.message.reply_text("Ошибка при обработке. Попробуй ещё раз.")


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
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("morning", morning))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("web", web_login))
    app.add_handler(CommandHandler("stick", stick))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^whoami$"), whoami))
    # Phase 3: free-form chat — last handler, catches all remaining text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_message))
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
