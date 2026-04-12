"""Dramatiq actors — reports (morning, evening), echo, scheduled workouts."""

import logging
from datetime import timedelta

import dramatiq
import sentry_sdk
from pydantic import validate_call
from sqlalchemy import select

from config import settings
from data.db import (
    Activity,
    ActivityHrv,
    AthleteSettings,
    HrvAnalysis,
    ScheduledWorkout,
    ThresholdDriftDTO,
    User,
    UserDTO,
    Wellness,
    WellnessPostDTO,
    get_sync_session,
)
from data.intervals.client import IntervalsSyncClient
from data.intervals.dto import RecoveryScoreDTO, ScheduledWorkoutDTO
from data.workout_adapter import compute_constraints, needs_adaptation, parse_humango_description
from tasks.dto import DateDTO
from tasks.formatter import build_evening_message, build_morning_message
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
    today = DateDTO.today()
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
    from tasks.utils import RampTrainingSuggestion

    summary = build_morning_message(wellness)
    webapp_url = settings.API_BASE_URL
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "Открыть отчёт",
                    "web_app": {"url": webapp_url},
                }
            ]
        ]
    }

    ramp = RampTrainingSuggestion(user=user, wellness=wellness)
    if ramp.is_test_needed:
        sport = ramp.suggested_sport or "Run"
        if ramp.days_since:
            summary += f"\n\n⚡ Ramp Test ({sport}): последний тест {ramp.days_since} дней назад"
        else:
            summary += f"\n\n⚡ Ramp Test ({sport}): тест ещё не выполнялся"

        keyboard["inline_keyboard"].append(
            [
                {
                    "text": f"Создать Ramp Test ({sport})",
                    "callback_data": f"ramp_test:{sport}",
                }
            ]
        )

    # Workout adaptation check
    today = DateDTO.today()

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
        max_zone, _ = compute_constraints(recovery, hrv_status, tsb)

        s = AthleteSettings.get(user.id, w.type or "Run")
        ftp = float(s.ftp) if s and s.ftp else 233
        lthr = s.lthr if s and s.lthr else 153

        if needs_adaptation(steps, max_zone, ftp, lthr):
            w_name = w.name or "Тренировка"
            summary += f"\n\n⚠️ {w_name} требует адаптации (recovery {recovery.category}, max Z{max_zone})"
            keyboard["inline_keyboard"].append(
                [
                    {
                        "text": f"Адаптировать: {w_name}",
                        "callback_data": f"adapt:{w.id}",
                    }
                ]
            )

    # Threshold drift detection
    drift: ThresholdDriftDTO = User.detect_threshold_drift(user_id=user.id)
    if drift:
        for alert in drift.alerts:
            summary += (
                f"\n⚠️ LTHR {alert.sport}: текущий порог {alert.config_value} bpm, "
                f"по тестам {alert.measured_avg} bpm ({alert.diff_pct:+.1f}%). Рекомендуем обновить"
            )
        keyboard["inline_keyboard"].append(
            [
                {"text": "Обновить зоны", "callback_data": "update_zones"},
            ]
        )

    tg = TelegramTool(user=user)
    tg.send_message(text=summary, reply_markup=keyboard, markdown=True)
    logger.info("Morning report sent for user %d", user.id)


@dramatiq.actor(queue_name="default")
@validate_call
def actor_compose_user_morning_report(
    user: UserDTO,
):
    _dt = DateDTO.today().isoformat()

    with get_sync_session() as session:
        _wellness_row = session.execute(
            select(Wellness).where(
                Wellness.user_id == user.id,
                Wellness.date == _dt,
            )
        ).scalar_one_or_none()

        if not _wellness_row or not _wellness_row.sleep_score or _wellness_row.ai_recommendation:
            return

        # Generate morning report: sync Claude API + MCP tools (per-user token)
        try:
            mcp = MCPTool(token=user.mcp_token, user_id=user.id, language=user.language)
            text = mcp.generate_morning_report_via_mcp(_dt)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Morning report generation failed for user %d", user.id)
            return
        if not text:
            return

        _wellness_row.ai_recommendation = text
        session.commit()
        session.refresh(_wellness_row)
        logger.info("Morning report saved for user %d, date %s", user.id, _dt)

    wellness_dto = WellnessPostDTO.model_validate(_wellness_row)

    _actor_send_user_morning_report.send(
        user=user,
        wellness=wellness_dto,
    )


# ---------------------------------------------------------------------------
#  Weekly Report
# ---------------------------------------------------------------------------


@dramatiq.actor(queue_name="default", time_limit=600_000)
@validate_call
def actor_compose_weekly_report(user: UserDTO):
    """Generate weekly training summary via Claude + MCP tools."""
    mcp = MCPTool(token=user.mcp_token, user_id=user.id, language=user.language)
    text = mcp.generate_weekly_report_via_mcp()

    if not text:
        logger.warning("Weekly report empty for user %d", user.id)
        return

    tg = TelegramTool(user=user)
    if not user.is_silent:
        tg.send_message(text=text, markdown=True)
    logger.info("Weekly report sent for user %d", user.id)


# ---------------------------------------------------------------------------
#  Evening Report
# ---------------------------------------------------------------------------


@dramatiq.actor(queue_name="default")
@validate_call
def actor_compose_user_evening_report(
    user: UserDTO,
) -> None:
    today = DateDTO.today()
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

    summary = build_evening_message(_wellness_row, activities, hrv_analyses, tomorrow_workouts)
    tg = TelegramTool(user=user)
    tg.send_message(text=summary)
