"""Shared constants for actor modules."""

import logging
from collections import defaultdict

import dramatiq
from dramatiq import group
from pydantic import validate_call
from sqlalchemy import select

from data.db import Activity, AthleteSettings, AthleteThresholdsDTO, User, UserDTO, Wellness, get_sync_session
from data.metrics import calculate_banister_for_date, calculate_sport_atl, calculate_sport_ctl
from tasks.dto import DateDTO

from .training_log import actor_fill_training_log, actor_fill_training_log_post

logger = logging.getLogger(__name__)


CATEGORY_TO_READINESS = {
    "excellent": "green",
    "good": "green",
    "moderate": "yellow",
    "low": "red",
}


def is_user_dormant(user_id: int, actor_name: str) -> bool:
    """Single-source gate for webhook-driven actors against dormant accounts.

    Returns True (skip the actor) when the User row is missing or has
    ``is_active=False`` — Intervals.icu keeps pushing webhooks for dormant
    users (we intentionally don't filter at the webhook receiver so event
    history stays consistent), but running the expensive recompute / API
    fetch / Telegram push is wasted spend.

    Logs at INFO with a stable ``actor_name`` tag for grep'ability.
    `actor_name` is the caller's actor function name — pass `__name__`-style
    string literal, not the function object.

    APP_SCOPE_CHANGED is intentionally NOT gated by this helper — token
    clearing on revoke must always run, regardless of dormancy. Direct
    DB writes (ACTIVITY_UPLOADED row save) also bypass — they're cheap
    and we want to preserve activity history for a reactivating user.

    Cron / scheduler dispatch is filtered separately via `@with_athletes`
    (which calls `User.get_active_athletes`) and doesn't need this helper.
    """
    with get_sync_session() as session:
        user_row = session.get(User, user_id)
        if user_row is None:
            logger.info("%s skipped: user %d no longer exists", actor_name, user_id)
            return True
        if not user_row.is_active:
            logger.info("%s skipped: user %d is inactive", actor_name, user_id)
            return True
    return False


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_enrich_wellness_sport_info(
    user: UserDTO,
    dt: DateDTO,
) -> None:
    """Enrich wellness with per-sport CTL + ATL from DB (not API).

    Uses a 200-day activity window so the CTL EMA (τ=42) and ATL EMA (τ=7) both
    have ~5τ of warm-up — see `docs/PER_SPORT_LOAD_SPEC.md`.
    """

    with get_sync_session() as session:
        activity_row: list[Activity] = Activity.get_windowed(
            user.id,
            filters=(Activity.icu_training_load.isnot(None),),
            as_of=dt,
            days=200,
            session=session,
        )

        # `as_of=dt` ensures the EMA decays through any rest gap between the
        # last activity and `dt` — without it the value freezes at the last
        # activity date and a 30-day rest leaves CTL at its pre-rest level.
        sport_ctl: dict[str, float] = calculate_sport_ctl(activity_row, as_of=dt)
        sport_atl: dict[str, float] = calculate_sport_atl(activity_row, as_of=dt)
        logger.info(
            "Sport load for user %d on %s: ctl=%s atl=%s (%d activities)",
            user.id,
            dt,
            sport_ctl,
            sport_atl,
            len(activity_row),
        )

        Wellness.update_sport_load(
            user_id=user.id,
            dt=dt,
            sport_ctl=sport_ctl,
            sport_atl=sport_atl,
            session=session,
        )


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_update_banister_ess(
    user: UserDTO,
    dt: DateDTO,
):
    """Calculate Banister model and update wellness.banister_recovery + wellness.ess_today.

    Requires resting_hr to be already saved in the wellness row before calculation.
    """

    with get_sync_session() as session:
        activity_rows: list[Activity] = Activity.get_windowed(
            user.id,
            filters=(Activity.average_hr.isnot(None), Activity.average_hr > 0),
            as_of=dt,
            session=session,
        )
        if not activity_rows:
            logger.info("No activities found for Banister ESS calculation for user %s on %s", user.id, dt)
            return

        activities_by_date: dict[str, list] = defaultdict(list)
        for act in activity_rows:
            activities_by_date[act.start_date_local].append(act)

        _wellness_row = session.execute(
            select(Wellness).where(Wellness.user_id == user.id, Wellness.date == dt.isoformat())
        ).scalar_one_or_none()

        if _wellness_row is None or not _wellness_row.resting_hr:
            logger.warning(
                "Cannot update Banister ESS: Wellness row not found or resting_hr missing for user %s on %s",
                user.id,
                dt,
            )
            return

        thresholds: AthleteThresholdsDTO = AthleteSettings.get_thresholds(user.id, session=session)

        banister_r, ess_today = calculate_banister_for_date(
            activities_by_date=activities_by_date,
            dt=dt,
            hr_rest=_wellness_row.resting_hr,
            hr_max=thresholds.max_hr or 179,
            lthr=thresholds.lthr_run or 153,
        )

        _wellness_row.banister_recovery = banister_r
        _wellness_row.ess_today = ess_today
        session.commit()


@dramatiq.actor(queue_name="default")
@validate_call
def actor_after_activity_update(
    user: UserDTO,
    dt: DateDTO,
):
    """After updating an activity, recalculate sport CTL and Banister ESS for the day."""
    g = group(
        [
            _actor_enrich_wellness_sport_info.message(user=user, dt=dt),
            _actor_update_banister_ess.message(user=user, dt=dt),
            actor_fill_training_log.message(user=user, dt=dt),
            actor_fill_training_log_post.message(user=user, dt=dt),
        ]
    )
    g.run()
