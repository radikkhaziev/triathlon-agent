import asyncio
import logging
import zoneinfo
from datetime import date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from ai.claude_agent import ClaudeAgent
from bot.formatter import build_evening_message, build_morning_message, build_post_activity_message
from config import settings
from data.database import (
    ActivityHrvRow,
    ActivityRow,
    create_training_log,
    get_activities_for_ctl,
    get_activities_for_date,
    get_activity_hrv_for_date,
    get_ai_workout_by_external_id,
    get_ai_workouts_for_date,
    get_ai_workouts_upcoming,
    get_existing_detail_ids,
    get_hrv_analysis,
    get_rhr_analysis,
    get_scheduled_workouts_for_date,
    get_session,
    get_training_log_for_date,
    get_training_log_unfilled_actual,
    get_training_log_unfilled_post,
    get_wellness,
    save_activities,
    save_activity_details,
    save_ai_workout,
    save_scheduled_workouts,
    save_wellness,
    update_training_log,
)
from data.hrv_activity import process_fit_job as _process_fit_job
from data.intervals_client import IntervalsClient
from data.metrics import calculate_sport_ctl
from data.models import Activity, RecoveryScore, ScheduledWorkout
from data.ramp_tests import create_ramp_test, detect_threshold_drift, get_threshold_freshness_data, should_suggest_ramp
from data.workout_adapter import adapt_workout

logger = logging.getLogger(__name__)


async def process_fit_job(batch_size: int = 5, bot: Bot | None = None) -> int:
    """Process FIT files for unanalyzed bike/run activities (DFA alpha 1).

    Runs every 5 min. Wrapper around data.hrv_activity.process_fit_job.
    Sends Telegram notification for each successfully processed activity.
    """
    try:
        results = await _process_fit_job(batch_size=batch_size)
        if results:
            logger.info("DFA pipeline processed %d activities", len(results))

        # Send notifications for processed activities
        if bot is not None:
            for activity_id, status in results:
                if status == "processed":
                    try:
                        await _send_post_activity_notification(activity_id, bot)
                    except Exception:
                        logger.warning("Failed to send post-activity notification for %s", activity_id, exc_info=True)

        return len(results)
    except Exception:
        logger.exception("DFA pipeline job failed")
        return 0


async def _send_post_activity_notification(activity_id: str, bot: Bot) -> None:
    """Send post-activity DFA notification to Telegram."""
    async with get_session() as session:
        activity = await session.get(ActivityRow, activity_id)
        hrv = await session.get(ActivityHrvRow, activity_id)

        if not activity or not hrv or hrv.processing_status != "processed":
            return

        msg = build_post_activity_message(activity, hrv)

    await bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=msg)


# Map canonical sport → Intervals.icu type names
_CANONICAL_TO_TYPE = {"swim": "Swim", "bike": "Ride", "run": "Run"}


async def evening_report_job(bot: Bot | None = None) -> None:
    """Send evening summary report to Telegram at 21:00."""
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()

    row = await get_wellness(today)
    activities = await get_activities_for_date(today)

    # Skip if no data at all
    if not activities and row is None:
        logger.debug("Evening report skipped — no data for %s", today)
        return

    hrv_analyses = await get_activity_hrv_for_date(today)
    tomorrow = today + timedelta(days=1)
    tomorrow_workouts = await get_scheduled_workouts_for_date(tomorrow)

    msg = build_evening_message(row, activities, hrv_analyses, tomorrow_workouts)

    if bot is not None:
        try:
            await bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=msg)
            logger.info("Evening report sent for %s", today)
        except Exception:
            logger.warning("Failed to send evening report", exc_info=True)


