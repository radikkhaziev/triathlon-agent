"""Endurance Score Dramatiq actors (Phase 2 of `docs/ENDURANCE_SCORE_SPEC.md`).

Two actors per spec §7.2:

  · `actor_snapshot_endurance_scores(user_id)` — per-user, idempotent upsert
    of today's ES row. Fired from Level-1 hooks (`actor_user_wellness`,
    `actor_fetch_user_activities`) after a successful data write, and as a
    sub-job of the all-users wrapper.

  · `actor_snapshot_endurance_scores_all_users` — Level-2 cron wrapper. Iterates
    every active user and dispatches the per-user actor. Default daily 18:30
    Belgrade trigger registered in `tasks/scheduler.py`.

The actor delegates all data-fetching + compute to `data.endurance_score_service`,
keeping the Dramatiq layer thin (per the project pattern — see
`tasks/actors/reports.py` vs `data/race_plan_service.py`).
"""

from __future__ import annotations

import logging

import dramatiq
from sqlalchemy import select

from data.db import User, get_sync_session
from data.endurance_score_service import recompute_and_upsert
from tasks.dto import local_today

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default", max_retries=0)
def actor_snapshot_endurance_scores(user_id: int) -> None:
    """Compute + upsert today's ES row for one user. Idempotent.

    Safe to call multiple times per day — `EnduranceScore.upsert` uses
    ``ON CONFLICT (user_id, snapshot_date) DO UPDATE`` so re-fires overwrite
    the day's row with fresh data instead of producing duplicates.

    ``max_retries=0`` because (a) we catch every exception below — the
    swallow makes Dramatiq retry config dead, so we make intent explicit;
    (b) the next Level-1 hook (any wellness/activities sync) or the daily
    Level-2 cron will retry implicitly. We don't want stuck wellness/
    activities pipelines because of an ES failure either.
    """
    today = local_today()
    try:
        outcome = recompute_and_upsert(user_id, today, force=True)
        logger.debug(
            "endurance_score user=%s date=%s score=%s zone=%s",
            user_id,
            today,
            outcome.result.score,
            outcome.result.zone_id,
        )
    except Exception:
        logger.exception("Failed to snapshot endurance score for user %s on %s", user_id, today)


@dramatiq.actor(queue_name="default", max_retries=1)
def actor_snapshot_endurance_scores_all_users() -> None:
    """Daily safety-net cron — fires per-user actor for every active user.

    Catches users whose Level-1 actors didn't fire today (Intervals.icu sync
    down, user offline) and captures natural decay of components rolling out
    of the 28d/8w windows even without a fresh wellness/activities write.
    """
    with get_sync_session() as session:
        # Only active athletes (have Intervals.icu connected) get a snapshot.
        # ``is_active`` alone would include demo / viewer / not-yet-onboarded
        # users → wasted compute writing `insufficient_data` rows.
        active_users = list(
            session.execute(select(User.id).where(User.is_active.is_(True), User.athlete_id.isnot(None)))
            .scalars()
            .all()
        )

    logger.info("Dispatching endurance_score snapshot for %d active athletes", len(active_users))
    for uid in active_users:
        actor_snapshot_endurance_scores.send(user_id=uid)
