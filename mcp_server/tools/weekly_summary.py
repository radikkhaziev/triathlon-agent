"""MCP tool for weekly training summary."""

from datetime import date, timedelta

from sqlalchemy import select

from data.db import Activity, IqosDaily, ScheduledWorkout, Wellness, get_session
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_weekly_summary(week_start_date: str = "") -> dict:
    """Get weekly summary: training sessions/TSS/hours by sport, wellness averages, IQOS, CTL delta."""
    user_id = get_current_user_id()

    if week_start_date:
        start = date.fromisoformat(week_start_date)
    else:
        today = date.today()
        start = today - timedelta(days=today.weekday())  # Monday

    end = start + timedelta(days=6)
    start_str, end_str = str(start), str(end)

    async with get_session() as session:
        # Activities
        activities = (
            await session.execute(
                select(Activity.type, Activity.moving_time, Activity.icu_training_load).where(
                    Activity.user_id == user_id,
                    Activity.start_date_local >= start_str,
                    Activity.start_date_local <= end_str,
                )
            )
        ).all()

        # Scheduled workouts
        planned = (
            await session.execute(
                select(ScheduledWorkout.type).where(
                    ScheduledWorkout.user_id == user_id,
                    ScheduledWorkout.start_date_local >= start_str,
                    ScheduledWorkout.start_date_local <= end_str,
                )
            )
        ).all()

        # Wellness
        wellness_rows = (
            await session.execute(
                select(Wellness.hrv, Wellness.resting_hr, Wellness.sleep_score, Wellness.sleep_seconds, Wellness.ctl)
                .where(Wellness.user_id == user_id, Wellness.date >= start_str, Wellness.date <= end_str)
                .order_by(Wellness.date.asc())
            )
        ).all()

        # IQOS
        iqos_rows = (
            await session.execute(
                select(IqosDaily.count).where(
                    IqosDaily.user_id == user_id, IqosDaily.date >= start_str, IqosDaily.date <= end_str
                )
            )
        ).all()

    # Training by sport
    by_sport: dict[str, dict] = {}
    total_tss = 0.0
    total_secs = 0
    for sport, moving_time, tss in activities:
        s = (sport or "Other").lower()
        if s not in by_sport:
            by_sport[s] = {"sessions": 0, "tss": 0.0, "hours": 0.0}
        by_sport[s]["sessions"] += 1
        by_sport[s]["tss"] += tss or 0
        by_sport[s]["hours"] += (moving_time or 0) / 3600
        total_tss += tss or 0
        total_secs += moving_time or 0

    for s in by_sport.values():
        s["tss"] = round(s["tss"], 1)
        s["hours"] = round(s["hours"], 1)

    sessions_planned = len(planned)
    sessions_completed = len(activities)
    compliance_pct = round(sessions_completed / sessions_planned * 100) if sessions_planned else 0

    # Wellness averages
    hrvs = [r[0] for r in wellness_rows if r[0] is not None]
    rhrs = [r[1] for r in wellness_rows if r[1] is not None]
    sleep_scores = [r[2] for r in wellness_rows if r[2] is not None]
    sleep_secs = [r[3] for r in wellness_rows if r[3] is not None]
    ctls = [r[4] for r in wellness_rows if r[4] is not None]

    hrv_avg = round(sum(hrvs) / len(hrvs), 1) if hrvs else None
    hrv_cv = (
        round((sum((h - hrv_avg) ** 2 for h in hrvs) / len(hrvs)) ** 0.5 / hrv_avg * 100, 1)
        if hrvs and hrv_avg
        else None
    )

    wellness = {
        "hrv_avg": hrv_avg,
        "hrv_min": min(hrvs) if hrvs else None,
        "hrv_max": max(hrvs) if hrvs else None,
        "hrv_cv": hrv_cv,
        "rhr_avg": round(sum(rhrs) / len(rhrs), 1) if rhrs else None,
        "sleep_avg_score": round(sum(sleep_scores) / len(sleep_scores), 1) if sleep_scores else None,
        "sleep_avg_hours": round(sum(sleep_secs) / len(sleep_secs) / 3600, 1) if sleep_secs else None,
        "sleep_7h_days": sum(1 for s in sleep_secs if s >= 25200) if sleep_secs else 0,
    }

    # IQOS
    iqos_counts = [r[0] for r in iqos_rows]
    iqos_total = sum(iqos_counts)
    iqos = {
        "total": iqos_total,
        "days_tracked": len(iqos_counts),
        "avg_per_day": round(iqos_total / len(iqos_counts), 1) if iqos_counts else 0,
    }

    # CTL delta
    load = {}
    if len(ctls) >= 2:
        load = {
            "ctl_start": round(ctls[0], 1),
            "ctl_end": round(ctls[-1], 1),
            "ctl_delta": round(ctls[-1] - ctls[0], 1),
        }

    return {
        "week": f"{start_str} to {end_str}",
        "training": {
            "sessions_planned": sessions_planned,
            "sessions_completed": sessions_completed,
            "compliance_pct": compliance_pct,
            "total_tss": round(total_tss, 1),
            "total_hours": round(total_secs / 3600, 1),
            "by_sport": by_sport,
        },
        "wellness": wellness,
        "iqos": iqos,
        "load": load,
    }