async def create_scheduler(bot: Bot | None = None) -> AsyncIOScheduler:
    if bot is None:
        logger.warning("Scheduler created without bot — morning reports won't be sent")

    scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

    scheduler.add_job(
        daily_metrics_job,
        trigger="cron",
        hour="5-23",
        minute="*/10",
        id="daily_metrics",
        kwargs={"bot": bot},
    )

    scheduler.add_job(
        scheduled_workouts_job,
        trigger="cron",
        hour="4-23",
        minute=0,
        id="scheduled_workouts",
    )

    scheduler.add_job(
        sync_activities_job,
        trigger="cron",
        hour="4-23",
        minute=30,
        id="sync_activities",
    )

    scheduler.add_job(
        process_fit_job,
        trigger="cron",
        hour="5-22",
        minute="*/5",
        id="process_fit",
        kwargs={"bot": bot},
    )

    scheduler.add_job(
        evening_report_job,
        trigger="cron",
        hour=21,
        minute=0,
        id="evening_report",
        kwargs={"bot": bot},
    )

    return scheduler


def _enrich_sport_info(wellness, sport_ctl: dict[str, float]) -> None:
    """Merge per-sport CTL into wellness.sport_info before persistence."""
    existing_info = list(wellness.sport_info) if wellness.sport_info else []
    existing_types = {(e.get("type") or "").lower(): i for i, e in enumerate(existing_info)}

    for canonical, ctl_val in sport_ctl.items():
        if ctl_val < 0:
            continue
        iv_type = _CANONICAL_TO_TYPE[canonical]
        iv_type_lower = iv_type.lower()
        if iv_type_lower in existing_types:
            existing_info[existing_types[iv_type_lower]]["ctl"] = ctl_val
        else:
            existing_info.append({"type": iv_type, "ctl": ctl_val})

    if existing_info:
        wellness.sport_info = existing_info


async def _send_morning_report(row, bot: Bot) -> None:
    """Send morning briefing to Telegram when AI recommendation is ready."""
    # Check threshold drift for alert
    drift = None
    try:
        drift = await detect_threshold_drift()
    except Exception:
        logger.warning("Failed to check threshold drift", exc_info=True)

    summary = build_morning_message(row, threshold_drift=drift)
    webapp_url = settings.API_BASE_URL
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть отчёт", web_app=WebAppInfo(url=webapp_url))]])

    await bot.send_message(
        chat_id=settings.TELEGRAM_CHAT_ID,
        text=summary,
        reply_markup=keyboard,
    )
    logger.info("Morning report sent for %s", row.id)


async def _generate_and_push_workout(wellness_row, dt: date) -> None:
    """Generate or adapt a workout and push it to Intervals.icu.

    Phase 1: if no planned workout → AI generates from scratch (suffix=generated)
    Phase 2: if planned workout exists → adapt if recovery requires it (suffix=adapted)
    """
    hrv_flatt = await get_hrv_analysis(str(dt), "flatt_esco")
    rhr_row = await get_rhr_analysis(str(dt))
    hrv_status = hrv_flatt.status if hrv_flatt else "insufficient_data"
    tsb = (wellness_row.ctl - wellness_row.atl) if wellness_row.ctl and wellness_row.atl else 0

    existing_workouts = await get_scheduled_workouts_for_date(dt)

    if existing_workouts:
        # Phase 2: try to adapt existing workout
        if wellness_row.recovery_category in ("excellent", "good") and hrv_status == "green":
            logger.info("No adaptation needed — recovery %s, HRV green", wellness_row.recovery_category)
            return

        recovery = RecoveryScore(
            score=wellness_row.recovery_score or 50,
            category=wellness_row.recovery_category or "moderate",
            recommendation=wellness_row.recovery_recommendation or "",
        )

        # Get latest Ra from yesterday's DFA
        ra = await _get_latest_ra(dt)

        # Try first workout that has a description
        for w_row in existing_workouts:
            if not w_row.description:
                continue
            original = ScheduledWorkout(
                id=w_row.id,
                start_date_local=dt,
                name=w_row.name,
                type=w_row.type,
                description=w_row.description,
                moving_time=w_row.moving_time,
            )
            workout = adapt_workout(
                original,
                recovery,
                hrv_status,
                tsb,
                ra,
                ftp=settings.ATHLETE_FTP,
                lthr=settings.ATHLETE_LTHR_RUN,
            )
            if workout:
                await _push_workout(workout, dt)
                return

        logger.info("No adaptation needed for planned workouts on %s", dt)
        return

    # Phase 1: no planned workout → generate from scratch
    if wellness_row.recovery_category == "low":
        logger.info("Skipping AI workout generation — recovery is low for %s", dt)
        return

    hrv_aie = await get_hrv_analysis(str(dt), "ai_endurance")
    agent = ClaudeAgent()
    workout = await agent.generate_workout(wellness_row, hrv_flatt, hrv_aie, rhr_row)

    if workout is None:
        logger.info("AI recommended rest day for %s", dt)
        return

    await _push_workout(workout, dt)


