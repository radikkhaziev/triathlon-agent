import asyncio
import copy
import html
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
from sqlalchemy import select as sa_select
from sqlalchemy import text
from sqlalchemy import update as sa_update
from telegram import ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update, WebAppInfo
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from api.auth import generate_code
from bot.agent import ClaudeAgent
from bot.decorator import athlete_required, user_required
from bot.donate_nudge import get_nudge_text, should_show_nudge
from bot.i18n import _
from bot.i18n import set_language as _set_lang
from bot.scheduler import create_scheduler
from bot.tools import MCPClient
from config import settings
from data.db import Activity, IqosDaily, StarTransaction, User, UserDTO, Wellness, get_session
from data.redis_client import close_redis, get_redis, init_redis
from sentry_config import init_sentry
from tasks.actors import actor_compose_user_morning_report, actor_update_zones
from tasks.formatter import rpe_label_with_emoji
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
    # Explicit reactivation: /start is an unambiguous re-engagement signal.
    # Webapp/Login Widget auth paths intentionally do NOT reactivate — see
    # `docs/MULTI_TENANT_SECURITY.md` §T14.
    if not user.is_active:
        await User.set_active_by_chat_id(chat_id, True)
        user.is_active = True
    logger.info("User resolved via /start: id=%s chat_id=%s username=%s", user.id, chat_id, tg_user.username)

    # New users (no athlete_id) land in the onboarding flow — a button that
    # opens the Settings page inside Telegram Mini App, where the "Подключить
    # Intervals.icu" OAuth flow is one tap away. Existing athletes get the
    # regular welcome + dashboard entry.
    if not user.athlete_id:
        settings_url = f"{settings.API_BASE_URL.rstrip('/')}/settings"
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        _("🔗 Подключить Intervals.icu"),
                        web_app=WebAppInfo(url=settings_url),
                    )
                ],
            ]
        )
        await update.message.reply_text(
            _(
                "Привет! Я AI-тренер для триатлетов.\n\n"
                "Чтобы начать, подключи свой аккаунт Intervals.icu — "
                "я буду синхронизировать wellness, активности и тренировки, "
                "и давать персональные рекомендации на основе твоих данных.\n\n"
                "Нажми кнопку ниже, откроется приложение с OAuth-подключением."
            ),
            reply_markup=keyboard,
        )
        return

    webapp_url = settings.API_BASE_URL
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(_("Открыть приложение"), web_app=WebAppInfo(url=webapp_url))],
        ]
    )
    await update.message.reply_text(
        _(
            "AI Coach — персональный тренер на основе данных.\n\n"
            "Что умеет бот:\n"
            "• Утренний анализ готовности (HRV, recovery, sleep)\n"
            "• AI-рекомендации по тренировкам\n"
            "• Адаптация плана под текущее состояние\n"
            "• Отслеживание прогресса к гонке\n\n"
        ),
        reply_markup=keyboard,
    )


