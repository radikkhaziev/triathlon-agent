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
import random
from datetime import date, datetime, timedelta, timezone

import dramatiq
import sentry_sdk
from pydantic import validate_call
from sqlalchemy import func, select

from bot.i18n import _, set_language
from data.db import Activity, User, UserBackfillState, UserDTO, Wellness, get_sync_session
from data.intervals.client import (
    QUOTA_DEFER_JITTER_SEC,
    IntervalsAccessError,
    IntervalsRateLimitError,
    IntervalsSyncClient,
    QuotaSnapshot,
    seconds_until_quota_reset,
)
from data.intervals.dto import ActivityDTO, WellnessDTO
from tasks.dto import DateDTO, local_today
from tasks.tools import TelegramTool

from ._constants import BOOTSTRAP_DAILY_RESERVE, BOOTSTRAP_PAUSE_JITTER_SEC, BOOTSTRAP_REQUESTS_PER_ACTIVITY
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
            newest = local_today() - timedelta(days=1)
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
        if db_user is None or not db_user.intervals_access_token_encrypted:
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
        if cursor_dt > state_cursor:
            # The DB cursor moved *backwards* relative to this message: a
            # ``start()`` reset (``bootstrap-sync --force`` / retry-backfill)
            # started a fresh generation while this copy slept in the delay
            # queue. It belongs to a superseded chain — drop it, or we'd fork.
            logger.info(
                "bootstrap_step: superseded generation arg=%s state=%s user=%d — dropping",
                cursor_dt,
                state_cursor,
                user.id,
            )
            return
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

    try:
        try:
            with IntervalsSyncClient.for_user(user) as client:
                wellness_rows: list[WellnessDTO] = client.get_wellness_range(oldest=cursor_dt, newest=chunk_end)
                activity_rows: list[ActivityDTO] = client.get_activities(oldest=cursor_dt, newest=chunk_end)
                # Snapshot while the client is open — never rely on state
                # surviving ``close()``.
                quota: QuotaSnapshot | None = client.quota
        except IntervalsAccessError as e:
            # Two scenarios collapse into this catch:
            #   (a) Race window — user revokes between the pre-check commit at the
            #       top of this actor and the API call here (auth_method flips
            #       'oauth' → 'none' under us).
            #   (b) Broken OAuth state — auth_method='oauth' but
            #       intervals_access_token decrypts to None (Fernet key mismatch
            #       after a key rotation, or an inconsistent partial write). The
            #       pre-check can't catch this because it only inspects
            #       auth_method, not the actual credential payload.
            # Both require backfill abort. Persist only the exception class name
            # in `state.last_error` — full `str(e)` may include API paths / auth
            # tokens in future subclasses; see backfill.py:181-187 warning.
            logger.info("Bootstrap chunk aborted for user=%d: %s", user.id, e)
            UserBackfillState.mark_failed(user.id, error=type(e).__name__)
            return

        _process_chunk(
            user,
            cursor_dt=cursor_dt,
            chunk_end=chunk_end,
            newest_dt=newest_dt,
            period_days=period_days,
            wellness_rows=wellness_rows,
            activity_rows=activity_rows,
            quota=quota,
        )
    except IntervalsRateLimitError as e:
        # QuotaAwareRetries re-enqueues this very message after Retry-After —
        # wherever inside the chunk the 429 surfaced. Stamp the pause so the
        # watchdog doesn't read the multi-hour silence as «stuck» and
        # mark_failed the row before the deferred copy wakes.
        _stamp_rate_limit_pause(user, e)
        raise


def _stamp_rate_limit_pause(user: UserDTO, e: IntervalsRateLimitError) -> None:
    resume_at = datetime.now(timezone.utc) + timedelta(seconds=e.retry_after + QUOTA_DEFER_JITTER_SEC)
    UserBackfillState.mark_quota_paused(user.id, resume_at=resume_at)


