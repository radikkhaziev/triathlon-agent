"""MCP tools for Garmin GDPR export data."""

from datetime import date, timedelta

from sqlalchemy import func, select

from data.db import (
    GarminAbnormalHrEvents,
    GarminDailySummary,
    GarminFitnessMetrics,
    GarminHealthStatus,
    GarminRacePredictions,
    GarminSleep,
    GarminTrainingLoad,
    GarminTrainingReadiness,
    get_session,
)
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


def _date_range(target_date: str, days_back: int) -> tuple[str, str]:
    ref = date.fromisoformat(target_date) if target_date else date.today()
    start = ref - timedelta(days=days_back - 1)
    return str(start), str(ref)


async def _data_freshness(user_id: int) -> dict:
    """Compute data freshness metadata for Garmin data."""
    async with get_session() as session:
        max_date = (
            await session.execute(select(func.max(GarminSleep.calendar_date)).where(GarminSleep.user_id == user_id))
        ).scalar()

    if not max_date:
        return {"data_covers_until": None, "days_stale": None, "freshness_warning": "No Garmin data imported yet."}

    days_stale = (date.today() - date.fromisoformat(max_date)).days
    warning = None
    if days_stale > 14:
        warning = f"Garmin data is {days_stale} days old. Request a new export at garmin.com/account/datamanagement."
    elif days_stale > 7:
        warning = f"Garmin data is {days_stale} days old. Use for trends and patterns, not current state."

    return {"data_covers_until": max_date, "days_stale": days_stale, "freshness_warning": warning}


@mcp.tool()
async def get_garmin_sleep(target_date: str = "", days_back: int = 7) -> dict:
    """Get Garmin sleep data: phases (deep/light/REM), 7 sub-scores, respiration, stress."""
    user_id = get_current_user_id()
    freshness = await _data_freshness(user_id)
    start, end = _date_range(target_date, days_back)
    rows = await GarminSleep.get_range(user_id, start, end)

    return {
        "data_freshness": freshness,
        "count": len(rows),
        "entries": [
            {
                "date": r.calendar_date,
                "sleep_start": r.sleep_start_gmt,
                "sleep_end": r.sleep_end_gmt,
                "phases": {
                    "deep_min": r.deep_sleep_secs // 60 if r.deep_sleep_secs else None,
                    "light_min": r.light_sleep_secs // 60 if r.light_sleep_secs else None,
                    "rem_min": r.rem_sleep_secs // 60 if r.rem_sleep_secs else None,
                    "awake_min": r.awake_sleep_secs // 60 if r.awake_sleep_secs else None,
                },
                "scores": {
                    "overall": r.overall_score,
                    "quality": r.quality_score,
                    "duration": r.duration_score,
                    "recovery": r.recovery_score,
                    "deep": r.deep_score,
                    "rem": r.rem_score,
                    "restfulness": r.restfulness_score,
                },
                "respiration": {
                    "avg": r.avg_respiration,
                    "low": r.lowest_respiration,
                    "high": r.highest_respiration,
                },
                "avg_sleep_stress": r.avg_sleep_stress,
                "awake_count": r.awake_count,
            }
            for r in rows
        ],
    }


@mcp.tool()
async def get_garmin_readiness(target_date: str = "", days_back: int = 7) -> dict:
    """Get Garmin Training Readiness: score (0-100), level, and contributing factors."""
    user_id = get_current_user_id()
    freshness = await _data_freshness(user_id)
    start, end = _date_range(target_date, days_back)
    rows = await GarminTrainingReadiness.get_range(user_id, start, end)

    return {
        "data_freshness": freshness,
        "count": len(rows),
        "entries": [
            {
                "date": r.calendar_date,
                "score": r.score,
                "level": r.level,
                "feedback": r.feedback_short,
                "factors": {
                    "hrv_pct": r.hrv_factor_pct,
                    "sleep_score_pct": r.sleep_score_factor_pct,
                    "sleep_history_pct": r.sleep_history_factor_pct,
                    "recovery_time_h": r.recovery_time,
                    "recovery_pct": r.recovery_factor_pct,
                    "acwr_pct": r.acwr_factor_pct,
                    "stress_history_pct": r.stress_history_factor_pct,
                },
                "hrv_weekly_avg": r.hrv_weekly_avg,
                "acute_load": r.acute_load,
            }
            for r in rows
        ],
    }


