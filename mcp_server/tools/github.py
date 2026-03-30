"""MCP tool for GitHub issue creation."""

from data.github import create_issue
from mcp_server.app import mcp


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
    return await create_issue(title=title, body=body, labels=labels)
