"""Dramatiq actors — reports (morning, evening), echo, scheduled workouts."""

import logging
import time
from datetime import date, timedelta

import dramatiq
import sentry_sdk
from pydantic import validate_call
from sqlalchemy import select

from bot.i18n import _, set_language
from config import settings
from data.db import (
    Activity,
    ActivityHrv,
    AthleteSettings,
    HrvAnalysis,
    ScheduledWorkout,
    ThresholdDriftDTO,
    User,
    UserBackfillState,
    UserDTO,
    WeeklyReport,
    Wellness,
    WellnessPostDTO,
    get_sync_session,
)
from data.intervals.client import IntervalsSyncClient
from data.intervals.dto import RecoveryScoreDTO, ScheduledWorkoutDTO
from data.weekly_preview import extract_weekly_preview
from data.workout_adapter import compute_constraints, needs_adaptation, parse_humango_description
from tasks.dto import local_today
from tasks.formatter import build_evening_message, build_morning_message, build_onboarding_hey_message, format_pace
from tasks.tools import MCPTool, TelegramTool

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default")
def actor_echo(message: str) -> str:
    """Log and return the message. Used for smoke-testing the queue."""
    logger.info("echo actor received: %s", message)
    return message


# ---------------------------------------------------------------------------
#  Scheduled Workouts
# ---------------------------------------------------------------------------


@dramatiq.actor(queue_name="default")
@validate_call
def actor_user_scheduled_workouts(user: UserDTO):
    today = local_today()
    newest = today + timedelta(days=14)

    with IntervalsSyncClient.for_user(user) as client:
        _workouts: list[ScheduledWorkoutDTO] = client.get_events(oldest=today, newest=newest)

    if not _workouts:
        logger.info("No scheduled workouts found for user %s (%s → %s)", user.id, today, newest)
        return

    count = ScheduledWorkout.save_bulk(
        user.id,
        _workouts,
        oldest=today,
        newest=newest,
    )
    logger.info("Synced %d scheduled workouts (%s → %s)", count, today, newest)