def _process_chunk(
    user: UserDTO,
    *,
    cursor_dt: date,
    chunk_end: date,
    newest_dt: date,
    period_days: int,
    wellness_rows: list[WellnessDTO],
    activity_rows: list[ActivityDTO],
    quota: QuotaSnapshot | None,
) -> None:
    """Persist and fan out one fetched chunk, then advance the cursor and
    continue the chain. Split out of the actor so one ``except
    IntervalsRateLimitError`` in the caller covers every API call of a chunk.
    """
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

    # Daily-quota reserve — decided BEFORE anything is persisted: ``save_bulk``
    # returns only NEW ids, so pausing after it would leave this chunk's
    # activities without their details fan-out forever. On resume the two
    # range fetches simply repeat. ``est_cost`` is an upper bound — rows the
    # ACTIVITY_UPLOADED webhook already ingested are deduped by ``save_bulk``
    # and never fan out.
    est_cost = BOOTSTRAP_REQUESTS_PER_ACTIVITY * len(activity_rows)
    needed = BOOTSTRAP_DAILY_RESERVE + est_cost
    if quota is not None and quota.remaining_day < needed:
        if quota.limit_day is not None and needed > quota.limit_day:
            # Unsatisfiable even on a fresh day — pausing would loop forever
            # (each wake burns two fetches and re-pauses). Proceed; the
            # Phase 2 deferral absorbs whatever 429s the fan-out hits.
            logger.error(
                "bootstrap_step: chunk from %s needs ~%d requests > daily limit %d for user=%d — "
                "cannot fit in any day, proceeding unpaused",
                cursor_dt,
                needed,
                quota.limit_day,
                user.id,
            )
        else:
            _pause_for_quota(user, cursor_dt=cursor_dt, period_days=period_days, quota=quota, est_cost=est_cost)
            return

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
        except IntervalsRateLimitError:
            # No API call lives in the helper today; this is insurance so a
            # future one can't be swallowed by the broad catch below. The
            # caller stamps the quota pause; the whole chunk re-runs after
            # the deferral (saves are idempotent).
            raise
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
    if not UserBackfillState.advance_cursor(user_id=user.id, cursor_dt=next_cursor, expected_cursor=cursor_dt):
        # Another copy of the chain (watchdog kick vs. woken quota-deferred
        # message, or a --force restart) already moved the cursor. It owns
        # the continuation — stopping here is what prevents a fork.
        logger.warning(
            "bootstrap_step: lost the cursor race at %s for user=%d — another copy owns the chain, stopping",
            cursor_dt,
            user.id,
        )
        return

    if chunk_end < newest_dt:
        actor_bootstrap_step.send(
            user=user,
            cursor_dt=next_cursor,
            period_days=period_days,
        )
        return

    _finalize_bootstrap(user)


def _pause_for_quota(
    user: UserDTO,
    *,
    cursor_dt: date,
    period_days: int,
    quota: QuotaSnapshot,
    est_cost: int,
) -> None:
    """Park the chain until the 00:00 UTC quota reset (+ jitter), leaving the
    cursor untouched so the same chunk re-runs. See spec «Intervals.icu rate
    limits», Phase 3."""
    delay_sec = seconds_until_quota_reset() + random.randint(0, BOOTSTRAP_PAUSE_JITTER_SEC)
    resume_at = datetime.now(timezone.utc) + timedelta(seconds=delay_sec)
    logger.warning(
        "bootstrap_step: user=%d chunk from %s needs ~%d requests + reserve %d but only %d/day left "
        "(%s) — pausing until %s",
        user.id,
        cursor_dt,
        est_cost,
        BOOTSTRAP_DAILY_RESERVE,
        quota.remaining_day,
        quota,
        resume_at.isoformat(timespec="minutes"),
    )
    sentry_sdk.add_breadcrumb(
        category="bootstrap",
        message=f"quota pause user={user.id} cursor={cursor_dt} remaining_day={quota.remaining_day}",
        level="warning",
    )
    UserBackfillState.mark_quota_paused(user.id, resume_at=resume_at)
    actor_bootstrap_step.send_with_options(
        kwargs=dict(user=user, cursor_dt=cursor_dt, period_days=period_days),
        delay=delay_sec * 1000,
    )


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