@athlete_required
async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle /dashboard command — alias for /morning."""
    webapp_url = settings.API_BASE_URL
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_("Открыть приложение"), web_app=WebAppInfo(url=webapp_url))]]
    )
    await update.message.reply_text(_("Web Dashboard"), reply_markup=keyboard)


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


@user_required
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


@user_required
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
async def handle_rpe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle Borg CR-10 RPE rating from post-activity notification.

    Single-shot: the first successful tap writes the value, removes the
    inline keyboard, and appends ``RPE: N 🔥`` to the message text. Any
    subsequent callback for the same activity gets a silent ``Уже оценено``
    answer with no DB change. See docs/RPE_SPEC.md.
    """
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    _prefix, activity_id, raw_value = parts
    try:
        value = int(raw_value)
    except ValueError:
        await query.answer()
        return
    if not 1 <= value <= 10:
        await query.answer()
        return

    # Atomic compare-and-swap: the single UPDATE combines three checks into
    # one statement — activity exists, belongs to this user, and is still
    # unrated. Two tenants / two concurrent taps / race-between-read-and-
    # write all collapse into a single winner (the row that matches all three
    # predicates). If rowcount is 0, we can't tell which check failed, but
    # from the user's perspective the answer is always "already rated" —
    # non-ownership is impossible in practice because notifications only go
    # to the activity owner.
    async with get_session() as session:
        result = await session.execute(
            sa_update(Activity)
            .where(
                Activity.id == activity_id,
                Activity.user_id == user.id,
                Activity.rpe.is_(None),
            )
            .values(rpe=value)
        )
        await session.commit()

    # Strip the RPE-scale rows from the current markup, keeping any other
    # buttons (📸 Card, etc.) intact — issue #230: tapping RPE removed the Card
    # button too because we used to pass ``reply_markup=None``.
    remaining_markup = _strip_rpe_rows(query.message.reply_markup if query.message else None)

    if result.rowcount == 0:
        await query.answer(_("Уже оценено"))
        # Best-effort: clear the stale RPE scale so repeat taps become no-ops.
        # Keep the rest of the keyboard (e.g. 📸 Card) so it remains usable.
        try:
            await query.edit_message_reply_markup(reply_markup=remaining_markup)
        except TelegramError:
            pass
        return

    await query.answer()
    # Prefer a single round-trip: update the text AND swap the keyboard in
    # one call. Fall back to `edit_message_reply_markup` only when there's
    # no text to update (shouldn't happen — the notification always has a
    # body — but defensive).
    new_text = None
    if query.message and query.message.text:
        new_text = f"{query.message.text}\nRPE: {rpe_label_with_emoji(value)}"
    try:
        if new_text is not None:
            await query.edit_message_text(new_text, reply_markup=remaining_markup)
        else:
            await query.edit_message_reply_markup(reply_markup=remaining_markup)
    except TelegramError:
        logger.warning("handle_rpe_callback: failed to update message", exc_info=True)


def _strip_rows_with_prefix(markup: InlineKeyboardMarkup | None, callback_prefix: str) -> InlineKeyboardMarkup | None:
    """Return a new markup with rows containing a matching callback removed.

    A row is dropped if **any** button in it has ``callback_data`` starting
    with ``callback_prefix``. Used by the post-activity notification to take
    a one-shot button out of circulation (RPE 1-10 scale, 📸 Card) without
    wiping the rest of the keyboard — issue #230 originally conflated the two.
    Returns ``None`` when nothing is left so Telegram drops the keyboard.
    """
    if markup is None or not markup.inline_keyboard:
        return None
    kept = [
        row
        for row in markup.inline_keyboard
        if not any((btn.callback_data or "").startswith(callback_prefix) for btn in row)
    ]
    return InlineKeyboardMarkup(kept) if kept else None


