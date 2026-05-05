"""Tests for mcp_server/tools/github.py — attribution + per-user rate limit + content caps."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limit_log():
    """Each test starts with an empty in-process counter — otherwise the
    sliding-window dict leaks state across tests in the same session."""
    from mcp_server.tools import github as gh

    gh._issue_create_log.clear()
    yield
    gh._issue_create_log.clear()


@pytest.fixture(autouse=True)
def _set_user_context():
    """Set ``mcp_user_id`` for the test and restore the previous value on
    teardown. ``mcp_server.context._current_user_id`` is a contextvar that
    leaks across tests in the same asyncio task — without restoration, a
    later test inherits ``user_id=42`` (or 100/101 below) and gets
    order-dependent failures that are painful to bisect."""
    from mcp_server.context import _current_user_id

    token = _current_user_id.set(42)
    yield
    _current_user_id.reset(token)


class TestAttribution:
    async def test_body_contains_only_user_id(self):
        """No `@username` and no `athlete_id` in the public-repo body —
        spec §13 (PII linkage avoidance). Only ``user_id=N`` survives."""
        from mcp_server.tools.github import create_github_issue

        with patch(
            "mcp_server.tools.github.create_issue",
            new=AsyncMock(return_value={"number": 1, "url": "x", "title": "x"}),
        ) as mock_create:
            await create_github_issue(title="Bug", body="something broke")

        call_kwargs = mock_create.await_args.kwargs
        body = call_kwargs["body"]
        assert "_Reported via MCP by user_id=42_" in body
        assert "@" not in body.split("---", 1)[1]  # no @username after the divider
        assert "athlete_id" not in body

    async def test_no_owner_gate(self):
        """Removing ``require_owner`` was the explicit feature change — any
        athlete with a valid mcp_token must be able to call this tool."""
        from mcp_server.tools.github import create_github_issue

        with patch(
            "mcp_server.tools.github.create_issue",
            new=AsyncMock(return_value={"number": 1, "url": "x", "title": "x"}),
        ):
            result = await create_github_issue(title="Title", body="body")

        assert "error" not in result


class TestContentCaps:
    async def test_title_truncated_to_max(self):
        from mcp_server.tools.github import _TITLE_MAX_LEN, create_github_issue

        with patch(
            "mcp_server.tools.github.create_issue",
            new=AsyncMock(return_value={"number": 1, "url": "x", "title": "x"}),
        ) as mock_create:
            await create_github_issue(title="A" * (_TITLE_MAX_LEN + 50), body="b")

        assert len(mock_create.await_args.kwargs["title"]) == _TITLE_MAX_LEN

    async def test_body_truncated_to_max_before_attribution(self):
        from mcp_server.tools.github import _BODY_MAX_LEN, create_github_issue

        with patch(
            "mcp_server.tools.github.create_issue",
            new=AsyncMock(return_value={"number": 1, "url": "x", "title": "x"}),
        ) as mock_create:
            await create_github_issue(title="Title", body="A" * (_BODY_MAX_LEN + 1000))

        body = mock_create.await_args.kwargs["body"]
        # Cap is applied to the user-supplied body BEFORE we append the
        # attribution divider — total wire length is body cap + attribution.
        a_run = body.split("\n\n---\n", 1)[0]
        assert len(a_run) == _BODY_MAX_LEN

    async def test_blank_title_rejected(self):
        from mcp_server.tools.github import create_github_issue

        with patch("mcp_server.tools.github.create_issue", new=AsyncMock()) as mock_create:
            result = await create_github_issue(title="   ", body="b")

        assert result == {"error": "title is required"}
        mock_create.assert_not_awaited()


class TestRateLimit:
    async def test_sixth_call_in_window_returns_error_with_eta(self):
        from mcp_server.tools.github import _ISSUE_RATE_MAX_PER_USER, create_github_issue

        with patch(
            "mcp_server.tools.github.create_issue",
            new=AsyncMock(return_value={"number": 1, "url": "x", "title": "x"}),
        ):
            for i in range(_ISSUE_RATE_MAX_PER_USER):
                ok = await create_github_issue(title=f"Title {i}", body="b")
                assert "error" not in ok, f"call {i} should succeed"

            blocked = await create_github_issue(title="One too many", body="b")

        assert "error" in blocked
        assert "Daily issue limit reached" in blocked["error"]

    async def test_second_user_unaffected_by_first_users_quota(self):
        """Sliding window is per-``user_id`` — exhausting tenant A must not
        block tenant B. Cross-tenant interference would let one athlete DoS
        the issue tracker for everyone."""
        from mcp_server.context import set_current_user_id
        from mcp_server.tools.github import _ISSUE_RATE_MAX_PER_USER, create_github_issue

        with patch(
            "mcp_server.tools.github.create_issue",
            new=AsyncMock(return_value={"number": 1, "url": "x", "title": "x"}),
        ):
            set_current_user_id(100)
            for i in range(_ISSUE_RATE_MAX_PER_USER):
                await create_github_issue(title=f"Title {i}", body="b")
            blocked_for_100 = await create_github_issue(title="6th", body="b")
            assert "error" in blocked_for_100

            set_current_user_id(101)
            ok_for_101 = await create_github_issue(title="From other user", body="b")

        assert "error" not in ok_for_101
