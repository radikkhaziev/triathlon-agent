"""MCP tools for GitHub issue creation and listing."""

import time

from data.github import create_issue, list_issues
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id
from mcp_server.sentry import sentry_tool

# Per-user sliding-window rate limit on issue creation.
# `radikkhaziev/triathlon-agent` is a public repo, so spam from a single
# tenant would be visible to anyone — and an LLM-driven loop ("create 50
# issues for X") could rack up issues fast without a guard. Cap at 5 issues
# per rolling 24h window, matching the in-process pattern already used for
# `_retry_backfill_last_success` / `_mcp_config_last_access` in
# api/routers/auth.py. Same single-worker caveat applies — migrate to Redis
# INCR+EXPIRE before adding `--workers N`.
_ISSUE_RATE_WINDOW_SEC = 24 * 60 * 60
_ISSUE_RATE_MAX_PER_USER = 5
_issue_create_log: dict[int, list[float]] = {}

# Defensive caps on AI-supplied content. GitHub's own limits are far higher,
# but the bot has free-form chat that could be prompt-injected into emitting
# multi-megabyte payloads — bound them at the boundary.
_TITLE_MAX_LEN = 200
_BODY_MAX_LEN = 8000


def _check_and_record_rate_limit(user_id: int, now_mono: float) -> int | None:
    """Sliding-window check. Returns ``retry_after_sec`` if limit hit, else None."""
    history = _issue_create_log.get(user_id, [])
    cutoff = now_mono - _ISSUE_RATE_WINDOW_SEC
    history = [t for t in history if t > cutoff]
    if len(history) >= _ISSUE_RATE_MAX_PER_USER:
        retry_after = int(history[0] + _ISSUE_RATE_WINDOW_SEC - now_mono) + 1
        _issue_create_log[user_id] = history
        return retry_after
    history.append(now_mono)
    _issue_create_log[user_id] = history
    if len(_issue_create_log) > 512:
        # Lazy cleanup: drop users with no entries in the live window.
        for uid in [u for u, h in _issue_create_log.items() if not any(t > cutoff for t in h)]:
            _issue_create_log.pop(uid, None)
    return None


@mcp.tool()
@sentry_tool
async def create_github_issue(
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> dict:
    """Create a GitHub issue in the triathlon-agent repository.

    Open to any authenticated athlete — the bot is the GitHub author, so we
    attribute the reporter by ``user_id`` only at the bottom of the body.
    Telegram username and Intervals.icu ``athlete_id`` are deliberately NOT
    written into the public repo (PII linkage avoidance, see
    ``docs/MULTI_TENANT_SECURITY.md`` §13).
    """
    user_id = get_current_user_id()

    retry_after = _check_and_record_rate_limit(user_id, time.monotonic())
    if retry_after is not None:
        hours = round(retry_after / 3600, 1)
        return {"error": (f"Daily issue limit reached ({_ISSUE_RATE_MAX_PER_USER}/24h). " f"Try again in ~{hours}h.")}

    title = (title or "").strip()[:_TITLE_MAX_LEN]
    body = (body or "").strip()[:_BODY_MAX_LEN]
    if not title:
        return {"error": "title is required"}

    attributed_body = f"{body}\n\n---\n_Reported via MCP by user_id={user_id}_"
    return await create_issue(title=title, body=attributed_body, labels=labels)


@mcp.tool()
async def get_github_issues(
    state: str = "open",  # "open", "closed", or "all"
    labels: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """List GitHub issues from the triathlon-agent repository."""
    return await list_issues(state=state, labels=labels, limit=limit)
