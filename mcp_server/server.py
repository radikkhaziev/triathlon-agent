"""MCP server entry point for Triathlon Agent.

Exposes athlete wellness, HRV, RHR, training load, recovery, and goal data
as MCP tools for Claude Desktop and future integrations.

Run: python -m mcp_server.server
"""

import mcp_server.tools.activities  # noqa: F401
import mcp_server.tools.activity_details  # noqa: F401
import mcp_server.tools.activity_hrv  # noqa: F401
import mcp_server.tools.ai_workouts  # noqa: F401
import mcp_server.tools.compliance  # noqa: F401
import mcp_server.tools.ctl_prediction  # noqa: F401
import mcp_server.tools.exercise_guidelines  # noqa: F401
import mcp_server.tools.garmin  # noqa: F401
import mcp_server.tools.github  # noqa: F401
import mcp_server.tools.goal  # noqa: F401
import mcp_server.tools.hrv  # noqa: F401
import mcp_server.tools.iqos  # noqa: F401
import mcp_server.tools.mood  # noqa: F401
import mcp_server.tools.progress  # noqa: F401
import mcp_server.tools.ramp_tests  # noqa: F401
import mcp_server.tools.recovery  # noqa: F401
import mcp_server.tools.rhr  # noqa: F401
import mcp_server.tools.scheduled_workouts  # noqa: F401
import mcp_server.tools.training_load  # noqa: F401
import mcp_server.tools.training_log  # noqa: F401
import mcp_server.tools.update_zones  # noqa: F401
import mcp_server.tools.usage  # noqa: F401
import mcp_server.tools.weekly_summary  # noqa: F401
import mcp_server.tools.weight  # noqa: F401

# Register tools (side-effect imports)
import mcp_server.tools.wellness  # noqa: F401
import mcp_server.tools.workout_cards  # noqa: F401
import mcp_server.tools.zones  # noqa: F401
from mcp_server.app import mcp  # noqa: F401 — re-export

# Register resources
from mcp_server.resources.athlete_profile import register_resources

register_resources(mcp)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