def _strip_rpe_rows(markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    return _strip_rows_with_prefix(markup, "rpe:")


@athlete_required
async def handle_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle 📸 Card button — dispatch workout card generation.

    Single-shot: remove the Card row from the keyboard on tap to prevent
    double-generation. RPE scale (if still present) stays clickable.
    """
    from tasks.actors import actor_generate_workout_card

    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 2:
        await query.answer()
        return

    activity_id = parts[1]
    await query.answer("📸 Generating card...")

    # Strip the Card button BEFORE dispatching so a rapid second tap (or a
    # retry after a flaky edit) can't queue a duplicate generation. Any other
    # rows (RPE scale) remain intact.
    remaining_markup = _strip_rows_with_prefix(query.message.reply_markup if query.message else None, "card:")
    try:
        await query.edit_message_reply_markup(reply_markup=remaining_markup)
    except TelegramError:
        logger.warning("handle_card_callback: failed to strip Card button", exc_info=True)

    user_dto = UserDTO.model_validate(user)
    actor_generate_workout_card.send(user=user_dto, activity_id=activity_id)


@athlete_required
async def web_login(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Handle /web command — generate one-time login code for desktop browser."""
    code = generate_code(str(user.chat_id))
    login_url = f"{settings.API_BASE_URL}/login"
    await update.message.reply_text(
        f"🔑 Код: `{code}`\n\nДействует 5 минут. Введите на странице:\n{login_url}",
        parse_mode="Markdown",
    )


@user_required
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

    lines.append("<pre>")
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
    lines.append("</pre>")

    # DB + user stats + token usage (single session)
    athletes: list[User] = []
    try:
        async with get_session() as session:
            total_users = (await session.execute(text("SELECT count(*) FROM users"))).scalar()
            active_users = (await session.execute(text("SELECT count(*) FROM users WHERE is_active = true"))).scalar()
            active_athletes = (
                await session.execute(
                    text("SELECT count(*) FROM users WHERE is_active = true AND athlete_id IS NOT NULL")
                )
            ).scalar()
            oauth_users = (
                await session.execute(text("SELECT count(*) FROM users WHERE intervals_auth_method = 'oauth'"))
            ).scalar()
            mcp_tokens = (
                await session.execute(text("SELECT count(*) FROM users WHERE mcp_token IS NOT NULL"))
            ).scalar()

            # Fetch active athletes for per-user Intervals.icu check
            athletes = list(
                (await session.execute(sa_select(User).where(User.is_active.is_(True), User.athlete_id.isnot(None))))
                .scalars()
                .all()
            )

            # Today's token usage per user
            today_str = datetime.now(TZ).strftime("%Y-%m-%d")
            usage_rows = (
                await session.execute(
                    text(
                        "SELECT u.username, u.role, a.input_tokens, a.output_tokens, "
                        "a.cache_read_tokens, a.request_count "
                        "FROM api_usage_daily a JOIN users u ON u.id = a.user_id "
                        "WHERE a.date = :dt ORDER BY (a.input_tokens + a.output_tokens) DESC"
                    ),
                    {"dt": today_str},
                )
            ).fetchall()

        lines.append(
            f"✅ <b>DB</b>: ok | 👥 {total_users} users | 🏃 {active_athletes} athletes | ✅ {active_users} active"
        )
        lines.append(f"🔑 <b>Auth</b>: {mcp_tokens} MCP | {oauth_users} OAuth")

        if usage_rows:
            lines.append(f"📊 <b>Tokens today</b> ({today_str}):")
            for row in usage_rows:
                username = html.escape(str(row[0] or "—"))
                role = html.escape(str(row[1] or "—"))
                inp = row[2] or 0
                out = row[3] or 0
                cache = row[4] or 0
                reqs = row[5] or 0
                total = inp + out
                lines.append(f"  @{username} ({role}): {total:,}t ({reqs} reqs, {cache:,} cached)")
        else:
            lines.append("📊 <b>Tokens today</b>: no usage")
    except Exception as e:
        lines.append(f"❌ <b>DB</b>: {html.escape(str(e))}")

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
                queue_info.append(f"{html.escape(key_str.replace('dramatiq:', ''))}={size}")

        redis_line = f"✅ <b>Redis</b>: ok | {html.escape(str(used))} | {db_size} keys"
        if queue_info:
            redis_line += f"\n📬 <b>Queues</b>: {', '.join(queue_info)}"
        else:
            redis_line += "\n📬 <b>Queues</b>: empty"
        lines.append(redis_line)
    except Exception as e:
        lines.append(f"❌ <b>Redis</b>: {html.escape(str(e))}")

    # Intervals.icu — reachability + per-athlete credential check
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get("https://intervals.icu/api/v1/athlete/0", auth=("x", "x"))
            reachable = resp.status_code in (200, 401, 403)
            lines.append(
                f"{'✅' if reachable else '❌'} <b>Intervals.icu</b>: "
                f"{'reachable' if reachable else f'HTTP {resp.status_code}'}"
            )
            for a in athletes:
                url = f"https://intervals.icu/api/v1/athlete/{a.athlete_id}"
                name = html.escape(str(a.username or f"id={a.id}"))
                method = html.escape(str(a.intervals_auth_method))
                try:
                    if a.intervals_access_token:
                        r = await http.get(
                            url,
                            headers={"Authorization": f"Bearer {a.intervals_access_token}"},
                        )
                    elif a.api_key:
                        r = await http.get(url, auth=("API_KEY", a.api_key))
                    else:
                        lines.append(f"  ⚠️ @{name}: no credentials")
                        continue
                    if r.status_code == 200:
                        lines.append(f"  ✅ @{name}: {method} ok")
                    elif r.status_code == 401:
                        lines.append(f"  ❌ @{name}: {method} invalid")
                    else:
                        lines.append(f"  ⚠️ @{name}: {method} HTTP {r.status_code}")
                except Exception as e:
                    lines.append(f"  ❌ @{name}: {type(e).__name__}")
    except Exception as e:
        lines.append(f"❌ <b>Intervals.icu</b>: {html.escape(str(e))}")

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
                lines.append("✅ <b>Anthropic</b>: ok")
            else:
                lines.append(f"⚠️ <b>Anthropic</b>: HTTP {resp.status_code}")
    except Exception as e:
        lines.append(f"❌ <b>Anthropic</b>: {html.escape(str(e)[:50])}")

    elapsed = round((time.monotonic() - start) * 1000)
    lines.append(f"⏱ Response: {elapsed}ms")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


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
        # Free-form chat watches only for race-creation previews — workouts come
        # through the /workout ConversationHandler, not here. Filtering narrowly
        # keeps tool_calls deep-copy cost minimal.
        result = await agent.chat(
            user_text,
            mcp_token=user.mcp_token,
            user_id=user.id,
            language=user.language,
            tool_calls_filter=_RACE_TOOLS,
        )

        pending_race = _extract_pending_preview(result.tool_calls, _RACE_TOOLS)
        context.user_data["pending_race"] = pending_race

        race_markup = None
        if pending_race is not None:
            race_markup = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Отправить в Intervals", callback_data="race_push")],
                    [InlineKeyboardButton("❌ Отмена", callback_data="race_cancel")],
                ]
            )

        # Telegram Markdown is fragile — fallback to plain text on parse error
        try:
            await update.message.reply_text(result.text, parse_mode="Markdown", reply_markup=race_markup)
        except Exception:
            await update.message.reply_text(result.text, reply_markup=race_markup)

        if should_show_nudge(user, result.nudge_boundary, result.request_count):
            # Nudge send is best-effort — never let it surface an outer error
            # after the main response was already delivered.
            nudge_text = get_nudge_text()
            try:
                await update.message.reply_text(nudge_text, parse_mode="Markdown")
            except Exception:
                try:
                    await update.message.reply_text(nudge_text)
                except Exception:
                    logger.warning("Failed to send donate nudge", exc_info=True)
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

        result = await agent.chat(
            user_message=caption,
            mcp_token=user.mcp_token,
            user_id=user.id,
            language=user.language,
            image_data=bytes(photo_bytes),
            image_url=image_url,
            tool_calls_filter=_RACE_TOOLS,
        )

        pending_race = _extract_pending_preview(result.tool_calls, _RACE_TOOLS)
        context.user_data["pending_race"] = pending_race

        race_markup = None
        if pending_race is not None:
            race_markup = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Отправить в Intervals", callback_data="race_push")],
                    [InlineKeyboardButton("❌ Отмена", callback_data="race_cancel")],
                ]
            )

        try:
            await update.message.reply_text(result.text, parse_mode="Markdown", reply_markup=race_markup)
        except Exception:
            await update.message.reply_text(result.text, reply_markup=race_markup)

        if should_show_nudge(user, result.nudge_boundary, result.request_count):
            nudge_text = get_nudge_text()
            try:
                await update.message.reply_text(nudge_text, parse_mode="Markdown")
            except Exception:
                try:
                    await update.message.reply_text(nudge_text)
                except Exception:
                    logger.warning("Failed to send donate nudge", exc_info=True)
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


