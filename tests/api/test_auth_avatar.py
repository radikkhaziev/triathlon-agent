"""Tests for GET /api/auth/avatar — authenticated avatar serving.

The cached PNG sits in `static/avatar/{chat_id}.png` (shared docker volume
with the worker), but direct `/static/avatar/*` access is blocked at the
server layer (see `api/server.py`). Bytes are served only via this
endpoint so the demo scrub applies — without it, anyone who can guess a
chat_id would bypass the JSON-level avatar_url scrub in /auth/me.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.routers.auth import auth_avatar

pytestmark = pytest.mark.real_db


def _user(*, role: str = "athlete", chat_id: str = "42") -> SimpleNamespace:
    return SimpleNamespace(id=1, chat_id=chat_id, role=role)


class TestAuthAvatar:
    @pytest.mark.asyncio
    async def test_serves_file_for_authenticated_user(self, tmp_path, monkeypatch):
        avatar_dir = tmp_path / "avatar"
        avatar_dir.mkdir()
        png = avatar_dir / "42.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        monkeypatch.setattr("data.avatar_storage.AVATAR_DIR", str(avatar_dir))

        resp = await auth_avatar(user=_user(chat_id="42"))
        # FastAPI FileResponse — path is the absolute file location.
        assert resp.path == str(png)
        assert resp.media_type == "image/png"
        # Private cache so avatars don't end up on CDN edges.
        assert "private" in resp.headers["cache-control"]

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_404(self, tmp_path, monkeypatch):
        """No user in session → 404 (deliberately same status as missing-file,
        no info leak about which case fired)."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await auth_avatar(user=None)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_demo_role_returns_404(self, tmp_path, monkeypatch):
        """Demo session reuses owner's chat_id — serving the file would leak
        the owner's photo. Same scrub as `/auth/me`'s avatar_url."""
        avatar_dir = tmp_path / "avatar"
        avatar_dir.mkdir()
        (avatar_dir / "42.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        monkeypatch.setattr("data.avatar_storage.AVATAR_DIR", str(avatar_dir))

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await auth_avatar(user=_user(role="demo", chat_id="42"))
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_file_returns_404(self, tmp_path, monkeypatch):
        """Authenticated but no cached file (actor never ran, or user revoked
        photo access) → 404. UI's <img onError> drops the URL and falls back
        to initials."""
        empty_dir = tmp_path / "avatar"
        empty_dir.mkdir()
        monkeypatch.setattr("data.avatar_storage.AVATAR_DIR", str(empty_dir))

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await auth_avatar(user=_user(chat_id="42"))
        assert exc_info.value.status_code == 404


class TestPublicStaticAvatarBlocked:
    """Regression guard against the W1 PII leak: `/static/avatar/*` must NOT
    be served by the StaticFiles mount. The block lives in `api/server.py` as
    a pre-mount route that 404s the whole subpath. Other /static/* subpaths
    must continue to serve normally."""

    def test_public_avatar_path_returns_404(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from api.server import app

        client = TestClient(app)
        r = client.get("/static/avatar/123456.png")
        assert r.status_code == 404

    def test_other_static_subpaths_not_blocked(self):
        """Sanity: only /static/avatar/* is blocked. Existing /static/uploads,
        /static/fit-files etc. still hit StaticFiles (which will 404 on a
        non-existent file — but reach the mount, not the blocking route)."""
        from fastapi.testclient import TestClient

        from api.server import app

        client = TestClient(app)
        # 404 from StaticFiles is expected (file doesn't exist), the point is
        # we're NOT hitting the avatar blocker — that's enough to prove
        # the block is narrow.
        r = client.get("/static/uploads/nonexistent.txt")
        assert r.status_code == 404

    def test_avatar_endpoint_has_no_chat_id_parameter(self):
        """Regression guard: `/api/auth/avatar` must serve the session-owner's
        avatar only — no path/query parameter for ``chat_id``. Otherwise any
        viewer could enumerate chat_ids and read foreign avatars, defeating
        the demo scrub. Catches a future `/api/auth/avatar/{chat_id}` admin
        endpoint added without the matching tenant guard.
        """
        from api.routers.auth import router

        avatar_routes = [r for r in router.routes if "avatar" in getattr(r, "path", "")]
        assert avatar_routes, "no avatar route registered — check router import"
        for route in avatar_routes:
            assert "{" not in route.path, (
                f"avatar route {route.path!r} must be parameter-free; a tenant-id "
                "in the URL re-opens the enumeration attack the blocker exists to close"
            )
