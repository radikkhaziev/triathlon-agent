"""Request-scoped context for MCP tools — carries the authenticated user."""

import contextvars

import sentry_sdk

_current_user_id: contextvars.ContextVar[int] = contextvars.ContextVar("mcp_user_id")


def set_current_user_id(user_id: int, athlete_id: str | None = None) -> None:
    _current_user_id.set(user_id)
    sentry_sdk.set_user(
        {
            "id": str(user_id),
            "username": f"athlete_{athlete_id}" if athlete_id else f"user_{user_id}",
        }
    )


def get_current_user_id() -> int:
    """Get the authenticated user ID for the current MCP request.

    Raises LookupError if called outside an authenticated MCP request.
    """
    return _current_user_id.get()


async def require_owner() -> int:
    """Return user_id if the current user is an owner, else raise PermissionError."""
    from data.db import User

    user_id = get_current_user_id()
    user = await User.get_by_id(user_id)
    if not user or user.role != "owner":
        raise PermissionError("Owner role required")
    return user_id
