"""Sentry decorator for MCP tools — spans + error capture."""

import functools

import sentry_sdk

from data.intervals.client import IntervalsRateLimitError


def sentry_tool(func):
    """Wrap MCP tool with Sentry span + error capture.

    ``IntervalsRateLimitError`` is re-raised without capture — an exhausted
    Intervals.icu quota is an operational condition, not a defect; the client
    already logged a WARNING + breadcrumb, and the tool error text reaches
    Claude (and so the user) as «quota exhausted, retry after …».
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        tool_name = func.__name__
        with sentry_sdk.start_span(op="mcp.tool", description=tool_name):
            try:
                return await func(*args, **kwargs)
            except IntervalsRateLimitError:
                raise
            except Exception as e:
                sentry_sdk.set_tag("mcp.tool", tool_name)
                sentry_sdk.capture_exception(e)
                raise

    return wrapper
