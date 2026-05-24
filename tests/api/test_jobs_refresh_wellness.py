"""Tests for POST /api/jobs/refresh-wellness (Halo redesign — Wellness page
«Обновить» button).

Covers the per-user 60s cooldown, the 429 payload + ``Retry-After`` header,
the happy-path Dramatiq dispatch, and the authorization level — the endpoint
must gate on ``require_athlete`` (not ``require_viewer``) so a demo/viewer
cannot burn the owner's Intervals.icu API budget.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import require_athlete, require_viewer
from api.routers.dashboard import _refresh_last, router


@pytest.fixture(autouse=True)
def _clear_cooldown():
    """``_refresh_last`` is module-level — wipe between tests so each one sees
    a fresh "never refreshed" baseline."""
    _refresh_last.clear()
    yield
    _refresh_last.clear()


@pytest.fixture
def dispatch_mocks():
    """Mock everything downstream of the cooldown guard: the Dramatiq actor
    and the ORM→DTO adapter (so no Redis / real DB row is needed)."""
    with (
        patch("api.routers.dashboard.actor_user_wellness") as actor,
        patch("api.routers.dashboard.UserDTO") as user_dto,
    ):
        user_dto.model_validate.return_value = MagicMock(name="UserDTO")
        yield MagicMock(actor=actor, user_dto=user_dto)


@pytest.fixture
async def client():
    test_app = FastAPI()
    test_app.include_router(router)

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.role = "owner"
    mock_user.is_active = True
    test_app.dependency_overrides[require_athlete] = lambda: mock_user
    # Stays open for the whole test — the cooldown case posts twice.
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        yield c


async def _post(client) -> "tuple[int, dict, dict]":
    resp = await client.post("/api/jobs/refresh-wellness")
    return resp.status_code, resp.json(), dict(resp.headers)


class TestHappyPath:
    async def test_first_call_dispatches_actor(self, client, dispatch_mocks):
        """A fresh user → 202, actor dispatched once for today with force=True."""
        status, body, _ = await _post(client)

        assert status == 202
        assert body["status"] == "accepted"
        assert body["job"] == "refresh-wellness"
        assert body["dt"]  # today's ISO date

        dispatch_mocks.actor.send.assert_called_once()
        kwargs = dispatch_mocks.actor.send.call_args.kwargs
        assert kwargs["force"] is True
        assert kwargs["dt"] == body["dt"]

    async def test_records_cooldown_timestamp(self, client, dispatch_mocks):
        """A successful call seeds ``_refresh_last`` so the next one is gated."""
        await _post(client)
        assert 1 in _refresh_last


class TestCooldown:
    async def test_second_call_within_window_returns_429(self, client, dispatch_mocks):
        """Two calls back-to-back → second hits the 60s cooldown."""
        first_status, _, _ = await _post(client)
        assert first_status == 202

        second_status, body, headers = await _post(client)
        assert second_status == 429
        assert body["detail"]["error"] == "cooldown"
        assert 0 < body["detail"]["retry_after_sec"] <= 60
        assert "retry-after" in headers
        # The 429 must NOT dispatch a second job.
        dispatch_mocks.actor.send.assert_called_once()

    async def test_call_after_window_elapsed_is_allowed(self, client, dispatch_mocks):
        """Pre-seed ``_refresh_last`` past the cooldown → call goes through."""
        _refresh_last[1] = time.monotonic() - 120  # 2 min ago, window is 60s
        status, _, _ = await _post(client)

        assert status == 202
        dispatch_mocks.actor.send.assert_called_once()

    async def test_cooldown_is_per_user_not_global(self, client, dispatch_mocks):
        """Another user's recent refresh must NOT gate this user. Regression
        guard against accidentally swapping the cooldown key to chat_id / IP /
        anything but ``user.id`` — that would let one athlete starve another.
        Verifies the key on ``_refresh_last`` is the authenticated user id."""
        _refresh_last[2] = time.monotonic()  # user 2 just refreshed
        status, _, _ = await _post(client)  # user 1 calls — should be untouched

        assert status == 202
        dispatch_mocks.actor.send.assert_called_once()
        assert 1 in _refresh_last  # this user's slot was set
        assert _refresh_last[2] > 0  # the OTHER user's slot was not stomped


class TestAuthorization:
    def test_endpoint_gates_on_require_athlete_not_viewer(self):
        """Security contract (docstring): refresh writes to *this* user's data
        and burns API budget, so it must require an athlete — a read-only
        demo/viewer must not reach it."""
        route = next(r for r in router.routes if getattr(r, "path", "") == "/api/jobs/refresh-wellness")
        dep_calls = {d.call for d in route.dependant.dependencies}

        assert require_athlete in dep_calls
        assert require_viewer not in dep_calls
