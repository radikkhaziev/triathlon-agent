import asyncio
import json
import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from data.database import save_daily_metrics
from data.garmin_client import GarminClient
from data.models import SleepData

logger = logging.getLogger(__name__)


async def create_scheduler(bot) -> AsyncIOScheduler:
    gc = GarminClient()
    status = "connected" if gc.profile else "disconnected"
    if gc.profile:
        status = json.dumps(gc.profile, indent=2)

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


async def daily_metrics_job(
    target_date: date | None = None,
    bot=None,
) -> None:
    garmin = GarminClient()

    dt = target_date or date.today()

    sleep: SleepData = await asyncio.to_thread(garmin.get_sleep, str(dt))

    await save_daily_metrics(dt, sleep_data=sleep, bot=bot)
