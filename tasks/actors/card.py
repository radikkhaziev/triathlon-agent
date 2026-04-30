"""Dramatiq actor — workout card PNG generation."""

import logging

import anthropic
import dramatiq
import sentry_sdk
from pydantic import validate_call
from sqlalchemy import select

from config import settings
from data.card_renderer import (
    RaceRecapCardData,
    RaceSplit,
    WorkoutCardData,
    render_race_recap_card,
    render_workout_card,
)
from data.db import Activity, ActivityDetail, Race, User, UserDTO
from data.db.common import get_sync_session
from data.intervals.client import IntervalsSyncClient
from tasks.tools import TelegramTool

logger = logging.getLogger(__name__)

_CARD_AI_PROMPT = (
    "You are a triathlon coach. Analyze this workout and write 2-3 sentences "
    "for an Instagram story card. Be concise, data-driven, no emojis. "
    "Mention key insights about intensity, efficiency, or recovery. English only.\n\n"
    "Formatting rules — the text is rendered onto a PNG (no markdown parser):\n"
    "- Plain text only. No markdown, no **bold**, no *italics*, no `code`.\n"
    "- No brackets like [word](url) and no hashtags.\n"
    "- No bullet lists, no headings, no blockquotes. Just sentences.\n\n"
    "Sport: {sport}\n"
    "Distance: {distance}\n"
    "Duration: {duration}\n"
    "Avg HR: {avg_hr}\n"
    "Avg Pace: {pace}\n"
    "Avg Power: {power}\n"
    "Elevation: {elevation}\n"
)


def _generate_card_ai_text(
    sport: str,
    distance: float | None,
    moving_time: int | None,
    avg_hr: float | None,
    pace: float | None,
    avg_power: float | None,
    elevation: float | None,
) -> str:
    """Generate 2-3 sentence workout analysis via Claude."""
    dist_str = f"{distance:.0f}m" if distance else "N/A"
    dur_str = f"{moving_time // 60}min" if moving_time else "N/A"
    hr_str = f"{int(avg_hr)}bpm" if avg_hr else "N/A"
    pace_str = f"{pace:.2f}m/s" if pace else "N/A"
    power_str = f"{int(avg_power)}W" if avg_power else "N/A"
    elev_str = f"{elevation:.0f}m" if elevation else "N/A"

    prompt = _CARD_AI_PROMPT.format(
        sport=sport,
        distance=dist_str,
        duration=dur_str,
        avg_hr=hr_str,
        pace=pace_str,
        power=power_str,
        elevation=elev_str,
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
        return f"{sport} session: {dur_str}, {hr_str}."


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

        # Generate AI text (primitives only — no ORM objects)
        ai_text = _generate_card_ai_text(
            sport=sport_type,
            distance=distance,
            moving_time=moving_time,
            avg_hr=average_hr,
            pace=pace,
            avg_power=avg_power,
            elevation=elevation_gain,
        )

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
        tg.send_document(
            document=png,
            filename=f"{sport_type.lower()}-card-{activity_id}.png",
            mime_type="image/png",
            caption=f"📸 {sport_type} card",
        )
        logger.info("Card sent for activity %s to user %d", activity_id, user.id)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("Card generation failed for activity %s", activity_id)


# ---------------------------------------------------------------------------
#  Race recap card (V0 of post-race social-share — see END-65)
# ---------------------------------------------------------------------------

_RACE_RECAP_PROMPT = (
    "You are a triathlon coach analysing a race that was just completed. "
    "Write exactly 2 short sentences for a 1080x1080 share card.\n"
    "Sentence 1: what went right (pacing / fueling / mindset). "
    "Sentence 2: the one thing to fix next time.\n"
    "Tone: experienced coach giving a debrief, not an Instagram caption. "
    "No emojis, no hashtags, no markdown, no exclamation marks. English only.\n\n"
    "Race: {race_name}\n"
    "Sport: {sport}\n"
    "Distance: {distance}\n"
    "Finish time: {finish}\n"
    "Goal time: {goal}\n"
    "Delta vs goal: {delta}\n"
    "Avg HR: {avg_hr}\n"
    "HR by quarter (bpm): {hr_quarters}\n"
    "RPE (Borg 1-10): {rpe}\n"
    "Race-day TSB: {tsb}\n"
)


def _format_seconds_for_prompt(secs: int | None) -> str:
    if secs is None:
        return "N/A"
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _generate_race_recap_ai_text(
    *,
    race_name: str,
    sport: str,
    distance_m: float | None,
    finish_sec: int | None,
    goal_sec: int | None,
    avg_hr: float | None,
    hr_quarters: list[int | None] | None,
    rpe: int | None,
    tsb: float | None,
) -> str:
    """Generate the 2-sentence coach narrative shown on the recap card."""
    delta = "N/A"
    if finish_sec is not None and goal_sec is not None:
        gap = finish_sec - goal_sec
        sign = "-" if gap < 0 else "+"
        delta = f"{sign}{_format_seconds_for_prompt(abs(gap))}"

    hr_q_repr = "N/A"
    if hr_quarters and any(q is not None for q in hr_quarters):
        hr_q_repr = ", ".join("—" if q is None else str(q) for q in hr_quarters)

    prompt = _RACE_RECAP_PROMPT.format(
        race_name=race_name,
        sport=sport,
        distance=f"{distance_m / 1000:.1f} km" if distance_m else "N/A",
        finish=_format_seconds_for_prompt(finish_sec),
        goal=_format_seconds_for_prompt(goal_sec),
        delta=delta,
        avg_hr=f"{int(avg_hr)} bpm" if avg_hr else "N/A",
        hr_quarters=hr_q_repr,
        rpe=f"{rpe}/10" if rpe is not None else "N/A",
        tsb=f"{tsb:+.0f}" if tsb is not None else "N/A",
    )

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY.get_secret_value())
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=160,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("Race recap AI text generation failed: %s", e)
        # Fall back to a flat, factual line so the card still has body copy
        # rather than a blank slot — a coach-toned default beats nothing.
        return f"{race_name}: {sport.lower()} done. Review pacing and HR drift before the next block."


