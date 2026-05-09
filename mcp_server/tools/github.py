"""MCP tools for GitHub issue creation and listing."""

import time

from data.db import User
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

# Labels that any athlete is allowed to set when filing an issue. Repo-managed
# triage / priority labels (`needs-spec`, `priority/*`, `area/*`, `release-blocker`,
# etc.) are deliberately NOT here — those are owner-only metadata. Anything an
# athlete passes outside this set is silently dropped (we don't error: AI may
# fabricate plausible-sounding labels and we don't want the whole call to fail
# over an unknown tag). Owner role bypasses the filter — see ``create_github_issue``.
_ATHLETE_ALLOWED_LABELS = frozenset({"bug", "enhancement", "question", "documentation"})


def _check_rate_limit(user_id: int, now_mono: float) -> int | None:
    """Read-only sliding-window check. Returns ``retry_after_sec`` if the user
    is currently over the cap, else None. Does NOT mutate the bucket — call
    ``_record_rate_limit`` after the request actually clears validation so
    rejected calls (blank title, etc.) don't burn a slot."""
    history = _issue_create_log.get(user_id, [])
    cutoff = now_mono - _ISSUE_RATE_WINDOW_SEC
    history = [t for t in history if t > cutoff]
    _issue_create_log[user_id] = history
    if len(history) >= _ISSUE_RATE_MAX_PER_USER:
        return int(history[0] + _ISSUE_RATE_WINDOW_SEC - now_mono) + 1
    return None


def _record_rate_limit(user_id: int, now_mono: float) -> None:
    """Append a successful issue-creation timestamp to the bucket."""
    _issue_create_log.setdefault(user_id, []).append(now_mono)
    if len(_issue_create_log) > 512:
        cutoff = now_mono - _ISSUE_RATE_WINDOW_SEC
        for uid in [u for u, h in _issue_create_log.items() if not any(t > cutoff for t in h)]:
            _issue_create_log.pop(uid, None)


def _format_retry_after(retry_after_sec: int) -> str:
    """Human-readable countdown. Sub-hour windows are the common case near the
    end of the limit window — printing ``~0.0h`` there is misleading, so we
    fall back to minutes."""
    if retry_after_sec < 3600:
        minutes = max(1, round(retry_after_sec / 60))
        return f"~{minutes} min"
    hours = round(retry_after_sec / 3600, 1)
    return f"~{hours}h"


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
    ``docs/MULTI_TENANT_SECURITY_SPEC.md`` §13).

    Owner role bypasses the rate limit and label-allow-list — they manage the
    repo and need full access to triage/priority/area metadata. Athletes are
    capped at ``_ISSUE_RATE_MAX_PER_USER`` issues per rolling 24h window and
    can only set labels in ``_ATHLETE_ALLOWED_LABELS``; anything outside that
    set is silently dropped.
    """
    user_id = get_current_user_id()
    user = await User.get_by_id(user_id)
    is_owner = bool(user and user.role == "owner")

    # 1) Validate input first — empty title would be rejected anyway by GitHub,
    #    no point burning a rate-limit slot on a malformed call.
    title = (title or "").strip()[:_TITLE_MAX_LEN]
    body = (body or "").strip()[:_BODY_MAX_LEN]
    if not title:
        return {"error": "title is required"}

    # 2) Filter labels for non-owners — strip anything outside the allow-list
    #    rather than erroring (AI tends to fabricate plausible label names).
    if labels is not None and not is_owner:
        labels = [lbl for lbl in labels if lbl in _ATHLETE_ALLOWED_LABELS] or None

    # 3) Rate-limit non-owners. Owner is exempt — they manage the repo.
    now_mono = time.monotonic()
    if not is_owner:
        retry_after = _check_rate_limit(user_id, now_mono)
        if retry_after is not None:
            return {
                "error": (
                    f"Issue creation limit reached "
                    f"({_ISSUE_RATE_MAX_PER_USER} per 24h window). "
                    f"Try again in {_format_retry_after(retry_after)}."
                )
            }

    attributed_body = f"{body}\n\n---\n_Reported via MCP by user_id={user_id}_"
    result = await create_issue(title=title, body=attributed_body, labels=labels)

    # 4) Record only on a real upstream success — GitHub failures (4xx/5xx)
    #    surface as ``{"error": ...}``, and those should NOT consume a slot.
    if not is_owner and "error" not in result:
        _record_rate_limit(user_id, now_mono)
    return result


@mcp.tool()
async def get_github_issues(
    state: str = "open",  # "open", "closed", or "all"
    labels: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """List GitHub issues from the triathlon-agent repository."""
    return await list_issues(state=state, labels=labels, limit=limit)