async def _push_workout(workout, dt: date) -> None:
    """Push a PlannedWorkout to Intervals.icu and save to local DB."""

    existing = await get_ai_workout_by_external_id(workout.external_id)
    if existing and existing.status == "active":
        logger.info("Workout already exists: %s", workout.external_id)
        return

    intervals = IntervalsClient()
    event_data = workout.to_intervals_event()
    result = await intervals.create_event(event_data)
    intervals_id = result.get("id")

    await save_ai_workout(
        date_str=str(dt),
        sport=workout.sport,
        slot=workout.slot,
        external_id=workout.external_id,
        intervals_id=intervals_id,
        name=workout.name,
        description="; ".join(s.text for s in workout.steps if s.text),
        duration_minutes=workout.duration_minutes,
        target_tss=workout.target_tss,
        rationale=workout.rationale,
    )
    logger.info(
        "Workout pushed: AI: %s (%s) (%s, %d min) for %s",
        workout.name,
        workout.suffix,
        workout.sport,
        workout.duration_minutes,
        dt,
    )


async def _maybe_suggest_ramp(wellness_row, dt: date) -> None:
    """Suggest a ramp test if thresholds are stale and athlete is ready."""
    tsb = (wellness_row.ctl - wellness_row.atl) if wellness_row.ctl and wellness_row.atl else 0
    sport = await should_suggest_ramp(
        recovery_score=wellness_row.recovery_score or 0,
        recovery_category=wellness_row.recovery_category or "moderate",
        tsb=tsb,
    )
    if not sport:
        return

    # Don't suggest if there's already a workout planned for tomorrow
    tomorrow = dt + timedelta(days=1)
    planned = await get_scheduled_workouts_for_date(tomorrow)
    if planned:
        logger.info("Skipping ramp suggestion — workout planned for %s", tomorrow)
        return

    # Check if we already pushed a ramp test recently
    upcoming = await get_ai_workouts_upcoming(days_ahead=14)
    if any("Ramp Test" in (w.name or "") for w in upcoming):
        return

    freshness = await get_threshold_freshness_data(sport)
    days_since = freshness.get("days_since") or 0

    workout = create_ramp_test(sport, tomorrow, days_since)
    await _push_workout(workout, tomorrow)
    logger.info("Ramp test suggested: %s on %s (thresholds %d days old)", sport, tomorrow, days_since)


async def _get_latest_ra(dt: date) -> float | None:
    """Get the latest Ra (readiness) from yesterday's DFA analysis."""
    yesterday = dt - timedelta(days=1)
    hrv_analyses = await get_activity_hrv_for_date(yesterday)
    for h in hrv_analyses:
        if h.ra_pct is not None:
            return h.ra_pct
    return None


