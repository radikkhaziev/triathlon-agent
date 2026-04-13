"""MCP tool for weekly training summary."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from data.db import Activity, IqosDaily, MoodCheckin, ScheduledWorkout, Wellness, get_session
from data.utils import extract_sport_ctl
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id

# Recovery category thresholds (same as data/metrics.py)
_RECOVERY_CATEGORIES = {"excellent": "green", "good": "green", "moderate": "yellow", "low": "red"}


@mcp.tool()
async def get_weekly_summary(week_start_date: str = "") -> dict:
    """Get weekly summary: training/wellness/mood/recovery/IQOS/per-sport CTL delta."""
    user_id = get_current_user_id()

    if week_start_date:
        start = date.fromisoformat(week_start_date)
    else:
        today = date.today()
        start = today - timedelta(days=today.weekday())  # Monday

    end = start + timedelta(days=6)
    start_str, end_str = str(start), str(end)

    async with get_session() as session:
        # Activities (include sport type for compliance matching, RPE for subjective load)
        activities = (
            await session.execute(
                select(
                    Activity.type,
                    Activity.moving_time,
                    Activity.icu_training_load,
                    Activity.rpe,
                ).where(
                    Activity.user_id == user_id,
                    Activity.start_date_local >= start_str,
                    Activity.start_date_local <= end_str,
                )
            )
        ).all()

        # Scheduled workouts (include sport type for per-sport compliance)
        planned = (
            await session.execute(
                select(ScheduledWorkout.type).where(
                    ScheduledWorkout.user_id == user_id,
                    ScheduledWorkout.start_date_local >= start_str,
                    ScheduledWorkout.start_date_local <= end_str,
                )
            )
        ).all()

        # Wellness (full row for recovery + weight + sport_info)
        wellness_rows = (
            await session.execute(
                select(
                    Wellness.hrv,
                    Wellness.resting_hr,
                    Wellness.sleep_score,
                    Wellness.sleep_secs,
                    Wellness.ctl,
                    Wellness.recovery_score,
                    Wellness.recovery_category,
                    Wellness.weight,
                    Wellness.sport_info,
                )
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

        # Mood check-ins
        start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)
        mood_rows = (
            await session.execute(
                select(MoodCheckin.energy, MoodCheckin.mood, MoodCheckin.anxiety).where(
                    MoodCheckin.user_id == user_id,
                    MoodCheckin.timestamp >= start_dt,
                    MoodCheckin.timestamp <= end_dt,
                )
            )
        ).all()

    # --- Training by sport ---
    by_sport: dict[str, dict] = {}
    total_tss = 0.0
    total_secs = 0
    actual_types: list[str] = []
    rpe_values: list[int] = []
    for sport, moving_time, tss, rpe in activities:
        s = (sport or "Other").lower()
        actual_types.append(s)
        if s not in by_sport:
            by_sport[s] = {"sessions": 0, "tss": 0.0, "hours": 0.0}
        by_sport[s]["sessions"] += 1
        by_sport[s]["tss"] += tss or 0
        by_sport[s]["hours"] += (moving_time or 0) / 3600
        total_tss += tss or 0
        total_secs += moving_time or 0
        if rpe is not None:
            rpe_values.append(rpe)

    for s in by_sport.values():
        s["tss"] = round(s["tss"], 1)
        s["hours"] = round(s["hours"], 1)

    # --- Compliance (by sport type, not just count) ---
    planned_types = [(r[0] or "Other").lower() for r in planned]
    sessions_planned = len(planned_types)
    sessions_completed = len(activities)

    # Per-sport compliance: count matched pairs
    unmatched_planned = list(planned_types)
    matched = 0
    for act_type in actual_types:
        if act_type in unmatched_planned:
            unmatched_planned.remove(act_type)
            matched += 1
    compliance_pct = round(matched / sessions_planned * 100) if sessions_planned else 0

    # --- Wellness averages ---
    hrvs = [r[0] for r in wellness_rows if r[0] is not None]
    rhrs = [r[1] for r in wellness_rows if r[1] is not None]
    sleep_scores = [r[2] for r in wellness_rows if r[2] is not None]
    sleep_secs = [r[3] for r in wellness_rows if r[3] is not None]
    ctls = [r[4] for r in wellness_rows if r[4] is not None]
    recovery_scores = [r[5] for r in wellness_rows if r[5] is not None]
    recovery_cats = [r[6] for r in wellness_rows if r[6] is not None]
    weights = [r[7] for r in wellness_rows if r[7] is not None]
    sport_infos = [r[8] for r in wellness_rows if r[8] is not None]

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

    # --- Recovery ---
    recovery: dict = {}
    if recovery_scores:
        recovery["avg_score"] = round(sum(recovery_scores) / len(recovery_scores), 1)
        # Count days by color
        color_counts = {"green": 0, "yellow": 0, "red": 0}
        for cat in recovery_cats:
            color = _RECOVERY_CATEGORIES.get(cat, "yellow")
            color_counts[color] += 1
        recovery["days_by_color"] = color_counts

    # --- Weight ---
    weight: dict = {}
    if weights:
        weight["start"] = weights[0]
        weight["end"] = weights[-1]
        weight["delta"] = round(weights[-1] - weights[0], 1)

    # --- Mood ---
    mood: dict = {}
    if mood_rows:
        energies = [r[0] for r in mood_rows if r[0] is not None]
        moods = [r[1] for r in mood_rows if r[1] is not None]
        anxieties = [r[2] for r in mood_rows if r[2] is not None]
        mood = {
            "checkins": len(mood_rows),
            "energy_avg": round(sum(energies) / len(energies), 1) if energies else None,
            "mood_avg": round(sum(moods) / len(moods), 1) if moods else None,
            "anxiety_avg": round(sum(anxieties) / len(anxieties), 1) if anxieties else None,
        }

    # --- IQOS ---
    iqos_counts = [r[0] for r in iqos_rows]
    iqos_total = sum(iqos_counts)
    iqos = {
        "total": iqos_total,
        "days_tracked": len(iqos_counts),
        "avg_per_day": round(iqos_total / len(iqos_counts), 1) if iqos_counts else 0,
    }

    # --- CTL delta (total + per-sport) ---
    load: dict = {}
    if len(ctls) >= 2:
        load["ctl_start"] = round(ctls[0], 1)
        load["ctl_end"] = round(ctls[-1], 1)
        load["ctl_delta"] = round(ctls[-1] - ctls[0], 1)

    # Per-sport CTL from first and last day's sport_info
    if len(sport_infos) >= 2:
        first_sport_ctl = extract_sport_ctl(sport_infos[0])
        last_sport_ctl = extract_sport_ctl(sport_infos[-1])
        per_sport_ctl: dict = {}
        for sport_key in ("swim", "ride", "run"):
            s_start = first_sport_ctl.get(sport_key)
            s_end = last_sport_ctl.get(sport_key)
            if s_start is not None and s_end is not None:
                per_sport_ctl[sport_key] = {
                    "start": s_start,
                    "end": s_end,
                    "delta": round(s_end - s_start, 1),
                }
        if per_sport_ctl:
            load["per_sport_ctl"] = per_sport_ctl

    # --- RPE (Borg CR-10 subjective load) ---
    # Null-aware aggregates: avg/min/max/distribution computed only over rated
    # sessions; `missing` reports the unrated count separately so Claude knows
    # how complete the data is. See docs/RPE_SPEC.md.
    rpe: dict = {
        "count": len(rpe_values),
        "missing": sessions_completed - len(rpe_values),
    }
    if rpe_values:
        distribution: dict[str, int] = {}
        for v in rpe_values:
            distribution[str(v)] = distribution.get(str(v), 0) + 1
        rpe["avg"] = round(sum(rpe_values) / len(rpe_values), 1)
        rpe["min"] = min(rpe_values)
        rpe["max"] = max(rpe_values)
        rpe["distribution"] = distribution

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
        "recovery": recovery,
        "weight": weight,
        "mood": mood,
        "iqos": iqos,
        "load": load,
        "rpe": rpe,
    }
