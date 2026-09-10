"""``sentry_tool`` — quota deferrals are re-raised without a Sentry event."""

from unittest.mock import patch

import pytest

from data.intervals.client import IntervalsRateLimitError
from mcp_server.sentry import sentry_tool


class TestSentryTool:
    async def test_rate_limit_reraised_without_capture(self):
        @sentry_tool
        async def tool():
            raise IntervalsRateLimitError(24173, method="POST", path="/athlete/i1/events")

        with patch("mcp_server.sentry.sentry_sdk.capture_exception") as capture:
            with pytest.raises(IntervalsRateLimitError):
                await tool()
        capture.assert_not_called()

    async def test_other_exceptions_captured_and_reraised(self):
        @sentry_tool
        async def tool():
            raise RuntimeError("boom")

        with patch("mcp_server.sentry.sentry_sdk.capture_exception") as capture:
            with pytest.raises(RuntimeError):
                await tool()
        capture.assert_called_once()

    async def test_result_passthrough(self):
        @sentry_tool
        async def tool():
            return "ok"

        assert await tool() == "ok"
