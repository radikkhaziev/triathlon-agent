"""Tests for mcp_server/tools/taper.py — thin delegation to the service.

All gate/resolution behaviour is covered in tests/test_taper_service.py;
here we only pin that the tool passes the contextvars user_id and its
parameters through to `get_taper_plan_for_user` unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from mcp_server.tools.taper import get_taper_plan


@pytest.mark.asyncio
async def test_delegates_to_service_with_context_user():
    service = AsyncMock(return_value={"available": False, "reason": "no_future_race"})
    with (
        patch("mcp_server.tools.taper.get_current_user_id", return_value=42),
        patch("mcp_server.tools.taper.get_taper_plan_for_user", service),
    ):
        result = await get_taper_plan(goal_id=7, race_date="2026-07-01", race_distance_class="long")
    service.assert_awaited_once_with(42, goal_id=7, race_date="2026-07-01", race_distance_class="long")
    assert result == {"available": False, "reason": "no_future_race"}