def _suggest_race_is_preview(inp: dict) -> bool:
    # suggest_race: dry_run=True means preview, default False pushes.
    return inp.get("dry_run") is True


def _suggest_race_apply_push(inp: dict) -> None:
    inp["dry_run"] = False


_PREVIEWABLE_TOOLS: dict[str, PreviewableTool] = {
    "suggest_workout": PreviewableTool(_suggest_workout_is_preview, _suggest_workout_apply_push),
    "compose_workout": PreviewableTool(_compose_workout_is_preview, _compose_workout_apply_push),
    "suggest_race": PreviewableTool(_suggest_race_is_preview, _suggest_race_apply_push),
}

_WORKOUT_TOOLS = {"suggest_workout", "compose_workout"}
_RACE_TOOLS = {"suggest_race"}


def _extract_pending_preview(tool_calls: list[dict], tool_filter: set[str] | None = None) -> dict | None:
    """Find the most recent previewed tool call (optionally filtered by tool name)
    that can be replayed as a push.

    Scans in reverse order so that if Claude revised the workout/race twice in
    one turn, we pick the final version. Returns ``{"name", "input"}`` where
    ``input`` is a fresh deep copy — safe to mutate without touching the
    agent's tool-use history.

    ``tool_filter`` restricts the scan to specific tool names (e.g. only
    ``{"suggest_race"}`` when extracting a race draft); None = all previewable tools.
    """
    for call in reversed(tool_calls):
        name = call.get("name")
        if not isinstance(name, str):
            continue
        if tool_filter is not None and name not in tool_filter:
            continue
        tool_cfg = _PREVIEWABLE_TOOLS.get(name)
        if tool_cfg is None:
            continue
        if tool_cfg.is_preview(call.get("input", {})):
            return {"name": name, "input": copy.deepcopy(call.get("input", {}))}
    return None


