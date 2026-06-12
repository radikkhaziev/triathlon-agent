"""Tests for GET /api/taper-plan — thin delegation to data/taper_service."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import require_viewer
from api.routers.dashboard import router as dashboard_router
from api.routers.dashboard import taper_plan

pytestmark = pytest.mark.real_db  # opt out of per-test DB truncate


@pytest.mark.asyncio
async def test_delegates_with_data_user_id():
    """Viewer role must resolve to the owner's data user id (demo viewers
    see the owner's taper plan, same as every other dashboard endpoint)."""
    envelope = {"available": False, "reason": "no_future_race"}
    service = AsyncMock(return_value=envelope)
    with (
        patch("api.routers.dashboard.get_taper_plan_for_user", service),
        patch("api.routers.dashboard.get_data_user_id", return_value=17),
    ):
        result = await taper_plan(user=SimpleNamespace(id=99, role="demo"))
    service.assert_awaited_once_with(17)
    assert result == envelope


@pytest.mark.asyncio
async def test_route_reachable_for_viewer_role():
    """Wiring test through the real dep chain: the route must depend on
    `require_viewer` (NOT `require_athlete`) — demo viewers see the owner's
    taper overlay. A unit test calling the handler directly would survive a
    dep swap; this one would not."""
    test_app = FastAPI()
    test_app.include_router(dashboard_router)
    viewer = MagicMock()
    viewer.id = 1
    viewer.role = "demo"
    viewer.is_active = True
    test_app.dependency_overrides[require_viewer] = lambda: viewer

    envelope = {"available": False, "reason": "no_future_race"}
    with (
        patch("api.routers.dashboard.get_taper_plan_for_user", AsyncMock(return_value=envelope)),
        patch("api.routers.dashboard.get_data_user_id", return_value=1),
    ):
        async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
            resp = await client.get("/api/taper-plan")
    assert resp.status_code == 200
    assert resp.json() == envelope
