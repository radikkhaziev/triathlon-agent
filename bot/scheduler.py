import asyncio
import logging
import zoneinfo
from datetime import date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from config import settings
from data.database import (
    ActivityHrvRow,
    ActivityRow,
    get_activities_for_ctl,
    get_activities_for_date,
    get_activity_hrv_for_date,
    get_existing_detail_ids,
    get_scheduled_workouts_for_date,
    get_session,
    get_wellness,
    save_activities,
    save_activity_details,
    save_scheduled_workouts,
    save_wellness,
)
from data.intervals_client import IntervalsClient
from data.metrics import calculate_sport_ctl
from data.models import Activity

logger = logging.getLogger(__name__)


async def process_fit_job(batch_size: int = 5, bot: Bot | None = None) -> int:
    """Process FIT files for unanalyzed bike/run activities (DFA alpha 1).

    Runs every 5 min. Wrapper around data.hrv_activity.process_fit_job.
    Sends Telegram notification for each successfully processed activity.
    """
    from data.hrv_activity import process_fit_job as _process_fit_job

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
    from bot.formatter import build_post_activity_message

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
    from bot.formatter import build_evening_message

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
    from bot.formatter import build_morning_message

    summary = build_morning_message(row)
    webapp_url = f"{settings.API_BASE_URL}/report.html"
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
    count = await save_activities(activities)
    logger.info("Synced %d activities (%s → %s)", count, oldest, newest)

    # Fetch details for activities that don't have them yet
    synced_ids = [a.id for a in activities]
    if synced_ids:
        await _fetch_missing_details(intervals, synced_ids)

    return count


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


async def scheduled_workouts_job() -> None:
    """Fetch planned workouts for the next 14 days and upsert into DB."""
    intervals = IntervalsClient()
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()
    newest = today + timedelta(days=14)

    workouts = await intervals.get_events(oldest=today, newest=newest)
    count = await save_scheduled_workouts(workouts, oldest=today, newest=newest)
    logger.info("Synced %d scheduled workouts (%s → %s)", count, today, newest)
