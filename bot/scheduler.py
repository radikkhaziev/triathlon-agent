import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from dramatiq import group
from pydantic import TypeAdapter
from sqlalchemy import select

from config import settings
from data.db import User, UserBackfillState, UserDTO, get_session
from tasks.actors import (
    actor_bootstrap_step,
    actor_compose_user_evening_report,
    actor_compose_weekly_report,
    actor_fetch_user_activities,
    actor_retrain_progression_model,
    actor_send_onboarding_hey,
    actor_sync_athlete_goals,
    actor_user_scheduled_workouts,
    actor_user_wellness,
)

from .decorator import with_athletes, with_legacy_athletes

logger = logging.getLogger(__name__)

_UserAdapter = TypeAdapter(UserDTO)
_BOOTSTRAP_STUCK_THRESHOLD_MIN = 15

# After this many consecutive watchdog re-kicks without the cursor advancing
# we give up and mark the row failed. Each kick is ~10 min apart, so 3 kicks
# ~= 30 min of bootstrap making zero progress. Prevents infinite re-kick of a
# chain that Dramatiq exhausted retries on (e.g. persistent Intervals 5xx on
# one date range) — see code review M1 + docs/OAUTH_BOOTSTRAP_SYNC_SPEC.md §17.
_BOOTSTRAP_MAX_WATCHDOG_KICKS = 3
_WATCHDOG_KICK_PREFIX = "watchdog_kick_"
_WATCHDOG_EXHAUSTED_SENTINEL = "watchdog_exhausted"


def _parse_kick_count(last_error: str | None) -> int:
    """Extract the watchdog kick counter from ``last_error``. Returns 0 for
    any other value — including None, sentinels like ``EMPTY_INTERVALS``, or
    unrelated error strings (we never overwrite those; instead we skip the
    row since it's not in a watchdog-retriable state)."""
    if not last_error or not last_error.startswith(_WATCHDOG_KICK_PREFIX):
        return 0
    try:
        return int(last_error[len(_WATCHDOG_KICK_PREFIX) :])
    except ValueError:
        return 0


@with_legacy_athletes
async def scheduler_scheduled_workouts(athletes: list[UserDTO]) -> None:
    """Fetch planned workouts for the next 14 days and upsert into DB."""
    _group = group([actor_user_scheduled_workouts.message(user=a) for a in athletes])
    _group.run()

    logger.info("Dispatched scheduled_workouts for %d athletes", len(athletes))


@with_legacy_athletes
async def scheduler_wellness(athletes: list[UserDTO]) -> None:
    """Wellness sync + morning report generation (staggered to avoid rate limits)."""
    group([actor_user_wellness.message(user=a) for a in athletes]).run()


@with_athletes
async def scheduler_evening_report_job(athletes: list[UserDTO]) -> None:
    _group = group([actor_compose_user_evening_report.message(user=a) for a in athletes])
    _group.run()


@with_athletes
async def scheduler_weekly_report_job(athletes: list[UserDTO]) -> None:
    for i, a in enumerate(athletes):
        actor_compose_weekly_report.send_with_options(kwargs={"user": a}, delay=i * 30_000)
    logger.info("Dispatched weekly report for %d athletes", len(athletes))


@with_legacy_athletes
async def scheduler_activities_job(athletes: list[UserDTO]) -> None:
    _group = group([actor_fetch_user_activities.message(user=a) for a in athletes])
    _group.run()


@with_athletes
async def scheduler_progression_model_job(athletes: list[UserDTO]) -> None:
    """Retrain progression models weekly for all athletes with enough data."""
    for i, a in enumerate(athletes):
        actor_retrain_progression_model.send_with_options(
            kwargs={"user": a, "sport": "Ride"},
            delay=i * 30_000,
        )
    logger.info("Dispatched progression model retraining for %d athletes", len(athletes))


@with_legacy_athletes
async def scheduler_sync_goals_job(athletes: list[UserDTO]) -> None:
    _group = group([actor_sync_athlete_goals.message(user=a) for a in athletes])
    _group.run()


