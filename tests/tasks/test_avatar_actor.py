"""Tests for tasks/actors/avatars.py — Telegram avatar sync."""

import io
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
from PIL import Image

from data.db import UserDTO
from tasks.actors import avatars


@pytest.fixture
def tmp_avatar_dir(tmp_path, monkeypatch):
    """Redirect the avatar dir to a per-test tmpdir. The actor reads from the
    shared `data.avatar_storage.AVATAR_DIR` constant — patching there flows to
    both the actor (via `avatar_path()`) and any other reader (e.g. the API)."""
    target = tmp_path / "avatar"
    monkeypatch.setattr("data.avatar_storage.AVATAR_DIR", str(target))
    return target


def _user(chat_id: str = "42") -> UserDTO:
    return UserDTO(id=1, chat_id=chat_id, username="rad", language="ru")


def _png_bytes() -> bytes:
    """Smallest viable PNG — 1×1 red pixel."""
    img = Image.new("RGB", (1, 1), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _patch_settings(monkeypatch):
    """Stub TELEGRAM_BOT_TOKEN so the actor doesn't need a real .env."""
    fake_token = MagicMock()
    fake_token.get_secret_value = MagicMock(return_value="test-token")
    monkeypatch.setattr(avatars.settings, "TELEGRAM_BOT_TOKEN", fake_token)


def _mock_httpx(handler):
    """Wrap an httpx MockTransport in a context-manager that drops in as
    ``httpx.Client(...)``."""
    transport = httpx.MockTransport(handler)

    class _Client(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs.pop("timeout", None)
            super().__init__(transport=transport, **kwargs)

    return _Client


class TestActorDownloadUserAvatar:
    def test_happy_path_writes_png(self, tmp_avatar_dir, monkeypatch):
        _patch_settings(monkeypatch)

        def handler(request):
            url = str(request.url)
            if "getUserProfilePhotos" in url:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "total_count": 1,
                            "photos": [[{"file_id": "small"}, {"file_id": "big"}]],
                        },
                    },
                )
            if "getFile" in url:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {"file_path": "photos/file_42.jpg"},
                    },
                )
            # Final binary download.
            return httpx.Response(200, content=_png_bytes())

        monkeypatch.setattr(avatars.httpx, "Client", _mock_httpx(handler))

        avatars.actor_download_user_avatar(_user("42").model_dump())

        target = tmp_avatar_dir / "42.png"
        assert target.is_file()
        assert target.stat().st_size > 0
        # Sanity: file is a real PNG, openable.
        Image.open(target).verify()

    def test_no_photo_deletes_existing_local_file(self, tmp_avatar_dir, monkeypatch):
        """User revoked photo access → local cache must be wiped, else the UI
        keeps showing the old avatar after privacy settings change."""
        _patch_settings(monkeypatch)
        os.makedirs(tmp_avatar_dir, exist_ok=True)
        stale = tmp_avatar_dir / "42.png"
        stale.write_bytes(b"old-png-bytes")
        assert stale.is_file()

        def handler(request):
            return httpx.Response(200, json={"ok": True, "result": {"total_count": 0, "photos": []}})

        monkeypatch.setattr(avatars.httpx, "Client", _mock_httpx(handler))

        avatars.actor_download_user_avatar(_user("42").model_dump())

        assert not stale.exists()

    def test_telegram_not_ok_deletes_local(self, tmp_avatar_dir, monkeypatch):
        """ok=false response (user blocked bot / hid photos) drops the cache."""
        _patch_settings(monkeypatch)
        os.makedirs(tmp_avatar_dir, exist_ok=True)
        (tmp_avatar_dir / "42.png").write_bytes(b"old")

        def handler(request):
            return httpx.Response(200, json={"ok": False, "description": "Forbidden: user not found"})

        monkeypatch.setattr(avatars.httpx, "Client", _mock_httpx(handler))

        avatars.actor_download_user_avatar(_user("42").model_dump())

        assert not (tmp_avatar_dir / "42.png").exists()

    def test_no_photo_no_existing_file_is_noop(self, tmp_avatar_dir, monkeypatch):
        """Don't crash when there's no avatar AND no local file to delete."""
        _patch_settings(monkeypatch)

        def handler(request):
            return httpx.Response(200, json={"ok": True, "result": {"total_count": 0, "photos": []}})

        monkeypatch.setattr(avatars.httpx, "Client", _mock_httpx(handler))

        # Should not raise.
        avatars.actor_download_user_avatar(_user("42").model_dump())
        assert not (tmp_avatar_dir / "42.png").exists()

    def test_network_error_raises_for_retry(self, tmp_avatar_dir, monkeypatch):
        """Transient HTTP errors propagate → dramatiq retries once. Existing
        cached avatar stays untouched in the meantime."""
        _patch_settings(monkeypatch)
        os.makedirs(tmp_avatar_dir, exist_ok=True)
        (tmp_avatar_dir / "42.png").write_bytes(b"existing")

        def handler(request):
            return httpx.Response(503)

        monkeypatch.setattr(avatars.httpx, "Client", _mock_httpx(handler))

        with pytest.raises(httpx.HTTPError):
            avatars.actor_download_user_avatar(_user("42").model_dump())

        # Old file preserved through the transient failure.
        assert (tmp_avatar_dir / "42.png").exists()

    def test_corrupted_image_does_not_retry(self, tmp_avatar_dir, monkeypatch):
        """Bad bytes from CDN — decode fails, swallow + report to Sentry. No
        retry storm: the same bytes would fail the same way next time."""
        _patch_settings(monkeypatch)

        def handler(request):
            url = str(request.url)
            if "getUserProfilePhotos" in url:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {"total_count": 1, "photos": [[{"file_id": "x"}]]},
                    },
                )
            if "getFile" in url:
                return httpx.Response(200, json={"ok": True, "result": {"file_path": "p"}})
            return httpx.Response(200, content=b"not-a-png")

        monkeypatch.setattr(avatars.httpx, "Client", _mock_httpx(handler))

        with patch.object(avatars.sentry_sdk, "capture_exception") as mock_sentry:
            avatars.actor_download_user_avatar(_user("42").model_dump())

        # No file written.
        assert not (tmp_avatar_dir / "42.png").exists()
        # Reported to Sentry so we notice if the rate of corruption climbs.
        mock_sentry.assert_called_once()

    def test_overwrites_existing_file_atomically(self, tmp_avatar_dir, monkeypatch):
        """Second sync replaces the cached PNG; no leftover .tmp file."""
        _patch_settings(monkeypatch)
        os.makedirs(tmp_avatar_dir, exist_ok=True)
        (tmp_avatar_dir / "42.png").write_bytes(b"old-content")

        def handler(request):
            url = str(request.url)
            if "getUserProfilePhotos" in url:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {"total_count": 1, "photos": [[{"file_id": "x"}]]},
                    },
                )
            if "getFile" in url:
                return httpx.Response(200, json={"ok": True, "result": {"file_path": "p"}})
            return httpx.Response(200, content=_png_bytes())

        monkeypatch.setattr(avatars.httpx, "Client", _mock_httpx(handler))

        avatars.actor_download_user_avatar(_user("42").model_dump())

        target = tmp_avatar_dir / "42.png"
        assert target.is_file()
        # New content replaces old; was b"old-content", now a valid PNG.
        assert target.read_bytes() != b"old-content"
        # tmp file cleaned up by os.replace — no leftover .tmp variants
        # regardless of the PID suffix the actor uses.
        leftovers = [p.name for p in tmp_avatar_dir.glob("42.png*") if p.name != "42.png"]
        assert leftovers == [], leftovers

    def test_oversized_download_aborts_without_writing(self, tmp_avatar_dir, monkeypatch):
        """Spoofed / mis-sized response over the byte cap must not write a file.
        Regression guard for the H1 (decompression-bomb / SSRF-MITM) hardening."""
        _patch_settings(monkeypatch)
        oversized = b"\x00" * (avatars._MAX_IMAGE_BYTES + 10)

        def handler(request):
            url = str(request.url)
            if "getUserProfilePhotos" in url:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {"total_count": 1, "photos": [[{"file_id": "x"}]]},
                    },
                )
            if "getFile" in url:
                return httpx.Response(200, json={"ok": True, "result": {"file_path": "p"}})
            return httpx.Response(200, content=oversized)

        monkeypatch.setattr(avatars.httpx, "Client", _mock_httpx(handler))

        # Should silently abort (logged warning), NOT raise.
        avatars.actor_download_user_avatar(_user("42").model_dump())

        assert not (tmp_avatar_dir / "42.png").exists()
        leftovers = list(tmp_avatar_dir.glob("42.png*"))
        assert leftovers == [], leftovers
