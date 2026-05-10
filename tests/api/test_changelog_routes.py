"""Tests for /api/changelog/latest — see docs/WEEKLY_CHANGELOG_SPEC.md §14.

We patch ``api.routers.changelog._fetch_latest_discussion`` instead of going
through httpx — keeps tests fast and decoupled from GraphQL response shape.
The cache is module-level state, so each test resets it explicitly via the
``fresh_cache`` fixture.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest  # noqa: F401  — pytest-asyncio collects via marker auto-discovery
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import require_viewer
from api.routers import changelog as changelog_module
from api.routers.changelog import router as changelog_router


def _build_client(role: str = "owner") -> AsyncClient:
    """ASGI test client with ``require_viewer`` overridden to a stub user."""
    test_app = FastAPI()
    test_app.include_router(changelog_router)

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.role = role
    mock_user.is_active = True
    test_app.dependency_overrides[require_viewer] = lambda: mock_user
    return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    """Reset the module-level cache before every test — module state would
    otherwise leak the previous test's value through the 1h TTL."""
    changelog_module._CACHE["value"] = None
    changelog_module._CACHE["expires_at"] = 0.0


@pytest.fixture
def enabled_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(changelog_module.settings, "CHANGELOG_DISCUSSION_CATEGORY_ID", "DIC_test")
    monkeypatch.setattr(changelog_module.settings, "GITHUB_TOKEN", SecretStr("ghp_test"))
    monkeypatch.setattr(changelog_module.settings, "GITHUB_REPO", "x/y")


# --------------------------------------------------------------------------- #
# Spec §14 — happy path, 404, cache, 503, demo viewer.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_returns_latest_discussion_shape(enabled_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch() -> dict:
        return {
            "url": "https://github.com/x/y/discussions/100",
            "title": "✨ Что нового — неделя 04–10 мая 2026",
            "published_at": "2026-05-10T13:00:00Z",
        }

    monkeypatch.setattr(changelog_module, "_fetch_latest_discussion", fake_fetch)

    async with _build_client() as client:
        resp = await client.get("/api/changelog/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == "https://github.com/x/y/discussions/100"
    assert body["title"] == "✨ Что нового — неделя 04–10 мая 2026"
    assert body["published_at"] == "2026-05-10T13:00:00Z"


@pytest.mark.asyncio
async def test_returns_404_when_no_discussions_yet(enabled_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch() -> None:
        return None

    monkeypatch.setattr(changelog_module, "_fetch_latest_discussion", fake_fetch)

    async with _build_client() as client:
        resp = await client.get("/api/changelog/latest")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_caches_response_for_one_hour(enabled_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call must NOT re-fetch — actor publishes weekly, no point dialing
    GitHub on every page load."""
    call_count = {"n": 0}

    async def fake_fetch() -> dict:
        call_count["n"] += 1
        return {"url": "u", "title": "t", "published_at": "2026-05-10T13:00:00Z"}

    monkeypatch.setattr(changelog_module, "_fetch_latest_discussion", fake_fetch)

    async with _build_client() as client:
        r1 = await client.get("/api/changelog/latest")
        r2 = await client.get("/api/changelog/latest")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_caches_404_so_empty_repo_does_not_burn_calls(
    enabled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh repo with no Discussion: 404 must be cached too, otherwise every
    page load hits GitHub until first publish lands."""
    call_count = {"n": 0}

    async def fake_fetch() -> None:
        call_count["n"] += 1
        return None

    monkeypatch.setattr(changelog_module, "_fetch_latest_discussion", fake_fetch)

    async with _build_client() as client:
        r1 = await client.get("/api/changelog/latest")
        r2 = await client.get("/api/changelog/latest")
    assert r1.status_code == 404
    assert r2.status_code == 404
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_returns_503_with_retry_after_when_github_unreachable(
    enabled_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom() -> dict:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(changelog_module, "_fetch_latest_discussion", boom)

    async with _build_client() as client:
        resp = await client.get("/api/changelog/latest")
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "300"


@pytest.mark.asyncio
async def test_503_does_not_poison_cache(enabled_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Transient GitHub failure must NOT lock the cache — next request retries."""
    call_count = {"n": 0}

    async def flaky() -> dict:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectError("transient")
        return {"url": "u", "title": "t", "published_at": "2026-05-10T13:00:00Z"}

    monkeypatch.setattr(changelog_module, "_fetch_latest_discussion", flaky)

    async with _build_client() as client:
        r1 = await client.get("/api/changelog/latest")
        r2 = await client.get("/api/changelog/latest")
    assert r1.status_code == 503
    assert r2.status_code == 200
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_returns_404_when_feature_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty env vars → 404 (sidebar hides link), no GitHub call."""
    from pydantic import SecretStr

    monkeypatch.setattr(changelog_module.settings, "CHANGELOG_DISCUSSION_CATEGORY_ID", "")
    monkeypatch.setattr(changelog_module.settings, "GITHUB_TOKEN", SecretStr(""))

    called = {"fetched": False}

    async def fail_fetch() -> dict:
        called["fetched"] = True
        raise AssertionError("fetch must not be called when feature disabled")

    monkeypatch.setattr(changelog_module, "_fetch_latest_discussion", fail_fetch)

    async with _build_client() as client:
        resp = await client.get("/api/changelog/latest")
    assert resp.status_code == 404
    assert called["fetched"] is False


@pytest.mark.asyncio
async def test_demo_viewer_can_read(enabled_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """``require_viewer`` accepts demo role — read-only contract per §11."""

    async def fake_fetch() -> dict:
        return {"url": "u", "title": "t", "published_at": "2026-05-10T13:00:00Z"}

    monkeypatch.setattr(changelog_module, "_fetch_latest_discussion", fake_fetch)

    async with _build_client(role="demo") as client:
        resp = await client.get("/api/changelog/latest")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_cache_is_shared_across_users(enabled_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """M1 — Discussion is a single per-repo resource; cache must be global,
    not per-user. A regression keying ``_CACHE[user.id]`` would cause user A's
    fill to miss for user B and double the GitHub traffic. With a global cache,
    user B hits the warm value and ``call_count`` stays at 1.
    """
    call_count = {"n": 0}

    async def fake_fetch() -> dict:
        call_count["n"] += 1
        return {"url": "u", "title": "t", "published_at": "2026-05-10T13:00:00Z"}

    monkeypatch.setattr(changelog_module, "_fetch_latest_discussion", fake_fetch)

    # User A (id=1) fills the cache.
    app_a = FastAPI()
    app_a.include_router(changelog_router)
    user_a = MagicMock(id=1, role="owner", is_active=True)
    app_a.dependency_overrides[require_viewer] = lambda: user_a
    async with AsyncClient(transport=ASGITransport(app=app_a), base_url="http://test") as client:
        r1 = await client.get("/api/changelog/latest")
    assert r1.status_code == 200

    # User B (id=42) — different id, must hit the cached value.
    app_b = FastAPI()
    app_b.include_router(changelog_router)
    user_b = MagicMock(id=42, role="owner", is_active=True)
    app_b.dependency_overrides[require_viewer] = lambda: user_b
    async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://test") as client:
        r2 = await client.get("/api/changelog/latest")
    assert r2.status_code == 200
    assert r2.json() == r1.json()
    assert call_count["n"] == 1, "cache must be global, not per-user"
