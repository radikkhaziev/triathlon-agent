"""MCP tools for AI-generated workout management (Phase 1: Adaptive Training Plan)."""

import logging
from datetime import date

import httpx

from config import settings
from data.database import AiWorkoutRow
from data.intervals_client import IntervalsClient
from data.models import PlannedWorkout, WorkoutStep
from mcp_server.app import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def suggest_workout(
    sport: str,
    name: str,
    steps: list[dict],
    duration_minutes: int,
    target_tss: int | None = None,
    rationale: str = "",
    target_date: str = "",
) -> str:
    """Push an AI-generated workout to the athlete's Intervals.icu calendar.

    The workout appears on the athlete's devices (Garmin/Wahoo) via Intervals.icu sync.
    Only creates workouts with AI: prefix — never modifies existing workouts.

    Steps use Intervals.icu workout_doc format. Each step is an object:
    - "text": step label ("Warm-up", "Tempo", "Cool-down")
    - "duration": seconds (600 = 10 min) — OR "distance" (not both!)
    - "distance": meters (100, 200, 1000) — for Swim and Run distance intervals
    - "hr": {"units": "%lthr", "value": 75} — for Run
    - "power": {"units": "%ftp", "value": 80} — for Ride
    - "pace": {"units": "%pace", "value": 90} — for Swim
    - "cadence": {"units": "rpm", "value": 90}
    For intervals: "reps": 3, "steps": [work_step, rest_step]

    Distance vs duration by sport:
    - Swim: always "distance" (meters), target "pace" (%pace from CSS)
    - Run intervals: "distance" for reps (400m, 1km), target "pace" or "hr"
    - Ride: always "duration" (seconds), target "power" (%ftp)

    Args:
        sport: Activity type — "Ride", "Run", "Swim", or "WeightTraining".
        name: Short workout name (e.g. "Z2 Endurance + 3x5m Tempo").
        steps: Structured workout steps (Intervals.icu workout_doc format).
        duration_minutes: Total duration in minutes.
        target_tss: Estimated Training Stress Score (optional).
        rationale: Why this workout (1-2 sentences).
        target_date: Date in YYYY-MM-DD format. Default: today.
    """
    if not settings.AI_WORKOUT_ENABLED:
        return "AI workout generation is disabled (AI_WORKOUT_ENABLED=false)"

    dt = date.fromisoformat(target_date) if target_date else date.today()

    workout = PlannedWorkout(
        sport=sport,
        name=name,
        steps=WorkoutStep.from_raw_list(steps),
        duration_minutes=duration_minutes,
        target_tss=target_tss,
        rationale=rationale,
        target_date=dt,
    )

    # Check for existing AI workout on this date+sport
    existing = await AiWorkoutRow.get_by_external_id(workout.external_id)

    # Push to Intervals.icu
    client = IntervalsClient()
    event_data = workout.to_intervals_event()
    intervals_id = None

    try:
        if existing and existing.intervals_id:
            try:
                result = await client.update_event(existing.intervals_id, event_data)
                intervals_id = result.get("id", existing.intervals_id)
                action = "updated"
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    # Event was deleted externally (UI or remove_ai_workout) — create new
                    logger.info("Event %s not found (404), creating new", existing.intervals_id)
                    result = await client.create_event(event_data)
                    intervals_id = result.get("id")
                    action = "created"
                else:
                    raise
        else:
            result = await client.create_event(event_data)
            intervals_id = result.get("id")
            action = "created"
    except Exception as e:
        logger.exception("Failed to push workout to Intervals.icu")
        return f"Error pushing to Intervals.icu: {e}"

    # Save to local DB
    await AiWorkoutRow.save(
        date_str=str(dt),
        sport=sport,
        slot=workout.slot,
        external_id=workout.external_id,
        intervals_id=intervals_id,
        name=name,
        description="; ".join(s.get("text", "") for s in steps if s.get("text")),
        duration_minutes=duration_minutes,
        target_tss=target_tss,
        rationale=rationale,
    )

    tss_part = f", ~{target_tss} TSS" if target_tss else ""
    return (
        f"Workout {action} in Intervals.icu: AI: {name} "
        f"({sport}, {duration_minutes} min{tss_part}). "
        f"It will sync to Garmin/Wahoo automatically."
    )


@mcp.tool()
async def remove_ai_workout(
    target_date: str,
    sport: str = "",
) -> str:
    """Remove an AI-generated workout from the Intervals.icu calendar.

    Only removes workouts created by TriCoach AI (with AI: prefix and external_id
    starting with 'tricoach:'). Does not affect manually created or coach-assigned workouts.

    Args:
        target_date: Date in YYYY-MM-DD format.
        sport: Sport type to remove (e.g. "Ride"). If empty, removes all AI workouts for the date.
    """
    if not settings.AI_WORKOUT_ENABLED:
        return "AI workout generation is disabled (AI_WORKOUT_ENABLED=false)"

    dt = date.fromisoformat(target_date)
    targets = await AiWorkoutRow.get_for_date(dt)
    if sport:
        targets = [w for w in targets if w.sport.lower() == sport.lower()]

    if not targets:
        return f"No AI workouts found for {target_date}" + (f" ({sport})" if sport else "")

    client = IntervalsClient()
    removed = []
    for w in targets:
        if w.intervals_id:
            try:
                await client.delete_event(w.intervals_id)
            except Exception:
                logger.warning("Failed to delete event %s from Intervals.icu", w.intervals_id)
        await AiWorkoutRow.cancel(w.external_id)
        removed.append(f"AI: {w.name} ({w.sport})")

    return f"Removed {len(removed)} workout(s): " + ", ".join(removed)


@mcp.tool()
async def list_ai_workouts(days_ahead: int = 7) -> dict:
    """List upcoming AI-generated workouts.

    Returns workouts with AI: prefix that were created by TriCoach AI
    and are currently active in the Intervals.icu calendar.

    Args:
        days_ahead: Number of days to look ahead (default: 7).
    """
    rows = await AiWorkoutRow.get_upcoming(days_ahead=days_ahead)
    return {
        "count": len(rows),
        "workouts": [
            {
                "date": r.date,
                "sport": r.sport,
                "name": f"AI: {r.name}",
                "duration_minutes": r.duration_minutes,
                "target_tss": r.target_tss,
                "rationale": r.rationale,
                "status": r.status,
            }
            for r in rows
        ],
    }
