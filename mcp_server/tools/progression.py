"""MCP tool for Training Progression analysis — SHAP-based coaching insights."""

from data.ml.progression import get_latest_analysis
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_progression_analysis(sport: str = "Ride") -> dict:
    """Get training progression analysis — what drives your efficiency improvements.

    Returns SHAP-based feature importance: which training patterns (volume,
    polarization, recovery, load) most affect your Efficiency Factor trend.
    Model is retrained weekly. Only available for Ride (Run data too noisy).

    Top positive features = what to do more of.
    Top negative features = what to reduce.
    """
    user_id = get_current_user_id()
    result = get_latest_analysis(user_id, sport)
    if not result:
        return {"status": "no_model", "message": "Not enough data yet — need 10+ weeks of training with power meter."}
    return {"status": "ok", **result}
