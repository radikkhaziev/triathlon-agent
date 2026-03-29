"""FastMCP application instance — shared across tools and resources."""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP(
    "Triathlon Agent",
    instructions="Personal triathlon training data and analysis. "
    "All CTL/ATL/TSB values come from Intervals.icu and thresholds are calibrated for its model.",
    streamable_http_path="/",
    # DNS rebinding protection disabled: MCP is mounted as sub-app inside FastAPI
    # behind reverse proxy, accessed via custom domain. Auth is handled by Bearer token
    # middleware in api/server.py (MCPAuthMiddleware).
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)