async def _record_training_log_pre(wellness_row, dt: date) -> None:
    """Record pre-workout context in training_log for today."""

    # Don't duplicate — skip if already recorded for this date
    existing = await get_training_log_for_date(dt)
    if existing:
        return

    workouts = await get_scheduled_workouts_for_date(dt)
    ai_workouts = await get_ai_workouts_for_date(dt)

    hrv_flatt = await get_hrv_analysis(str(dt), "flatt_esco")
    rhr_row = await get_rhr_analysis(str(dt))

    hrv_status = hrv_flatt.status if hrv_flatt else "insufficient_data"
    hrv_7d = hrv_flatt.rmssd_7d if hrv_flatt else 0
    hrv_today = float(wellness_row.hrv) if wellness_row.hrv else 0
    hrv_delta = ((hrv_today - hrv_7d) / hrv_7d * 100) if hrv_today and hrv_7d else 0.0
    tsb = (wellness_row.ctl - wellness_row.atl) if wellness_row.ctl and wellness_row.atl else 0
    ra = await _get_latest_ra(dt)

    pre_kwargs = dict(
        pre_recovery_score=wellness_row.recovery_score,
        pre_recovery_category=wellness_row.recovery_category,
        pre_hrv_status=hrv_status,
        pre_hrv_delta_pct=round(hrv_delta, 1),
        pre_rhr_today=rhr_row.rhr_today if rhr_row else None,
        pre_rhr_status=rhr_row.status if rhr_row else None,
        pre_tsb=round(tsb, 1),
        pre_ctl=wellness_row.ctl,
        pre_atl=wellness_row.atl,
        pre_ra_pct=ra,
        pre_sleep_score=wellness_row.sleep_score,
    )

    if workouts:
        for w in workouts:
            # Check if this workout was adapted
            adapted = next(
                (a for a in ai_workouts if a.sport == w.type and "adapted" in (a.rationale or "").lower()),
                None,
            )
            await create_training_log(
                date=str(dt),
                sport=w.type,
                source="adapted" if adapted else "humango",
                original_name=w.name,
                original_description=(w.description or "")[:500],
                original_duration_sec=w.moving_time,
                adapted_name=adapted.name if adapted else None,
                adapted_description=adapted.description if adapted else None,
                adapted_duration_sec=(adapted.duration_minutes * 60) if adapted else None,
                adaptation_reason=adapted.rationale if adapted else None,
                **pre_kwargs,
            )
    elif ai_workouts:
        for a in ai_workouts:
            await create_training_log(
                date=str(dt),
                sport=a.sport,
                source="ai",
                original_name=a.name,
                original_duration_sec=(a.duration_minutes or 0) * 60,
                **pre_kwargs,
            )
    else:
        await create_training_log(
            date=str(dt),
            source="none",
            **pre_kwargs,
        )

    logger.info("Training log pre-context recorded for %s", dt)


async def _fill_training_log_post(wellness_row, dt: date) -> None:
    """Fill post-outcome for yesterday's training_log entry using today's wellness."""

    yesterday = dt - timedelta(days=1)
    unfilled = await get_training_log_unfilled_post()
    targets = [r for r in unfilled if r.date == str(yesterday)]

    if not targets:
        return

    hrv_flatt = await get_hrv_analysis(str(dt), "flatt_esco")
    hrv_7d = hrv_flatt.rmssd_7d if hrv_flatt else 0
    hrv_today = float(wellness_row.hrv) if wellness_row.hrv else 0
    hrv_delta = ((hrv_today - hrv_7d) / hrv_7d * 100) if hrv_today and hrv_7d else 0.0
    rhr_row = await get_rhr_analysis(str(dt))
    ra = await _get_latest_ra(dt)

    for log in targets:
        pre_score = log.pre_recovery_score or 0
        post_score = wellness_row.recovery_score or 0

        await update_training_log(
            log.id,
            post_recovery_score=post_score,
            post_hrv_delta_pct=round(hrv_delta, 1),
            post_rhr_today=rhr_row.rhr_today if rhr_row else None,
            post_sleep_score=wellness_row.sleep_score,
            post_ra_pct=ra,
            recovery_delta=round(post_score - pre_score, 1),
        )

    logger.info("Training log post-outcome filled for %s (%d entries)", yesterday, len(targets))


