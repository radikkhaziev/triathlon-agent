import asyncio
import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from data.database import save_daily_metrics
from data.garmin_client import GarminClient
from data.models import SleepData

logger = logging.getLogger(__name__)


def create_scheduler() -> AsyncIOScheduler:
    # Initialize the GarminClient singleton with credentials.
    # All subsequent GarminClient() calls return this instance.
    try:
        GarminClient(settings.GARMIN_EMAIL, settings.GARMIN_PASSWORD.get_secret_value())
    except Exception as exc:
        logger.error("Failed to initialize GarminClient: %s", exc)

    scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

    # scheduler.add_job(
    #     garmin_sync_job,
    #     trigger="cron",
    #     hour=settings.MORNING_REPORT_HOUR,
    #     minute=max(0, settings.MORNING_REPORT_MINUTE - 30),
    #     id="garmin_sync",
    #     replace_existing=True,
    # )

    # scheduler.add_job(
    #     morning_report_job,
    #     trigger="cron",
    #     hour=settings.MORNING_REPORT_HOUR,
    #     minute=settings.MORNING_REPORT_MINUTE,
    #     id="morning_report",
    #     replace_existing=True,
    # )

    scheduler.add_job(
        daily_metrics_job,
        trigger="cron",
        hour="5-20",
        minute="*/15",
        id="daily_metrics",
    )

    return scheduler


async def garmin_sync_job() -> None:
    logger.info("Starting Garmin data sync")

    from data.database import save_activity, save_tss_history
    from data.metrics import calc_hr_tss, calc_power_tss, calc_swim_tss
    from data.models import SportType

    client = GarminClient()
    activities = await asyncio.to_thread(client.get_activities, 0, 30)

    resting_hr = settings.ATHLETE_RESTING_HR
    max_hr = float(settings.ATHLETE_MAX_HR)
    lthr_run = float(settings.ATHLETE_LTHR_RUN)
    lthr_bike = float(settings.ATHLETE_LTHR_BIKE)
    ftp = settings.ATHLETE_FTP
    css = settings.ATHLETE_CSS

    for act in activities:
        tss = act.tss
        if tss is None and act.avg_hr:
            if act.sport == SportType.RUN:
                tss = calc_hr_tss(
                    act.duration_seconds, act.avg_hr, resting_hr, max_hr, lthr_run
                )
            elif act.sport == SportType.BIKE:
                if act.normalized_power:
                    tss = calc_power_tss(
                        act.duration_seconds, act.normalized_power, ftp
                    )
                else:
                    tss = calc_hr_tss(
                        act.duration_seconds, act.avg_hr, resting_hr, max_hr, lthr_bike
                    )
            elif act.sport == SportType.SWIM and act.distance_meters:
                tss = calc_swim_tss(act.distance_meters, act.duration_seconds, css)

        act_date = act.start_time.date()
        await save_activity(
            activity_id=act.activity_id,
            dt=act_date,
            sport=act.sport.value,
            duration_sec=act.duration_seconds,
            distance_m=act.distance_meters,
            avg_hr=act.avg_hr,
            max_hr=act.max_hr,
            avg_power=act.avg_power,
            norm_power=act.normalized_power,
            tss=tss,
        )

        if tss is not None:
            await save_tss_history(act_date, act.sport.value, tss)

    logger.info("Garmin sync complete: %d activities processed", len(activities))


