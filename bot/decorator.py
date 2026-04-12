import functools

from pydantic import TypeAdapter

from bot.i18n import _, set_language
from data.db import User, UserDTO

_UserListAdapter = TypeAdapter(list[UserDTO])


def with_athletes(fn):
    """Decorator: injects `athletes: list[UserDTO]` as first argument."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        users: list[User] = await User.get_active_athletes()
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
