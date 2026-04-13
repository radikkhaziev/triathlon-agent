import asyncio
import copy
import logging
import os
import time
import uuid
import zoneinfo
from datetime import datetime, timedelta
from typing import Callable, NamedTuple

import httpx
import psutil
import sentry_sdk
from sqlalchemy import text
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from api.auth import generate_code
from bot.agent import ClaudeAgent
from bot.decorator import athlete_required
from bot.i18n import _
from bot.i18n import set_language as _set_lang
from bot.scheduler import create_scheduler
from bot.tools import MCPClient
from config import settings
from data.db import IqosDaily, User, UserDTO, Wellness, get_session
from data.redis_client import close_redis, get_redis, init_redis
from sentry_config import init_sentry
from tasks.actors import actor_compose_user_morning_report, actor_update_zones
from tasks.utils import RampTrainingSuggestion

logger = logging.getLogger(__name__)

init_sentry()

agent = ClaudeAgent()

TZ = zoneinfo.ZoneInfo(settings.TIMEZONE)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command — welcome message + ensure user exists in DB."""
    tg_user = update.effective_user
    chat_id = str(tg_user.id)

    user = await User.get_or_create_from_telegram(
        chat_id=chat_id,
        username=tg_user.username,
        display_name=tg_user.full_name,
    )
    logger.info("User resolved via /start: id=%s chat_id=%s username=%s", user.id, chat_id, tg_user.username)

    webapp_url = settings.API_BASE_URL
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Открыть приложение", web_app=WebAppInfo(url=webapp_url))],
        ]
    )
    await update.message.reply_text(
        "AI Coach — персональный тренер на основе данных.\n\n"
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


@athlete_required
async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle /morning command — show report if ready, otherwise dispatch generation."""
    dt = datetime.now(TZ).date()
    row = await Wellness.get(user.id, dt)
    webapp_url = settings.API_BASE_URL
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(_("Открыть отчёт"), web_app=WebAppInfo(url=webapp_url))]])

    if not row:
        await update.message.reply_text(_("Нет данных за сегодня. Данные обновляются автоматически каждые 10 минут."))
        return

    if row.ai_recommendation:
        await update.message.reply_text(_("Утренний отчёт готов."), reply_markup=keyboard)
        return

    # Report not generated yet — dispatch dramatiq task
    actor_compose_user_morning_report.send(user=UserDTO.model_validate(user).model_dump())

    await update.message.reply_text(_("Отчёт формируется, подождите пару минут."), reply_markup=keyboard)


@athlete_required
async def set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle /lang command — show language picker inline buttons."""
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
                InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
            ]
        ]
    )
    await update.message.reply_text("🌐 Choose language:", reply_markup=keyboard)


@athlete_required
async def handle_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle language selection callback."""
    query = update.callback_query
    await query.answer()

    lang = query.data.split(":")[1]
    if lang not in ("ru", "en"):
        return

    async with get_session() as session:
        db_user = await session.get(User, user.id)
        db_user.language = lang
        await session.commit()

    _set_lang(lang)
    label = "🇷🇺 Русский" if lang == "ru" else "🇬🇧 English"
    await query.edit_message_text(f"✅ {label}")


