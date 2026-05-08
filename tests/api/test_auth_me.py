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
    role: str = "athlete",
    athlete_id: str | None = "i001",
    bot_chat_initialized: bool = True,
    intervals_auth_method: str = "oauth",
    sports: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        role=role,
        athlete_id=athlete_id,
        bot_chat_initialized=bot_chat_initialized,
        intervals_auth_method=intervals_auth_method,
        intervals_oauth_scope=None,
        language="ru",
        sports=sports,
    )


def _thresholds(*, available_sports: list[str] | None = None) -> SimpleNamespace:
    """Threshold stub. ``available_sports`` is computed inside the real
    get_thresholds from AthleteSettings rows; tests inject the result directly
    so they don't need to mock the full row-iteration path."""
    return SimpleNamespace(
        age=None,
        lthr_run=None,
        lthr_bike=None,
        ftp=None,
        css=None,
        threshold_pace_run=None,
        available_sports=available_sports if available_sports is not None else ["ride", "run", "swim"],
    )


@pytest.fixture
def _stub_athlete_lookups():
    """``auth_me`` calls AthleteSettings.get_thresholds + AthleteGoal.get_goal_dto.
    Tests don't care about thresholds/goal — return empty stubs so the body
    builds. ``available_sports`` defaults to all-three so the prefill path is
    exercised; tests override per-case via the helper above."""
    with (
        patch("api.routers.auth.AthleteSettings.get_thresholds", new=AsyncMock(return_value=_thresholds())),
        patch("api.routers.auth.AthleteGoal.get_goal_dto", new=AsyncMock(return_value=None)),
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
    """`sports` + `available_sports_from_settings` drive the SportsPicker gate
    in App.tsx (NULL=show picker) and the prefill checkboxes inside it."""

    @pytest.mark.asyncio
    async def test_sports_null_propagates(self, _stub_athlete_lookups):
        result = await auth_me(user=_user(sports=None))
        assert result["sports"] is None

    @pytest.mark.asyncio
    async def test_sports_value_propagates(self, _stub_athlete_lookups):
        result = await auth_me(user=_user(sports=["run", "ride"]))
        assert result["sports"] == ["run", "ride"]

    @pytest.mark.asyncio
    async def test_available_sports_full_triathlete(self, _stub_athlete_lookups):
        """Default fixture seeds Run/Ride/Swim AthleteSettings rows."""
        result = await auth_me(user=_user())
        assert result["available_sports_from_settings"] == ["ride", "run", "swim"]

    @pytest.mark.asyncio
    async def test_available_sports_run_only(self):
        """Runner with only a Run AthleteSettings row → picker prefills [run].

        get_thresholds derives available_sports from AthleteSettings rows; we
        inject the post-derivation value directly to keep the test focused on
        auth_me's response shape rather than re-testing the row-iteration."""
        with (
            patch(
                "api.routers.auth.AthleteSettings.get_thresholds",
                new=AsyncMock(return_value=_thresholds(available_sports=["run"])),
            ),
            patch("api.routers.auth.AthleteGoal.get_goal_dto", new=AsyncMock(return_value=None)),
        ):
            result = await auth_me(user=_user(sports=None))
        assert result["available_sports_from_settings"] == ["run"]

    @pytest.mark.asyncio
    async def test_available_sports_ignores_unmapped_disciplines(self):
        """Intervals.icu sport rows like `Workout` / `Yoga` aren't in our enum;
        they must NOT bleed into available_sports_from_settings.

        Real-DB coverage of the filter itself lives in
        ``tests/db/test_athlete_settings.py::TestGetThresholdsAvailableSports::
        test_filters_unmapped_intervals_disciplines``. Here we just assert
        auth_me forwards whatever the DTO carries."""
        with (
            patch(
                "api.routers.auth.AthleteSettings.get_thresholds",
                new=AsyncMock(return_value=_thresholds(available_sports=["run"])),
            ),
            patch("api.routers.auth.AthleteGoal.get_goal_dto", new=AsyncMock(return_value=None)),
        ):
            result = await auth_me(user=_user())
        assert result["available_sports_from_settings"] == ["run"]

    @pytest.mark.asyncio
    async def test_demo_role_pins_sports(self, _stub_athlete_lookups):
        """Demo never passes through SportsPicker — pin to all three so the
        gate releases immediately on the demo tour."""
        result = await auth_me(user=_user(role="demo", sports=None))
        assert result["sports"] == ["ride", "run", "swim"]

    @pytest.mark.asyncio
    async def test_sports_narrower_than_available(self):
        """Asymmetric case (TC2): athlete has Run+Ride+Swim AthleteSettings rows
        synced from Intervals.icu but has narrowed their picker selection to
        just ["run"]. `sports` reflects the narrowed choice; the prefill list
        still shows all three so the user can re-broaden later."""
        with (
            patch(
                "api.routers.auth.AthleteSettings.get_thresholds",
                new=AsyncMock(return_value=_thresholds(available_sports=["ride", "run", "swim"])),
            ),
            patch("api.routers.auth.AthleteGoal.get_goal_dto", new=AsyncMock(return_value=None)),
        ):
            result = await auth_me(user=_user(sports=["run"]))
        assert result["sports"] == ["run"]
        assert result["available_sports_from_settings"] == ["ride", "run", "swim"]
