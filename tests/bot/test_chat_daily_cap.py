"""Tests for ``bot.main._enforce_chat_daily_cap`` — chat daily-cap gate.

Pure logic + a mocked ``ApiUsageDaily.get_today_request_count`` and
``update.message.reply_text``. We don't go through Telegram or hit DB.

Two-tier model: non-donors get ``CHAT_DAILY_LIMIT`` (default 40), donors
(``last_donation_at`` within ``CHAT_DONOR_WINDOW_DAYS``) get
``CHAT_DAILY_LIMIT_DONOR`` (default 100). 0 = unlimited per tier.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.main import _enforce_chat_daily_cap, _is_donor


def _user(
    user_id: int = 1,
    role: str = "athlete",
    last_donation_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, role=role, last_donation_at=last_donation_at)


def _update_with_reply(reply_mock: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(message=SimpleNamespace(reply_text=reply_mock))


class TestChatDailyCap:
    @pytest.mark.asyncio
    async def test_disabled_when_limit_is_zero(self, monkeypatch):
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT", 0)
        reply = AsyncMock()
        # ``get_today_request_count`` must NOT be called when the gate is
        # disabled — the early-out saves a DB round-trip per message.
        with patch("bot.main.ApiUsageDaily.get_today_request_count", new=AsyncMock()) as mock_count:
            allowed = await _enforce_chat_daily_cap(_update_with_reply(reply), _user())

        assert allowed is True
        reply.assert_not_called()
        mock_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_when_under_cap(self, monkeypatch):
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT", 40)
        reply = AsyncMock()
        with patch("bot.main.ApiUsageDaily.get_today_request_count", new=AsyncMock(return_value=39)):
            allowed = await _enforce_chat_daily_cap(_update_with_reply(reply), _user())

        assert allowed is True
        reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocks_at_exactly_cap(self, monkeypatch):
        """39 used + cap=40 still allows. 40 used = the 41st request → blocked."""
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT", 40)
        reply = AsyncMock()
        with patch("bot.main.ApiUsageDaily.get_today_request_count", new=AsyncMock(return_value=40)):
            allowed = await _enforce_chat_daily_cap(_update_with_reply(reply), _user())

        assert allowed is False
        reply.assert_called_once()
        msg = reply.call_args.args[0]
        assert "40/40" in msg

    @pytest.mark.asyncio
    async def test_blocks_above_cap(self, monkeypatch):
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT", 40)
        reply = AsyncMock()
        with patch("bot.main.ApiUsageDaily.get_today_request_count", new=AsyncMock(return_value=120)):
            allowed = await _enforce_chat_daily_cap(_update_with_reply(reply), _user())

        assert allowed is False
        reply.assert_called_once()
        assert "120/40" in reply.call_args.args[0]

    @pytest.mark.asyncio
    async def test_owner_also_subject_to_cap(self, monkeypatch):
        """Cap is global anti-abuse, NOT a permission tier — owner is included.
        Regression guard: a future ``if user.role == 'owner': return True`` would
        re-introduce abuse-by-owner-account during testing."""
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT", 40)
        reply = AsyncMock()
        with patch("bot.main.ApiUsageDaily.get_today_request_count", new=AsyncMock(return_value=40)):
            allowed = await _enforce_chat_daily_cap(_update_with_reply(reply), _user(role="owner"))

        assert allowed is False
        reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_donor_stop_message_mentions_donor_cap(self, monkeypatch):
        """Soft conversion nudge: tell non-donors what they'd get for donating."""
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT", 40)
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT_DONOR", 100)
        reply = AsyncMock()
        with patch("bot.main.ApiUsageDaily.get_today_request_count", new=AsyncMock(return_value=40)):
            await _enforce_chat_daily_cap(_update_with_reply(reply), _user())

        msg = reply.call_args.args[0]
        assert "100" in msg
        assert "/donate" in msg

    @pytest.mark.asyncio
    async def test_non_donor_stop_message_when_donor_tier_unlimited(self, monkeypatch):
        """``CHAT_DAILY_LIMIT_DONOR=0`` (unlimited) — stop-message must NOT
        render '0/день' (reads as the opposite). Pitch unlimited explicitly."""
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT", 40)
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT_DONOR", 0)
        reply = AsyncMock()
        with patch("bot.main.ApiUsageDaily.get_today_request_count", new=AsyncMock(return_value=40)):
            await _enforce_chat_daily_cap(_update_with_reply(reply), _user())

        msg = reply.call_args.args[0]
        # The bug we're guarding against: rendering "лимит 0/день" — that
        # reads as the literal opposite of the intent. The non-donor cap
        # `40/40` contains `0/` so we can't grep for that; instead assert
        # the explicit unlimited copy is present.
        assert "безлимит" in msg
        assert "/donate" in msg


