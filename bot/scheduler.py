import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dramatiq import group
from pydantic import TypeAdapter
from sqlalchemy import select

from config import settings
from data.db import AthleteGoal, User, UserBackfillState, UserDTO, get_session
from tasks.actors import (
    actor_bootstrap_step,
    actor_compose_user_evening_report,
    actor_compose_weekly_report,
    actor_publish_weekly_changelog,
    actor_retrain_progression_model,
    actor_retrain_race_models,
    actor_send_onboarding_hey,
    actor_send_pre_race_plan_push,
    actor_snapshot_endurance_scores_all_users,
)
from tasks.dto import local_today

from .decorator import with_athletes

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
async def scheduler_ml_retrain_job(athletes: list[UserDTO]) -> None:
    """Retrain weekly ML models (progression + race-projection) for all athletes.

    Dispatches to the isolated `ml_retrain` queue (issue #348) — processed by
    a dedicated single-threaded `ml-worker` container (`--threads 1 --processes 1`)
    so XGBoost CPU spikes don't compete with the default worker (Telegram /
    wellness / webhooks). Worker pulls FIFO one-by-one — no parallel CPU spike
    by construction; explicit `delay` would just idle the worker waiting for
    the next message's visibility timestamp, so we send without delays.

    Race retrain shares the slot but has its own actor — `InsufficientDataError`
    in race-train doesn't poison progression's run (and vice versa).
    """
    for a in athletes:
        actor_retrain_progression_model.send(user=a, sport="Ride")
        actor_retrain_race_models.send(user=a)
    logger.info("Dispatched progression + race-model retraining for %d athletes", len(athletes))


async def scheduler_deactivate_inactive_users_job() -> None:
    """Flip dormant users to ``is_active=False`` to stop morning-report token
    spend on accounts that haven't touched the bot or webapp in 30 days.

    Reversible — users return to active state on next /start (via
    ``set_active_by_chat_id``). See ``User.deactivate_stale`` for the SQL.
    Daily 04:00 Belgrade — quiet window between ML retrain (Sun 03:00) and
    the first morning-wellness fetch (~04:00).
    """
    ids = await User.deactivate_stale(threshold_days=30)
    if ids:
        logger.info("Deactivated %d stale users: %s", len(ids), ids)


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


async def scheduler_pre_race_plan_push_job() -> None:
    """Daily fan-out: any active goals with ``event_date == today + 1``?

    Bypass the ``with_athletes`` decorator — most days no users qualify, so a
    goal-side query (filtered by event_date) is cheaper than iterating all
    athletes. The actor handles per-goal idempotency via
    ``payload.pushed_for_race_date`` (PR2.6 / spec §12 step 5).
    """
    target_date = local_today() + timedelta(days=1)
    async with get_session() as session:
        result = await session.execute(
            select(AthleteGoal, User)
            .join(User, AthleteGoal.user_id == User.id)
            .where(
                AthleteGoal.event_date == target_date,
                AthleteGoal.is_active.is_(True),
                User.is_active.is_(True),
            )
        )
        # Pre-extract everything we need INSIDE the session-with block. Per
        # review L1: ORM rows become detached after the ``async with`` exits,
        # and any future field access (e.g. ``goal.event_name``) on a detached
        # async row would raise ``MissingGreenlet``. Eager extraction makes the
        # dispatch loop tolerant to refactors that need richer fields.
        dispatches = [(goal.id, _UserAdapter.validate_python(user_row)) for goal, user_row in result.all()]

    if not dispatches:
        logger.debug("No goals scheduled for race tomorrow (%s)", target_date)
        return

    target_iso = target_date.isoformat()
    for goal_id, user_dto in dispatches:
        actor_send_pre_race_plan_push.send(user=user_dto, goal_id=goal_id, race_date=target_iso)
    logger.info("Dispatched pre-race push for %d goals (race_date=%s)", len(dispatches), target_iso)


