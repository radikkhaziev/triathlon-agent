import asyncio
import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from data.database import save_daily_metrics
from data.garmin_client import GarminClient

logger = logging.getLogger(__name__)


async def create_scheduler(bot) -> AsyncIOScheduler:
    gc = GarminClient()
    status = "connected" if gc.profile else "disconnected"

    await bot.send_message(
        chat_id=settings.TELEGRAM_CHAT_ID,
        text=f"Bot started\nGarmin: {status}",
    )

    scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

    scheduler.add_job(
        daily_metrics_job,
        trigger="cron",
        hour="6-23",
        minute="*/15",
        id="daily_metrics",
        kwargs={"bot": bot},
    )

    return scheduler


async def _fetch_garmin_data(garmin: GarminClient, dt: date) -> dict:
    """Fetch all morning data from Garmin in parallel threads."""
    date_str = str(dt)

    # Parallel threads are safe: GarminClient._rate_limit() uses threading.Lock
    # to serialize requests with 1s spacing even when called from multiple threads.
    sleep, hrv, body_battery, resting_hr, readiness, workouts = await asyncio.gather(
        asyncio.to_thread(garmin.get_sleep, date_str),
        asyncio.to_thread(garmin.get_hrv, date_str),
        asyncio.to_thread(garmin.get_body_battery, date_str, date_str),
        asyncio.to_thread(garmin.get_resting_hr, date_str),
        asyncio.to_thread(garmin.get_training_readiness, date_str),
        asyncio.to_thread(garmin.get_scheduled_workouts, date_str, date_str),
    )

    bb_morning = body_battery[0].start_value if body_battery else None

    return {
        "sleep": sleep,
        "hrv": hrv,
        "body_battery_morning": bb_morning,
        "resting_hr": resting_hr,
        "readiness": readiness,
        "workouts": workouts,
    }


async def daily_metrics_job(
    target_date: date | None = None,
    bot=None,
) -> None:
    garmin = GarminClient()
    dt = target_date or date.today()

    data = await _fetch_garmin_data(garmin, dt)

    await save_daily_metrics(
        dt,
        sleep_data=data["sleep"],
        hrv_data=data["hrv"],
        body_battery_morning=data["body_battery_morning"],
        resting_hr=data["resting_hr"],
        readiness=data["readiness"],
        workouts=data["workouts"],
        bot=bot,
    )
