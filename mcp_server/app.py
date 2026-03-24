"""FastMCP application instance — shared across tools and resources."""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP(
    "Triathlon Agent",
    instructions="Personal triathlon training data and analysis. "
    "All CTL/ATL/TSB values come from Intervals.icu and thresholds are calibrated for its model.",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)
