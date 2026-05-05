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
        # Wording is fixed: "per 24h window" reads correctly near the boundary
        # where a "Daily" cap with "~0.0h to go" would have been misleading.
        assert "Issue creation limit reached" in blocked["error"]
        assert "24h" in blocked["error"]

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

    async def test_invalid_request_does_not_consume_a_slot(self):
        """Validation rejection (blank title) must NOT burn a rate-limit
        slot. Otherwise an athlete spamming malformed calls could lock
        themselves out for 24h before successfully filing one issue.
        Regression guard for the bug Copilot caught (PR #301)."""
        from mcp_server.tools.github import _ISSUE_RATE_MAX_PER_USER, create_github_issue

        with patch(
            "mcp_server.tools.github.create_issue",
            new=AsyncMock(return_value={"number": 1, "url": "x", "title": "x"}),
        ):
            # Five rejected calls — should NOT touch the bucket.
            for _ in range(_ISSUE_RATE_MAX_PER_USER):
                bad = await create_github_issue(title="", body="b")
                assert bad == {"error": "title is required"}

            # All slots still available; 5 valid calls must succeed.
            for i in range(_ISSUE_RATE_MAX_PER_USER):
                ok = await create_github_issue(title=f"Real {i}", body="b")
                assert "error" not in ok, f"valid call {i} blocked unexpectedly"

    async def test_failed_upstream_does_not_consume_a_slot(self):
        """If GitHub itself returns 500/422/etc., ``create_issue`` returns
        ``{"error": ...}``. That call shouldn't burn a slot either — the
        athlete didn't actually file an issue."""
        from mcp_server.tools.github import _ISSUE_RATE_MAX_PER_USER, create_github_issue

        upstream_fail = {"error": "GitHub API returned 500: server boom"}
        with patch(
            "mcp_server.tools.github.create_issue",
            new=AsyncMock(return_value=upstream_fail),
        ):
            for _ in range(_ISSUE_RATE_MAX_PER_USER):
                result = await create_github_issue(title="Whatever", body="b")
                assert result == upstream_fail

        # Now flip to success — all 5 slots must still be open.
        with patch(
            "mcp_server.tools.github.create_issue",
            new=AsyncMock(return_value={"number": 1, "url": "x", "title": "x"}),
        ):
            for i in range(_ISSUE_RATE_MAX_PER_USER):
                ok = await create_github_issue(title=f"Real {i}", body="b")
                assert "error" not in ok


class TestLabelsAllowList:
    """Athletes can only set a small safe set of labels — repo-managed
    triage / priority / area metadata is owner-only. Owner bypasses both
    the filter and the rate limit. See ``docs/MULTI_TENANT_SECURITY.md`` §13.
    """

    async def test_athlete_labels_filtered_to_allow_list(self):
        from types import SimpleNamespace

        from mcp_server.tools.github import _ATHLETE_ALLOWED_LABELS, create_github_issue

        # Athlete role — User.get_by_id returns a non-owner.
        athlete = SimpleNamespace(role="athlete", username="bob", athlete_id="i1")
        with (
            patch("mcp_server.tools.github.User.get_by_id", new=AsyncMock(return_value=athlete)),
            patch(
                "mcp_server.tools.github.create_issue",
                new=AsyncMock(return_value={"number": 1, "url": "x", "title": "x"}),
            ) as mock_create,
        ):
            await create_github_issue(
                title="Bug",
                body="b",
                labels=["bug", "priority/high", "area/auth", "needs-spec", "enhancement"],
            )

        forwarded = mock_create.await_args.kwargs["labels"]
        assert set(forwarded) == {"bug", "enhancement"}
        assert all(lbl in _ATHLETE_ALLOWED_LABELS for lbl in forwarded)

    async def test_athlete_with_only_disallowed_labels_falls_back_to_none(self):
        """If an athlete passes only triage labels, we drop them all and
        forward ``None`` rather than an empty list — matches the
        ``data.github.create_issue`` contract that None means "no labels"."""
        from types import SimpleNamespace

        from mcp_server.tools.github import create_github_issue

        athlete = SimpleNamespace(role="athlete", username="bob", athlete_id="i1")
        with (
            patch("mcp_server.tools.github.User.get_by_id", new=AsyncMock(return_value=athlete)),
            patch(
                "mcp_server.tools.github.create_issue",
                new=AsyncMock(return_value={"number": 1, "url": "x", "title": "x"}),
            ) as mock_create,
        ):
            await create_github_issue(
                title="Bug",
                body="b",
                labels=["priority/high", "area/auth", "needs-spec"],
            )

        assert mock_create.await_args.kwargs["labels"] is None

    async def test_owner_keeps_full_label_control_and_skips_rate_limit(self):
        from types import SimpleNamespace

        from mcp_server.tools.github import _ISSUE_RATE_MAX_PER_USER, create_github_issue

        owner = SimpleNamespace(role="owner", username="radik", athlete_id="i_owner")
        with (
            patch("mcp_server.tools.github.User.get_by_id", new=AsyncMock(return_value=owner)),
            patch(
                "mcp_server.tools.github.create_issue",
                new=AsyncMock(return_value={"number": 1, "url": "x", "title": "x"}),
            ) as mock_create,
        ):
            await create_github_issue(
                title="Triage me",
                body="b",
                labels=["priority/high", "area/auth", "needs-spec"],
            )
            # Owner is exempt from the rate limit — well beyond athlete cap.
            for i in range(_ISSUE_RATE_MAX_PER_USER + 5):
                ok = await create_github_issue(title=f"Owner issue {i}", body="b")
                assert "error" not in ok

        first_call_labels = mock_create.await_args_list[0].kwargs["labels"]
        assert first_call_labels == ["priority/high", "area/auth", "needs-spec"]
