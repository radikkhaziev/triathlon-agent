"""Unit tests for `bot.donate_nudge.should_show_nudge`.

Pure logic, no DB — we construct `User` instances in memory. The function
reads `config.settings` at call time, so we monkeypatch there.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.donate_nudge import NUDGE_MESSAGES, get_nudge_text, should_show_nudge


def _user(role: str = "athlete", last_donation_at: datetime | None = None) -> SimpleNamespace:
    """Minimal User-shaped object. `should_show_nudge` only reads `.role` and `.last_donation_at`."""
    return SimpleNamespace(role=role, last_donation_at=last_donation_at)


class TestShouldShowNudge:

    def test_boundary_false_blocks(self):
        assert should_show_nudge(_user(), nudge_boundary=False, request_count=5) is False

    def test_boundary_true_shows(self):
        assert should_show_nudge(_user(), nudge_boundary=True, request_count=5) is True

    def test_owner_shown_by_default(self, monkeypatch):
        monkeypatch.setattr("config.settings.DONATE_NUDGE_SKIP_OWNER", False)
        assert should_show_nudge(_user(role="owner"), nudge_boundary=True, request_count=5) is True

    def test_owner_suppressed_when_flag_on(self, monkeypatch):
        monkeypatch.setattr("config.settings.DONATE_NUDGE_SKIP_OWNER", True)
        assert should_show_nudge(_user(role="owner"), nudge_boundary=True, request_count=5) is False

    def test_owner_flag_doesnt_affect_athlete(self, monkeypatch):
        monkeypatch.setattr("config.settings.DONATE_NUDGE_SKIP_OWNER", True)
        assert should_show_nudge(_user(role="athlete"), nudge_boundary=True, request_count=5) is True

    def test_recent_donation_suppresses(self):
        recent = datetime.now(timezone.utc) - timedelta(days=3)
        assert should_show_nudge(_user(last_donation_at=recent), nudge_boundary=True, request_count=5) is False

    def test_old_donation_does_not_suppress(self):
        old = datetime.now(timezone.utc) - timedelta(days=30)
        assert should_show_nudge(_user(last_donation_at=old), nudge_boundary=True, request_count=5) is True

    def test_donation_just_outside_boundary_does_not_suppress(self, monkeypatch):
        # 7 days + 1 second ago → just outside the 7-day suppression window.
        # Not testing the exact equality boundary (that would require freezing
        # `datetime.now` inside should_show_nudge to avoid flakiness).
        monkeypatch.setattr("config.settings.DONATE_NUDGE_SUPPRESS_DAYS", 7)
        just_outside = datetime.now(timezone.utc) - timedelta(days=7, seconds=1)
        assert should_show_nudge(_user(last_donation_at=just_outside), nudge_boundary=True, request_count=5) is True

    def test_daily_cap_enforced(self, monkeypatch):
        # MAX_PER_DAY=2, EVERY_N=5 → show on 5 and 10, suppress on 15+
        monkeypatch.setattr("config.settings.DONATE_NUDGE_MAX_PER_DAY", 2)
        monkeypatch.setattr("config.settings.DONATE_NUDGE_EVERY_N", 5)
        assert should_show_nudge(_user(), nudge_boundary=True, request_count=5) is True
        assert should_show_nudge(_user(), nudge_boundary=True, request_count=10) is True
        assert should_show_nudge(_user(), nudge_boundary=True, request_count=15) is False
        assert should_show_nudge(_user(), nudge_boundary=True, request_count=20) is False

    def test_cap_respects_custom_n(self, monkeypatch):
        # With EVERY_N=10 and MAX=2, show on 10 and 20, suppress on 30
        monkeypatch.setattr("config.settings.DONATE_NUDGE_EVERY_N", 10)
        monkeypatch.setattr("config.settings.DONATE_NUDGE_MAX_PER_DAY", 2)
        assert should_show_nudge(_user(), nudge_boundary=True, request_count=10) is True
        assert should_show_nudge(_user(), nudge_boundary=True, request_count=20) is True
        assert should_show_nudge(_user(), nudge_boundary=True, request_count=30) is False


class TestGetNudgeText:

    def test_returns_from_pool(self):
        for _ in range(20):
            assert get_nudge_text() in NUDGE_MESSAGES

    def test_all_messages_contain_donate_command(self):
        for msg in NUDGE_MESSAGES:
            assert "/donate" in msg

    def test_all_messages_use_italic_markdown(self):
        # Each nudge should have matching `_..._` pair for italic rendering.
        for msg in NUDGE_MESSAGES:
            assert msg.count("_") >= 2
