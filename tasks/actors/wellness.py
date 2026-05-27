"""Dramatiq actors — wellness pipeline: HRV, RHR, Banister, recovery."""

import logging
import time
from datetime import date

import dramatiq
from dramatiq import group, pipeline
from pydantic import validate_call
from sqlalchemy import select

from data.db import HrvAnalysis, RhrAnalysis, UserDTO, Wellness, get_sync_session
from data.intervals.client import IntervalsAccessError, IntervalsSyncClient
from data.intervals.dto import RecoveryScoreDTO, RhrStatusDTO, RmssdStatusDTO, WellnessDTO
from data.metrics import combined_recovery_score, rhr_baseline, rmssd_flatt_esco
from tasks.dto import ORMDTO, DateDTO, local_today

from ._constants import MORNING_REPORT_DELAY_SEC
from .common import CATEGORY_TO_READINESS, _actor_update_banister_ess, actor_after_activity_update, is_user_dormant
from .endurance import actor_snapshot_endurance_scores

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_calculate_rhr(
    user: UserDTO,
    dt: DateDTO,
) -> RhrStatusDTO:
    """Thin wrapper — delegates min-days handling to `data.metrics.rhr_baseline`."""
    rhr_rows: list[float] = Wellness.get_rhr_history(user_id=user.id, dt=dt)
    return rhr_baseline(rhr_rows)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_calculate_hrv(
    user: UserDTO,
    dt: DateDTO,
) -> RmssdStatusDTO:
    """Thin wrapper — delegates min-days handling to `data.metrics.rmssd_flatt_esco`.

    Returns a Pydantic model — Dramatiq's PydanticEncoder (tasks/middleware.py)
    auto-dumps it to JSON, and the next actor in the pipeline rehydrates via
    its own ``@validate_call`` annotation.
    """
    hrv_rows: list[float] = Wellness.get_hrv_history(user_id=user.id, dt=dt)
    return rmssd_flatt_esco(hrv_rows)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_update_rhr_analysis(
    prev: RhrStatusDTO,
    *,
    user: UserDTO,
    dt: DateDTO,
) -> RhrStatusDTO:
    """Persist RHR baseline result to rhr_analysis table (upsert)."""
    if prev.status == "insufficient_data":
        return prev  # no DB update if not enough data

    _dt = dt.isoformat()
    with get_sync_session() as session:
        _rhr_row = session.get(RhrAnalysis, (user.id, _dt))
        if _rhr_row is None:
            _rhr_row = RhrAnalysis(user_id=user.id, date=_dt)
            session.add(_rhr_row)

        _rhr_row.status = prev.status
        _rhr_row.rhr_today = prev.rhr_today
        _rhr_row.rhr_7d = prev.rhr_7d
        _rhr_row.rhr_sd_7d = prev.rhr_sd_7d
        _rhr_row.rhr_30d = prev.rhr_30d
        _rhr_row.rhr_sd_30d = prev.rhr_sd_30d
        _rhr_row.rhr_60d = prev.rhr_60d
        _rhr_row.rhr_sd_60d = prev.rhr_sd_60d
        _rhr_row.lower_bound = prev.lower_bound
        _rhr_row.upper_bound = prev.upper_bound
        _rhr_row.cv_7d = prev.cv_7d
        _rhr_row.days_available = prev.days_available
        if prev.trend:
            _rhr_row.trend_direction = prev.trend.direction
            _rhr_row.trend_slope = prev.trend.slope
            _rhr_row.trend_r_squared = prev.trend.r_squared
        session.commit()

    return prev


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_update_hrv_analysis(
    prev: RmssdStatusDTO,
    *,
    user: UserDTO,
    dt: DateDTO,
) -> RmssdStatusDTO:
    """Persist Flatt/Esco HRV result to hrv_analysis table (upsert).

    Historical `algorithm='ai_endurance'` rows in the table are left untouched —
    we stopped writing them when the second algorithm was retired (issue #307);
    schema keeps `algorithm` in the PK so existing data stays addressable.
    """
    if prev.status == "insufficient_data":
        return prev  # no DB update if not enough data

    _dt = dt.isoformat()
    with get_sync_session() as session:
        _hrv_row = session.get(HrvAnalysis, (user.id, _dt, "flatt_esco"))
        if _hrv_row is None:
            _hrv_row = HrvAnalysis(user_id=user.id, date=_dt, algorithm="flatt_esco")
            session.add(_hrv_row)

        _hrv_row.status = prev.status
        _hrv_row.rmssd_7d = prev.rmssd_7d
        _hrv_row.rmssd_sd_7d = prev.rmssd_sd_7d
        _hrv_row.rmssd_60d = prev.rmssd_60d
        _hrv_row.rmssd_sd_60d = prev.rmssd_sd_60d
        _hrv_row.lower_bound = prev.lower_bound
        _hrv_row.upper_bound = prev.upper_bound
        _hrv_row.cv_7d = prev.cv_7d
        _hrv_row.swc = prev.swc
        _hrv_row.days_available = prev.days_available
        if prev.trend:
            _hrv_row.trend_direction = prev.trend.direction
            _hrv_row.trend_slope = prev.trend.slope
            _hrv_row.trend_r_squared = prev.trend.r_squared
        session.commit()

    return prev


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_update_recovery_score(
    user: UserDTO,
    dt: DateDTO,
    force: bool = False,
):
    with get_sync_session() as session:
        _wellness_row = Wellness.get(user_id=user.id, dt=dt, session=session)
        _hrv_row = HrvAnalysis.get(user_id=user.id, dt=dt, algorithm="flatt_esco", session=session)
        _rhr_row = RhrAnalysis.get(user_id=user.id, dt=dt, session=session)

        if not _wellness_row or not _hrv_row or not _rhr_row:
            return

        recovery: RecoveryScoreDTO = combined_recovery_score(
            rmssd=_hrv_row,
            rhr=_rhr_row,
            banister_recovery=_wellness_row.banister_recovery,
            sleep_score=_wellness_row.sleep_score,
        )

        _wellness_row.recovery_score = recovery.score
        _wellness_row.recovery_category = recovery.category
        _wellness_row.recovery_recommendation = recovery.recommendation
        _wellness_row.readiness_score = int(recovery.score)  # legacy alias
        _wellness_row.readiness_level = CATEGORY_TO_READINESS.get(recovery.category, "yellow")

        session.commit()


