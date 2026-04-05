"""MCP tools for GitHub issue creation and listing."""

from data.github import create_issue, list_issues
from mcp_server.app import mcp
from mcp_server.context import require_owner


@mcp.tool()
async def create_github_issue(
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> dict:
    """Create a GitHub issue in the triathlon-agent repository.

    Use for tracking bugs, feature requests, and tasks discovered during conversation.
    Title should be English, imperative mood ("Add X", "Fix Y").
    Body should use Markdown with sections: Context, What needs to happen, Acceptance criteria.

    Args:
        title: Issue title in English, imperative mood.
        body: Markdown body with structured sections.
        labels: Labels to apply (e.g. ["bug"], ["enhancement", "needs-implementation"]).
    """
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
    """List GitHub issues from the triathlon-agent repository.

    Use to check existing issues before creating new ones (avoid duplicates),
    review open tasks, or reference issue numbers in conversation.

    Args:
        state: Filter by state: "open", "closed", or "all". Default: "open".
        labels: Filter by labels (e.g. ["bug"], ["enhancement"]).
        limit: Max issues to return (default: 10, max: 100).
    """
    return await list_issues(state=state, labels=labels, limit=limit)
