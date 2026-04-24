"""Chunk-recursive OAuth bootstrap backfill.

After OAuth connect, ``actor_bootstrap_step`` walks the athlete's last year of
wellness + activities from Intervals.icu, chunk by chunk, updating a persistent
cursor in ``user_backfill_state``. When the cursor reaches ``newest_dt`` the
step finalizes inline: detects empty-import, sends Telegram completion,
marks the row ``status='completed'`` (or ``'completed'`` + ``EMPTY_INTERVALS``
sentinel).

See ``docs/OAUTH_BOOTSTRAP_SYNC_SPEC.md``.
"""

import logging
from datetime import date, timedelta

import dramatiq
import sentry_sdk
from pydantic import validate_call
from sqlalchemy import func, select

from bot.i18n import _, set_language
from data.db import Activity, User, UserBackfillState, UserDTO, Wellness, get_sync_session
from data.intervals.client import IntervalsSyncClient
from data.intervals.dto import ActivityDTO, WellnessDTO
from tasks.dto import DateDTO
from tasks.tools import TelegramTool

from .activities import actor_update_activity_details
from .wellness import process_wellness_analysis_sync

logger = logging.getLogger(__name__)

CHUNK_DAYS = 30
_FIVE_MINUTES_MS = 5 * 60 * 1000