def _apply_push_flag(pending: dict) -> None:
    """Flip the preview flag in place so the tool executes as a real push.

    Raises KeyError if the pending draft references a tool we don't know how
    to push. This should not happen in practice because
    :func:`_extract_pending_preview` only stores tools from
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

    response_text = ""
    tool_calls: list[dict] = []
    try:
        result = await agent.chat(
            prompt,
            mcp_token=user.mcp_token,
            user_id=user.id,
            tool_calls_filter=set(_PREVIEWABLE_TOOLS.keys()),
        )
        response_text = result.text
        tool_calls = result.tool_calls
        context.user_data["workout_messages"].append({"role": "assistant", "content": response_text})
    except Exception:
        logger.exception("Workout generation failed")
        response_text = "Ошибка при генерации. Попробуй ещё раз или /cancel."

    # Always replace so a previous draft can't linger if the new turn produced none.
    context.user_data["pending_workout"] = _extract_pending_preview(tool_calls, _WORKOUT_TOOLS)

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Отправить в Intervals", callback_data="workout_push")],
            [InlineKeyboardButton("❌ Отмена", callback_data="workout_cancel")],
        ]
    )

    try:
        await query.message.reply_text(response_text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(response_text, reply_markup=keyboard)

    return WORKOUT_DIALOG


@athlete_required
async def workout_dialog_text(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> int:
    """User sends text to refine the workout."""
    user_text = update.message.text
    context.user_data.setdefault("workout_messages", [])

    sport = context.user_data.get("workout_sport", "Run")
    prompt = f"[Контекст: создаём тренировку {sport}]\n\n{user_text}"

    await update.message.chat.send_action("typing")

    response_text = ""
    tool_calls: list[dict] = []
    try:
        result = await agent.chat(
            prompt,
            mcp_token=user.mcp_token,
            user_id=user.id,
            tool_calls_filter=set(_PREVIEWABLE_TOOLS.keys()),
        )
        response_text = result.text
        tool_calls = result.tool_calls
        context.user_data["workout_messages"].append({"role": "assistant", "content": response_text})
    except Exception:
        logger.exception("Workout dialog error")
        response_text = "Ошибка. Попробуй ещё раз или /cancel."

    # Always replace so a previous draft can't linger if the new turn produced none.
    context.user_data["pending_workout"] = _extract_pending_preview(tool_calls, _WORKOUT_TOOLS)

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Отправить в Intervals", callback_data="workout_push")],
            [InlineKeyboardButton("❌ Отмена", callback_data="workout_cancel")],
        ]
    )

    try:
        await update.message.reply_text(response_text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(response_text, reply_markup=keyboard)

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


# ---------------------------------------------------------------------------
# /race — free-form chat → suggest_race preview → confirm button
# ---------------------------------------------------------------------------
#
# Unlike /workout, race creation has no ConversationHandler — Claude collects
# the fields through normal chat, then renders a confirm button tied to the
# single `pending_race` draft in context.user_data. race_push / race_cancel
# are registered as standalone CallbackQueryHandlers.


@athlete_required
async def race_push(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Replay the cached dry-run suggest_race with dry_run=False via direct MCP call.

    Mirrors workout_push (bot/main.py) — no second Claude inference, so the event
    that reaches Intervals.icu is bit-for-bit what the user saw in the preview.
    """
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    # Consume-on-read: pop immediately so a duplicate tap cannot replay the draft.
    pending = context.user_data.pop("pending_race", None)
    if not pending:
        await query.message.reply_text("Не нашёл черновик гонки — опиши её снова в чате.")
        return

    await query.message.chat.send_action("typing")

    tool_name = pending["name"]
    try:
        _apply_push_flag(pending)
    except KeyError:
        logger.error("race_push: unknown tool %s cached in pending_race", tool_name)
        await query.message.reply_text(
            "Не могу отправить: внутренняя ошибка (неизвестный тип). Попробуй снова описать гонку."
        )
        return

    try:
        mcp = MCPClient(token=user.mcp_token)
        result = await mcp.call_tool(tool_name, pending["input"])
        if isinstance(result, dict) and result.get("error"):
            logger.warning("MCP tool %s returned error: %s", tool_name, result["error"])
            response = f"Ошибка при отправке: {result['error']}"
        elif isinstance(result, dict) and result.get("text"):
            response = result["text"]
        else:
            response = "Гонка отправлена в Intervals.icu."
    except Exception:
        logger.exception("Race push failed (tool=%s)", tool_name)
        response = "Ошибка при отправке в Intervals.icu. Попробуй ещё раз."

    # Plain text — response may contain URLs / names with Markdown metachars.
    await query.message.reply_text(response)


