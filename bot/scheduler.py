import logging
from datetime import date, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from data.database import save_scheduled_workouts, save_wellness
from data.intervals_client import IntervalsClient

logger = logging.getLogger(__name__)


async def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

    scheduler.add_job(
        daily_metrics_job,
        trigger="cron",
        hour="5-23",
        minute="*/15",
        id="daily_metrics",
    )

    scheduler.add_job(
        scheduled_workouts_job,
        trigger="cron",
        hour="4-23",
        minute=0,
        id="scheduled_workouts",
    )

    return scheduler


async def daily_metrics_job(
    target_date: date | None = None,
) -> None:
    intervals = IntervalsClient()
    dt = target_date or date.today()
    is_today = dt == date.today()

    wellness = await intervals.get_wellness(dt)

    await save_wellness(dt, wellness=wellness, run_ai=is_today)


async def scheduled_workouts_job() -> None:
    """Fetch planned workouts for the next 14 days and upsert into DB."""
    intervals = IntervalsClient()
    today = date.today()
    newest = today + timedelta(days=14)

    workouts = await intervals.get_events(oldest=today, newest=newest)
    count = await save_scheduled_workouts(workouts)
    logger.info("Synced %d scheduled workouts (%s → %s)", count, today, newest)