@dramatiq.actor(queue_name="default", max_retries=3, time_limit=_FIVE_MINUTES_MS)
@validate_call
def actor_bootstrap_step(
    user: UserDTO,
    cursor_dt: DateDTO,
    period_days: int = 365,
) -> None:
    """Process a single chunk of the backfill range, then self-reschedule or finalize.

    The first invocation initializes ``user_backfill_state`` (on_conflict overwrite
    of a stale completed/failed row). Subsequent invocations read the state,
    guard ``status=='running'`` and abort on OAuth revoke.
    """
    with get_sync_session() as session:
        state = UserBackfillState.get(user.id, session=session)

        if state is None:
            newest = date.today() - timedelta(days=1)
            state = UserBackfillState.start(
                user_id=user.id,
                period_days=period_days,
                oldest_dt=cursor_dt,
                newest_dt=newest,
                session=session,
            )
        elif state.status != "running":
            logger.info(
                "bootstrap_step: state=%s for user=%d, skipping further chunks",
                state.status,
                user.id,
            )
            return

        db_user = session.get(User, user.id)
        if db_user is None or db_user.intervals_auth_method == "none":
            logger.info("bootstrap_step: OAuth revoked for user=%d, aborting", user.id)
            UserBackfillState.mark_failed(user.id, error="OAuth revoked during backfill", session=session)
            return

        newest_dt = state.newest_dt
        state_cursor = state.cursor_dt

    # Cursor CAS — the message argument ``cursor_dt`` is what Dramatiq re-delivers
    # on retry, but the DB cursor may have already advanced past it (e.g. the step
    # crashed after ``advance_cursor`` committed but before ``.send(next)``).
    # Trust the DB as source of truth: skip the chunk, re-enqueue with the current
    # ``state.cursor_dt`` so the chain continues exactly once from where it really is.
    if state_cursor != cursor_dt:
        logger.info(
            "bootstrap_step: retry with stale cursor arg=%s state=%s user=%d — re-enqueuing from state cursor",
            cursor_dt,
            state_cursor,
            user.id,
        )
        if state_cursor > newest_dt:
            _finalize_bootstrap(user)
            return
        actor_bootstrap_step.send(
            user=user,
            cursor_dt=state_cursor,
            period_days=period_days,
        )
        return

    if cursor_dt > newest_dt:
        # Shouldn't happen, but a defensive short-circuit keeps us from looping
        # when someone calls with a cursor past the end.
        _finalize_bootstrap(user)
        return

    chunk_end = min(cursor_dt + timedelta(days=CHUNK_DAYS - 1), newest_dt)
    logger.info(
        "bootstrap_step: user=%d chunk [%s .. %s] (period_days=%d)",
        user.id,
        cursor_dt,
        chunk_end,
        period_days,
    )

    with IntervalsSyncClient.for_user(user) as client:
        wellness_rows: list[WellnessDTO] = client.get_wellness_range(oldest=cursor_dt, newest=chunk_end)
        activity_rows: list[ActivityDTO] = client.get_activities(oldest=cursor_dt, newest=chunk_end)

    # Strava activities cannot be read via Intervals.icu API (licensing).
    # Mirrors actor_fetch_user_activities — filter before persisting.
    before = len(activity_rows)
    activity_rows = [a for a in activity_rows if (a.source or "").upper() != "STRAVA"]
    if before != len(activity_rows):
        logger.info(
            "bootstrap_step: skipped %d Strava activity(ies) for user=%d",
            before - len(activity_rows),
            user.id,
        )

    # Save activities in bulk (ON CONFLICT) — returns only NEW ids so we can
    # dispatch activity-details only for fresh rows (idempotent re-chunk = no-op).
    new_activity_ids: list[str] = Activity.save_bulk(user, activities=activity_rows) if activity_rows else []

    # Wellness is processed *inline and chronologically* via the sync helper.
    # Fanning out ``actor_user_wellness.send`` (original design) races the
    # rolling HRV/RHR baselines across workers — day N+5's 7-day window can
    # miss day N if the day-N message hasn't drained yet. ``process_wellness_
    # analysis_sync`` commits the full analysis chain for each day before the
    # next iteration starts, so rolling baselines always read a complete prior
    # history. See docs/OAUTH_BOOTSTRAP_SYNC_SPEC.md §17.
    #
    # Sort key is a real ``date``, not the raw string — Intervals.icu happens
    # to return ISO-formatted IDs today (lexicographic == chronological), but
    # we don't want the ordering invariant coupled to that accident. Rows
    # with a missing/unparseable ID are pushed to the end so a malformed
    # single row doesn't break the whole chunk's ordering.
    def _sort_key(row: WellnessDTO) -> date:
        try:
            return date.fromisoformat(row.id) if row.id else date.max
        except ValueError:
            logger.warning("bootstrap: unparseable wellness id=%r for user=%d", row.id, user.id)
            return date.max

    failures = 0
    for w in sorted(wellness_rows, key=_sort_key):
        try:
            process_wellness_analysis_sync(user, w)
        except Exception:
            # Swallowing here is deliberate — we want the chunk to finish and
            # the cursor to advance even if one day's analysis fails. But we
            # capture to Sentry so these don't disappear into log noise, and
            # a gap in day N means day N+1's rolling baseline reads an
            # incomplete history (silent quality degradation).
            failures += 1
            logger.exception(
                "bootstrap: wellness analysis failed user=%d date=%s — continuing chunk",
                user.id,
                w.id,
            )
            sentry_sdk.capture_exception()

    if failures:
        logger.warning(
            "bootstrap: %d/%d wellness-day(s) failed in chunk [%s..%s] for user=%d",
            failures,
            len(wellness_rows),
            cursor_dt,
            chunk_end,
            user.id,
        )

    for aid in new_activity_ids:
        actor_update_activity_details.send(user=user, activity_id=aid)

    next_cursor = chunk_end + timedelta(days=1)
    UserBackfillState.advance_cursor(user_id=user.id, cursor_dt=next_cursor)

    if chunk_end < newest_dt:
        actor_bootstrap_step.send(
            user=user,
            cursor_dt=next_cursor,
            period_days=period_days,
        )
        return

    _finalize_bootstrap(user)


