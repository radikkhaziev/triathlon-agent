import logging
from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

from bot.formatter import format_goal_message, format_morning_message, format_status_message
from config import settings
from data.models import DailyMetrics, GoalProgress, ReadinessLevel

logger = logging.getLogger(__name__)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Welcome to your Triathlon Coach Bot!* \U0001f3ca\U0001f6b4\U0001f3c3\n\n"
        "I analyze your Garmin data every morning and send you a training report "
        "with AI-powered recommendations.\n\n"
        "*Commands:*\n"
        "/report \u2014 Get today's morning report\n"
        "/status \u2014 Quick status (numbers only)\n"
        "/week \u2014 Weekly training summary\n"
        "/goal \u2014 Goal progress breakdown\n"
        "/zones \u2014 HR threshold zones\n"
        "/sync \u2014 Manually sync Garmin data\n",
        parse_mode="Markdown",
    )


async def report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Generating morning report...")

    from bot.scheduler import morning_report_job

    try:
        message = await morning_report_job()
        keyboard = _report_keyboard()
        await update.message.reply_text(message, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        logger.exception("Failed to generate report")
        await update.message.reply_text("Failed to generate report. Check logs for details.")


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from data.database import get_daily_metrics

    row = get_daily_metrics(date.today())
    if row is None:
        await update.message.reply_text("No data for today yet. Try /sync first.")
        return

    metrics = DailyMetrics(
        date=date.fromisoformat(row.date),
        readiness_score=row.readiness_score or 0,
        readiness_level=ReadinessLevel(row.readiness_level or "yellow"),
        hrv_delta_pct=((row.hrv_last or 0) - (row.hrv_baseline or 1)) / (row.hrv_baseline or 1) * 100,
        sleep_score=row.sleep_score or 0,
        body_battery_morning=row.body_battery or 0,
        resting_hr=row.resting_hr or 0,
        ctl=row.ctl or 0,
        atl=row.atl or 0,
        tsb=row.tsb or 0,
        ctl_swim=row.ctl_swim or 0,
        ctl_bike=row.ctl_bike or 0,
        ctl_run=row.ctl_run or 0,
    )
    await update.message.reply_text(format_status_message(metrics), parse_mode="Markdown")


async def week_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from data.database import get_activities, get_daily_metrics_range

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    activities = get_activities(week_start, today)
    metrics = get_daily_metrics_range(week_start, today)

    if not activities and not metrics:
        await update.message.reply_text("No data for this week yet.")
        return

    lines = ["*This Week's Training*\n"]
    total_tss = 0.0
    for act in activities:
        sport_emoji = {"swimming": "\U0001f3ca", "cycling": "\U0001f6b4", "running": "\U0001f3c3"}.get(
            act.sport or "", "\U0001f3cb"
        )
        tss = act.tss or 0
        total_tss += tss
        duration_min = (act.duration_sec or 0) / 60
        lines.append(f"{sport_emoji} {act.date} \u2014 {duration_min:.0f}min, TSS {tss:.0f}")

    lines.append(f"\n*Total TSS: {total_tss:.0f}*")

    if metrics:
        last = metrics[-1]
        lines.append(f"CTL `{last.ctl or 0:.0f}` | ATL `{last.atl or 0:.0f}` | TSB `{last.tsb or 0:+.0f}`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def goal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from data.database import get_daily_metrics

    row = get_daily_metrics(date.today())
    goal = _build_goal_progress(row)
    await update.message.reply_text(format_goal_message(goal), parse_mode="Markdown")


async def zones_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from data.metrics import HR_ZONES

    lthr_run = settings.ATHLETE_LTHR_RUN
    lthr_bike = settings.ATHLETE_LTHR_BIKE

    lines = ["*Heart Rate Zones*\n", f"*Running* (LTHR: {lthr_run} bpm)"]
    for zone, (low, high) in HR_ZONES["run"].items():
        lines.append(f"  Z{zone}: {int(lthr_run * low)}-{int(lthr_run * high)} bpm")

    lines.append(f"\n*Cycling* (LTHR: {lthr_bike} bpm)")
    for zone, (low, high) in HR_ZONES["bike"].items():
        lines.append(f"  Z{zone}: {int(lthr_bike * low)}-{int(lthr_bike * high)} bpm")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def sync_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Syncing Garmin data...")

    from bot.scheduler import garmin_sync_job

    try:
        await garmin_sync_job()
        await update.message.reply_text("Sync complete!")
    except Exception:
        logger.exception("Sync failed")
        await update.message.reply_text("Sync failed. Check logs for details.")


async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"*Current Settings*\n\n"
        f"Morning report: {settings.MORNING_REPORT_HOUR}:{settings.MORNING_REPORT_MINUTE:02d} ({settings.TIMEZONE})\n"
        f"Goal: {settings.GOAL_EVENT_NAME} on {settings.GOAL_EVENT_DATE}\n"
        f"LTHR Run: {settings.ATHLETE_LTHR_RUN} bpm\n"
        f"LTHR Bike: {settings.ATHLETE_LTHR_BIKE} bpm\n"
        f"FTP: {settings.ATHLETE_FTP:.0f} W\n"
        f"CSS: {settings.ATHLETE_CSS:.0f} s/100m",
        parse_mode="Markdown",
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "week_plan":
        await query.message.reply_text("Use /week to see this week's plan.")
    elif query.data == "load_chart":
        await query.message.reply_text("Open the dashboard for interactive charts.")


def _report_keyboard() -> InlineKeyboardMarkup:
    webapp_url = settings.WEBAPP_URL
    buttons = []
    if webapp_url:
        buttons.append([InlineKeyboardButton("\U0001f4ca Open Dashboard", web_app=WebAppInfo(url=webapp_url))])
    buttons.append([
        InlineKeyboardButton("\U0001f4c5 Week Plan", callback_data="week_plan"),
        InlineKeyboardButton("\U0001f4c8 Load Chart", callback_data="load_chart"),
    ])
    return InlineKeyboardMarkup(buttons)


def _build_goal_progress(row) -> GoalProgress:
    event_date = settings.GOAL_EVENT_DATE
    weeks_remaining = max(0, (event_date - date.today()).days // 7)

    swim_target = settings.GOAL_SWIM_CTL_TARGET
    bike_target = settings.GOAL_BIKE_CTL_TARGET
    run_target = settings.GOAL_RUN_CTL_TARGET

    ctl_swim = (row.ctl_swim or 0) if row else 0
    ctl_bike = (row.ctl_bike or 0) if row else 0
    ctl_run = (row.ctl_run or 0) if row else 0

    swim_pct = min(100, (ctl_swim / swim_target) * 100) if swim_target else 0
    bike_pct = min(100, (ctl_bike / bike_target) * 100) if bike_target else 0
    run_pct = min(100, (ctl_run / run_target) * 100) if run_target else 0
    overall_pct = (swim_pct + bike_pct + run_pct) / 3

    return GoalProgress(
        event_name=settings.GOAL_EVENT_NAME,
        event_date=event_date,
        weeks_remaining=weeks_remaining,
        overall_pct=overall_pct,
        swim_pct=swim_pct,
        bike_pct=bike_pct,
        run_pct=run_pct,
        on_track=overall_pct >= (100 - weeks_remaining * 2),
    )