async def morning_report_job() -> str:
    logger.info("Generating morning report")

    from ai.claude_agent import ClaudeAgent
    from bot.formatter import format_morning_message
    from bot.handlers import _build_goal_progress
    from data.database import (
        get_daily_metrics,
        get_scheduled_workouts,
        get_tss_history,
        save_daily_metrics,
    )
    from data.metrics import calculate_readiness, update_ctl_atl
    from data.models import DailyMetrics, ReadinessLevel, ScheduledWorkout

    today = date.today()
    today_str = str(today)

    resting_hr_baseline = settings.ATHLETE_RESTING_HR

    garmin = GarminClient()

    sleep = await asyncio.to_thread(garmin.get_sleep, today_str)
    hrv = await asyncio.to_thread(garmin.get_hrv, today_str)
    resting_hr = (
        await asyncio.to_thread(garmin.get_resting_hr, today_str) or resting_hr_baseline
    )
    stress = await asyncio.to_thread(garmin.get_stress, today_str)

    body_battery_list = await asyncio.to_thread(
        garmin.get_body_battery, today_str, today_str
    )
    body_battery = body_battery_list[0].start_value if body_battery_list else 50

    readiness_score, readiness_level = calculate_readiness(
        hrv, sleep, body_battery, resting_hr, resting_hr_baseline
    )

    tss_rows = await get_tss_history(days=42)
    daily_tss: dict[str, float] = {}
    sport_tss: dict[str, dict[str, float]] = {}
    for row in tss_rows:
        daily_tss[row.date] = daily_tss.get(row.date, 0) + (row.tss or 0)
        sport_tss.setdefault(row.sport, {})[row.date] = row.tss or 0

    sorted_dates = sorted(daily_tss.keys())
    tss_list = [daily_tss[d] for d in sorted_dates]
    ctl, atl, tsb = update_ctl_atl(tss_list)

    def sport_ctl(sport: str) -> float:
        dates = sorted(sport_tss.get(sport, {}).keys())
        values = [sport_tss[sport][d] for d in dates]
        c, _, _ = update_ctl_atl(values) if values else (0.0, 0.0, 0.0)
        return c

    ctl_swim = sport_ctl("swimming")
    ctl_bike = sport_ctl("cycling")
    ctl_run = sport_ctl("running")

    hrv_delta_pct = (
        ((hrv.hrv_last_night - hrv.hrv_weekly_avg) / hrv.hrv_weekly_avg * 100)
        if hrv.hrv_weekly_avg
        else 0
    )

    await save_daily_metrics(
        today,
        sleep_score=sleep.score,
        sleep_duration=sleep.duration,
        hrv_last=hrv.hrv_last_night,
        hrv_baseline=hrv.hrv_weekly_avg,
        body_battery=body_battery,
        resting_hr=resting_hr,
        stress_score=int(stress.avg_stress),
        readiness_score=readiness_score,
        readiness_level=readiness_level.value,
        ctl=ctl,
        atl=atl,
        tsb=tsb,
        ctl_swim=ctl_swim,
        ctl_bike=ctl_bike,
        ctl_run=ctl_run,
    )

    metrics = DailyMetrics(
        date=today,
        readiness_score=readiness_score,
        readiness_level=readiness_level,
        hrv_delta_pct=hrv_delta_pct,
        sleep_score=sleep.score,
        body_battery_morning=body_battery,
        resting_hr=resting_hr,
        ctl=ctl,
        atl=atl,
        tsb=tsb,
        ctl_swim=ctl_swim,
        ctl_bike=ctl_bike,
        ctl_run=ctl_run,
    )

    workout_rows = await get_scheduled_workouts(today)
    workout = None
    if workout_rows:
        w = workout_rows[0]
        from data.models import SportType

        try:
            sport = SportType(w.sport)
        except ValueError:
            sport = SportType.OTHER
        workout = ScheduledWorkout(
            scheduled_date=today,
            workout_name=w.workout_name or "Workout",
            sport=sport,
            description=w.description,
            planned_duration_seconds=None,
            planned_tss=w.planned_tss,
        )

    row = await get_daily_metrics(today)
    goal = _build_goal_progress(row)

    sleep_hours = sleep.duration / 3600
    sleep_duration_str = f"{int(sleep_hours)}h {int((sleep_hours % 1) * 60)}m"

    agent = ClaudeAgent()
    ai_text = await agent.get_morning_recommendation(
        metrics=metrics,
        hrv_last=hrv.hrv_last_night,
        hrv_baseline=hrv.hrv_weekly_avg,
        sleep_duration_str=sleep_duration_str,
        stress_score=int(stress.avg_stress),
        resting_hr_baseline=resting_hr_baseline,
        workout=workout,
        goal=goal,
    )

    await save_daily_metrics(today, ai_recommendation=ai_text)

    message = format_morning_message(metrics, workout, goal, ai_text)

    bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    chat_id = settings.TELEGRAM_CHAT_ID
    if bot_token and chat_id:
        import telegram

        bot = telegram.Bot(token=bot_token)
        from bot.handlers import _report_keyboard

        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="Markdown",
            reply_markup=_report_keyboard(),
        )

    return message


async def daily_metrics_job() -> None:
    try:
        garmin = GarminClient()
    except RuntimeError as exc:
        logger.warning("Skipping daily_metrics_job: %s", exc)
        return

    today = date.today()
    today_str = str(today)

    sleep: SleepData = await asyncio.to_thread(garmin.get_sleep, today_str)

    await save_daily_metrics(today, sleep_data=sleep)
