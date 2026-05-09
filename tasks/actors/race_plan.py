"""Dramatiq actor — 24h pre-race plan push (PR2.6, spec §12 step 5).

Cron-driven: ``scheduler_pre_race_plan_push_job`` (in ``bot/scheduler.py``)
fires daily, finds users whose goal.event_date == today + 1, fans out one
``actor_send_pre_race_plan_push`` per (user, goal). The actor pulls the
latest plan row, renders it as a Markdown Telegram message, and sends with
an inline ``Open in webapp`` button.

Idempotency: ``payload.pushed_for_race_date`` is stamped after a successful
send. A second invocation for the same race_date short-circuits before
rendering — covers Dramatiq retry, scheduler misfire, and the rare case
where the cron fires twice (e.g. operator-triggered manual run).

Failures are non-fatal: if the user has no plan for the goal, log+skip
(athlete didn't generate one — that's their choice). If Telegram is
unreachable, log+skip without marking — next day's cron won't help (race
is today by then), so we accept silent loss rather than retry-loop.
"""

import logging

import dramatiq
from pydantic import validate_call

from bot.i18n import set_language
from bot.race_plan_telegram import build_open_in_webapp_keyboard, render_race_plan_for_telegram
from config import settings
from data.db import RacePlan, UserDTO
from tasks.tools import TelegramTool

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default")
@validate_call
def actor_send_pre_race_plan_push(user: UserDTO, goal_id: int, race_date: str) -> None:
    """Send the race-day plan to the athlete 24h before the event.

    Parameters
    ----------
    user
        Athlete to message (UserDTO; ``chat_id``, ``language`` are used).
    goal_id
        ``athlete_goals.id`` — the race we're pushing for.
    race_date
        Goal's ``event_date`` ISO string. Used as the idempotency key.
        Recomputed by the scheduler each tick — a goal that gets rescheduled
        will get a new push for the new date.
    """
    plan_row = RacePlan.get_latest_for_race(goal_id, user_id=user.id)
    if plan_row is None:
        logger.info(
            "No race plan to push for user_id=%d goal_id=%d race_date=%s — skipping",
            user.id,
            goal_id,
            race_date,
        )
        return

    payload = plan_row.payload or {}
    if payload.get("pushed_for_race_date") == race_date:
        logger.info(
            "Pre-race push already sent for user_id=%d goal_id=%d race_date=%s — skipping",
            user.id,
            goal_id,
            race_date,
        )
        return

    set_language(user.language or "ru")
    # Inline ``payload.race.name`` is the goal-snapshot copy (spec §11.3);
    # safer than re-querying AthleteGoal which might have been deleted.
    race_block = payload.get("race") or {}
    event_name = race_block.get("name") or "Race"

    text = render_race_plan_for_telegram(payload, event_name=event_name)
    keyboard = build_open_in_webapp_keyboard(settings.API_BASE_URL)

    tg = TelegramTool(user=user)
    sent = tg.send_message(text=text, reply_markup=keyboard, markdown=True)
    if not sent:
        # Telegram returned None → user blocked the bot, chat unreachable, etc.
        # Don't mark as pushed: next day is race day, no point retrying anyway,
        # but at least the watchdog logs surface the failure pattern.
        logger.warning(
            "Pre-race push send returned None for user_id=%d goal_id=%d race_date=%s — not marking",
            user.id,
            goal_id,
            race_date,
        )
        return

    if not RacePlan.mark_pushed_for_race_date(plan_row.id, race_date, user_id=user.id):
        # Defensive: row vanished between get and mark (concurrent deletion).
        # Athlete already got the message — log so we know an unmarked-send
        # case happened.
        logger.warning(
            "Pre-race push sent but mark_pushed failed for user_id=%d plan_id=%d (row vanished?)",
            user.id,
            plan_row.id,
        )
        return

    logger.info(
        "Pre-race push sent for user_id=%d goal_id=%d race_date=%s plan_id=%d",
        user.id,
        goal_id,
        race_date,
        plan_row.id,
    )
