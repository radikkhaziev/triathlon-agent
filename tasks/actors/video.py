"""Dramatiq actor — request a workout video from video.endurai.me.

Designed to chain after ``actor_download_user_avatar`` in a pipeline: the
previous actor's result (a ``UserDTO`` dict with ``avatar_url`` set) lands
as the first positional arg of ``actor_render_workout_video``.
"""

import logging

import anthropic
import dramatiq
import httpx
import sentry_sdk
from pydantic import validate_call

from bot.i18n import _, set_language
from config import settings
from data.db import Activity, ActivityDetail, ActivityHrv, User, UserDTO, Wellness
from data.db.common import get_sync_session
from data.intervals.client import IntervalsSyncClient
from tasks.tools import TelegramTool

logger = logging.getLogger(__name__)

_INSIGHT_PROMPT = (
    "You are a triathlon coach. Write ONE short sentence (≤120 characters) "
    "for an Instagram-story video overlay. Be concise, data-driven, no emojis, "
    "no markdown. Match the athlete's language: {language}.\n\n"
    "Sport: {sport}\n"
    "Distance: {distance_km} km\n"
    "Duration: {duration_min} min\n"
    "Avg HR: {avg_hr} bpm\n"
    "Pace: {pace}\n"
    "HRV: {hrv} ms\n"
    "Recovery: {recovery}\n"
)


def _format_pace(pace_ms: float | None, sport: str) -> str:
    """Convert m/s pace to mm:ss/km (Run) or mm:ss/100m (Swim). Empty for Ride/Other."""
    if not pace_ms or pace_ms <= 0 or sport not in ("Run", "Swim"):
        return ""
    seconds_per_unit = 100 / pace_ms if sport == "Swim" else 1000 / pace_ms
    mins = int(seconds_per_unit // 60)
    secs = int(round(seconds_per_unit % 60))
    if secs == 60:
        mins += 1
        secs = 0
    return f"{mins}:{secs:02d}"


def _generate_insight(
    sport: str,
    distance_km: float,
    duration_min: int | None,
    avg_hr: int | None,
    pace: str,
    hrv: float | None,
    recovery: str,
    language: str,
) -> str:
    """Generate a ≤120-char one-sentence insight via Claude. Falls back to a templated line on failure."""
    prompt = _INSIGHT_PROMPT.format(
        sport=sport,
        distance_km=f"{distance_km:.1f}",
        duration_min=duration_min or "N/A",
        avg_hr=avg_hr or "N/A",
        pace=pace or "N/A",
        hrv=f"{hrv:.0f}" if hrv else "N/A",
        recovery=recovery or "N/A",
        language="Russian" if language == "ru" else "English",
    )
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY.get_secret_value())
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip().strip('"').strip()
        return text[:120]
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("Video insight generation failed: %s", e)
        if language == "ru":
            return f"{sport}: {distance_km:.1f} км, средний пульс {avg_hr or '—'}."
        return f"{sport}: {distance_km:.1f} km at {avg_hr or '—'} bpm."


def _fetch_polyline(user: User, activity_id: str) -> str | None:
    try:
        with IntervalsSyncClient.for_user(user) as client:
            detail = client.get_activity_detail(activity_id)
    except Exception as e:
        logger.warning("Polyline fetch failed for %s: %s", activity_id, e)
        return None
    if not detail:
        return None
    map_data = detail.get("map") or {}
    polyline = map_data.get("polyline") or map_data.get("summary_polyline")
    return polyline or None