def _finalize_bootstrap(user: UserDTO) -> None:
    """Last chunk processed — detect empty-import, mark completed, Telegram notify.

    ``actor_user_wellness`` already triggers ``actor_after_activity_update`` per
    day, which fills training_log PRE/ACTUAL/POST. No separate global recompute
    is needed here — training_log is maintained day-by-day along the chunk
    recursion path.
    """
    with get_sync_session() as session:
        state = UserBackfillState.get(user.id, session=session)
        if state is None:
            logger.warning("bootstrap finalize: no state for user=%d", user.id)
            return

        wellness_count = session.execute(
            select(func.count(Wellness.date)).where(
                Wellness.user_id == user.id,
                Wellness.date >= state.oldest_dt.isoformat(),
                Wellness.date <= state.newest_dt.isoformat(),
            )
        ).scalar_one()

        activity_count = session.execute(
            select(func.count(Activity.id)).where(
                Activity.user_id == user.id,
                Activity.start_date_local >= state.oldest_dt.isoformat(),
                Activity.start_date_local <= state.newest_dt.isoformat(),
            )
        ).scalar_one()

    if wellness_count == 0 and activity_count == 0:
        final_status = "completed"
        final_error = "EMPTY_INTERVALS"
    else:
        final_status = "completed"
        final_error = None

    UserBackfillState.mark_finished(
        user_id=user.id,
        status=final_status,
        last_error=final_error,
    )

    logger.info(
        "bootstrap finalize: user=%d status=%s wellness=%d activities=%d (period=%d)",
        user.id,
        final_status,
        wellness_count,
        activity_count,
        state.period_days,
    )

    # Wellness counts in Telegram notification: activities are saved synchronously
    # via ``Activity.save_bulk`` inside every chunk so activity_count is accurate,
    # BUT wellness is dispatched fire-and-forget through ``actor_user_wellness.send``.
    # When we reach finalize, the *last* chunk's ~30 wellness actors may still be
    # in flight and haven't committed their rows yet.
    #
    # We delay the user-facing count read by 60s to let that tail drain. EMPTY_INTERVALS
    # detection is NOT subject to this race because activity_count alone is enough
    # to disqualify it (and bootstrap fires on brand-new OAuth users, where zero
    # activities over 365 days is a strong signal of "Intervals hasn't ingested yet").
    _actor_send_bootstrap_completion_notification.send_with_options(
        kwargs=dict(
            user=user,
            period_days=state.period_days,
            empty_import=final_error == "EMPTY_INTERVALS",
        ),
        delay=60_000,
    )


# ---------------------------------------------------------------------------
# Telegram notifications — dedicated actors per §6.1
# ---------------------------------------------------------------------------


@dramatiq.actor(queue_name="default")
@validate_call
def actor_send_bootstrap_start_notification(user: UserDTO) -> None:
    """Telegram ping sent right after the OAuth callback, while the bootstrap
    chain is being enqueued. Public name — called from `api/routers/intervals/oauth.py`
    via `tasks.actors` package import."""
    set_language(user.language or "ru")
    text = _(
        "🔄 Intervals.icu подключён. Загружаю историю за последний год — обычно 3-5 минут.\n"
        "Пришлю уведомление когда закончу."
    )
    TelegramTool(user=user).send_message(text=text)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_send_bootstrap_completion_notification(
    user: UserDTO,
    period_days: int,
    empty_import: bool = False,
) -> None:
    """Send Telegram completion notification.

    This actor re-queries wellness/activity counts at dispatch time (scheduled
    with a 60s delay from ``_finalize_bootstrap``) so the numbers shown to the
    user are final — not the racy snapshot that `_finalize_bootstrap` captured
    while the last chunk's ``actor_user_wellness.send`` tail was still draining.
    """
    set_language(user.language or "ru")
    if empty_import:
        text = _(
            "ℹ️ Intervals.icu ещё не подтянул Garmin-историю. "
            "Попробую снова через час; можешь также нажать «Повторить импорт» в настройках."
        )
    else:
        with get_sync_session() as session:
            state = UserBackfillState.get(user.id, session=session)
            if state is None:
                logger.warning("completion notification: no state for user=%d", user.id)
                return
            wellness_count = session.execute(
                select(func.count(Wellness.date)).where(
                    Wellness.user_id == user.id,
                    Wellness.date >= state.oldest_dt.isoformat(),
                    Wellness.date <= state.newest_dt.isoformat(),
                )
            ).scalar_one()
            activity_count = session.execute(
                select(func.count(Activity.id)).where(
                    Activity.user_id == user.id,
                    Activity.start_date_local >= state.oldest_dt.isoformat(),
                    Activity.start_date_local <= state.newest_dt.isoformat(),
                )
            ).scalar_one()
        text = _("✅ История загружена: {wellness} дней wellness, {activities} активностей за {period} дней.").format(
            wellness=int(wellness_count),
            activities=int(activity_count),
            period=period_days,
        )
    TelegramTool(user=user).send_message(text=text)