def process_wellness_analysis_sync(user: UserDTO, wellness: WellnessDTO) -> None:
    """Synchronous wellness analysis chain for contexts that need strict
    chronological ordering — specifically the OAuth bootstrap backfill.

    The normal ``actor_user_wellness`` actor dispatches the RHR / HRV / recovery
    work as a Dramatiq group which runs on workers in parallel. That's fine
    for daily cron (days arrive one at a time) but breaks during backfill where
    30 days from a single chunk are fanned out simultaneously — day N+5's HRV
    baseline reads a 7-day history that may not yet include day N's committed
    row.

    This helper runs the same computation inline in the caller's transaction
    order: save wellness → RHR → HRV → Banister ESS → recovery score. Everything
    commits before return, so the next day's call sees a fully-analyzed prior
    day. Post-activity enrichment (sport CTL, training_log) and athlete-settings
    sync still fan out async — those have no cross-day ordering dependency.
    """
    from .athlets import actor_sync_athlete_settings

    result: ORMDTO = Wellness.save(user_id=user.id, wellness=wellness)
    dt: date = date.fromisoformat(wellness.id) if wellness.id else local_today()

    if not result.is_changed:
        # Still trigger training_log recompute for the day so activities get
        # PRE/ACTUAL/POST filled in even when wellness hasn't changed between
        # retries. Fan-out — no ordering dep here.
        actor_after_activity_update.send(user=user, dt=dt)
        return

    rhr_status = _actor_calculate_rhr(user=user, dt=dt)
    _actor_update_rhr_analysis(rhr_status, user=user, dt=dt)

    hrv_status = _actor_calculate_hrv(user=user, dt=dt)
    _actor_update_hrv_analysis(hrv_status, user=user, dt=dt)

    _actor_update_banister_ess(user=user, dt=dt)
    _actor_update_recovery_score(user=user, dt=dt)

    actor_sync_athlete_settings.send(user=user)
    actor_after_activity_update.send(user=user, dt=dt)


