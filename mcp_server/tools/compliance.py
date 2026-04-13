"""MCP tool for workout compliance analysis: planned vs actual."""

import re

from sqlalchemy import select

from data.db import Activity, ActivityDetail, ScheduledWorkout, TrainingLog, get_session
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_workout_compliance(activity_id: str) -> dict:
    """Compare completed activity against scheduled workout. Returns duration + intensity compliance."""
    user_id = get_current_user_id()

    async with get_session() as session:
        activity = (
            await session.execute(select(Activity).where(Activity.id == activity_id, Activity.user_id == user_id))
        ).scalar_one_or_none()

        if not activity:
            return {"error": f"Activity {activity_id} not found."}

        detail = (
            await session.execute(select(ActivityDetail).where(ActivityDetail.activity_id == activity_id))
        ).scalar_one_or_none()

        act_date = activity.start_date_local
        act_type = activity.type
        act_moving_time = activity.moving_time
        act_avg_hr = activity.average_hr
        act_tss = activity.icu_training_load
        act_rpe = activity.rpe
        det_avg_power = detail.avg_power if detail else None
        det_hr_zone_times = detail.hr_zone_times if detail else None
        det_power_zone_times = detail.power_zone_times if detail else None

    workouts = await ScheduledWorkout.get_for_date(user_id, act_date)
    matched = next((w for w in workouts if w.type == act_type), None)

    logs = await TrainingLog.get_for_date(user_id, act_date)
    log_entry = next((entry for entry in logs if str(entry.actual_activity_id) == str(activity_id)), None)

    planned = None
    power_target = None
    if matched:
        power_target = _parse_power_target(matched.description)
        planned = {
            "name": matched.name,
            "duration_min": matched.moving_time // 60 if matched.moving_time else None,
            "power_target": power_target,
        }

    actual = {
        "sport": act_type,
        "duration_min": act_moving_time // 60 if act_moving_time else None,
        "avg_hr": act_avg_hr,
        "avg_power": det_avg_power,
        "tss": act_tss,
        "rpe": act_rpe,
        "max_zone": log_entry.actual_max_zone_time if log_entry else None,
    }

    compliance = _compute_compliance(planned, actual, power_target, det_hr_zone_times, det_power_zone_times)

    return {
        "activity_id": activity_id,
        "date": act_date,
        "planned": planned,
        "actual": actual,
        "compliance": compliance,
        "training_log_compliance": log_entry.compliance if log_entry else None,
    }


def _parse_power_target(description: str | None) -> dict | None:
    """Extract power targets from workout description (HumanGo format)."""
    if not description:
        return None

    # Match patterns like "low: 91 W" and "high: 114 W"
    lows = re.findall(r"low:\s*(\d+)\s*W", description)
    highs = re.findall(r"high:\s*(\d+)\s*W", description)

    if not lows and not highs:
        return None

    # Use the most common interval target (skip warmup/cooldown which are usually first/last)
    # Take the middle range if multiple intervals
    low_vals = [int(v) for v in lows]
    high_vals = [int(v) for v in highs]

    if len(low_vals) > 2:
        # Skip first (warmup) and last (cooldown), average the rest
        low_avg = round(sum(low_vals[1:-1]) / len(low_vals[1:-1]))
        high_avg = round(sum(high_vals[1:-1]) / len(high_vals[1:-1])) if len(high_vals) > 2 else high_vals[-1]
    else:
        low_avg = low_vals[-1] if low_vals else None
        high_avg = high_vals[-1] if high_vals else None

    return {"low_w": low_avg, "high_w": high_avg}


def _compute_compliance(
    planned: dict | None,
    actual: dict,
    power_target: dict | None,
    hr_zone_times: list | None,
    power_zone_times: list | None,
) -> dict:
    if not planned:
        return {"overall": "unplanned", "note": "No scheduled workout found for this date/sport."}

    result: dict = {}
    scores: list[str] = []

    # Duration compliance
    if planned.get("duration_min") and actual.get("duration_min"):
        duration_pct = round(actual["duration_min"] / planned["duration_min"] * 100)
        result["duration_pct"] = duration_pct
        if 90 <= duration_pct <= 110:
            scores.append("excellent")
        elif 70 <= duration_pct <= 130:
            scores.append("good")
        elif 50 <= duration_pct <= 150:
            scores.append("partial")
        else:
            scores.append("off_target")

    # Power intensity compliance
    if power_target and actual.get("avg_power"):
        low = power_target.get("low_w")
        high = power_target.get("high_w")
        avg_p = actual["avg_power"]
        result["power_target"] = power_target

        if low and high:
            if low <= avg_p <= high:
                result["intensity_in_target"] = True
                result["power_deviation_pct"] = 0
                scores.append("excellent")
            else:
                result["intensity_in_target"] = False
                mid = (low + high) / 2
                result["power_deviation_pct"] = round((avg_p - mid) / mid * 100, 1)
                if low * 0.9 <= avg_p <= high * 1.1:
                    scores.append("good")
                else:
                    scores.append("partial")

    # Zone distribution (informational)
    if hr_zone_times and len(hr_zone_times) >= 5:
        zones = hr_zone_times[1:6] if len(hr_zone_times) >= 6 else hr_zone_times[:5]
        total = sum(zones)
        if total > 0:
            result["hr_zone_distribution"] = {f"Z{i+1}": round(z / total * 100) for i, z in enumerate(zones)}

    # Overall from worst score
    if not scores:
        result["overall"] = "unknown"
        result["note"] = "Missing duration and power data."
    else:
        rank = {"off_target": 0, "partial": 1, "good": 2, "excellent": 3}
        worst = min(scores, key=lambda s: rank.get(s, 0))
        result["overall"] = worst

    return result
