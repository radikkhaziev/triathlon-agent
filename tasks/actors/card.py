"""Dramatiq actor — workout card PNG generation."""

import logging

import anthropic
import dramatiq
import sentry_sdk
from pydantic import validate_call
from sqlalchemy import select

from config import settings
from data.card_renderer import WorkoutCardData, render_workout_card
from data.db import Activity, ActivityDetail, User, UserDTO
from data.db.common import get_sync_session
from data.intervals.client import IntervalsSyncClient
from tasks.tools import TelegramTool

logger = logging.getLogger(__name__)

_CARD_AI_PROMPT = (
    "You are a triathlon coach. Analyze this workout and write 2-3 sentences "
    "for an Instagram story card. Be concise, data-driven, no emojis. "
    "Mention key insights about intensity, efficiency, or recovery. English only.\n\n"
    "Sport: {sport}\n"
    "Distance: {distance}\n"
    "Duration: {duration}\n"
    "Avg HR: {avg_hr}\n"
    "Avg Pace: {pace}\n"
    "Avg Power: {power}\n"
    "Elevation: {elevation}\n"
)


def _generate_card_ai_text(activity: Activity, detail: ActivityDetail | None) -> str:
    """Generate 2-3 sentence workout analysis via Claude."""
    distance = f"{detail.distance:.0f}m" if detail and detail.distance else "N/A"
    duration_min = f"{activity.moving_time // 60}min" if activity.moving_time else "N/A"
    avg_hr = f"{int(activity.average_hr)}bpm" if activity.average_hr else "N/A"
    pace = f"{detail.pace:.2f}m/s" if detail and detail.pace else "N/A"
    power = f"{int(detail.avg_power)}W" if detail and detail.avg_power else "N/A"
    elevation = f"{detail.elevation_gain:.0f}m" if detail and detail.elevation_gain else "N/A"

    prompt = _CARD_AI_PROMPT.format(
        sport=activity.type or "Run",
        distance=distance,
        duration=duration_min,
        avg_hr=avg_hr,
        pace=pace,
        power=power,
        elevation=elevation,
    )

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY.get_secret_value())
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("Card AI text generation failed: %s", e)
        # Fallback template
        return f"{activity.type or 'Workout'} session: {duration_min}, {avg_hr}."


def _fetch_latlng(user: User, activity_id: str) -> list[tuple[float, float]]:
    """Fetch GPS track from Intervals.icu streams API."""
    try:
        with IntervalsSyncClient.for_user(user) as client:
            streams = client.get_activity_streams(activity_id, types=["latlng"])
    except Exception as e:
        logger.warning("GPS streams fetch failed for %s: %s", activity_id, e)
        return []

    if not streams:
        return []

    for s in streams:
        if s.get("type") == "latlng" and s.get("data") and s.get("data2"):
            return list(zip(s["data"], s["data2"]))
    return []


@dramatiq.actor(queue_name="default", time_limit=120_000)
@validate_call
def actor_generate_workout_card(user: UserDTO, activity_id: str):
    """Generate a workout card PNG and send it to the user via Telegram."""
    # Extract all needed values inside session to avoid DetachedInstanceError
    with get_sync_session() as session:
        activity = session.get(Activity, activity_id)
        if not activity or activity.user_id != user.id:
            logger.warning("Card generation: activity %s not found for user %d", activity_id, user.id)
            return

        detail = session.execute(
            select(ActivityDetail).where(ActivityDetail.activity_id == activity_id)
        ).scalar_one_or_none()

        user_orm = session.get(User, user.id)
        if user_orm is None:
            return

        sport_type = activity.type or "Run"
        moving_time = activity.moving_time
        average_hr = activity.average_hr
        distance = detail.distance if detail else None
        pace = detail.pace if detail else None
        avg_power = int(detail.avg_power) if detail and detail.avg_power else None
        elevation_gain = detail.elevation_gain if detail else None

    try:
        # Fetch GPS track
        latlng = _fetch_latlng(user_orm, activity_id)

        # Generate AI text
        ai_text = _generate_card_ai_text(activity, detail)

        # Build card data
        pace_sec_per_km = 1000 / pace if pace and pace > 0 else None

        card_data = WorkoutCardData(
            sport_type=sport_type,
            distance_m=distance,
            duration_sec=moving_time,
            avg_pace_sec_per_km=pace_sec_per_km,
            avg_power=avg_power,
            avg_hr=int(average_hr) if average_hr else None,
            elevation_gain=elevation_gain,
            ai_text=ai_text,
            latlng=latlng,
        )

        # Render card
        png = render_workout_card(card_data)
        logger.info("Card generated for activity %s: %d bytes", activity_id, len(png))

        # Send to user
        tg = TelegramTool(user=user)
        tg.send_photo(photo=png, caption=f"📸 {sport_type} card")
        logger.info("Card sent for activity %s to user %d", activity_id, user.id)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("Card generation failed for activity %s", activity_id)
