"""MCP tools for ramp tests and threshold management (ATP Phase 4)."""

import asyncio
from datetime import date

from data.db import AiWorkout, ThresholdFreshnessDTO, User
from data.intervals.client import IntervalsAsyncClient
from data.intervals.dto import PlannedWorkoutDTO
from data.ramp_tests import create_ramp_test
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_threshold_freshness(sport: str = "") -> dict:
    """Check how fresh HRVT1/HRVT2 thresholds are.

    Returns days since last valid DFA threshold test, last measured values,
    and recent test history. Thresholds older than 21 days are considered stale.

    Args:
        sport: Filter by sport ("Ride" or "Run"). Empty = all sports.
    """
    user_id = get_current_user_id()

    freshness, drift = await asyncio.gather(
        User.get_threshold_freshness(user_id, sport=sport),
        User.detect_threshold_drift(user_id),
    )

    result = freshness.model_dump()
    if drift and drift.alerts:
        result["drift_alerts"] = [a.model_dump() for a in drift.alerts]

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
    user_id = get_current_user_id()

    if sport not in ("Ride", "Run"):
        return f"Ramp test not supported for {sport}. Only Ride and Run."

    dt = date.fromisoformat(target_date) if target_date else date.today()

    freshness: ThresholdFreshnessDTO = await User.get_threshold_freshness(
        user_id, sport=sport
    )  # Get full DTO for days_since
    days_since = freshness.days_since or 0

    workout: PlannedWorkoutDTO = create_ramp_test(sport, dt, days_since)

    event_data = workout.to_intervals_event()

    async with IntervalsAsyncClient.for_user(user_id) as client:
        result = await client.create_event(event_data)

    intervals_id = result.id
    await AiWorkout.save(
        user_id=user_id,
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
