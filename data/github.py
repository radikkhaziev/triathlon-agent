"""GitHub API client for issue creation and listing."""

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


async def create_issue(title: str, body: str, labels: list[str] | None = None) -> dict:
    """Create a GitHub issue via REST API.

    Returns dict with number, url, title on success, or dict with error key on failure.
    """
    token = settings.GITHUB_TOKEN.get_secret_value()
    repo = settings.GITHUB_REPO

    if not token:
        return {"error": "GITHUB_TOKEN is not configured"}
    if not repo:
        return {"error": "GITHUB_REPO is not configured"}

    payload: dict = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/repos/{repo}/issues",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=15,
            )
    except httpx.HTTPError as exc:
        logger.error("GitHub API request failed: %s", exc)
        return {"error": f"GitHub API request failed: {exc}"}

    if resp.status_code == 201:
        data = resp.json()
        result = {
            "number": data["number"],
            "url": data["html_url"],
            "title": data["title"],
        }
        logger.info("Created GitHub issue #%d: %s", data["number"], data["title"])
        return result

    logger.error("GitHub API error %d: %s", resp.status_code, resp.text[:200])
    return {"error": f"GitHub API returned {resp.status_code}: {resp.text[:200]}"}


async def list_issues(
    state: str = "open",
    labels: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """List GitHub issues via REST API.

    Returns dict with issues list on success, or dict with error key on failure.
    """
    token = settings.GITHUB_TOKEN.get_secret_value()
    repo = settings.GITHUB_REPO

    if not token:
        return {"error": "GITHUB_TOKEN is not configured"}
    if not repo:
        return {"error": "GITHUB_REPO is not configured"}

    params: dict = {
        "state": state,
        "per_page": min(limit, 100),
        "sort": "created",
        "direction": "desc",
    }
    if labels:
        params["labels"] = ",".join(labels)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}/issues",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=15,
            )
    except httpx.HTTPError as exc:
        logger.error("GitHub API request failed: %s", exc)
        return {"error": f"GitHub API request failed: {exc}"}

    if resp.status_code == 200:
        data = resp.json()
        # GitHub API returns PRs in issues endpoint — filter them out
        issues = [
            {
                "number": item["number"],
                "title": item["title"],
                "state": item["state"],
                "labels": [lbl["name"] for lbl in item.get("labels", [])],
                "created_at": item["created_at"][:10],
                "url": item["html_url"],
            }
            for item in data
            if "pull_request" not in item
        ]
        return {"count": len(issues), "issues": issues}

    logger.error("GitHub API error %d: %s", resp.status_code, resp.text[:200])
    return {"error": f"GitHub API returned {resp.status_code}: {resp.text[:200]}"}