# ---------------------------------------------------------------------------
#  Morning Report
# ---------------------------------------------------------------------------


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_send_user_morning_report(
    user: UserDTO,
    wellness: WellnessPostDTO,
):
    """Sends morning report to user via Telegram."""
    from tasks.utils import RampTrainingSuggestion, user_ramp_sports

    set_language(user.language or "ru")
    summary = build_morning_message(wellness)
    webapp_url = settings.API_BASE_URL
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": _("Открыть отчёт"),
                    "web_app": {"url": webapp_url},
                }
            ]
        ]
    }

    # Honour the athlete's sport selection — runners-only get no Ride
    # suggestions, riders-only get no Run, etc. NULL `user.sports` (gate
    # not yet passed) falls back to ``["Run"]`` only — conservative default
    # that doesn't spam Ride-tests to runners-only. Empty list (athlete
    # picked only sports we don't support yet, e.g. swim) suppresses the
    # entire ramp section: RampTrainingSuggestion.is_test_needed returns
    # False because the freshness loop iterates an empty list.
    ramp = RampTrainingSuggestion(user=user, wellness=wellness, sports=user_ramp_sports(user.sports))
    if ramp.is_test_needed:
        sport = ramp.suggested_sport or "Run"
        if ramp.days_since:
            summary += f"\n\n⚡ Ramp Test ({sport}): {_('последний тест')} {ramp.days_since} {_('дней назад')}"
        else:
            summary += f"\n\n⚡ Ramp Test ({sport}): {_('тест ещё не выполнялся')}"

        keyboard["inline_keyboard"].append(
            [
                {
                    "text": f"{_('Создать Ramp Test')} ({sport})",
                    "callback_data": f"ramp_test:{sport}",
                }
            ]
        )

    # Workout adaptation check
    today = local_today()

    hrv_flatt = HrvAnalysis.get(
        user_id=user.id,
        dt=today,
        algorithm="flatt_esco",
    )
    hrv_status = hrv_flatt.status if hrv_flatt else "insufficient_data"

    tsb = (wellness.ctl - wellness.atl) if wellness.ctl is not None and wellness.atl is not None else 0

    scheduled = ScheduledWorkout.get_for_date(user.id, today)
    for w in scheduled:
        if not w.description:
            continue
        steps = parse_humango_description(w.description)
        if not steps:
            continue

        recovery = RecoveryScoreDTO(
            score=wellness.recovery_score or 50,
            category=wellness.recovery_category or "moderate",
            recommendation="",
        )
        max_zone, _constraint_detail = compute_constraints(recovery, hrv_status, tsb)

        s = AthleteSettings.get(user.id, w.type or "Run")
        ftp = float(s.ftp) if s and s.ftp else 233
        lthr = s.lthr if s and s.lthr else 153

        if needs_adaptation(steps, max_zone, ftp, lthr):
            w_name = w.name or _("Тренировка")
            summary += f"\n\n⚠️ {w_name} {_('требует адаптации')} (recovery {recovery.category}, max Z{max_zone})"
            keyboard["inline_keyboard"].append(
                [
                    {
                        "text": f"{_('Адаптировать')}: {w_name}",
                        "callback_data": f"adapt:{w.id}",
                    }
                ]
            )

    # Threshold drift detection — line shape depends on the alert metric.
    # LTHR alerts carry HR (bpm); THRESHOLD_PACE alerts carry pace (sec/km).
    # Pre-2026-05-08 the line was hardcoded "LTHR {sport}: ... bpm" for every
    # alert, which mislabelled THRESHOLD_PACE as LTHR with bpm units.
    drift: ThresholdDriftDTO | None = User.detect_threshold_drift(user_id=user.id)
    if drift:
        for alert in drift.alerts:
            if alert.metric == "LTHR":
                summary += (
                    f"\n⚠️ LTHR {alert.sport}: {_('текущий порог')} {alert.config_value} bpm, "
                    f"{_('по тестам')} {alert.measured} bpm ({alert.diff_pct:+.1f}%). "
                    f"{_('Рекомендуем обновить')}"
                )
            elif alert.metric == "THRESHOLD_PACE":
                cfg = format_pace(alert.config_value) or "—"
                meas = format_pace(alert.measured) or "—"
                summary += (
                    f"\n⚠️ {_('Threshold pace')} {alert.sport}: "
                    f"{_('текущий порог')} {cfg}, {_('по тестам')} {meas} "
                    f"({alert.diff_pct:+.1f}%). {_('Рекомендуем обновить')}"
                )
            else:
                logger.warning("Unknown drift metric %s in morning report — skipping", alert.metric)
                continue
        keyboard["inline_keyboard"].append(
            [
                {"text": _("Обновить зоны"), "callback_data": "update_zones"},
            ]
        )

    tg = TelegramTool(user=user)
    tg.send_message(text=summary, reply_markup=keyboard, markdown=True)
    logger.info("Morning report sent for user %d", user.id)


def _clear_sentinel(user_id: int, dt: str) -> None:
    """Clear __generating__ sentinel so retry can attempt again."""
    with get_sync_session() as session:
        row = Wellness.get(user_id, dt, session=session)
        if row and row.ai_recommendation and row.ai_recommendation.startswith("__generating__"):
            row.ai_recommendation = None
            session.commit()


