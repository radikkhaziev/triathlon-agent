import asyncio
import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from data.database import save_daily_metrics
from data.garmin_client import GarminClient
from data.models import SleepData

logger = logging.getLogger(__name__)


def create_scheduler() -> AsyncIOScheduler:
    # Initialize the GarminClient singleton with credentials.
    # All subsequent GarminClient() calls return this instance.
    try:
        GarminClient(settings.GARMIN_EMAIL, settings.GARMIN_PASSWORD.get_secret_value())
    except Exception as exc:
        logger.error("Failed to initialize GarminClient: %s", exc)

    scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

    scheduler.add_job(
        daily_metrics_job,
        trigger="cron",
        hour="5-20",
        minute="*/15",
        id="daily_metrics",
    )

    return scheduler


async def daily_metrics_job() -> None:
    try:
        garmin = GarminClient()
    except RuntimeError as exc:
        logger.warning("Skipping daily_metrics_job: %s", exc)
        return

    today = date.today()
    today_str = str(today)

    sleep: SleepData = await asyncio.to_thread(garmin.get_sleep, today_str)

    await save_daily_metrics(today, sleep_data=sleep)
