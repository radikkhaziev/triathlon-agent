"""FastMCP application instance — shared across tools and resources."""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP(
    "Triathlon Agent",
    instructions="Personal triathlon training data and analysis. "
    "All CTL/ATL/TSB values come from Intervals.icu and thresholds are calibrated for its model.",
    streamable_http_path="/",
    # Stateless HTTP: each request is self-contained, no in-memory session_id dict.
    # Why: stateful mode hands out a session_id on first POST, stores
    # {session_id: transport} in process memory. An `api` container restart
    # (deploy / OOM / scale) drops that dict; clients holding a cached
    # Mcp-Session-Id (mcp-remote stores it in ~/.mcp-auth) then hit 404
    # "Session not found" per the MCP spec. We only expose tools + static
    # resources — no server-initiated subscriptions or resumable streams —
    # so stateless is a strict upgrade for our topology.
    stateless_http=True,
    # DNS rebinding protection disabled: MCP is mounted as sub-app inside FastAPI
    # behind reverse proxy, accessed via custom domain. Auth is handled by Bearer token
    # middleware in api/server.py (MCPAuthMiddleware).
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)
