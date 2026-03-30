"""GitHub API client for issue creation."""

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
