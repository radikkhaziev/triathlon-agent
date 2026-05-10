"""REST endpoint for the latest weekly changelog Discussion.

Backs the webapp sidebar's "What's new" link (PR2 of WEEKLY_CHANGELOG_SPEC).
The actor (``tasks/actors/changelog.py``) publishes once a week to GitHub
Discussions; this endpoint hands the latest entry to the frontend so it can
render the unread-badge link.

Auth: ``require_viewer`` — demo can read alongside the rest of the dashboard.

Cache: 1h in-process dict with TTL. The actor writes once per week so we'd
otherwise hit GitHub on every page load. Acceptable to be stale by up to an
hour; re-publishing the actor doesn't expire the cache (next refetch
naturally picks up the new newest-discussion).

Failure mode: 503 with ``Retry-After: 300`` so the webapp can hide the link
until GitHub's back. The sidebar treats 503/404 identically — no link shown.
"""

import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException

from api.deps import require_viewer
from config import settings
from data.db import User
from data.github import LATEST_DISCUSSION_QUERY

logger = logging.getLogger(__name__)

router = APIRouter()

GITHUB_GRAPHQL = "https://api.github.com/graphql"
CACHE_TTL_SEC = 3600
RETRY_AFTER_SEC = 300

# Sentinel for "GitHub said no discussions exist". Cached separately from
# happy path so a fresh repo with no Discussion yet doesn't burn one GraphQL
# call per page load until the first publish lands.
#
# NOTE: per-process — fine on single-worker uvicorn. Under ``--workers N`` you'd
# get N× GitHub calls (one cache fill per worker). When we scale workers,
# migrate to Redis (``SET ... EX 3600``) — the value is small and shared across
# tenants since the Discussion is a single global per-repo resource.
_CACHE: dict[str, object] = {"value": None, "expires_at": 0.0}

# Single-flight guard for cache misses. Without it, N concurrent requests at
# TTL boundary would each fan out to GitHub. Only the first awaiter does the
# upstream call; the rest re-check the cache after acquiring the lock.
_CACHE_REFRESH_LOCK = asyncio.Lock()


def _split_repo(repo: str) -> tuple[str, str]:
    """``"owner/name"`` → ``("owner", "name")``."""
    owner, _, name = repo.partition("/")
    return owner, name


async def _fetch_latest_discussion() -> dict | None:
    """Returns ``{url, title, published_at}`` or ``None`` if repo has no discussions yet.

    Raises ``httpx.HTTPError`` / ``RuntimeError`` on GitHub-side failure — the
    caller maps both to HTTP 503.
    """
    token = settings.GITHUB_TOKEN.get_secret_value()
    owner, name = _split_repo(settings.GITHUB_REPO)
    payload = {
        "query": LATEST_DISCUSSION_QUERY,
        "variables": {
            "categoryId": settings.CHANGELOG_DISCUSSION_CATEGORY_ID,
            "owner": owner,
            "name": name,
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(GITHUB_GRAPHQL, json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    nodes = data["data"]["repository"]["discussions"]["nodes"]
    if not nodes:
        return None
    node = nodes[0]
    return {
        "url": node["url"],
        "title": node["title"],
        "published_at": node["createdAt"],
    }


@router.get("/api/changelog/latest")
async def get_latest_changelog(user: User = Depends(require_viewer)) -> dict:
    """Latest entry from the GitHub Discussions Announcements category.

    Returns ``{url, title, published_at}`` (ISO timestamp). 404 if no
    discussion exists yet. 503 + ``Retry-After: 300`` on GitHub failure —
    webapp hides the sidebar link in both cases.

    Does NOT leak ``GITHUB_TOKEN`` or any internal IDs into the response.
    """
    # Disabled feature behaves like "no discussions yet" — the webapp hides
    # the link, no error logs, no GitHub call.
    if not settings.CHANGELOG_DISCUSSION_CATEGORY_ID or not settings.GITHUB_TOKEN.get_secret_value():
        raise HTTPException(status_code=404, detail="changelog disabled")

    now = time.time()
    if now < float(_CACHE["expires_at"]):
        cached = _CACHE["value"]
        if cached is None:
            raise HTTPException(status_code=404, detail="no discussions yet")
        return cached  # type: ignore[return-value]

    # Single-flight: only the first awaiter does the upstream call; later
    # awaiters re-check the cache after acquiring the lock and serve the
    # filled value without hitting GitHub again.
    async with _CACHE_REFRESH_LOCK:
        now = time.time()
        if now < float(_CACHE["expires_at"]):
            cached = _CACHE["value"]
            if cached is None:
                raise HTTPException(status_code=404, detail="no discussions yet")
            return cached  # type: ignore[return-value]

        try:
            latest = await _fetch_latest_discussion()
        except (httpx.HTTPError, RuntimeError) as exc:
            # Don't poison the cache on transient errors — next request retries.
            # ``Retry-After`` must go on the exception itself; ``response.headers``
            # is dropped when FastAPI replaces the body with the error JSON.
            logger.warning("Changelog fetch failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="changelog upstream unavailable",
                headers={"Retry-After": str(RETRY_AFTER_SEC)},
            ) from exc

        _CACHE["value"] = latest
        _CACHE["expires_at"] = now + CACHE_TTL_SEC

    if latest is None:
        raise HTTPException(status_code=404, detail="no discussions yet")
    return latest
