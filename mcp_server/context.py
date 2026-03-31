"""Request-scoped context for MCP tools — carries the authenticated user."""

import contextvars

_current_user_id: contextvars.ContextVar[int] = contextvars.ContextVar("mcp_user_id")


def set_current_user_id(user_id: int) -> None:
    _current_user_id.set(user_id)


def get_current_user_id() -> int:
    """Get the authenticated user ID for the current MCP request.

    Raises LookupError if called outside an authenticated MCP request.
    """
    return _current_user_id.get()