@dramatiq.actor(queue_name="default")
@validate_call
def actor_compose_user_morning_report(
    user: UserDTO,
):
    _dt = local_today().isoformat()

    # Transaction 1: short lock — claim the slot with sentinel, release immediately.
    # Prevents race: two concurrent actors both see ai_recommendation=None.
    with get_sync_session() as session:
        _wellness_row = session.execute(
            select(Wellness).where(Wellness.user_id == user.id, Wellness.date == _dt).with_for_update()
        ).scalar_one_or_none()

        if not _wellness_row or not _wellness_row.sleep_score:
            return

        # Sentinel format: "__generating__:1713520800" (unix timestamp).
        # Allow retry if sentinel is stuck >10 min (worker crash).
        if _wellness_row.ai_recommendation:
            if _wellness_row.ai_recommendation.startswith("__generating__"):
                parts = _wellness_row.ai_recommendation.split(":", 1)
                if len(parts) == 2:
                    try:
                        set_at = float(parts[1])
                        if time.time() - set_at < 600:
                            return
                    except ValueError:
                        pass
                logger.warning("Stale __generating__ sentinel for user %d, retrying", user.id)
            else:
                return

        _wellness_row.ai_recommendation = f"__generating__:{time.time():.0f}"
        session.commit()

    # Fetch user credentials (outside lock)
    with get_sync_session() as session:
        _user_orm = session.get(User, user.id)
    if _user_orm is None:
        logger.warning("Morning report skipped: user %d no longer exists", user.id)
        _clear_sentinel(user.id, _dt)
        return

    # Generate report (no DB lock held — can take 30-120s)
    try:
        mcp = MCPTool(token=_user_orm.mcp_token, user_id=user.id, language=user.language)
        text = mcp.generate_morning_report_via_mcp(_dt)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("Morning report generation failed for user %d", user.id)
        _clear_sentinel(user.id, _dt)
        return
    if not text:
        _clear_sentinel(user.id, _dt)
        return

    # Transaction 2: save the real report
    with get_sync_session() as session:
        _wellness_row = Wellness.get(user.id, _dt, session=session)
        if not _wellness_row:
            logger.warning("Morning report: wellness row disappeared for user %d, date %s", user.id, _dt)
            return
        _wellness_row.ai_recommendation = text
        session.commit()
        session.refresh(_wellness_row)
        wellness_dto = WellnessPostDTO.model_validate(_wellness_row)
        logger.info("Morning report saved for user %d, date %s", user.id, _dt)

    _actor_send_user_morning_report.send(
        user=user,
        wellness=wellness_dto,
    )


# ---------------------------------------------------------------------------
#  Weekly Report
# ---------------------------------------------------------------------------


def _weekly_report_chat_keyboard(week_start_iso: str) -> dict:
    """Inline keyboard with a single «Open full report» WebApp button.

    Routes to the webapp's ``/weekly/<iso_monday>`` page (PR3). Uses
    Telegram's ``web_app`` button type (Mini App launch) rather than a
    plain URL — keeps the athlete inside the Telegram client instead of
    bouncing to the system browser. Same UX choice as the race-plan push
    (``bot/race_plan_telegram.py:build_open_in_webapp_keyboard``).
    """
    url = f"{settings.API_BASE_URL.rstrip('/')}/weekly/{week_start_iso}"
    return {
        "inline_keyboard": [
            [
                {
                    "text": _("📊 Открыть полный отчёт"),
                    "web_app": {"url": url},
                }
            ]
        ]
    }


def generate_and_save_weekly_report(user: UserDTO) -> tuple[str, date] | None:
    """Generate this week's report via Claude+MCP and persist it. No chat send.

    Returns ``(content_md, week_start)`` on success, ``None`` when generation
    yielded empty text or the user record has vanished between dispatch and
    actor run. Persists BEFORE the caller decides what to do with the text
    — same idempotent ``WeeklyReport.upsert`` path whether the trigger is
    the Sunday cron or the manual ``create-weekly-report`` CLI.

    Splitting this out lets the CLI command exercise the exact pipeline
    without triggering a Telegram notification, which is the whole point of
    the manual path: backfill / dev-test without disturbing the athlete.
    """
    # UserDTO carries no credentials (issue #147) — re-fetch ORM for mcp_token.
    with get_sync_session() as session:
        _user_orm = session.get(User, user.id)
    if _user_orm is None:
        logger.warning("Weekly report skipped: user %d no longer exists", user.id)
        return None
    mcp = MCPTool(token=_user_orm.mcp_token, user_id=user.id, language=user.language)
    text = mcp.generate_weekly_report_via_mcp()

    if not text:
        logger.warning("Weekly report empty for user %d", user.id)
        return None

    # Monday of the summarised week. The cron fires Sunday 19:00 Belgrade,
    # so weekday()==6 → week_start = today − 6 days = same week's Monday.
    # The CLI inherits this anchor — running mid-week creates/overwrites the
    # row for the current Mon-Sun window.
    today = local_today()
    week_start = today - timedelta(days=today.weekday())

    WeeklyReport.upsert(
        user_id=user.id,
        week_start=week_start,
        content_md=text,
        model=MCPTool.WEEKLY_MODEL,
    )
    return text, week_start