def _build_payload(
    user: UserDTO,
    activity: Activity,
    detail: ActivityDetail | None,
    hrv: ActivityHrv | None,
    wellness: Wellness | None,
    polyline: str | None,
    insight: str,
) -> dict:
    sport = activity.type or "Other"
    distance_km = round((detail.distance / 1000.0) if detail and detail.distance else 0.0, 2)
    pace_str = _format_pace(detail.pace if detail else None, sport)

    activity_block: dict = {
        "sport": sport,
        "distance_km": distance_km,
        "pace": pace_str,
        "avg_hr": int(activity.average_hr) if activity.average_hr else 0,
        "hr_state": "STEADY",
    }
    if activity.moving_time:
        activity_block["duration_sec"] = activity.moving_time
    if detail:
        if detail.max_hr:
            activity_block["max_hr"] = detail.max_hr
        if detail.elevation_gain:
            activity_block["elevation_gain_m"] = round(detail.elevation_gain)
        if detail.avg_cadence:
            activity_block["cadence_avg"] = round(detail.avg_cadence)
        if detail.decoupling is not None:
            activity_block["decoupling_pct"] = round(detail.decoupling, 1)
    if hrv:
        if hrv.dfa_a1_mean is not None:
            activity_block["dfa_a1_mean"] = round(hrv.dfa_a1_mean, 2)
        if hrv.hrvt1_hr is not None:
            activity_block["hrvt1_hr"] = round(hrv.hrvt1_hr)
    activity_block["started_at"] = activity.start_date_local
    if activity.is_race:
        activity_block["is_race"] = True
    if activity.rpe is not None:
        activity_block["rpe"] = activity.rpe

    if polyline:
        activity_block["track"] = {
            "polyline": polyline,
            "render_style": "map",
            "color_by": "none",
            "map_style": "dark",
        }

    wellness_block: dict = {
        "hrv": round(wellness.hrv, 1) if wellness and wellness.hrv else 0.0,
        "rhr": int(wellness.resting_hr) if wellness and wellness.resting_hr else 0,
    }
    if wellness:
        if wellness.recovery_score is not None:
            wellness_block["recovery_score"] = int(wellness.recovery_score)
        if wellness.recovery_category:
            wellness_block["recovery_category"] = wellness.recovery_category
        if wellness.sleep_secs:
            wellness_block["sleep_hours"] = round(wellness.sleep_secs / 3600.0, 1)
        if wellness.ctl is not None:
            wellness_block["ctl"] = round(wellness.ctl, 1)
        if wellness.atl is not None:
            wellness_block["atl"] = round(wellness.atl, 1)
        if wellness.ctl is not None and wellness.atl is not None:
            wellness_block["tsb"] = round(wellness.ctl - wellness.atl, 1)

    user_block: dict = {"language": user.language or "ru"}
    if user.username:
        user_block["display_name"] = user.username
    if user.avatar_url:
        user_block["avatar_url"] = user.avatar_url

    return {
        "user_id": user.id,
        "idempotency_key": f"act-{activity.id}-v1",
        "activity_id": str(activity.id),
        "user": user_block,
        "props": {
            "wellness": wellness_block,
            "activity": activity_block,
            "insight": insight,
        },
    }


@dramatiq.actor(queue_name="default", time_limit=120_000)
@validate_call
def actor_render_workout_video(user: UserDTO, activity_id: str) -> None:
    """Collect activity context, generate AI insight, and POST to the video render service.

    First positional arg comes from the previous pipeline step (``actor_download_user_avatar``),
    which decorates ``UserDTO`` with ``avatar_url``.
    """
    if not settings.VIDEO_API_URL:
        logger.info("VIDEO_API_URL not configured, skipping video render for activity %s", activity_id)
        return

    set_language(user.language or "ru")
    tg = TelegramTool(user=user)

    with get_sync_session() as session:
        activity = session.get(Activity, activity_id)
        if not activity or activity.user_id != user.id:
            logger.warning("Video render: activity %s not found for user %d", activity_id, user.id)
            return
        detail = session.get(ActivityDetail, activity_id)
        hrv = session.get(ActivityHrv, activity_id)
        wellness = Wellness.get(user_id=user.id, dt=activity.start_date_local, session=session)
        user_orm = session.get(User, user.id)

    if not activity.average_hr or (detail is None or not detail.distance):
        tg.send_message(text=_("🎬 Не получилось собрать данные для видео — попробуйте позже."))
        return
    if not wellness or wellness.hrv is None or wellness.resting_hr is None:
        tg.send_message(text=_("🎬 Нет HRV/RHR за этот день — видео без них не собрать."))
        return

    polyline = _fetch_polyline(user_orm, activity_id)

    sport = activity.type or "Other"
    pace_str = _format_pace(detail.pace, sport)
    duration_min = activity.moving_time // 60 if activity.moving_time else None
    insight = _generate_insight(
        sport=sport,
        distance_km=detail.distance / 1000.0,
        duration_min=duration_min,
        avg_hr=int(activity.average_hr),
        pace=pace_str,
        hrv=wellness.hrv,
        recovery=wellness.recovery_category or "",
        language=user.language or "ru",
    )

    payload = _build_payload(user, activity, detail, hrv, wellness, polyline, insight)

    try:
        resp = httpx.post(
            f"{settings.VIDEO_API_URL.rstrip('/')}/render",
            json=payload,
            headers={"Authorization": f"Bearer {settings.VIDEO_API_TOKEN.get_secret_value()}"},
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        sentry_sdk.capture_exception(e)
        logger.exception("Video render request failed for activity %s", activity_id)
        tg.send_message(text=_("🎬 Видео-сервис недоступен, попробуйте позже."))
        return

    body = resp.json() if resp.content else {}
    task_id = body.get("task_id", "?")
    logger.info("Video render queued for activity %s: task_id=%s", activity_id, task_id)
    tg.send_message(text=_("🎬 Видео в очереди (beta). Пришлю готовое, как только сервис закончит."))