def _hr_quartiles_from_stream(user: User, activity_id: str) -> list[int | None]:
    """Average HR per quarter of the heartrate stream, padded to length 4.

    The Intervals.icu heartrate stream is a uniformly-sampled time series
    (1Hz typically), so a positional split into four equal chunks is a
    reasonable proxy for "first quarter / second quarter / …" without
    decoding the stream's resolution. Quartiles with ≤2 samples or whose
    samples are all None/0 fall back to ``None``.
    """
    try:
        with IntervalsSyncClient.for_user(user) as client:
            streams = client.get_activity_streams(activity_id, types=["heartrate"])
    except Exception as e:
        logger.warning("HR streams fetch failed for %s: %s", activity_id, e)
        return [None, None, None, None]

    if not streams:
        return [None, None, None, None]

    series: list[float] = []
    for s in streams:
        if s.get("type") == "heartrate" and isinstance(s.get("data"), list):
            series = [v for v in s["data"] if isinstance(v, (int, float)) and v > 0]
            break

    if len(series) < 4:
        return [None, None, None, None]

    n = len(series)
    quarters: list[int | None] = []
    for i in range(4):
        a = (n * i) // 4
        b = (n * (i + 1)) // 4
        chunk = series[a:b]
        if not chunk:
            quarters.append(None)
        else:
            quarters.append(int(round(sum(chunk) / len(chunk))))
    return quarters


def _splits_from_intervals(detail: ActivityDetail | None) -> list[RaceSplit]:
    """Best-effort split list from ``ActivityDetail.intervals``.

    Reads ``icu_intervals`` lap entries (each lap → one split). Tri-leg
    detection is intentionally out of scope for V0 — we surface whatever
    laps the user (or Intervals.icu auto-lap) recorded, in order. Returns
    an empty list when the structure is missing or unrecognised so the
    renderer skips the splits panel cleanly.
    """
    if detail is None or not detail.intervals:
        return []
    icu_intervals = (detail.intervals or {}).get("icu_intervals") or []
    splits: list[RaceSplit] = []
    for idx, iv in enumerate(icu_intervals, start=1):
        # Accept either explicit duration or start/end deltas. Some Intervals
        # exports use ``elapsed_time`` while others use the start/end pair —
        # we tolerate both.
        duration = iv.get("elapsed_time")
        if duration is None:
            start = iv.get("start_time")
            end = iv.get("end_time")
            if start is not None and end is not None:
                duration = end - start
        if duration is None or duration <= 0:
            continue
        label = iv.get("label") or iv.get("name") or f"L{idx}"
        distance_m = iv.get("distance")
        splits.append(RaceSplit(label=str(label)[:24], time_sec=int(duration), distance_m=distance_m))
    return splits