@dramatiq.actor(queue_name="default")
@validate_call
def actor_user_wellness(
    user: UserDTO,
    dt: DateDTO | None = None,
    wellness: WellnessDTO | None = None,
    force: bool = False,
    force_inactive: bool = False,
):
    from .athlets import actor_sync_athlete_settings
    from .reports import actor_compose_user_morning_report

    # Skip dormant accounts — the WELLNESS_UPDATED webhook keeps firing for
    # inactive users (we intentionally don't filter at the webhook layer so
    # event history stays consistent), but running HRV/RHR/Banister recalcs
    # + the Intervals.icu API call for someone who hasn't touched the bot in
    # 30+ days burns quota for no benefit. The morning-report compose actor
    # already has its own `is_active` guard (`reports.py:269`) — this gate
    # short-circuits *upstream* so the API fetch + recalcs don't happen
    # either. First bot interaction reactivates via `bot/decorator._wake_user`.
    #
    # ``force_inactive=True`` is the admin override (CLI sync-wellness /
    # recalc-sport-load) — operator can backfill data for a stale-deactivated
    # user without reactivating them first.
    if not force_inactive and is_user_dormant(user.id, "actor_user_wellness"):
        return

    today = local_today()
    dt = dt or today

    if wellness is None:
        try:
            with IntervalsSyncClient.for_user(user) as client:
                wellness: WellnessDTO = client.get_wellness(dt)
        except IntervalsAccessError as e:
            logger.info("Skipping wellness fetch for user %d on %s: %s", user.id, dt, e)
            return

    if not wellness:
        logger.info("No wellness data found for user %s on %s", user.id, dt)
        return

    result: ORMDTO = Wellness.save(user_id=user.id, wellness=wellness)

    _dt = wellness.id

    if not result.is_changed and not force:
        logger.debug("Wellness unchanged for user %s on %s, skipping pipelines", user.id, _dt)
        return

    # Endurance Score Level-1 hook (spec §7.0). Fire-and-forget — recompute
    # depends on the wellness row we just wrote (CTL / ramp_rate / sport_ctl
    # feed LongTerm + Recent + composite VO2max), and the idempotent upsert
    # in `EnduranceScore.upsert` makes re-fires from multiple sources (this
    # actor + actor_fetch_user_activities + Level-2 cron) safe.
    actor_snapshot_endurance_scores.send(user_id=user.id)

    # all independent tasks run in parallel
    group(
        [
            actor_sync_athlete_settings.message(user=user),
            actor_after_activity_update.message(user=user, dt=_dt),
        ]
    ).run()

    g = group(
        [
            pipeline(
                [
                    _actor_calculate_rhr.message(user=user, dt=_dt),
                    _actor_update_rhr_analysis.message(user=user, dt=_dt),
                ]
            ),
            pipeline(
                [
                    _actor_calculate_hrv.message(user=user, dt=_dt),
                    _actor_update_hrv_analysis.message(user=user, dt=_dt),
                ]
            ),
        ]
    )
    g.add_completion_callback(_actor_update_recovery_score.message(user=user, dt=_dt, force=force))
    g.run()

    _row: Wellness = result.row
    # Reuse the ``today`` snapshot taken at the top of the actor — re-reading
    # ``local_today()`` here would let a long-running invocation that crosses
    # midnight evaluate the same wellness row against two different dates,
    # which can either skip or double-fire the morning-report dispatch.
    if (
        _dt == today.isoformat()
        and _is_free_for_morning_report(_row.ai_recommendation)
        and _row.sleep_score is not None
        and _row.recovery_score is not None
    ):
        # Intervals.icu sometimes recomputes yesterday's CTL/ATL shortly after
        # wake-up (late activities, late HRV). `recompute_today_loads` uses
        # yesterday's value as the baseline, so we delay the compose by
        # 10 min to let Intervals settle. Re-firing cron in this window sees
        # ai_recommendation = "__scheduled__:..." and `_is_free_for_morning_report`
        # returns False → skip re-dispatch. The compose actor has a matching
        # `__scheduled__` branch that lets the delayed run claim the slot.
        with get_sync_session() as session:
            locked = session.execute(
                select(Wellness).where(Wellness.user_id == user.id, Wellness.date == _dt).with_for_update()
            ).scalar_one_or_none()
            if not locked or not _is_free_for_morning_report(locked.ai_recommendation):
                return
            # Sentinel stores SET-time (not eligibility) to keep the format
            # symmetric with ``__generating__:{set_at}`` and avoid easy mix-ups
            # in future fixes. Eligibility is derived in `_is_free_for_morning_report`.
            locked.ai_recommendation = f"__scheduled__:{time.time():.0f}"
            session.commit()

        actor_compose_user_morning_report.send_with_options(
            kwargs={"user": user}, delay=MORNING_REPORT_DELAY_SEC * 1000
        )


def _is_free_for_morning_report(ai_recommendation: str | None) -> bool:
    """True if the wellness row can accept a new morning-report dispatch.

    Free = null, or a stale ``__scheduled__:{set_at}`` sentinel whose delayed
    message never arrived (Redis loss / broker eviction). A sentinel is stale
    if it's older than 2× ``MORNING_REPORT_DELAY_SEC`` — one delay buys the
    delayed message its scheduled fire-time, the second buys grace if the
    broker hiccupped. Sentinels with corrupt timestamps are also treated as
    free so a single broken row doesn't permanently lock the slot.
    """
    if not ai_recommendation:
        return True
    if ai_recommendation.startswith("__scheduled__:"):
        try:
            set_at = float(ai_recommendation.split(":", 1)[1])
        except (ValueError, IndexError):
            return True
        return time.time() - set_at > 2 * MORNING_REPORT_DELAY_SEC
    return False
