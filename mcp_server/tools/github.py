"""MCP tools for GitHub issue creation and listing."""

from data.github import create_issue, list_issues
from mcp_server.app import mcp
from mcp_server.context import require_owner
from mcp_server.sentry import sentry_tool


@mcp.tool()
@sentry_tool
async def create_github_issue(
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> dict:
    """Create a GitHub issue in the triathlon-agent repository."""
    try:
        await require_owner()
    except PermissionError:
        return {"error": "Only owner can create GitHub issues"}

    return await create_issue(title=title, body=body, labels=labels)


@mcp.tool()
async def get_github_issues(
    state: str = "open",  # "open", "closed", or "all"
    labels: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """List GitHub issues from the triathlon-agent repository."""
    return await list_issues(state=state, labels=labels, limit=limit)