@dramatiq.actor(queue_name="default", time_limit=180_000)
@validate_call
def actor_generate_race_recap_card(user: UserDTO, activity_id: str):
    """Render and deliver the post-race recap card for an ``is_race=true`` activity.

    Idempotent: re-running for the same ``activity_id`` rebuilds the PNG and
    re-sends it as a fresh document (Telegram has no in-place document
    replace; the previous send is left in chat history but the new one
    supersedes it visually).
    """
    with get_sync_session() as session:
        activity = session.get(Activity, activity_id)
        if not activity or activity.user_id != user.id:
            logger.warning("Race recap: activity %s not found for user %d", activity_id, user.id)
            return
        if not activity.is_race:
            logger.info("Race recap: activity %s is not flagged is_race, skipping", activity_id)
            return

        detail = session.execute(
            select(ActivityDetail).where(ActivityDetail.activity_id == activity_id)
        ).scalar_one_or_none()
        race = session.execute(
            select(Race).where(Race.activity_id == activity_id, Race.user_id == user.id)
        ).scalar_one_or_none()

        user_orm = session.get(User, user.id)
        if user_orm is None:
            return

        sport_type = activity.type or "Run"
        moving_time = activity.moving_time
        average_hr = activity.average_hr
        activity_rpe = activity.rpe
        race_name = race.name if race else (activity.name or sport_type)
        finish_time = race.finish_time_sec if race else moving_time
        goal_time = race.goal_time_sec if race else None
        distance_m = (race.distance_m if race and race.distance_m else (detail.distance if detail else None))
        race_day_tsb = race.race_day_tsb if race else None
        race_day_recovery = race.race_day_recovery_score if race else None
        race_rpe = race.rpe if race and race.rpe is not None else activity_rpe
        race_splits_payload = race.splits if race else None
        intervals_splits = _splits_from_intervals(detail)

    try:
        # Splits priority: explicit Race.splits JSON (manually curated) wins
        # over best-effort intervals derivation. Both still go through the
        # ``RaceSplit`` dataclass so the renderer never sees raw dicts.
        if isinstance(race_splits_payload, list) and race_splits_payload:
            splits = [
                RaceSplit(
                    label=str(s.get("label", "")) or f"L{i + 1}",
                    time_sec=int(s["time_sec"]),
                    distance_m=s.get("distance_m"),
                )
                for i, s in enumerate(race_splits_payload)
                if isinstance(s, dict) and s.get("time_sec")
            ]
        else:
            splits = intervals_splits

        hr_quarters = _hr_quartiles_from_stream(user_orm, activity_id)

        ai_text = _generate_race_recap_ai_text(
            race_name=race_name,
            sport=sport_type,
            distance_m=distance_m,
            finish_sec=finish_time,
            goal_sec=goal_time,
            avg_hr=average_hr,
            hr_quarters=hr_quarters,
            rpe=race_rpe,
            tsb=race_day_tsb,
        )

        card_data = RaceRecapCardData(
            race_name=race_name,
            sport_type=sport_type,
            finish_time_sec=finish_time,
            goal_time_sec=goal_time,
            distance_m=distance_m,
            splits=splits,
            avg_hr_quarters=hr_quarters,
            rpe=race_rpe,
            race_day_tsb=race_day_tsb,
            race_day_recovery_score=race_day_recovery,
            ai_text=ai_text,
        )

        png = render_race_recap_card(card_data)
        logger.info("Race recap card generated for activity %s: %d bytes", activity_id, len(png))

        tg = TelegramTool(user=user)
        tg.send_document(
            document=png,
            filename=f"race-recap-{activity_id}.png",
            mime_type="image/png",
            caption=f"🏁 {race_name}",
        )
        logger.info("Race recap card sent for activity %s to user %d", activity_id, user.id)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("Race recap card generation failed for activity %s", activity_id)