async def scheduler_watchdog_bootstrap() -> None:
    """Re-kick bootstrap chains that stalled after Dramatiq exhausted retries.

    Rows with ``status='running'`` AND ``last_step_at`` older than 15 min are
    stuck — either the worker died mid-step, or all retries of the ``send(next)``
    call after ``advance_cursor`` failed to persist in Redis. We re-dispatch
    ``actor_bootstrap_step`` with the DB cursor; the actor's cursor CAS guard
    will pick up from the last committed position and continue the chain.

    Escalation: after ``_BOOTSTRAP_MAX_WATCHDOG_KICKS`` consecutive kicks
    without the cursor advancing (tracked via the ``watchdog_kick_N`` counter
    in ``last_error``), we give up on the row and ``mark_failed``. Prevents
    infinite re-kick of a chain Dramatiq can never complete — a persistent
    Intervals 5xx on one date range would otherwise spin forever at our
    upstream's expense.
    """
    stuck = await UserBackfillState.list_stuck(threshold_min=_BOOTSTRAP_STUCK_THRESHOLD_MIN)
    if not stuck:
        return

    async with get_session() as session:
        for state in stuck:
            prev_kicks = _parse_kick_count(state.last_error)
            if prev_kicks >= _BOOTSTRAP_MAX_WATCHDOG_KICKS:
                logger.error(
                    "watchdog_bootstrap: exhausted user=%d cursor=%s kicks=%d — marking failed",
                    state.user_id,
                    state.cursor_dt,
                    prev_kicks,
                )
                await UserBackfillState.mark_failed(
                    user_id=state.user_id,
                    error=_WATCHDOG_EXHAUSTED_SENTINEL,
                )
                continue

            db_user = await session.get(User, state.user_id)
            if db_user is None or not db_user.is_active:
                logger.warning(
                    "watchdog_bootstrap: skip user_id=%d (missing or inactive)",
                    state.user_id,
                )
                continue

            user_dto = _UserAdapter.validate_python(db_user)
            next_kicks = prev_kicks + 1
            logger.warning(
                "watchdog_bootstrap: re-kick user=%d cursor=%s chunks_done=%d kick=%d/%d",
                state.user_id,
                state.cursor_dt,
                state.chunks_done,
                next_kicks,
                _BOOTSTRAP_MAX_WATCHDOG_KICKS,
            )
            await UserBackfillState.bump_watchdog_kick(
                user_id=state.user_id,
                kick_number=next_kicks,
            )
            actor_bootstrap_step.send(
                user=user_dto,
                cursor_dt=state.cursor_dt.isoformat(),
                period_days=state.period_days,
            )


async def scheduler_onboarding_hey_job() -> None:
    """Cron tick: nudge athletes who completed OAuth bootstrap 24-48h ago
    (issue #258). The SQL filter is intentionally simple — only
    ``status='completed'``, ``hey_message IS NULL``, and ``finished_at`` in
    the [24h, 48h] window. We do NOT additionally check whether the athlete
    has already chatted: the message is friendly and getting it once even
    after an early reply is fine. Idempotency is owned by the actor —
    ``mark_hey_sent`` uses a ``RETURNING`` row guard so two parallel actors
    never both send.
    """
    user_ids = await UserBackfillState.list_eligible_for_hey(
        min_age=timedelta(hours=24),
        max_age=timedelta(hours=48),
    )
    if not user_ids:
        return

    # Batch-load Users in a single query rather than N+1 ``session.get()``.
    async with get_session() as session:
        result = await session.execute(select(User).where(User.id.in_(user_ids)))
        db_users = list(result.scalars().all())
        users = [_UserAdapter.validate_python(u) for u in db_users]

    for u in users:
        actor_send_onboarding_hey.send(user=u)
    logger.info("Dispatched onboarding hey-message for %d athletes", len(users))


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
        scheduler_wellness,
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

    scheduler.add_job(
        scheduler_progression_model_job,
        trigger=CronTrigger(day_of_week="sun", hour=16, minute=0, timezone=settings.TIMEZONE),
        id="scheduler_progression_model_job",
    )

    scheduler.add_job(
        scheduler_watchdog_bootstrap,
        trigger="cron",
        minute="*/10",
        id="scheduler_watchdog_bootstrap",
    )

    # Post-onboarding hey-message — daytime only (09:00-21:00 local) so we
    # don't ping someone in their sleep. Hourly is plenty: candidates come
    # off the cron the first tick after their 24h mark and have a 24h grace
    # window to be picked up. See issue #258.
    scheduler.add_job(
        scheduler_onboarding_hey_job,
        trigger="cron",
        hour="9-21",
        minute=0,
        id="scheduler_onboarding_hey_job",
    )

    return scheduler
