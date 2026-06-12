"""Tests for ``api/deps.py:get_current_user`` against the stale-deactivation
flow.

T14 policy (`docs/MULTI_TENANT_SECURITY_SPEC.md` §T14): the webapp / JWT-Bearer
path **does NOT** reactivate dormant users — a webapp request isn't a
re-engagement signal (a bot-blocked user may still have a JWT cookie open).
Only bot-side interactions (`bot/decorator.py:_wake_user`, `/start`,
`my_chat_member` MEMBER) reactivate.

But the JWT path still uses ``include_inactive=True`` on the lookup — so a
dormant user can fetch ``/api/auth/me`` and see ``authenticated: true`` with
the "paused" state, rather than getting a misleading 401-loop. The follow-up
deps (``require_athlete`` / ``require_owner``) then bounce them with a 403
that the frontend can translate to a "your account is paused" message.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _user(*, is_active: bool, role: str = "athlete", athlete_id: str | None = "i001") -> MagicMock:
    """ORM-shaped User stand-in. MagicMock so tests can check whether the
    code under test mutated ``is_active`` (it must not, per T14)."""
    user = MagicMock()
    user.id = 1
    user.chat_id = "999"
    user.athlete_id = athlete_id
    user.is_active = is_active
    user.role = role
    user.language = "ru"
    return user


@pytest.fixture
def jwt_token():
    """Stub `verify_jwt` to return a chat_id with a plain session token
    (purpose=None — the only non-demo purpose session auth accepts; foreign
    purposes like OAuth state are rejected outright). Tests that need demo
    purpose override via their own patch."""
    with patch("api.deps.verify_jwt", return_value=("999", None)):
        yield "Bearer fake-jwt"


class TestDormantUserViaJwt:
    @pytest.mark.asyncio
    async def test_dormant_user_is_found_not_reactivated(self, jwt_token):
        """Dormant user opens webapp → ``get_by_chat_id`` returns the row
        (include_inactive=True), but is_active stays False and
        set_active_by_chat_id is NEVER called. This is the T14 contract:
        webapp visits don't prove the bot channel works."""
        from api.deps import get_current_user

        user = _user(is_active=False)

        with (
            patch("api.deps.User.get_by_chat_id", new=AsyncMock(return_value=user)) as get_by_chat_id,
            patch("api.deps.User.set_active_by_chat_id", new=AsyncMock()) as set_active,
            patch("api.deps.User.touch_last_action", new=AsyncMock()) as touch,
        ):
            result = await get_current_user(authorization=jwt_token)

        # User returned for the dependency chain — `require_athlete` will
        # bounce with 403 downstream, but `/api/auth/me` (`require_viewer`)
        # gets through and surfaces the row to the frontend.
        assert result is user
        assert user.is_active is False  # NO mutation
        set_active.assert_not_awaited()
        # Touch is gated on `is_active` — dormant users don't keep ticking
        # the cron's idle clock forward.
        touch.assert_not_awaited()
        # The lookup MUST use include_inactive=True, otherwise the JWT path
        # would 401 a perfectly-authenticated-but-deactivated user.
        get_by_chat_id.assert_awaited_once_with("999", include_inactive=True)

    @pytest.mark.asyncio
    async def test_active_user_gets_touched(self, jwt_token):
        """Sanity: the same path for an active user still bumps last_action_at
        (this is what the stale-cron reads to decide deactivation)."""
        from api.deps import get_current_user

        user = _user(is_active=True)

        with (
            patch("api.deps.User.get_by_chat_id", new=AsyncMock(return_value=user)),
            patch("api.deps.User.set_active_by_chat_id", new=AsyncMock()) as set_active,
            patch("api.deps.User.touch_last_action", new=AsyncMock()) as touch,
        ):
            await get_current_user(authorization=jwt_token)

        set_active.assert_not_awaited()  # already active, no flip needed
        touch.assert_awaited_once_with(user.id)

    @pytest.mark.asyncio
    async def test_demo_role_skips_touch_and_wake(self):
        """Demo JWT minted with owner's chat_id → resolves to owner User.
        Touching the owner's row from any demo session would falsely keep
        them "alive" in the cron's eyes; the touch must skip. Wake-up also
        skips (the owner isn't dormant in any realistic demo path)."""
        from api.deps import get_current_user

        user = _user(is_active=True, role="athlete")  # the owner's real role

        with (
            patch("api.deps.verify_jwt", return_value=("999", "demo")),
            patch("api.deps.User.get_by_chat_id", new=AsyncMock(return_value=user)),
            patch("api.deps.User.set_active_by_chat_id", new=AsyncMock()) as set_active,
            patch("api.deps.User.touch_last_action", new=AsyncMock()) as touch,
        ):
            result = await get_current_user(authorization="Bearer demo-jwt")

        assert result.role == "demo"  # virtual role pinned
        touch.assert_not_awaited()
        set_active.assert_not_awaited()