@athlete_required
async def web_login(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle /web command — generate one-time login code for desktop browser."""
    code = generate_code(str(user.chat_id))
    login_url = f"{settings.API_BASE_URL}/login"
    await update.message.reply_text(
        f"🔑 Код: `{code}`\n\nДействует 5 минут. Введите на странице:\n{login_url}",
        parse_mode="Markdown",
    )


@athlete_required
async def silent(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle /silent command — toggle silent mode."""
    async with get_session() as session:
        db_user = await session.get(User, user.id)
        db_user.is_silent = not db_user.is_silent
        await session.commit()
        status = _("включён") if db_user.is_silent else _("выключен")

    await update.message.reply_text(f"🔇 {_('Тихий режим')} {status}")


@athlete_required
async def stick(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle /stick command — increment IQOS stick counter for today. Owner only."""
    if user.role != "owner":
        await update.message.reply_text("Нет доступа.")
        return

    dt = datetime.now(TZ).date()
    row = await IqosDaily.increment(user_id=user.id, target_date=dt)
    await update.message.reply_text(f"🚬 Стик #{row.count} за {dt.strftime('%d.%m')}")


@athlete_required
async def health(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle /health command — server diagnostics. Owner only."""
    if user.role != "owner":
        await update.message.reply_text("Нет доступа.")
        return

    lines = []
    start = time.monotonic()

    # System (htop-style) — cpu_percent in thread to avoid blocking event loop
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot
    up_str = f"{uptime.days}d {uptime.seconds // 3600}h:{(uptime.seconds % 3600) // 60:02d}m"

    cpu_per_core = await asyncio.to_thread(psutil.cpu_percent, interval=0.5, percpu=True)
    cpu_total = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    load1, load5, load15 = psutil.getloadavg()
    tasks = len(psutil.pids())

    def _bar(pct: float, width: int = 20) -> str:
        filled = int(pct / 100 * width)
        return "█" * filled + "░" * (width - filled)

    lines.append("```")
    for i, pct in enumerate(cpu_per_core):
        lines.append(f"CPU{i} [{_bar(pct)}] {pct:5.1f}%")
    mem_str = f"{mem.used / 1024**3:.2f}G/{mem.total / 1024**3:.2f}G"
    lines.append(f"Mem  [{_bar(mem.percent)}] {mem_str}")
    swap_str = f"{swap.used / 1024**3:.2f}G/{swap.total / 1024**3:.2f}G"
    lines.append(f"Swp  [{_bar(swap.percent)}] {swap_str}")
    lines.append(f"Disk [{_bar(disk.percent)}] {disk.percent}%")
    lines.append("")
    lines.append(f"Tasks: {tasks}  Load: {load1:.2f} {load5:.2f} {load15:.2f}")
    lines.append(f"Uptime: {up_str}  CPU: {cpu_total}%")
    lines.append("```")

    # DB + token counts (single session)
    try:
        async with get_session() as session:
            active_users = (await session.execute(text("SELECT count(*) FROM users WHERE is_active = true"))).scalar()
            mcp_tokens = (
                await session.execute(text("SELECT count(*) FROM users WHERE mcp_token IS NOT NULL"))
            ).scalar()
            api_keys = (
                await session.execute(text("SELECT count(*) FROM users WHERE api_key_encrypted IS NOT NULL"))
            ).scalar()
        lines.append(f"✅ *DB*: ok | {active_users} active users")
        lines.append(f"🔑 *Tokens*: {mcp_tokens} MCP | {api_keys} API keys")
    except Exception as e:
        lines.append(f"❌ *DB*: {e}")

    # Redis + Dramatiq queues
    try:
        r = get_redis()
        await r.ping()
        info = await r.info("memory")
        used = info.get("used_memory_human", "?")
        db_size = await r.dbsize()

        queue_info = []
        keys = await r.keys("dramatiq:*")
        for key in keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            key_type = await r.type(key)
            key_type_str = key_type.decode() if isinstance(key_type, bytes) else key_type
            if key_type_str == "list":
                size = await r.llen(key)
            elif key_type_str == "zset":
                size = await r.zcard(key)
            elif key_type_str == "set":
                size = await r.scard(key)
            else:
                continue
            if size > 0:
                queue_info.append(f"{key_str.replace('dramatiq:', '')}={size}")

        redis_line = f"✅ *Redis*: ok | {used} | {db_size} keys"
        if queue_info:
            redis_line += f"\n📬 *Queues*: {', '.join(queue_info)}"
        else:
            redis_line += "\n📬 *Queues*: empty"
        lines.append(redis_line)
    except Exception as e:
        lines.append(f"❌ *Redis*: {e}")

    # Intervals.icu API (generic check, no real credentials)
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get("https://intervals.icu/api/v1/athlete/0", auth=("x", "x"))
            if resp.status_code in (200, 401, 403):
                lines.append("✅ *Intervals.icu*: reachable")
            else:
                lines.append(f"⚠️ *Intervals.icu*: HTTP {resp.status_code}")
    except Exception as e:
        lines.append(f"❌ *Intervals.icu*: {e}")

    # Anthropic API (model list — no token cost)
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": settings.ANTHROPIC_API_KEY.get_secret_value(),
                    "anthropic-version": "2023-06-01",
                },
            )
            if resp.status_code == 200:
                lines.append("✅ *Anthropic*: ok")
            else:
                lines.append(f"⚠️ *Anthropic*: HTTP {resp.status_code}")
    except Exception as e:
        lines.append(f"❌ *Anthropic*: {str(e)[:50]}")

    elapsed = round((time.monotonic() - start) * 1000)
    lines.append(f"⏱ Response: {elapsed}ms")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@athlete_required