@athlete_required
async def race_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Discard the pending race draft."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    context.user_data.pop("pending_race", None)
    await query.message.reply_text(_("Отменено."))


@athlete_required
async def race_command(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """`/race` — lightweight entry point into race creation/edit via chat.

    Sends a priming message so the athlete knows exactly what to provide, then
    leaves the heavy lifting to the existing free-form chat flow (which already
    catches `suggest_race(dry_run=True)` and renders the confirm button).
    No ConversationHandler state — free-form chat is the state.
    """
    await update.message.reply_text(
        _(
            "🏁 Опиши гонку одним сообщением: название, приоритет (A/B/C), дату, "
            "опционально вид (Run / Ride / Swim / Triathlon), дистанцию и target CTL.\n\n"
            "Пример: «Ironman 70.3 Belgrade 15 сентября, RACE A, триатлон, "
            "CTL target 75».\n\n"
            "Чтобы удалить гонку — напиши «удали RACE_A» (или B / C)."
        )
    )


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
        result = await agent.chat(prompt, mcp_token=user.mcp_token, user_id=user.id, tool_calls_filter=set())
        response = result.text
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
# /donate — Telegram Stars (see docs/DONATE_SPEC.md)
# ---------------------------------------------------------------------------

ALLOWED_DONATE_AMOUNTS = (50, 200, 500)


@user_required
async def donate(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Show donate message + 3 amount buttons. Open to all roles (viewer+)."""

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"☕ {ALLOWED_DONATE_AMOUNTS[0]}", callback_data=f"donate:{ALLOWED_DONATE_AMOUNTS[0]}"
                ),
                InlineKeyboardButton(
                    f"🏊 {ALLOWED_DONATE_AMOUNTS[1]}", callback_data=f"donate:{ALLOWED_DONATE_AMOUNTS[1]}"
                ),
                InlineKeyboardButton(
                    f"🏆 {ALLOWED_DONATE_AMOUNTS[2]}", callback_data=f"donate:{ALLOWED_DONATE_AMOUNTS[2]}"
                ),
            ]
        ]
    )
    await update.message.reply_text(
        _(
            "Поддержать проект EndurAI 💪\n\n"
            "Бот бесплатный, но если хочешь поддержать разработку — "
            "можно отправить Stars прямо здесь."
        ),
        reply_markup=keyboard,
    )


@user_required
async def donate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Parse `donate:{amount}`, whitelist-check, send XTR invoice."""
    query = update.callback_query

    try:
        amount = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    if amount not in ALLOWED_DONATE_AMOUNTS:
        await query.answer(_("Недопустимая сумма"), show_alert=True)
        return

    await query.answer()
    payload = f"donate_{user.id}_{amount}_{int(time.time())}"
    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title=_("Поддержать EndurAI"),
        description=_("Спасибо за поддержку проекта!"),
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(_("Поддержка"), amount)],
    )


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer pre_checkout_query within 10s. No DB/network before answer().

    Defense-in-depth: the invoice is generated by our own `send_invoice` call,
    so `currency`/`total_amount` are under our control — but we still validate
    them here to catch bugs (wrong amount passed to `send_invoice`) and to
    prevent future invoice types accidentally matching the `donate_` prefix.

    Global handler — if new invoice types are added later (e.g. subscriptions
    from #152), extend this check. See docs/DONATE_SPEC.md §5.1.
    """
    query = update.pre_checkout_query
    valid = (
        query.invoice_payload.startswith("donate_")
        and query.currency == "XTR"
        and query.total_amount in ALLOWED_DONATE_AMOUNTS
    )
    if valid:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message=_("Неизвестный тип платежа. Попробуйте снова через /donate"))


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle successful_payment message. Idempotent via UNIQUE(charge_id).

    Authoritative user lookup from `from_user.id`, NOT from payload — payload
    is untrusted metadata only useful for logs. See docs/DONATE_SPEC.md §5.2
    and docs/MULTI_TENANT_SECURITY.md §T14.
    """
    payment = update.message.successful_payment
    chat_id = str(update.effective_user.id)

    try:
        user = await User.get_by_chat_id(chat_id)
        if user is None:
            logger.warning(
                "successful_payment for unknown chat_id=%s charge_id=%s — manual recovery needed",
                chat_id,
                payment.telegram_payment_charge_id,
            )
            with sentry_sdk.new_scope() as scope:
                scope.set_extra("chat_id", chat_id)
                scope.set_extra("charge_id", payment.telegram_payment_charge_id)
                scope.set_extra("amount", payment.total_amount)
                scope.set_extra("payload", payment.invoice_payload)
                sentry_sdk.capture_message("Star donation from unknown user", level="error")
            return

        tx = await StarTransaction.create(
            user_id=user.id,
            amount=payment.total_amount,
            charge_id=payment.telegram_payment_charge_id,
            payload=payment.invoice_payload,
        )
        if tx is None:
            # Duplicate webhook — already recorded, no second "thanks" message.
            logger.info("Duplicate successful_payment charge_id=%s", payment.telegram_payment_charge_id)
            return

        # Drive the donate-nudge suppression window (DONATE_SPEC §11.2a).
        await User.mark_donation(user.id)

        sentry_sdk.add_breadcrumb(
            category="donation",
            message=f"Star donation received: {payment.total_amount} from user {user.id}",
            level="info",
        )
        await update.message.reply_text(_("Спасибо за поддержку! 🙏"))
    except Exception:
        # Money is already taken on Telegram's side — losing the DB row is
        # unrecoverable without operator intervention. Log with full context
        # and swallow: Telegram will not resend `successful_payment`, so
        # re-raising only produces noise. PTB's global error handler would
        # also report to Sentry, causing duplicate events.
        with sentry_sdk.new_scope() as scope:
            scope.set_extra("chat_id", chat_id)
            scope.set_extra("charge_id", payment.telegram_payment_charge_id)
            scope.set_extra("amount", payment.total_amount)
            scope.set_extra("payload", payment.invoice_payload)
            sentry_sdk.capture_exception()
        logger.exception("successful_payment handler failed charge_id=%s", payment.telegram_payment_charge_id)


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track user block/unblock of the bot via `my_chat_member` updates.

    `kicked` = blocked the bot, `member` = (re)started it. Flips `users.is_active`
    so scheduled broadcasts (which go through `User.get_active_athletes`) skip
    blocked users without hitting the Telegram API. Reactivation is explicit:
    this handler on `MEMBER` transitions, and `bot/main.py:start` when the
    user sends `/start`. Webapp/Login Widget auth paths deliberately do not
    reactivate — see `docs/MULTI_TENANT_SECURITY.md` §T14.
    """
    cmu = update.my_chat_member
    if cmu is None or cmu.chat.type != "private":
        return
    new_status = cmu.new_chat_member.status
    if new_status not in (ChatMember.BANNED, ChatMember.MEMBER):
        return
    active = new_status == ChatMember.MEMBER
    await User.set_active_by_chat_id(cmu.chat.id, active)
    logger.info("User %s is_active=%s (my_chat_member=%s)", cmu.chat.id, active, new_status)


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
    app.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(CommandHandler("donate", donate))
    app.add_handler(CallbackQueryHandler(donate_callback, pattern=r"^donate:\d+$"))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("morning", morning))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("web", web_login))
    app.add_handler(CommandHandler("stick", stick))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("race", race_command))
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
    app.add_handler(CallbackQueryHandler(handle_rpe_callback, pattern=r"^rpe:"))
    app.add_handler(CallbackQueryHandler(handle_card_callback, pattern=r"^card:"))
    # Race creation confirm/cancel — standalone, not inside ConversationHandler.
    # Race is requested via free-form chat (Claude emits suggest_race(dry_run=True)),
    # not through a multi-state /race wizard, so these handlers live outside the
    # workout ConversationHandler.
    app.add_handler(CallbackQueryHandler(race_push, pattern=r"^race_push$"))
    app.add_handler(CallbackQueryHandler(race_cancel, pattern=r"^race_cancel$"))
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
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    start_bot()
