import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dramatiq import group

from config import settings
from data.db import UserDTO
from tasks.actors import (
    actor_compose_user_evening_report,
    actor_compose_user_morning_report,
    actor_fetch_user_activities,
    actor_sync_athlete_goals,
    actor_user_scheduled_workouts,
    actor_user_wellness,
)

from .decorator import with_athletes

logger = logging.getLogger(__name__)


@with_athletes
async def scheduler_scheduled_workouts(athletes: list[UserDTO]) -> None:
    """Fetch planned workouts for the next 14 days and upsert into DB."""
    _group = group([actor_user_scheduled_workouts.message(user=a) for a in athletes])
    _group.run()

    logger.info("Dispatched scheduled_workouts for %d athletes", len(athletes))


@with_athletes
async def scheduler_wellness_job(athletes: list[UserDTO]) -> None:
    _group = group([actor_user_wellness.message(user=a) for a in athletes])
    _group.run()


@with_athletes
async def scheduler_morning_report_job(athletes: list[UserDTO]) -> None:
    _group = group([actor_compose_user_morning_report.message(user=a) for a in athletes])
    _group.run()


@with_athletes
async def scheduler_evening_report_job(athletes: list[UserDTO]) -> None:
    _group = group([actor_compose_user_evening_report.message(user=a) for a in athletes])
    _group.run()


@with_athletes
async def scheduler_activities_job(athletes: list[UserDTO]) -> None:
    _group = group([actor_fetch_user_activities.message(user=a) for a in athletes])
    _group.run()


@with_athletes
async def scheduler_sync_goals_job(athletes: list[UserDTO]) -> None:
    _group = group([actor_sync_athlete_goals.message(user=a) for a in athletes])
    _group.run()


async def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

    scheduler.add_job(
        scheduler_scheduled_workouts,
        trigger="cron",
        hour="4-23",
        minute=0,
        id="scheduler_scheduled_workouts",
    )

    # TODO: объединить scheduler_wellness_job, scheduler_morning_report_job
    scheduler.add_job(
        scheduler_wellness_job,
        trigger="cron",
        hour="4-23",
        minute="*/10",
        id="scheduler_wellness_job",
    )

    scheduler.add_job(
        scheduler_morning_report_job,
        trigger="cron",
        hour="5-11",
        minute="*/10",
        id="scheduler_morning_report_job",
    )

    scheduler.add_job(
        scheduler_activities_job,
        trigger="cron",
        hour="4-23",
        minute="*/10",
        id="scheduler_activities_job",
    )

    scheduler.add_job(
        scheduler_evening_report_job,
        trigger="cron",
        hour=19,
        minute=00,
        id="scheduler_evening_report_job",
    )

    scheduler.add_job(
        scheduler_sync_goals_job,
        trigger="cron",
        hour="4-23",
        minute=30,
        id="scheduler_sync_goals_job",
    )

    return scheduler
