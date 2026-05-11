"""Tests for api/routers/athlete.py — PATCH /api/athlete/profile.

Semantics mirror the goal-patch tests:
- empty body → 400
- bounds enforced by pydantic (18-90) → 422 surfaces at the router boundary
- audit log captures user_id + fields
- multi-tenant safety: user_id always derived from the auth principal, never
  trusted from the request body (no body field for user_id exists).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from api.deps import require_athlete
from api.dto import AthleteProfilePatchRequest
from api.routers.athlete import patch_athlete_profile
from api.routers.athlete import router as athlete_router


def _user(user_id: int = 1, age: int | None = 35) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        role="athlete",
        is_active=True,
        athlete_id="i001",
        language="ru",
        age=age,
    )


class TestPatchAthleteProfile:
    @pytest.mark.asyncio
    async def test_empty_body_returns_400(self):
        body = AthleteProfilePatchRequest()  # nothing set
        with pytest.raises(HTTPException) as exc:
            await patch_athlete_profile(body=body, user=_user())
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_age_set_calls_update_with_value(self):
        body = AthleteProfilePatchRequest(age=42)
        with patch("api.routers.athlete.User.update_age", AsyncMock()) as update_mock:
            out = await patch_athlete_profile(body=body, user=_user(age=35))

        update_mock.assert_awaited_once_with(1, 42)
        assert out == {"age": 42}

    @pytest.mark.asyncio
    async def test_age_explicit_null_clears_field(self):
        body = AthleteProfilePatchRequest(age=None)
        with patch("api.routers.athlete.User.update_age", AsyncMock()) as update_mock:
            out = await patch_athlete_profile(body=body, user=_user())

        update_mock.assert_awaited_once_with(1, None)
        assert out == {"age": None}

    def test_age_below_min_is_422(self):
        with pytest.raises(ValidationError):
            AthleteProfilePatchRequest(age=17)

    def test_age_above_max_is_422(self):
        with pytest.raises(ValidationError):
            AthleteProfilePatchRequest(age=91)

    @pytest.mark.asyncio
    async def test_audit_log_emitted_on_success(self):
        body = AthleteProfilePatchRequest(age=42)
        with (
            patch("api.routers.athlete.User.update_age", AsyncMock()),
            patch("api.routers.athlete.logger.info") as mock_info,
        ):
            await patch_athlete_profile(body=body, user=_user())

        calls_rendered = [
            str(call.args[0]) % call.args[1:] if len(call.args) > 1 else str(call.args[0])
            for call in mock_info.call_args_list
        ]
        assert any("PATCH /api/athlete/profile" in r for r in calls_rendered)
        assert any("user_id=1" in r for r in calls_rendered)
        assert any("age" in r for r in calls_rendered)


class TestPatchAthleteProfileAuthWiring:
    """Integration test pinning the endpoint to ``require_athlete``.

    The unit tests above bypass ``Depends`` entirely, so a future regression
    swapping ``require_athlete`` → ``require_viewer`` would still pass them.
    This TestClient-based test exercises the dep wiring so demo principals
    receive 403 on write, and athletes get through.
    """

    def _client(self, principal):
        app = FastAPI()
        app.include_router(athlete_router)
        app.dependency_overrides[require_athlete] = lambda: principal
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_demo_principal_blocked_by_require_athlete(self):
        # The real `require_athlete` would 403 on role=="demo". Simulate by
        # raising HTTPException from the override — same surface for the caller.
        def _demo_dep():
            raise HTTPException(status_code=403, detail="Read-only demo mode")

        app = FastAPI()
        app.include_router(athlete_router)
        app.dependency_overrides[require_athlete] = _demo_dep
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.patch("/api/athlete/profile", json={"age": 42})
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_athlete_principal_reaches_handler(self):
        athlete = MagicMock()
        athlete.id = 1
        athlete.role = "athlete"
        athlete.is_active = True
        athlete.athlete_id = "i001"
        athlete.age = 35  # value pre-PATCH

        with patch("api.routers.athlete.User.update_age", AsyncMock()):
            async with self._client(athlete) as client:
                r = await client.patch("/api/athlete/profile", json={"age": 42})
        assert r.status_code == 200
        assert r.json() == {"age": 42}
