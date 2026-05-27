import functools
import logging

from pydantic import TypeAdapter

from bot.i18n import _, set_language
from data.db import User, UserDTO

logger = logging.getLogger(__name__)

_UserListAdapter = TypeAdapter(list[UserDTO])


def with_athletes(fn):
    """Decorator: injects `athletes: list[UserDTO]` as first argument."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        users: list[User] = await User.get_active_athletes()
        athletes: list[UserDTO] = _UserListAdapter.validate_python(users)
        return await fn(athletes, *args, **kwargs)

    return wrapper


async def _wake_user(user: User) -> None:
    """Flip ``is_active=True`` if a dormant user just interacted with the bot.

    The daily ``deactivate_stale`` cron pauses idle accounts to stop token
    spend on morning reports; the moment they come back (a message, command,
    callback) we reactivate so the next morning report lands again. This is
    the bot-side mirror of the webapp's ``get_current_user`` wake-up path.
    """
    if user.is_active:
        return
    logger.info("Reactivating dormant user id=%d on bot interaction", user.id)
    await User.set_active_by_chat_id(user.chat_id, True)
    user.is_active = True


def athlete_required(fn):
    """Decorator for Telegram handlers: resolve athlete by chat_id, pass as `user` kwarg.

    Dormant accounts (``is_active=False`` set by the stale-deactivation cron
    or a prior Telegram-block flip) are reactivated on first interaction —
    a returning user shouldn't need to type ``/start`` first. Users without
    ``athlete_id`` still bounce: this decorator is athlete-scoped commands
    only (use ``user_required`` for pre-onboarding paths).
    """

    @functools.wraps(fn)
    async def wrapper(update, context, *args, **kwargs):
        # `include_inactive=True` so we can see dormant rows and reactivate
        # below; otherwise a stale-deactivated user would look "missing" and
        # we'd send the bounce message instead of waking them up.
        user = await User.get_by_chat_id(str(update.effective_user.id), include_inactive=True)
        if user:
            set_language(user.language or "ru")
        if not user or not user.athlete_id:
            if update.callback_query:
                await update.callback_query.answer(_("Нет доступа."), show_alert=True)
            elif update.message:
                await update.message.reply_text(_("Нет доступа."))
            return
        await _wake_user(user)
        await User.touch_last_action(user.id)
        return await fn(update, context, *args, user=user, **kwargs)

    return wrapper


def user_required(fn):
    """Decorator for Telegram handlers: resolve any user (viewer or athlete),
    pass as `user` kwarg.

    Weaker than `athlete_required` — does NOT require `athlete_id`. Use for
    commands that make sense for not-yet-onboarded users: `/silent`, `/lang`,
    `/donate`, `/whoami`, etc. For commands that read athlete-scoped data
    (`/dashboard`, `/workout`), stick with `athlete_required`.

    Lookup: `User.get_by_chat_id(update.effective_user.id)`. The column name
    `users.chat_id` is legacy — it stores the Telegram **user** ID, which in
    private chats (the only chat type this bot handles) equals `chat.id`.
    We use `effective_user.id` to identify the sender regardless of chat type,
    matching the existing `athlete_required` convention.

    Dormant accounts (`is_active=False`) are reactivated on first interaction
    — a returning user shouldn't need to type ``/start`` first. Only fully
    missing rows bounce with "Сначала отправьте /start".
    """

    @functools.wraps(fn)
    async def wrapper(update, context, *args, **kwargs):
        # `include_inactive=True` so dormant rows are findable for reactivation.
        user = await User.get_by_chat_id(str(update.effective_user.id), include_inactive=True)
        if user:
            set_language(user.language or "ru")
        if not user:
            msg = _("Сначала отправьте /start")
            if update.callback_query:
                await update.callback_query.answer(msg, show_alert=True)
            elif update.message:
                await update.message.reply_text(msg)
            return
        await _wake_user(user)
        await User.touch_last_action(user.id)
        return await fn(update, context, *args, user=user, **kwargs)

    return wrapper
