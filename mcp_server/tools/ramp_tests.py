"""MCP tools for ramp tests and threshold management (ATP Phase 4)."""

from datetime import date

from data.database import AiWorkoutRow
from data.intervals_client import IntervalsClient
from data.ramp_tests import create_ramp_test, detect_threshold_drift, get_threshold_freshness_data
from mcp_server.app import mcp


@mcp.tool()
async def get_threshold_freshness(sport: str = "") -> dict:
    """Check how fresh HRVT1/HRVT2 thresholds are.

    Returns days since last valid DFA threshold test, last measured values,
    and recent test history. Thresholds older than 21 days are considered stale.

    Args:
        sport: Filter by sport ("Ride" or "Run"). Empty = all sports.
    """
    data = await get_threshold_freshness_data(sport)
    drift = await detect_threshold_drift()

    result = {**data}
    if drift and drift["alerts"]:
        result["drift_alerts"] = drift["alerts"]

    return result


@mcp.tool()
async def create_ramp_test_tool(
    sport: str,
    target_date: str = "",
) -> str:
    """Create a ramp test workout in Intervals.icu calendar.

    Ramp tests are progressive step-based workouts used to determine
    HRVT1/HRVT2 thresholds via DFA alpha 1 analysis. Chest strap required.

    Only Ride and Run are supported. Each step is 5 minutes for DFA stabilization.

    Args:
        sport: "Ride" or "Run"
        target_date: Date in YYYY-MM-DD format. Default: today.
    """
    if sport not in ("Ride", "Run"):
        return f"Ramp test not supported for {sport}. Only Ride and Run."

    dt = date.fromisoformat(target_date) if target_date else date.today()

    freshness = await get_threshold_freshness_data(sport)
    days_since = freshness.get("days_since") or 0

    workout = create_ramp_test(sport, dt, days_since)

    client = IntervalsClient()
    event_data = workout.to_intervals_event()
    result = await client.create_event(event_data)
    intervals_id = result.get("id")

    await AiWorkoutRow.save(
        user_id=1,  # TODO: per-user
        date_str=str(dt),
        sport=sport,
        slot=workout.slot,
        external_id=workout.external_id,
        intervals_id=intervals_id,
        name=workout.name,
        description="Ramp test for HRVT1/HRVT2",
        duration_minutes=workout.duration_minutes,
        target_tss=None,
        rationale=workout.rationale,
    )

    return (
        f"Ramp test created: AI: {workout.name} (generated) on {dt}. "
        f"{workout.duration_minutes} min, {len(workout.steps)} steps. "
        "Chest strap required for DFA analysis."
    )
