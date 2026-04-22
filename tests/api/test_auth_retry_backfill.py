"""Tests for POST /api/auth/retry-backfill + helpers.

Covers the two-guard model: business cooldown (``_backfill_retry_retry_after``)
vs anti-spam rate limit (``_retry_backfill_last_success``). Also validates
``_sanitize_last_error`` — the allowlist that stops raw httpx exception
strings from leaking to the UI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.routers.auth import (
    _COMPLETED_DATA_COOLDOWN,
    _EMPTY_INTERVALS_COOLDOWN,
    _RETRY_BACKFILL_RATE_WINDOW_SEC,
    _backfill_retry_retry_after,
    _retry_backfill_last_success,
    _sanitize_last_error,
    auth_retry_backfill,
)

pytestmark = pytest.mark.real_db  # opt out of per-test DB truncate


def _user(
    *,
    user_id: int = 1,
    role: str = "athlete",
    athlete_id: str | None = "i001",
    intervals_auth_method: str = "oauth",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        role=role,
        athlete_id=athlete_id,
        intervals_auth_method=intervals_auth_method,
        is_active=True,
        language="ru",
    )


def _state(
    *,
    status: str = "completed",
    finished_at: datetime | None = None,
    last_error: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(status=status, finished_at=finished_at, last_error=last_error)


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    """Rate-limit dict is module-level — wipe between tests so each one sees
    a fresh "last_success=None" baseline."""
    _retry_backfill_last_success.clear()
    yield
    _retry_backfill_last_success.clear()


# ---------------------------------------------------------------------------
# _sanitize_last_error — allowlist
# ---------------------------------------------------------------------------


class TestSanitizeLastError:
    def test_none_passes_through(self):
        assert _sanitize_last_error(None) is None

    def test_known_sentinel_passes_through(self):
        assert _sanitize_last_error("EMPTY_INTERVALS") == "EMPTY_INTERVALS"
        assert _sanitize_last_error("watchdog_exhausted") == "watchdog_exhausted"
        assert _sanitize_last_error("OAuth revoked during backfill") == "OAuth revoked during backfill"

    def test_watchdog_kick_hidden(self):
        """In-flight watchdog counter is bookkeeping, not a user-facing error."""
        assert _sanitize_last_error("watchdog_kick_1") is None
        assert _sanitize_last_error("watchdog_kick_3") is None

    def test_unknown_collapsed_to_internal(self):
        """Defence against a future caller passing raw httpx error strings —
        URLs with query params might contain tokens."""
        assert _sanitize_last_error("HTTPError(403) at https://intervals.icu?key=SECRET") == "internal"
        assert _sanitize_last_error("ConnectionError") == "internal"
        assert _sanitize_last_error("") == "internal"


# ---------------------------------------------------------------------------
# _backfill_retry_retry_after — business cooldown
# ---------------------------------------------------------------------------


class TestBackfillRetryRetryAfter:
    def test_running_returns_none(self):
        state = _state(status="running", finished_at=None)
        assert _backfill_retry_retry_after(state, datetime.now(timezone.utc)) is None

    def test_failed_returns_none(self):
        state = _state(status="failed", finished_at=datetime.now(timezone.utc))
        assert _backfill_retry_retry_after(state, datetime.now(timezone.utc)) is None

    def test_completed_no_finished_at_returns_none(self):
        state = _state(status="completed", finished_at=None)
        assert _backfill_retry_retry_after(state, datetime.now(timezone.utc)) is None

    def test_completed_data_within_7d_returns_seconds(self):
        now = datetime.now(timezone.utc)
        state = _state(status="completed", finished_at=now - timedelta(days=3))
        remaining = _backfill_retry_retry_after(state, now)
        assert remaining is not None
        assert remaining > 0
        # 4 days left, roughly
        assert abs(remaining - int(timedelta(days=4).total_seconds())) < 60

    def test_completed_data_past_7d_returns_none(self):
        now = datetime.now(timezone.utc)
        state = _state(status="completed", finished_at=now - _COMPLETED_DATA_COOLDOWN - timedelta(seconds=1))
        assert _backfill_retry_retry_after(state, now) is None

    def test_empty_intervals_within_1h_returns_seconds(self):
        now = datetime.now(timezone.utc)
        state = _state(
            status="completed",
            finished_at=now - timedelta(minutes=15),
            last_error="EMPTY_INTERVALS",
        )
        remaining = _backfill_retry_retry_after(state, now)
        assert remaining is not None
        assert 2000 < remaining < 2800  # ~45 min left

    def test_empty_intervals_past_1h_returns_none(self):
        now = datetime.now(timezone.utc)
        state = _state(
            status="completed",
            finished_at=now - _EMPTY_INTERVALS_COOLDOWN - timedelta(seconds=1),
            last_error="EMPTY_INTERVALS",
        )
        assert _backfill_retry_retry_after(state, now) is None


# ---------------------------------------------------------------------------
# Endpoint — auth / authorization preconditions
# ---------------------------------------------------------------------------


class TestAuthRetryBackfillPreconditions:
    @pytest.mark.asyncio
    async def test_no_user_returns_401(self):
        with pytest.raises(HTTPException) as exc:
            await auth_retry_backfill(user=None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_demo_returns_403(self):
        """Demo reject MUST happen before rate-limit lookup — otherwise demo
        requests keyed by owner's user.id would drain owner's budget."""
        user = _user(role="demo")
        with pytest.raises(HTTPException) as exc:
            await auth_retry_backfill(user=user)
        assert exc.value.status_code == 403
        # Rate-limit dict must be untouched
        assert user.id not in _retry_backfill_last_success

    @pytest.mark.asyncio
    async def test_no_athlete_id_returns_400(self):
        with pytest.raises(HTTPException) as exc:
            await auth_retry_backfill(user=_user(athlete_id=None))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_intervals_none_returns_400(self):
        with pytest.raises(HTTPException) as exc:
            await auth_retry_backfill(user=_user(intervals_auth_method="none"))
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Endpoint — dual cooldown + rate limit behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatch_mocks():
    """Fixture that mocks everything downstream of the guard chain."""
    with (
        patch("api.routers.auth.UserBackfillState") as state_cls,
        patch("api.routers.auth.get_session") as session_cm,
        patch("api.routers.auth.actor_bootstrap_step") as actor,
        patch("api.routers.auth._UserDTOAdapter") as adapter,
    ):
        state_cls.get = AsyncMock(return_value=None)
        state_cls.start = AsyncMock()

        session = MagicMock()
        session.get = AsyncMock(return_value=SimpleNamespace(id=1))
        session_cm.return_value.__aenter__ = AsyncMock(return_value=session)
        session_cm.return_value.__aexit__ = AsyncMock(return_value=None)

        adapter.validate_python = MagicMock(return_value=_user())

        yield SimpleNamespace(state_cls=state_cls, actor=actor, session=session)


class TestAuthRetryBackfillFlow:
    @pytest.mark.asyncio
    async def test_happy_path_dispatches_and_records(self, dispatch_mocks):
        user = _user()
        result = await auth_retry_backfill(user=user)
        assert result["status"] == "running"
        dispatch_mocks.state_cls.start.assert_awaited_once()
        dispatch_mocks.actor.send.assert_called_once()
        # Anti-spam rate limit recorded
        assert user.id in _retry_backfill_last_success

    @pytest.mark.asyncio
    async def test_status_running_returns_409(self, dispatch_mocks):
        dispatch_mocks.state_cls.get = AsyncMock(return_value=_state(status="running"))
        with pytest.raises(HTTPException) as exc:
            await auth_retry_backfill(user=_user())
        assert exc.value.status_code == 409
        dispatch_mocks.actor.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_within_cooldown_returns_429(self, dispatch_mocks):
        now = datetime.now(timezone.utc)
        dispatch_mocks.state_cls.get = AsyncMock(
            return_value=_state(status="completed", finished_at=now - timedelta(days=2))
        )
        with pytest.raises(HTTPException) as exc:
            await auth_retry_backfill(user=_user())
        assert exc.value.status_code == 429
        assert "Retry-After" in (exc.value.headers or {})
        dispatch_mocks.actor.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_state_allowed(self, dispatch_mocks):
        """Failed is explicitly retryable — neither business cooldown nor
        'already running' guard should block."""
        dispatch_mocks.state_cls.get = AsyncMock(
            return_value=_state(
                status="failed",
                finished_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        )
        result = await auth_retry_backfill(user=_user())
        assert result["status"] == "running"
        dispatch_mocks.actor.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_anti_spam_rate_limit_blocks(self, dispatch_mocks):
        """Even a would-be-allowed retry is blocked if the user pinged the
        endpoint less than 1h ago. Tested by pre-seeding the dict."""
        import time

        user = _user()
        _retry_backfill_last_success[user.id] = time.monotonic()
        with pytest.raises(HTTPException) as exc:
            await auth_retry_backfill(user=user)
        assert exc.value.status_code == 429
        assert "Retry-After" in (exc.value.headers or {})
        dispatch_mocks.actor.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_window_elapsed_allows_retry(self, dispatch_mocks):
        import time

        user = _user()
        _retry_backfill_last_success[user.id] = time.monotonic() - _RETRY_BACKFILL_RATE_WINDOW_SEC - 10
        result = await auth_retry_backfill(user=user)
        assert result["status"] == "running"
        dispatch_mocks.actor.send.assert_called_once()