async def scheduler_endurance_snapshot_job() -> None:
    """Daily 18:30 Belgrade — safety-net Endurance Score snapshot for all users.

    Spec §7.0 Level 2 / §7.1. Captures users whose Level-1 hooks (wellness +
    activities actors) didn't fire that day (Intervals.icu sync down, user
    offline) and catches natural decay of components rolling out of the 28d
    / 8w windows even without a fresh write. Fires at 18:30 — 30 min before
    the weekly-report cron, leaving the actor's per-user dispatch + compute
    a comfortable window to drain. ``misfire_grace_time=3600`` covers a
    restart within the hour; ``coalesce=True`` collapses missed firings into
    one. The actor itself is idempotent.
    """
    actor_snapshot_endurance_scores_all_users.send()


async def scheduler_publish_weekly_changelog_job() -> None:
    """Sunday 15:00 — single fan-out, no per-user dispatch.

    Чем не 19:30 после weekly: 4-часовой буфер до weekly report (Sun 19:00)
    нужен чтобы успеть глазами проверить Discussion и поправить вручную если
    Claude выдал ересь — weekly report уйдёт атлетам со ссылкой на свежий
    changelog.
    """
    actor_publish_weekly_changelog.send()


async def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

    scheduler.add_job(
        scheduler_evening_report_job,
        trigger="cron",
        day_of_week="mon-sat",
        hour=19,
        minute=0,
        id="scheduler_evening_report_job",
        misfire_grace_time=3600,
        coalesce=True,
    )

    scheduler.add_job(
        scheduler_weekly_report_job,
        trigger=CronTrigger(day_of_week="sun", hour=19, minute=0, timezone=settings.TIMEZONE),
        id="scheduler_weekly_report_job",
        misfire_grace_time=7200,
        coalesce=True,
    )

    scheduler.add_job(
        scheduler_ml_retrain_job,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=settings.TIMEZONE),
        id="scheduler_ml_retrain_job",
        misfire_grace_time=7200,
        coalesce=True,
    )

    # Daily stale-user deactivation — 04:00 Belgrade, quiet window between ML
    # retrain (Sun 03:00) and the first morning-wellness fetch (~04:00).
    scheduler.add_job(
        scheduler_deactivate_inactive_users_job,
        trigger=CronTrigger(hour=4, minute=0, timezone=settings.TIMEZONE),
        id="scheduler_deactivate_inactive_users_job",
        misfire_grace_time=7200,
        coalesce=True,
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

    # 24h pre-race plan push (PR2.6, spec §12 step 5). 08:00 local is the
    # waking-hours sweet spot: athlete sees the plan over morning coffee, has
    # a full day to read + raise questions in chat, before tomorrow's race.
    # ``misfire_grace_time=7200`` (2h) covers a deploy/restart window without
    # silently dropping the only push the athlete gets — coalesce true means
    # multiple missed firings collapse into one (idempotency in the actor
    # protects against double-send anyway).
    scheduler.add_job(
        scheduler_pre_race_plan_push_job,
        trigger=CronTrigger(hour=8, minute=0, timezone=settings.TIMEZONE),
        id="scheduler_pre_race_plan_push_job",
        misfire_grace_time=7200,
        coalesce=True,
    )

    # Endurance Score daily snapshot (docs/ENDURANCE_SCORE_SPEC.md §7.1).
    # 18:30 Belgrade — Level-2 safety-net for all active users. See the job's
    # docstring for why this slot.
    scheduler.add_job(
        scheduler_endurance_snapshot_job,
        trigger=CronTrigger(hour=18, minute=30, timezone=settings.TIMEZONE),
        id="scheduler_endurance_snapshot_job",
        misfire_grace_time=3600,
        coalesce=True,
    )

    # Weekly changelog publisher (docs/WEEKLY_CHANGELOG_SPEC.md). Sun 15:00
    # leaves a 4h buffer before the weekly report (Sun 19:00) so the owner
    # can glance over the Discussion and patch by hand if Claude misfired.
    # Empty CHANGELOG_REPO_ID / CHANGELOG_DISCUSSION_CATEGORY_ID make the
    # actor a no-op — registering the job unconditionally is harmless.
    scheduler.add_job(
        scheduler_publish_weekly_changelog_job,
        trigger=CronTrigger(day_of_week="sun", hour=15, minute=0, timezone=settings.TIMEZONE),
        id="scheduler_publish_weekly_changelog_job",
        misfire_grace_time=7200,
        coalesce=True,
    )

    return scheduler
