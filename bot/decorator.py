import functools

from pydantic import TypeAdapter
from sqlalchemy import select

from bot.i18n import _, set_language
from data.db import User, UserDTO, get_session

_UserListAdapter = TypeAdapter(list[UserDTO])


def with_athletes(fn):
    """Decorator: injects `athletes: list[UserDTO]` as first argument."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        users: list[User] = await User.get_active_athletes()
        athletes: list[UserDTO] = _UserListAdapter.validate_python(users)
        return await fn(athletes, *args, **kwargs)

    return wrapper


def with_athletes_without_oauth(fn):
    """Temporary decorator for scheduler jobs that need to run even for athletes who haven't completed
    onboarding (and thus lack an IntervalsSyncClient).
    Use `with_athletes` instead where possible,
    and remove this once all scheduler jobs are migrated.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        async with get_session() as session:
            result = await session.execute(
                select(User).where(
                    User.is_active.is_(True),
                    User.intervals_auth_method == "api_key",
                    User.athlete_id.isnot(None),
                )
            )
            users = list(result.scalars().all())
        athletes: list[UserDTO] = _UserListAdapter.validate_python(users)
        return await fn(athletes, *args, **kwargs)

    return wrapper


def athlete_required(fn):
    """Decorator for Telegram handlers: resolve active athlete by chat_id, pass as `user` kwarg."""

    @functools.wraps(fn)
    async def wrapper(update, context, *args, **kwargs):
        user = await User.get_by_chat_id(str(update.effective_user.id))
        if user:
            set_language(user.language or "ru")
        if not user or not user.athlete_id or not user.is_active:
            if update.callback_query:
                await update.callback_query.answer(_("Нет доступа."), show_alert=True)
            elif update.message:
                await update.message.reply_text(_("Нет доступа."))
            return
        return await fn(update, context, *args, user=user, **kwargs)

    return wrapper


def user_required(fn):
    """Decorator for Telegram handlers: resolve any active user (viewer or
    athlete), pass as `user` kwarg.

    Weaker than `athlete_required` — does NOT require `athlete_id`. Use for
    commands that make sense for not-yet-onboarded users: `/silent`, `/lang`,
    `/donate`, `/whoami`, etc. For commands that read athlete-scoped data
    (`/morning`, `/workout`), stick with `athlete_required`.

    Lookup: `User.get_by_chat_id(update.effective_user.id)`. The column name
    `users.chat_id` is legacy — it stores the Telegram **user** ID, which in
    private chats (the only chat type this bot handles) equals `chat.id`.
    We use `effective_user.id` to identify the sender regardless of chat type,
    matching the existing `athlete_required` convention.

    Fallback: if the row is missing or `is_active=False`, reply with
    "Сначала отправьте /start" on both `message` and `callback_query` paths
    so the user gets consistent guidance on how to recover.
    """

    @functools.wraps(fn)
    async def wrapper(update, context, *args, **kwargs):
        user = await User.get_by_chat_id(str(update.effective_user.id))
        if user:
            set_language(user.language or "ru")
        if not user or not user.is_active:
            msg = _("Сначала отправьте /start")
            if update.callback_query:
                await update.callback_query.answer(msg, show_alert=True)
            elif update.message:
                await update.message.reply_text(msg)
            return
        return await fn(update, context, *args, user=user, **kwargs)

    return wrapper