@mcp.tool()
async def get_garmin_daily_metrics(target_date: str = "", days_back: int = 7) -> dict:
    """Get Garmin daily metrics: stress, body battery, steps, health baselines, and ACWR."""
    user_id = get_current_user_id()
    freshness = await _data_freshness(user_id)
    start, end = _date_range(target_date, days_back)

    daily = {r.calendar_date: r for r in await GarminDailySummary.get_range(user_id, start, end)}
    health = {r.calendar_date: r for r in await GarminHealthStatus.get_range(user_id, start, end)}
    load = {r.calendar_date: r for r in await GarminTrainingLoad.get_range(user_id, start, end)}

    all_dates = sorted(set(daily) | set(health) | set(load))

    entries = []
    for dt in all_dates:
        d = daily.get(dt)
        h = health.get(dt)
        lo = load.get(dt)
        entries.append(
            {
                "date": dt,
                "stress": {
                    "avg": d.avg_stress if d else None,
                    "max": d.max_stress if d else None,
                    "high_min": d.stress_high_secs // 60 if d and d.stress_high_secs else None,
                    "low_min": d.stress_low_secs // 60 if d and d.stress_low_secs else None,
                    "rest_min": d.stress_rest_secs // 60 if d and d.stress_rest_secs else None,
                },
                "body_battery": {
                    "high": d.body_battery_high if d else None,
                    "low": d.body_battery_low if d else None,
                    "charged": d.body_battery_charged if d else None,
                    "drained": d.body_battery_drained if d else None,
                },
                "steps": d.total_steps if d else None,
                "resting_hr": d.resting_hr if d else None,
                "health_baselines": {
                    "hrv": {"value": h.hrv_value, "status": h.hrv_status} if h and h.hrv_value else None,
                    "hr": {"value": h.hr_value, "status": h.hr_status} if h and h.hr_value else None,
                    "spo2": {"value": h.spo2_value, "status": h.spo2_status} if h and h.spo2_value else None,
                    "respiration": (
                        {"value": h.respiration_value, "status": h.respiration_status}
                        if h and h.respiration_value
                        else None
                    ),
                },
                "training_load": {
                    "acwr": lo.acwr if lo else None,
                    "status": lo.acwr_status if lo else None,
                    "acute": lo.acute_load if lo else None,
                    "chronic": lo.chronic_load if lo else None,
                },
            }
        )

    return {"data_freshness": freshness, "count": len(entries), "entries": entries}


@mcp.tool()
async def get_garmin_race_predictions(target_date: str = "", days_back: int = 30) -> dict:
    """Get Garmin race predictions: 5K, 10K, half marathon, marathon times."""
    user_id = get_current_user_id()
    freshness = await _data_freshness(user_id)
    start, end = _date_range(target_date, days_back)
    rows = await GarminRacePredictions.get_range(user_id, start, end)

    def _fmt(secs: int | None) -> str | None:
        if not secs:
            return None
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    return {
        "data_freshness": freshness,
        "count": len(rows),
        "entries": [
            {
                "date": r.calendar_date,
                "5k": {"secs": r.prediction_5k_secs, "time": _fmt(r.prediction_5k_secs)},
                "10k": {"secs": r.prediction_10k_secs, "time": _fmt(r.prediction_10k_secs)},
                "half": {"secs": r.prediction_half_secs, "time": _fmt(r.prediction_half_secs)},
                "marathon": {"secs": r.prediction_marathon_secs, "time": _fmt(r.prediction_marathon_secs)},
            }
            for r in rows
        ],
    }


@mcp.tool()
async def get_garmin_vo2max_trend(sport: str = "cycling", days_back: int = 90) -> dict:
    """Get VO2max, endurance score, and max MET trend from Garmin fitness metrics."""
    user_id = get_current_user_id()
    freshness = await _data_freshness(user_id)
    start, end = _date_range("", days_back)
    rows = await GarminFitnessMetrics.get_range(user_id, start, end)

    vo2_field = "vo2max_running" if sport.lower() == "running" else "vo2max_cycling"
    entries = []
    for r in rows:
        vo2 = getattr(r, vo2_field)
        if vo2 or r.endurance_score or r.max_met:
            entries.append(
                {
                    "date": r.calendar_date,
                    "vo2max": vo2,
                    "endurance_score": r.endurance_score,
                    "max_met": r.max_met,
                    "fitness_age": r.fitness_age,
                }
            )

    return {"data_freshness": freshness, "sport": sport, "count": len(entries), "entries": entries}


@mcp.tool()
async def get_garmin_abnormal_hr_events(days_back: int = 30) -> dict:
    """Get abnormal HR events detected by Garmin (high HR alerts)."""
    user_id = get_current_user_id()
    freshness = await _data_freshness(user_id)
    start, end = _date_range("", days_back)
    rows = await GarminAbnormalHrEvents.get_range(user_id, start, end)

    return {
        "data_freshness": freshness,
        "count": len(rows),
        "entries": [
            {
                "date": r.calendar_date,
                "timestamp": r.timestamp_gmt,
                "hr_value": r.hr_value,
                "threshold": r.threshold_value,
            }
            for r in rows
        ],
    }