async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle free-form text messages — AI chat via tool-use."""
    user_text = update.message.text
    if not user_text or not user_text.strip():
        return

    # Include reply context if replying to a message
    reply = update.message.reply_to_message
    if reply and reply.text:
        user_text = f"[В ответ на: {reply.text}]\n\n{user_text}"

    await update.message.chat.send_action("typing")

    try:
        response = await agent.chat(user_text, mcp_token=user.mcp_token, user_id=user.id, language=user.language)

        # Telegram Markdown is fragile — fallback to plain text on parse error
        try:
            await update.message.reply_text(response, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(response)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error("Chat error: %s", e, exc_info=True)
        await update.message.reply_text(_("Ошибка при обработке. Попробуй ещё раз."))


@athlete_required
async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle photo messages — download, save locally, pass to AI chat with vision."""

    photo = update.message.photo[-1]  # highest resolution
    caption = update.message.caption or ""

    await update.message.chat.send_action("typing")

    try:
        # Download photo from Telegram
        file = await photo.get_file()
        if file.file_size and file.file_size > 5 * 1024 * 1024:  # 5 MB limit
            await update.message.reply_text(_("Фото слишком большое (макс 5 МБ)."))
            return
        photo_bytes = await file.download_as_bytearray()

        # Save to static/uploads/
        uploads_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.jpg"
        filepath = os.path.join(uploads_dir, filename)
        with open(filepath, "wb") as f:
            f.write(photo_bytes)

        image_url = f"{settings.API_BASE_URL}/static/uploads/{filename}"

        response = await agent.chat(
            user_message=caption,
            mcp_token=user.mcp_token,
            user_id=user.id,
            language=user.language,
            image_data=bytes(photo_bytes),
            image_url=image_url,
        )

        try:
            await update.message.reply_text(response, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(response)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error("Photo chat error: %s", e, exc_info=True)
        await update.message.reply_text(_("Ошибка при обработке фото. Попробуй ещё раз."))


# ---------------------------------------------------------------------------
# /workout — ConversationHandler
# ---------------------------------------------------------------------------

WORKOUT_CHOOSE_SPORT, WORKOUT_DIALOG = range(2)


class PreviewableTool(NamedTuple):
    """Contract for tools that support a two-phase preview/push flow.

    is_preview(input) returns True if the tool invocation was a preview
    (safe to replay as a push). apply_push(input) mutates the input dict
    in place to flip whichever boolean flag turns preview into a real push.
    """

    is_preview: Callable[[dict], bool]
    apply_push: Callable[[dict], None]


def _suggest_workout_is_preview(inp: dict) -> bool:
    # suggest_workout: dry_run=True means preview, default False pushes.
    return inp.get("dry_run") is True


def _suggest_workout_apply_push(inp: dict) -> None:
    inp["dry_run"] = False


def _compose_workout_is_preview(inp: dict) -> bool:
    # compose_workout: push_to_intervals=False (or absent) means preview,
    # True pushes. Default in the tool signature is False.
    return inp.get("push_to_intervals") is not True


def _compose_workout_apply_push(inp: dict) -> None:
    inp["push_to_intervals"] = True


_PREVIEWABLE_TOOLS: dict[str, PreviewableTool] = {
    "suggest_workout": PreviewableTool(_suggest_workout_is_preview, _suggest_workout_apply_push),
    "compose_workout": PreviewableTool(_compose_workout_is_preview, _compose_workout_apply_push),
}


def _extract_pending_workout(tool_calls: list[dict]) -> dict | None:
    """Find the most recent previewed workout tool call that can be replayed as a push.

    Scans in reverse order so that if Claude revised the workout twice in one
    turn, we pick the final version. Returns ``{"name", "input"}`` where
    ``input`` is a fresh deep copy — safe to mutate without touching the
    agent's tool-use history.
    """
    for call in reversed(tool_calls):
        name = call.get("name")
        tool_cfg = _PREVIEWABLE_TOOLS.get(name) if isinstance(name, str) else None
        if tool_cfg is None:
            continue
        if tool_cfg.is_preview(call.get("input", {})):
            return {"name": name, "input": copy.deepcopy(call.get("input", {}))}
    return None


def _apply_push_flag(pending: dict) -> None:
    """Flip the preview flag in place so the tool executes as a real push.

    Raises KeyError if the pending draft references a tool we don't know how
    to push. This should not happen in practice because
    :func:`_extract_pending_workout` only stores tools from
    ``_PREVIEWABLE_TOOLS``, but the guard prevents a silent no-op in which
    the caller would re-run the tool still in preview mode and show a
    misleading success message to the user.
    """
    name = pending["name"]
    tool_cfg = _PREVIEWABLE_TOOLS.get(name)
    if tool_cfg is None:
        raise KeyError(f"No push flag config for tool {name!r}")
    tool_cfg.apply_push(pending["input"])


@athlete_required
async def workout_start(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> int:
    """Entry point: /workout → show sport selection."""
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💪 Фитнес", callback_data="workout:WeightTraining"),
                InlineKeyboardButton("🏃 Run", callback_data="workout:Run"),
            ],
            [
                InlineKeyboardButton("🏊 Swim", callback_data="workout:Swim"),
                InlineKeyboardButton("🚴 Ride", callback_data="workout:Ride"),
            ],
        ]
    )
    await update.message.reply_text(_("Выбери вид тренировки:"), reply_markup=keyboard)
    return WORKOUT_CHOOSE_SPORT