class TestDonorTier:
    @pytest.mark.asyncio
    async def test_donor_uses_higher_cap(self, monkeypatch):
        """Donor at 60 used + non-donor cap=40 — would be blocked as non-donor,
        but donor cap=100 lets them through."""
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT", 40)
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT_DONOR", 100)
        monkeypatch.setattr("config.settings.CHAT_DONOR_WINDOW_DAYS", 7)
        reply = AsyncMock()
        recent = datetime.now(timezone.utc) - timedelta(days=3)
        with patch("bot.main.ApiUsageDaily.get_today_request_count", new=AsyncMock(return_value=60)):
            allowed = await _enforce_chat_daily_cap(
                _update_with_reply(reply),
                _user(last_donation_at=recent),
            )

        assert allowed is True
        reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_donor_blocked_at_donor_cap(self, monkeypatch):
        """When the donor exhausts their tier, the stop-message is the simple
        one (no /donate nudge — they already donated)."""
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT", 40)
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT_DONOR", 100)
        monkeypatch.setattr("config.settings.CHAT_DONOR_WINDOW_DAYS", 7)
        reply = AsyncMock()
        recent = datetime.now(timezone.utc) - timedelta(days=3)
        with patch("bot.main.ApiUsageDaily.get_today_request_count", new=AsyncMock(return_value=100)):
            allowed = await _enforce_chat_daily_cap(
                _update_with_reply(reply),
                _user(last_donation_at=recent),
            )

        assert allowed is False
        msg = reply.call_args.args[0]
        assert "100/100" in msg
        assert "/donate" not in msg  # don't nudge a donor who just maxed their tier

    @pytest.mark.asyncio
    async def test_expired_donation_falls_back_to_non_donor_cap(self, monkeypatch):
        """Donation older than ``CHAT_DONOR_WINDOW_DAYS`` no longer counts."""
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT", 40)
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT_DONOR", 100)
        monkeypatch.setattr("config.settings.CHAT_DONOR_WINDOW_DAYS", 7)
        reply = AsyncMock()
        stale = datetime.now(timezone.utc) - timedelta(days=10)
        with patch("bot.main.ApiUsageDaily.get_today_request_count", new=AsyncMock(return_value=41)):
            allowed = await _enforce_chat_daily_cap(
                _update_with_reply(reply),
                _user(last_donation_at=stale),
            )

        assert allowed is False  # over 40 (non-donor cap)
        msg = reply.call_args.args[0]
        assert "41/40" in msg

    @pytest.mark.asyncio
    async def test_donor_unlimited_when_donor_cap_zero(self, monkeypatch):
        """0 = unlimited for that tier, independent of usage."""
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT", 40)
        monkeypatch.setattr("config.settings.CHAT_DAILY_LIMIT_DONOR", 0)
        monkeypatch.setattr("config.settings.CHAT_DONOR_WINDOW_DAYS", 7)
        reply = AsyncMock()
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        # ``get_today_request_count`` must NOT be called when the tier is
        # unlimited — DB round-trip skipped on every donor message.
        with patch("bot.main.ApiUsageDaily.get_today_request_count", new=AsyncMock()) as mock_count:
            allowed = await _enforce_chat_daily_cap(
                _update_with_reply(reply),
                _user(last_donation_at=recent),
            )

        assert allowed is True
        reply.assert_not_called()
        mock_count.assert_not_called()


class TestIsDonor:
    """``_is_donor`` is the source of truth for tier resolution. Pure logic,
    no DB. ``CHAT_DONOR_WINDOW_DAYS`` mirrors ``DONATE_NUDGE_SUPPRESS_DAYS``
    so a single donation grants exactly one week of both perks."""

    def test_no_donation_is_not_donor(self):
        assert _is_donor(_user(last_donation_at=None)) is False

    def test_recent_donation_is_donor(self, monkeypatch):
        monkeypatch.setattr("config.settings.CHAT_DONOR_WINDOW_DAYS", 7)
        recent = datetime.now(timezone.utc) - timedelta(days=3)
        assert _is_donor(_user(last_donation_at=recent)) is True

    def test_stale_donation_is_not_donor(self, monkeypatch):
        monkeypatch.setattr("config.settings.CHAT_DONOR_WINDOW_DAYS", 7)
        stale = datetime.now(timezone.utc) - timedelta(days=8)
        assert _is_donor(_user(last_donation_at=stale)) is False

    def test_at_window_boundary_is_not_donor(self, monkeypatch):
        """Donation EXACTLY at the window boundary falls outside — strict
        ``last_donation_at > cutoff`` comparison, mirroring
        ``bot/donate_nudge.should_show_nudge`` semantics. Catches a regression
        if anyone reverts to the old ``timedelta.days < N`` floor variant
        (which would round-trip the equality differently)."""
        monkeypatch.setattr("config.settings.CHAT_DONOR_WINDOW_DAYS", 7)
        boundary = datetime.now(timezone.utc) - timedelta(days=7, seconds=1)
        assert _is_donor(_user(last_donation_at=boundary)) is False