async def sync_activities_job(days: int = 90) -> int:
    """Sync completed activities from Intervals.icu into the activities table.

    Runs as a separate cron job (every hour at :30).
    After upsert, fetches extended details for new activities that don't have
    an activity_details row yet. Pauses 1 sec between detail API calls.

    Returns count of upserted activities.
    """
    intervals = IntervalsClient()
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()
    oldest = today - timedelta(days=days)
    newest = today

    activities = await intervals.get_activities(oldest=oldest, newest=newest)
    count = await save_activities(activities)
    logger.info("Synced %d activities (%s → %s)", count, oldest, newest)

    # Fetch details for activities that don't have them yet
    synced_ids = [a.id for a in activities]
    if synced_ids:
        await _fetch_missing_details(intervals, synced_ids)

    # Fill training log actual data for unfilled entries
    try:
        await _fill_training_log_actual()
    except Exception:
        logger.warning("Failed to fill training log actual data", exc_info=True)

    return count


async def _fill_training_log_actual() -> None:
    """Fill actual workout data for training_log entries that have no compliance yet."""

    unfilled = await get_training_log_unfilled_actual()
    logger.info("Training log actual: %d unfilled entries", len(unfilled))
    if not unfilled:
        return

    filled_count = 0
    for log in unfilled:
        log_date = date.fromisoformat(log.date)
        logger.info(
            "Training log #%d: date=%s sport=%s workout_id=%s",
            log.id,
            log.date,
            log.sport,
            log.workout_id,
        )
        activities = await get_activities_for_date(log_date)
        logger.info(
            "Training log #%d: found %d activities for %s: %s",
            log.id,
            len(activities),
            log.date,
            [(a.id, a.type, a.moving_time) for a in activities],
        )

        if not activities:
            # No activity for this date — mark as skipped
            logger.info("Training log #%d: no activities, marking skipped", log.id)
            await update_training_log(log.id, compliance="skipped")
            filled_count += 1
            continue

        # Match by sport if specified
        matched = None
        if log.sport:
            matched = next((a for a in activities if a.type == log.sport), None)
            logger.info(
                "Training log #%d: sport match '%s' → %s",
                log.id,
                log.sport,
                matched.id if matched else "no match",
            )
        if not matched:
            matched = activities[0]  # best guess — first activity of the day
            logger.info("Training log #%d: fallback to first activity %s", log.id, matched.id)

        compliance = _detect_compliance(log, matched)
        logger.info(
            "Training log #%d: compliance=%s (activity=%s, sport=%s, duration=%s, hr=%s)",
            log.id,
            compliance,
            matched.id,
            matched.type,
            matched.moving_time,
            matched.average_hr,
        )

        await update_training_log(
            log.id,
            actual_activity_id=matched.id,
            actual_sport=matched.type,
            actual_duration_sec=matched.moving_time,
            actual_avg_hr=matched.average_hr,
            actual_tss=matched.icu_training_load,
            compliance=compliance,
        )
        filled_count += 1

    if filled_count:
        logger.info("Training log actual filled: %d entries", filled_count)


def _detect_compliance(log, activity) -> str:
    """Detect which plan variant the athlete followed."""
    if log.source == "none":
        return "unplanned"

    actual_dur = activity.moving_time or 0

    # Check adapted match
    if log.adapted_duration_sec:
        adapted_ratio = actual_dur / log.adapted_duration_sec if log.adapted_duration_sec else 0
        if 0.7 <= adapted_ratio <= 1.3:
            return "followed_adapted"

    # Check original match
    if log.original_duration_sec:
        original_ratio = actual_dur / log.original_duration_sec if log.original_duration_sec else 0
        if 0.7 <= original_ratio <= 1.3:
            if log.source == "ai":
                return "followed_ai"
            return "followed_original"

    return "modified"


