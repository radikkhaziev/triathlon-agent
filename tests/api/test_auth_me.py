"""Tests for GET /api/auth/me — bot-chat fields exposure (issue #266).

The frontend's banner (App.tsx) and Settings/OnboardingPrompt CTAs read
``bot_chat_initialized`` + ``bot_username`` from this endpoint. If the
contract drifts, the entire UX layer of the #266 fix silently breaks (no
backend gate change, but the user never sees the /start prompt).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routers.auth import auth_me

pytestmark = pytest.mark.real_db  # opt out of per-test DB truncate


def _user(
    *,
    user_id: int = 1,
    chat_id: str = "42",
    role: str = "athlete",
    athlete_id: str | None = "i001",
    bot_chat_initialized: bool = True,
    intervals_auth_method: str = "oauth",
    sports: list[str] | None = None,
    display_name: str | None = "Radik Khaziev",
    username: str | None = "radik",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        chat_id=chat_id,
        role=role,
        athlete_id=athlete_id,
        bot_chat_initialized=bot_chat_initialized,
        intervals_auth_method=intervals_auth_method,
        intervals_oauth_scope=None,
        language="ru",
        sports=sports,
        display_name=display_name,
        username=username,
    )


def _thresholds() -> SimpleNamespace:
    """Threshold stub. Tests don't care about per-sport thresholds; auth_me
    just forwards a few fields onto its response."""
    return SimpleNamespace(
        age=None,
        lthr_run=None,
        lthr_bike=None,
        ftp=None,
        css=None,
        threshold_pace_run=None,
    )


@pytest.fixture
def _stub_athlete_lookups():
    """``auth_me`` calls get_thresholds + get_goal_dto + get_all +
    get_latest_weight + get_latest_vo2max. Tests in the bot-chat/sports
    classes don't care about profile internals — return empty stubs so the
    body builds without touching the real DB."""
    with (
        patch("api.routers.auth.AthleteSettings.get_thresholds", new=AsyncMock(return_value=_thresholds())),
        patch("api.routers.auth.AthleteGoal.get_goal_dto", new=AsyncMock(return_value=None)),
        patch("api.routers.auth.AthleteSettings.get_all", new=AsyncMock(return_value=[])),
        patch("api.routers.auth.Wellness.get_latest_weight", new=AsyncMock(return_value=None)),
        patch("api.routers.auth.Wellness.get_latest_vo2max", new=AsyncMock(return_value=None)),
    ):
        yield


class TestBotChatFieldsExposed:
    @pytest.mark.asyncio
    async def test_bot_chat_initialized_true_propagates(self, _stub_athlete_lookups):
        result = await auth_me(user=_user(bot_chat_initialized=True))
        assert result["bot_chat_initialized"] is True

    @pytest.mark.asyncio
    async def test_bot_chat_initialized_false_propagates(self, _stub_athlete_lookups):
        """The False case is the entire point — frontend banner depends on it."""
        result = await auth_me(user=_user(bot_chat_initialized=False))
        assert result["bot_chat_initialized"] is False

    @pytest.mark.asyncio
    async def test_bot_username_exposed_from_settings(self, _stub_athlete_lookups):
        with patch("api.routers.auth.settings") as mock_settings:
            mock_settings.TELEGRAM_BOT_USERNAME = "endurai_test_bot"
            result = await auth_me(user=_user())
        assert result["bot_username"] == "endurai_test_bot"

    @pytest.mark.asyncio
    async def test_demo_role_pins_bot_chat_initialized_true(self, _stub_athlete_lookups):
        """Demo browses owner data read-only; no Telegram I/O ever happens, so
        a False value would surface a meaningless /start CTA. Pin to True."""
        result = await auth_me(user=_user(role="demo", bot_chat_initialized=False))
        assert result["bot_chat_initialized"] is True


class TestSportsExposed:
    """`sports` drives the SportsPicker gate in App.tsx — NULL = show picker."""

    @pytest.mark.asyncio
    async def test_sports_null_propagates(self, _stub_athlete_lookups):
        result = await auth_me(user=_user(sports=None))
        assert result["sports"] is None

    @pytest.mark.asyncio
    async def test_sports_value_propagates(self, _stub_athlete_lookups):
        result = await auth_me(user=_user(sports=["run", "ride"]))
        assert result["sports"] == ["run", "ride"]

    @pytest.mark.asyncio
    async def test_demo_role_pins_sports(self, _stub_athlete_lookups):
        """Demo never passes through SportsPicker — pin to all three so the
        gate releases immediately on the demo tour."""
        result = await auth_me(user=_user(role="demo", sports=None))
        assert result["sports"] == ["ride", "run", "swim"]


class TestIdentityExposed:
    """`display_name` + `username` drive the Settings identity card. Sourced
    from the *authenticated* user (Telegram first+last), NOT data_uid."""

    @pytest.mark.asyncio
    async def test_identity_propagates(self, _stub_athlete_lookups):
        result = await auth_me(user=_user(display_name="Radik Khaziev", username="radik"))
        assert result["display_name"] == "Radik Khaziev"
        assert result["username"] == "radik"

    @pytest.mark.asyncio
    async def test_identity_null_for_legacy_rows(self, _stub_athlete_lookups):
        """Legacy rows (created via CLI / before the column) carry no Telegram
        identity — the field is null and the UI falls back to the id monogram."""
        result = await auth_me(user=_user(display_name=None, username=None))
        assert result["display_name"] is None
        assert result["username"] is None

    @pytest.mark.asyncio
    async def test_demo_role_scrubs_identity(self, _stub_athlete_lookups):
        """Демо JWT минтится с owner's chat_id → `get_current_user` возвращает
        OWNER User row. Без scrub'а демо-сессия отдавала бы реальные
        owner.display_name/username в /api/auth/me → leak в Settings/Sidebar.
        Контракт: demo-блок pin'ит identity в None (как `intervals.athlete_id`
        → "demo")."""
        result = await auth_me(user=_user(role="demo", display_name="Radik Khaziev", username="radik"))
        assert result["display_name"] is None
        assert result["username"] is None


class TestProfilePersonalFields:
    """G1=B: the Halo Personal card reads ``profile.weight`` +
    ``profile.hr_max`` (per-sport, read-only). If this contract drifts the
    card silently renders blanks — there's no other backend signal."""

    @pytest.mark.asyncio
    async def test_hr_max_maps_per_sport(self):
        rows = [
            SimpleNamespace(sport="Run", max_hr=190),
            SimpleNamespace(sport="Ride", max_hr=182),
            SimpleNamespace(sport="Swim", max_hr=170),
        ]
        with (
            patch("api.routers.auth.AthleteSettings.get_thresholds", new=AsyncMock(return_value=_thresholds())),
            patch("api.routers.auth.AthleteGoal.get_goal_dto", new=AsyncMock(return_value=None)),
            patch("api.routers.auth.AthleteSettings.get_all", new=AsyncMock(return_value=rows)),
            patch("api.routers.auth.Wellness.get_latest_weight", new=AsyncMock(return_value=72.5)),
            patch("api.routers.auth.Wellness.get_latest_vo2max", new=AsyncMock(return_value=48.5)),
        ):
            result = await auth_me(user=_user())
        assert result["profile"]["hr_max"] == {"run": 190, "bike": 182, "swim": 170}
        assert result["profile"]["weight"] == 72.5
        assert result["profile"]["vo2max"] == 48.5

    @pytest.mark.asyncio
    async def test_missing_sport_and_weight_are_none(self, _stub_athlete_lookups):
        """Empty ``get_all`` + no weight/vo2max row → all keys present, valued
        None (frontend renders an em-dash, never KeyErrors)."""
        result = await auth_me(user=_user())
        assert result["profile"]["hr_max"] == {"run": None, "bike": None, "swim": None}
        assert result["profile"]["weight"] is None
        assert result["profile"]["vo2max"] is None

    @pytest.mark.asyncio
    async def test_partial_sport_keeps_other_keys_none(self):
        """Only Run configured → its key fills, Bike/Swim stay None (the
        frontend per-tile ``?? '—'`` depends on every key always existing)."""
        with (
            patch("api.routers.auth.AthleteSettings.get_thresholds", new=AsyncMock(return_value=_thresholds())),
            patch("api.routers.auth.AthleteGoal.get_goal_dto", new=AsyncMock(return_value=None)),
            patch(
                "api.routers.auth.AthleteSettings.get_all",
                new=AsyncMock(return_value=[SimpleNamespace(sport="Run", max_hr=190)]),
            ),
            patch("api.routers.auth.Wellness.get_latest_weight", new=AsyncMock(return_value=None)),
            patch("api.routers.auth.Wellness.get_latest_vo2max", new=AsyncMock(return_value=None)),
        ):
            result = await auth_me(user=_user())
        assert result["profile"]["hr_max"] == {"run": 190, "bike": None, "swim": None}
        assert result["profile"]["weight"] is None
        assert result["profile"]["vo2max"] is None


