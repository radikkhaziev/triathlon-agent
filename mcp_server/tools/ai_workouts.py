"""MCP tools for AI-generated workout management (Phase 1: Adaptive Training Plan)."""

import logging
from datetime import date

import httpx

from bot.formatter import build_workout_pushed_message
from config import settings
from data.db import AiWorkout, User
from data.intervals.client import IntervalsAsyncClient
from data.intervals.dto import PlannedWorkoutDTO, WorkoutStepDTO
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id
from mcp_server.sentry import sentry_tool

logger = logging.getLogger(__name__)


async def _send_workout_notification(
    user_id: int,
    sport: str,
    name: str,
    duration_minutes: int,
    target_tss: int | None,
    suffix: str,
    intervals_id: int | None,
    target_date: date,
) -> None:
    """Send Telegram notification about pushed workout to the requesting user."""
    user = await User.get_by_id(user_id)
    if not user or not user.chat_id:
        return

    msg = build_workout_pushed_message(
        sport=sport,
        name=name,
        duration_minutes=duration_minutes,
        target_tss=target_tss,
        suffix=suffix,
        intervals_id=intervals_id,
        athlete_id=user.athlete_id or "",
        target_date=target_date,
    )
    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json={"chat_id": user.chat_id, "text": msg})
        resp.raise_for_status()


@mcp.tool()
@sentry_tool
async def suggest_workout(
    sport: str,
    name: str,
    steps: list[dict],
    duration_minutes: int,
    target_tss: int | None = None,
    rationale: str = "",
    target_date: str = "",
    dry_run: bool = False,
) -> str:
    """Generate an AI workout and push to athlete's Intervals.icu calendar. Syncs to Garmin/Wahoo.

    IMPORTANT — units in `steps`:
      - `duration` is in **SECONDS**, not minutes. 5 minutes = 300, 1 hour = 3600.
      - `distance` is in **METERS** (Swim/Run only). Mutually exclusive with `duration`.
      - `reps` is repeat count for a repeat group (group has `steps` sub-list, no own `duration`).

    Example step (10-minute Z2): {"text": "Z2", "duration": 600, "power": {"units": "%ftp", "value": 70}}
    Example repeat (3x5min tempo w/ 2min rest):
      {"reps": 3, "text": "Tempo", "steps": [
         {"text": "On",  "duration": 300, "power": {"units": "%ftp", "value": 88}},
         {"text": "Off", "duration": 120, "power": {"units": "%ftp", "value": 55}}
      ]}

    Total step seconds must approximately match `duration_minutes * 60`, otherwise the workout
    will be rejected as a unit-mismatch (common failure mode: passing minutes where seconds are expected).
    """

    dt = date.fromisoformat(target_date) if target_date else date.today()

    workout = PlannedWorkoutDTO(
        sport=sport,
        name=name,
        steps=WorkoutStepDTO.from_raw_list(steps),
        duration_minutes=duration_minutes,
        target_tss=target_tss,
        rationale=rationale,
        target_date=dt,
    )

    tss_part = f", ~{target_tss} TSS" if target_tss else ""

    if dry_run:
        return (
            f"Preview: AI: {name} ({sport}, {duration_minutes} min{tss_part}).\n"
            f"Rationale: {rationale}\n"
            f"Steps: {len(steps)} step(s). "
            f"Use suggest_workout with dry_run=False or press 'Отправить' to push."
        )

    user_id = get_current_user_id()

    # Check for existing AI workout on this date+sport
    existing = await AiWorkout.get_by_external_id(user_id, workout.external_id)

    # Push to Intervals.icu
    event_data = workout.to_intervals_event()
    intervals_id = None

    try:
        async with IntervalsAsyncClient.for_user(user_id) as client:
            if existing and existing.intervals_id:
                try:
                    result = await client.update_event(existing.intervals_id, event_data)
                    intervals_id = result.id or existing.intervals_id
                    action = "updated"
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        logger.info("Event %s not found (404), creating new", existing.intervals_id)
                        result = await client.create_event(event_data)
                        intervals_id = result.id
                        action = "created"
                    else:
                        raise
            else:
                result = await client.create_event(event_data)
                intervals_id = result.id
                action = "created"
    except Exception as e:
        logger.exception("Failed to push workout to Intervals.icu")
        return f"Error pushing to Intervals.icu: {e}"

    # Save to local DB
    await AiWorkout.save(
        user_id=user_id,
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

    # Send Telegram notification
    try:
        await _send_workout_notification(
            user_id=user_id,
            sport=sport,
            name=name,
            duration_minutes=duration_minutes,
            target_tss=target_tss,
            suffix=workout.suffix,
            intervals_id=intervals_id,
            target_date=dt,
        )
    except Exception:
        logger.warning("Failed to send workout notification from MCP", exc_info=True)

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
    """Remove an AI-generated workout from Intervals.icu calendar for a given date."""

    user_id = get_current_user_id()
    dt = date.fromisoformat(target_date)
    targets = await AiWorkout.get_for_date(user_id, dt)
    if sport:
        targets = [w for w in targets if w.sport.lower() == sport.lower()]

    if not targets:
        return f"No AI workouts found for {target_date}" + (f" ({sport})" if sport else "")

    removed = []
    async with IntervalsAsyncClient.for_user(user_id) as client:
        for w in targets:
            if w.intervals_id:
                try:
                    await client.delete_event(w.intervals_id)
                except Exception:
                    logger.warning("Failed to delete event %s from Intervals.icu", w.intervals_id)
            await AiWorkout.cancel(user_id, w.external_id)
            removed.append(f"AI: {w.name} ({w.sport})")

    return f"Removed {len(removed)} workout(s): " + ", ".join(removed)


@mcp.tool()
async def list_ai_workouts(days_ahead: int = 7) -> dict:
    """List upcoming AI-generated workouts in the Intervals.icu calendar."""
    user_id = get_current_user_id()
    rows = await AiWorkout.get_upcoming(user_id=user_id, days_ahead=days_ahead)
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
