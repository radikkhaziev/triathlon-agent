"""Sentry decorator for MCP tools — spans + error capture."""

import functools

import sentry_sdk


def sentry_tool(func):
    """Wrap MCP tool with Sentry span + error capture."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        tool_name = func.__name__
        with sentry_sdk.start_span(op="mcp.tool", description=tool_name):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                sentry_sdk.set_tag("mcp.tool", tool_name)
                sentry_sdk.capture_exception(e)
                raise

    return wrapper