@dramatiq.actor(queue_name="default", time_limit=600_000)
@validate_call
def actor_compose_weekly_report(user: UserDTO):
    """Generate weekly training summary via Claude + MCP tools.

    Chat send is a short notification: localised label + extracted preview
    + WebApp button → ``/weekly/<week_start>`` (PR3 webapp route). Keeps
    the message under 600 chars so Telegram's 4096 visible-text limit is
    nowhere near an issue (the original drop-bug PR1 addressed). Full
    markdown lives in ``weekly_reports.content_md`` and renders in the
    webapp on tap.
    """
    result = generate_and_save_weekly_report(user)
    if result is None:
        return
    text, week_start = result

    set_language(user.language or "ru")
    preview = extract_weekly_preview(text)
    chat_text = f"{_('📊 Недельный отчёт готов')}\n\n{preview}"
    keyboard = _weekly_report_chat_keyboard(week_start.isoformat())

    tg = TelegramTool(user=user)
    if not user.is_silent:
        tg.send_message(text=chat_text, reply_markup=keyboard, markdown=True)
    logger.info("Weekly report saved+sent for user %d week=%s", user.id, week_start.isoformat())


# ---------------------------------------------------------------------------
#  Evening Report
# ---------------------------------------------------------------------------


@dramatiq.actor(queue_name="default")
@validate_call
def actor_compose_user_evening_report(
    user: UserDTO,
) -> None:
    today = local_today()
    _dt = today.isoformat()

    with get_sync_session() as session:
        _wellness_row = session.execute(
            select(Wellness).where(
                Wellness.user_id == user.id,
                Wellness.date == _dt,
            )
        ).scalar_one_or_none()

        if not _wellness_row:
            return

        activities = Activity.get_for_date(user.id, _dt, session=session)

        if not activities:
            return

        hrv_analyses = ActivityHrv.get_for_date(user.id, _dt, session=session)
        tomorrow = today + timedelta(days=1)
        tomorrow_workouts = ScheduledWorkout.get_for_date(user.id, dt=tomorrow, session=session)

    set_language(user.language or "ru")
    summary = build_evening_message(_wellness_row, activities, hrv_analyses, tomorrow_workouts)
    tg = TelegramTool(user=user)
    tg.send_message(text=summary)


# ---------------------------------------------------------------------------
#  Post-onboarding "hey" reminder (issue #258)
# ---------------------------------------------------------------------------


@dramatiq.actor(queue_name="default")
@validate_call
def actor_send_onboarding_hey(user: UserDTO) -> None:
    """Post-onboarding nudge for athletes 24-48h after bootstrap completion
    (issue #258).

    Mark-first ordering: ``mark_hey_sent`` returns ``False`` if another
    instance already won the race (e.g. two cron ticks fired close together
    or Dramatiq redelivered the same message). Only the winner sends. The
    rare cost is a missed nudge if Telegram fails immediately after a
    successful UPDATE — much better than the alternative (double-send),
    since the message is one-shot UX.
    """
    if not UserBackfillState.mark_hey_sent(user_id=user.id):
        logger.info("Onboarding hey skipped (already sent / race) user_id=%d", user.id)
        return
    set_language(user.language or "ru")
    text = build_onboarding_hey_message()
    tg = TelegramTool(user=user)
    tg.send_message(text=text)
    logger.info("Sent onboarding hey-message to user_id=%d", user.id)