class TestAvatarUrlExposed:
    """`avatar_url` is the URL of the authenticated avatar endpoint when the
    cached file exists; null when missing. Direct /static/avatar/* access is
    blocked at server.py — the URL is intentionally chat_id-free so it can't
    be used to fetch another user's photo by guessing IDs."""

    @pytest.mark.asyncio
    async def test_returns_url_when_avatar_file_exists(self, _stub_athlete_lookups, tmp_path, monkeypatch):
        avatar_dir = tmp_path / "avatar"
        avatar_dir.mkdir()
        (avatar_dir / "42.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        monkeypatch.setattr("data.avatar_storage.AVATAR_DIR", str(avatar_dir))

        result = await auth_me(user=_user(chat_id="42"))
        assert result["avatar_url"] == "/api/auth/avatar"

    @pytest.mark.asyncio
    async def test_returns_null_when_no_avatar_file(self, _stub_athlete_lookups, tmp_path, monkeypatch):
        """User revoked photo access → actor wiped the file → API drops the URL."""
        empty_dir = tmp_path / "avatar"
        empty_dir.mkdir()
        monkeypatch.setattr("data.avatar_storage.AVATAR_DIR", str(empty_dir))

        result = await auth_me(user=_user(chat_id="42"))
        assert result["avatar_url"] is None

    @pytest.mark.asyncio
    async def test_demo_role_scrubs_avatar_url(self, _stub_athlete_lookups, tmp_path, monkeypatch):
        """Demo session reuses the owner's User row; serving the owner's photo
        would leak PII the same way display_name/username do."""
        avatar_dir = tmp_path / "avatar"
        avatar_dir.mkdir()
        (avatar_dir / "42.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        monkeypatch.setattr("data.avatar_storage.AVATAR_DIR", str(avatar_dir))

        result = await auth_me(user=_user(role="demo", chat_id="42"))
        assert result["avatar_url"] is None
