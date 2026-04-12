import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from dramatiq import group

from config import settings
from data.db import UserDTO
from tasks.actors import (
    actor_compose_user_evening_report,
    actor_compose_user_morning_report,
    actor_compose_weekly_report,
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
async def scheduler_wellness_and_reports_job(athletes: list[UserDTO]) -> None:
    """Wellness sync + morning report generation (staggered to avoid rate limits)."""
    group([actor_user_wellness.message(user=a) for a in athletes]).run()
    for i, a in enumerate(athletes):
        actor_compose_user_morning_report.send_with_options(kwargs={"user": a}, delay=60_000 + i * 20_000)


@with_athletes
async def scheduler_evening_report_job(athletes: list[UserDTO]) -> None:
    _group = group([actor_compose_user_evening_report.message(user=a) for a in athletes])
    _group.run()


@with_athletes
async def scheduler_weekly_report_job(athletes: list[UserDTO]) -> None:
    for i, a in enumerate(athletes):
        actor_compose_weekly_report.send_with_options(kwargs={"user": a}, delay=i * 30_000)
    logger.info("Dispatched weekly report for %d athletes", len(athletes))


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

    scheduler.add_job(
        scheduler_wellness_and_reports_job,
        trigger=OrTrigger(
            [
                CronTrigger(hour="4-8", minute="*/10", timezone=settings.TIMEZONE),
                CronTrigger(hour="9-22", minute="*/30", timezone=settings.TIMEZONE),
            ]
        ),
        id="scheduler_wellness_and_reports_job",
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
        minute=0,
        id="scheduler_evening_report_job",
    )

    scheduler.add_job(
        scheduler_weekly_report_job,
        trigger=CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=settings.TIMEZONE),
        id="scheduler_weekly_report_job",
    )

    scheduler.add_job(
        scheduler_sync_goals_job,
        trigger="cron",
        hour="4-23",
        minute=30,
        id="scheduler_sync_goals_job",
    )

    return scheduler