@athlete_required
async def workout_sport_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> int:
    """Sport selected → ask Claude to generate first workout variant."""
    query = update.callback_query
    await query.answer()

    sport = query.data.split(":", 1)[1]
    await query.edit_message_reply_markup(reply_markup=None)

    context.user_data["workout_sport"] = sport
    context.user_data["workout_messages"] = []

    await query.message.chat.send_action("typing")

    now_local = datetime.now(TZ)
    target_date = now_local.date() + timedelta(days=1) if now_local.hour >= 19 else now_local.date()
    target_date_iso = target_date.isoformat()
    day_label = "завтра" if target_date != now_local.date() else "сегодня"
    context.user_data["workout_target_date"] = target_date_iso

    prompt = (
        f"Сгенерируй тренировку на {day_label} ({target_date_iso}). Вид спорта: {sport}. "
        f"Перед генерацией вызови get_activities для target_date={target_date_iso}, "
        f"чтобы учесть активности, уже выполненные в этот день, в rationale и оценке нагрузки. "
        f"Затем используй suggest_workout с target_date={target_date_iso} и dry_run=True "
        f"(только превью, не отправляй)."
    )
    if sport == "WeightTraining":
        prompt = (
            f"Сгенерируй фитнес-тренировку на {day_label} ({target_date_iso}). "
            f"Сначала вызови get_activities для target_date={target_date_iso}, чтобы учесть "
            "активности, уже выполненные в этот день. Затем get_animation_guidelines и "
            f"list_exercise_cards, и собери тренировку через compose_workout с "
            f"target_date={target_date_iso} и push_to_intervals=False "
            "(preview-режим, пользователь подтвердит через кнопку)."
        )

    tool_calls: list[dict] = []
    try:
        response = await agent.chat(
            prompt,
            mcp_token=user.mcp_token,
            user_id=user.id,
            tool_calls_out=tool_calls,
            tool_calls_filter=set(_PREVIEWABLE_TOOLS.keys()),
        )
        context.user_data["workout_messages"].append({"role": "assistant", "content": response})
    except Exception:
        logger.exception("Workout generation failed")
        response = "Ошибка при генерации. Попробуй ещё раз или /cancel."

    # Always replace so a previous draft can't linger if the new turn produced none.
    context.user_data["pending_workout"] = _extract_pending_workout(tool_calls)

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Отправить в Intervals", callback_data="workout_push")],
            [InlineKeyboardButton("❌ Отмена", callback_data="workout_cancel")],
        ]
    )

    try:
        await query.message.reply_text(response, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(response, reply_markup=keyboard)

    return WORKOUT_DIALOG


@athlete_required
async def workout_dialog_text(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> int:
    """User sends text to refine the workout."""
    user_text = update.message.text
    context.user_data.setdefault("workout_messages", [])

    sport = context.user_data.get("workout_sport", "Run")
    prompt = f"[Контекст: создаём тренировку {sport}]\n\n{user_text}"

    await update.message.chat.send_action("typing")

    tool_calls: list[dict] = []
    try:
        response = await agent.chat(
            prompt,
            mcp_token=user.mcp_token,
            user_id=user.id,
            tool_calls_out=tool_calls,
            tool_calls_filter=set(_PREVIEWABLE_TOOLS.keys()),
        )
        context.user_data["workout_messages"].append({"role": "assistant", "content": response})
    except Exception:
        logger.exception("Workout dialog error")
        response = "Ошибка. Попробуй ещё раз или /cancel."

    # Always replace so a previous draft can't linger if the new turn produced none.
    context.user_data["pending_workout"] = _extract_pending_workout(tool_calls)

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Отправить в Intervals", callback_data="workout_push")],
            [InlineKeyboardButton("❌ Отмена", callback_data="workout_cancel")],
        ]
    )

    try:
        await update.message.reply_text(response, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(response, reply_markup=keyboard)

    return WORKOUT_DIALOG


@athlete_required
async def workout_push(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> int:
    """Push the cached dry-run workout to Intervals.icu via direct MCP call.

    Replays the exact tool invocation Claude made during the dry-run with
    ``dry_run=False`` — no second Claude inference pass, so the workout that
    reaches Intervals.icu is bit-for-bit what the user saw in the preview.
    """
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    # Consume-on-read: pop immediately so a duplicate tap (or a follow-up
    # tap from an older message's inline button) cannot replay the draft.
    pending = context.user_data.pop("pending_workout", None)
    if not pending:
        await query.message.reply_text("Не нашёл черновик тренировки — сгенерируй её заново через /workout.")
        context.user_data.pop("workout_sport", None)
        context.user_data.pop("workout_messages", None)
        return ConversationHandler.END

    await query.message.chat.send_action("typing")

    tool_name = pending["name"]
    try:
        _apply_push_flag(pending)  # flip dry_run / push_to_intervals in place
    except KeyError:
        logger.error("workout_push: unknown tool %s cached in pending_workout", tool_name)
        await query.message.reply_text(
            "Не могу отправить: внутренняя ошибка (неизвестный тип тренировки). " "Сгенерируй заново через /workout."
        )
        context.user_data.pop("workout_sport", None)
        context.user_data.pop("workout_messages", None)
        return ConversationHandler.END

    try:
        mcp = MCPClient(token=user.mcp_token)
        result = await mcp.call_tool(tool_name, pending["input"])
        # MCPClient.call_tool returns a dict. Tools that respond with plain
        # text get wrapped as {"text": "..."}. JSON-RPC errors → {"error": "..."}.
        if isinstance(result, dict) and result.get("error"):
            logger.warning("MCP tool %s returned error: %s", tool_name, result["error"])
            response = f"Ошибка при отправке: {result['error']}"
        elif isinstance(result, dict) and result.get("text"):
            response = result["text"]
        else:
            response = "Тренировка отправлена в Intervals.icu."
    except Exception:
        logger.exception("Workout push failed (tool=%s)", tool_name)
        response = "Ошибка при отправке в Intervals.icu. Попробуй ещё раз или /cancel."

    # No parse_mode: the response comes straight from an MCP tool and may
    # contain user-controlled strings (workout name, rationale, upstream
    # error payloads) with Markdown special chars like _ * [ ] ( ). Sending
    # as plain text avoids broken formatting and fake clickable links.
    await query.message.reply_text(response)

    context.user_data.pop("workout_sport", None)
    context.user_data.pop("workout_messages", None)
    return ConversationHandler.END


@athlete_required
async def workout_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> int:
    """Cancel workout creation."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(_("Отменено."))

    context.user_data.pop("workout_sport", None)
    context.user_data.pop("workout_messages", None)
    context.user_data.pop("pending_workout", None)
    return ConversationHandler.END


async def workout_cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel via /cancel command."""
    await update.message.reply_text(_("Создание тренировки отменено."))
    context.user_data.pop("workout_sport", None)
    context.user_data.pop("workout_messages", None)
    context.user_data.pop("pending_workout", None)
    return ConversationHandler.END


@athlete_required
async def handle_adapt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle 'Адаптировать' button from morning report → start workout dialog."""
    query = update.callback_query
    await query.answer()

    workout_id = query.data.split(":", 1)[1]
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.chat.send_action("typing")

    prompt = (
        f"Тренировка (id={workout_id}) требует адаптации под текущее состояние атлета. "
        f"Получи данные о тренировке через get_scheduled_workouts, "
        f"оцени текущее восстановление через get_recovery, "
        f"и предложи адаптированную версию через suggest_workout с dry_run=True."
    )

    try:
        response = await agent.chat(prompt, mcp_token=user.mcp_token, user_id=user.id)
    except Exception:
        logger.exception("Adapt workout failed")
        response = "Ошибка при адаптации. Попробуй через /workout."
        try:
            await query.message.reply_text(response)
        except Exception:
            pass
        return

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Отправить в Intervals", callback_data="workout_push")],
            [InlineKeyboardButton("❌ Отмена", callback_data="workout_cancel")],
        ]
    )

    try:
        await query.message.reply_text(response, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(response, reply_markup=keyboard)


@athlete_required
async def handle_ramp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle 'Создать Ramp Test' inline button press."""
    query = update.callback_query
    await query.answer()

    sport = query.data.split(":", 1)[1] if ":" in query.data else "Run"
    await query.edit_message_reply_markup(reply_markup=None)

    ramp = RampTrainingSuggestion(user=UserDTO.model_validate(user), wellness=None)
    msg = ramp.plan_ramp(sport=sport)
    await query.message.reply_text(f"⚡ {msg}")


@athlete_required
async def handle_update_zones_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle 'Обновить зоны' inline button press — dispatch actor."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    actor_update_zones.send(user=UserDTO.model_validate(user))
    await query.message.reply_text("⚡ Обновление зон запущено")


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
    await init_redis()
    # See docs/MULTI_TENANT_SECURITY.md §5.1: per-tenant credentials in multi-tenant

    scheduler = await create_scheduler()
    scheduler.start()
    application.bot_data["scheduler"] = scheduler
    logger.info("Scheduler started")


async def _post_shutdown(application: Application) -> None:
    scheduler = application.bot_data.get("scheduler")
    if scheduler and scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")

    await close_redis()


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
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("lang", set_lang))
    app.add_handler(CommandHandler("silent", silent))
    workout_conv = ConversationHandler(
        entry_points=[CommandHandler("workout", workout_start)],
        states={
            WORKOUT_CHOOSE_SPORT: [
                CallbackQueryHandler(workout_sport_chosen, pattern=r"^workout:"),
            ],
            WORKOUT_DIALOG: [
                CallbackQueryHandler(workout_push, pattern=r"^workout_push$"),
                CallbackQueryHandler(workout_cancel, pattern=r"^workout_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, workout_dialog_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", workout_cancel_command)],
    )
    app.add_handler(workout_conv)
    app.add_handler(CallbackQueryHandler(handle_adapt_callback, pattern=r"^adapt:"))
    app.add_handler(CallbackQueryHandler(handle_ramp_callback, pattern=r"^ramp_test:"))
    app.add_handler(CallbackQueryHandler(handle_update_zones_callback, pattern=r"^update_zones$"))
    app.add_handler(CallbackQueryHandler(handle_lang_callback, pattern=r"^lang:"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^whoami$"), whoami))
    # Photo handler — download, save, pass to AI chat with vision
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    # Phase 3: free-form chat — last handler, catches all remaining text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_message))
    return app


# ---------------------------------------------------------------------------
# Polling mode (local development)
# ---------------------------------------------------------------------------


def start_bot() -> None:
    """Start the Telegram bot with polling (for local development)."""
    if settings.TELEGRAM_WEBHOOK_URL:
        raise RuntimeError("TELEGRAM_WEBHOOK_URL is set — bot runs via webhook in api service, not polling")
    app = build_application()
    logger.info("Bot started (polling mode)")
    app.run_polling()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    start_bot()
