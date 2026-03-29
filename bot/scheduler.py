import logging
import zoneinfo
from datetime import date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from bot.formatter import build_evening_message, build_morning_message, build_post_activity_message
from bot.utils import (
    enrich_sport_info,
    fetch_missing_details,
    fill_training_log_actual,
    fill_training_log_post,
    generate_and_push_workout,
    maybe_suggest_ramp,
    record_training_log_pre,
)
from config import settings
from data.database import ActivityHrvRow, ActivityRow, ScheduledWorkoutRow, WellnessRow, get_session
from data.hrv_activity import process_fit_job as _process_fit_job
from data.intervals_client import IntervalsClient
from data.metrics import calculate_sport_ctl
from data.models import Activity
from data.ramp_tests import detect_threshold_drift

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


async def evening_report_job(bot: Bot | None = None) -> None:
    """Send evening summary report to Telegram at 21:00."""
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()

    row = await WellnessRow.get(today)
    activities = await ActivityRow.get_for_date(today)

    # Skip if no data at all
    if not activities and row is None:
        logger.debug("Evening report skipped — no data for %s", today)
        return

    hrv_analyses = await ActivityHrvRow.get_for_date(today)
    tomorrow = today + timedelta(days=1)
    tomorrow_workouts = await ScheduledWorkoutRow.get_for_date(tomorrow)

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
    count = await ActivityRow.save_bulk(activities)
    logger.info("Synced %d activities (%s → %s)", count, oldest, newest)

    # Fetch details for activities that don't have them yet
    synced_ids = [a.id for a in activities]
    if synced_ids:
        await fetch_missing_details(intervals, synced_ids)

    # Fill training log actual data for unfilled entries
    try:
        await fill_training_log_actual()
    except Exception:
        logger.warning("Failed to fill training log actual data", exc_info=True)

    return count


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
        activity_rows = await ActivityRow.get_for_ctl(days=90, as_of=dt)
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
        enrich_sport_info(wellness, sport_ctl)
    except Exception:
        logger.warning("Failed to enrich sport_info with per-sport CTL", exc_info=True)

    # Delay AI until sleep data is available, with 11:00 deadline
    has_sleep = wellness.sleep_score is not None
    past_deadline = datetime.now(tz).hour >= 11
    run_ai = is_today and (has_sleep or past_deadline)

    row, ai_is_new = await WellnessRow.save(dt, wellness=wellness, run_ai=run_ai)

    # Send morning report once — only when AI recommendation first appears
    if ai_is_new and bot is not None:
        try:
            await _send_morning_report(row, bot)
        except Exception:
            logger.warning("Failed to send morning report", exc_info=True)

    # Generate AI workout if enabled and auto-push is on
    if ai_is_new and settings.AI_WORKOUT_ENABLED and settings.AI_WORKOUT_AUTO_PUSH:
        try:
            await generate_and_push_workout(row, dt)
        except Exception:
            logger.warning("Failed to generate/push AI workout", exc_info=True)

    # Suggest ramp test if thresholds are stale
    if ai_is_new and settings.AI_WORKOUT_ENABLED:
        try:
            await maybe_suggest_ramp(row, dt)
        except Exception:
            logger.warning("Failed to check/suggest ramp test", exc_info=True)

    # Training Log: record pre-context for today + fill post-outcome for yesterday
    # Independent of AI — runs on every first wellness save
    if is_today and row:
        try:
            await record_training_log_pre(row, dt)
        except Exception:
            logger.warning("Failed to record training log pre-context", exc_info=True)
        try:
            await fill_training_log_post(row, dt)
        except Exception:
            logger.warning("Failed to fill training log post-outcome", exc_info=True)


async def scheduled_workouts_job() -> None:
    """Fetch planned workouts for the next 14 days and upsert into DB."""
    intervals = IntervalsClient()
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()
    newest = today + timedelta(days=14)

    workouts = await intervals.get_events(oldest=today, newest=newest)
    count = await ScheduledWorkoutRow.save_bulk(workouts, oldest=today, newest=newest)
    logger.info("Synced %d scheduled workouts (%s → %s)", count, today, newest)
