"""Tests for POST /api/auth/demo — public passwordless mint.

Contract (docs/DEMO_PUBLIC_ACCESS_SPEC.md Phase 3, Option A):
* gated by ``DEMO_ENABLED`` (404 when off — the kill switch),
* no password required,
* token carries ``purpose="demo"`` and a 24h TTL (NOT ``JWT_EXPIRY_DAYS``) so
  flipping the flag off closes the door within a day,
* per-IP rate limit on the mint endpoint.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routers.auth import _DEMO_MAX_ATTEMPTS, _DEMO_TOKEN_TTL_SEC, _demo_attempts, auth_demo
from config import settings

pytestmark = pytest.mark.real_db  # no DB access — opt out of per-test truncate


def _request(ip: str = "203.0.113.7") -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=ip))


def _jwt_payload(token: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))


@pytest.fixture(autouse=True)
def _clean_rate_limit():
    _demo_attempts.clear()
    yield
    _demo_attempts.clear()


async def test_disabled_flag_404s(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        await auth_demo(_request())
    assert exc.value.status_code == 404


async def test_mints_demo_token_without_password(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_ENABLED", True)
    owner = SimpleNamespace(chat_id="42")
    with patch("api.routers.auth.User.get_owner", new=AsyncMock(return_value=owner)):
        out = await auth_demo(_request())

    assert out["role"] == "demo"
    assert out["expires_in_hours"] == 24

    payload = _jwt_payload(out["token"])
    assert payload["sub"] == "42"
    assert payload["purpose"] == "demo"
    # 24h TTL, not JWT_EXPIRY_DAYS — the DEMO_ENABLED kill switch relies on it.
    assert payload["exp"] - payload["iat"] == _DEMO_TOKEN_TTL_SEC


async def test_503_when_no_owner(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_ENABLED", True)
    with patch("api.routers.auth.User.get_owner", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await auth_demo(_request())
    assert exc.value.status_code == 503


async def test_kill_switch_rejects_existing_demo_tokens(monkeypatch):
    """DEMO_ENABLED=false must invalidate already-minted demo tokens at
    verification time — not after the 24h TTL runs out."""
    from api.auth import create_jwt
    from api.deps import get_current_user

    token = create_jwt("42", purpose="demo", ttl_seconds=3600)
    monkeypatch.setattr(settings, "DEMO_ENABLED", False)
    assert await get_current_user(authorization=f"Bearer {token}") is None


async def test_non_session_purpose_is_not_a_session(monkeypatch):
    """JWTs with a foreign purpose (e.g. OAuth state) share the signing secret
    but must never authenticate as a session — they transit through redirect
    URLs and browser history."""
    from api.auth import create_jwt
    from api.deps import get_current_user

    monkeypatch.setattr(settings, "DEMO_ENABLED", True)
    token = create_jwt("42", purpose="intervals_oauth")
    assert await get_current_user(authorization=f"Bearer {token}") is None


async def test_per_ip_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_ENABLED", True)
    owner = SimpleNamespace(chat_id="42")
    with patch("api.routers.auth.User.get_owner", new=AsyncMock(return_value=owner)):
        for _ in range(_DEMO_MAX_ATTEMPTS):
            await auth_demo(_request("198.51.100.1"))
        with pytest.raises(HTTPException) as exc:
            await auth_demo(_request("198.51.100.1"))
        assert exc.value.status_code == 429
        # Another IP is unaffected.
        out = await auth_demo(_request("198.51.100.2"))
    assert out["role"] == "demo"