async def _fetch_missing_details(intervals: IntervalsClient, activity_ids: list[str]) -> int:
    """Fetch and save activity details for IDs that lack an activity_details row.

    Returns count of details fetched.
    """
    existing_ids = await get_existing_detail_ids(activity_ids)
    missing_ids = [aid for aid in activity_ids if aid not in existing_ids]
    if not missing_ids:
        return 0

    fetched = 0
    for i, aid in enumerate(missing_ids):
        try:
            detail = await intervals.get_activity_detail(aid)
            if detail is None:
                logger.debug("Activity %s not found (404), skipping", aid)
                continue

            try:
                intervals_data = await intervals.get_activity_intervals(aid)
            except Exception:
                logger.warning("Failed to fetch intervals for %s, saving detail only", aid)
                intervals_data = None

            await save_activity_details(aid, detail, intervals_data)
            fetched += 1
            logger.debug("Fetched details for activity %s", aid)
        except Exception:
            logger.warning("Failed to fetch details for activity %s", aid, exc_info=True)

        if i < len(missing_ids) - 1:
            await asyncio.sleep(1)

    if fetched:
        logger.info("Fetched details for %d new activities", fetched)
    return fetched


async def daily_metrics_job(
    target_date: date | None = None,
    bot: Bot | None = None,
) -> None:
    intervals = IntervalsClient()
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()
    dt = target_date or today
    is_today = dt == today

    wellness = await intervals.get_wellness(dt)

    # Enrich sport_info with per-sport CTL from DB (not API)
    try:
        activity_rows = await get_activities_for_ctl(days=90, as_of=dt)
        activities = [
            Activity(
                id=r.id,
                start_date_local=r.start_date_local,
                type=r.type,
                icu_training_load=r.icu_training_load,
                moving_time=r.moving_time,
            )
            for r in activity_rows
        ]
        sport_ctl = calculate_sport_ctl(activities)
        _enrich_sport_info(wellness, sport_ctl)
    except Exception:
        logger.warning("Failed to enrich sport_info with per-sport CTL", exc_info=True)

    # Delay AI until sleep data is available, with 11:00 deadline
    has_sleep = wellness.sleep_score is not None
    past_deadline = datetime.now(tz).hour >= 11
    run_ai = is_today and (has_sleep or past_deadline)

    row, ai_is_new = await save_wellness(dt, wellness=wellness, run_ai=run_ai)

    # Send morning report once — only when AI recommendation first appears
    if ai_is_new and bot is not None:
        try:
            await _send_morning_report(row, bot)
        except Exception:
            logger.warning("Failed to send morning report", exc_info=True)

    # Generate AI workout if enabled and auto-push is on
    if ai_is_new and settings.AI_WORKOUT_ENABLED and settings.AI_WORKOUT_AUTO_PUSH:
        try:
            await _generate_and_push_workout(row, dt)
        except Exception:
            logger.warning("Failed to generate/push AI workout", exc_info=True)

    # Suggest ramp test if thresholds are stale
    if ai_is_new and settings.AI_WORKOUT_ENABLED:
        try:
            await _maybe_suggest_ramp(row, dt)
        except Exception:
            logger.warning("Failed to check/suggest ramp test", exc_info=True)

    # Training Log: record pre-context for today + fill post-outcome for yesterday
    # Independent of AI — runs on every first wellness save
    if is_today and row:
        try:
            await _record_training_log_pre(row, dt)
        except Exception:
            logger.warning("Failed to record training log pre-context", exc_info=True)
        try:
            await _fill_training_log_post(row, dt)
        except Exception:
            logger.warning("Failed to fill training log post-outcome", exc_info=True)


async def scheduled_workouts_job() -> None:
    """Fetch planned workouts for the next 14 days and upsert into DB."""
    intervals = IntervalsClient()
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()
    newest = today + timedelta(days=14)

    workouts = await intervals.get_events(oldest=today, newest=newest)
    count = await save_scheduled_workouts(workouts, oldest=today, newest=newest)
    logger.info("Synced %d scheduled workouts (%s → %s)", count, today, newest)
