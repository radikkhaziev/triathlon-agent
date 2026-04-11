"""MCP tool for weight trend analysis."""

from datetime import date, timedelta

from sqlalchemy import select

from data.db import GarminBioMetrics, Wellness, get_session
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_weight_trend(days_back: int = 30, target_kg: float = 0) -> dict:
    """Get weight trend: current, average, slope (kg/week), direction, and estimated target date."""
    user_id = get_current_user_id()
    ref = date.today()
    start = ref - timedelta(days=days_back - 1)
    start_str, end_str = str(start), str(ref)

    # Collect from both sources, deduplicate by date (Garmin preferred)
    weights: dict[str, float] = {}

    # Wellness weight
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Wellness.date, Wellness.weight)
                .where(
                    Wellness.user_id == user_id,
                    Wellness.date >= start_str,
                    Wellness.date <= end_str,
                    Wellness.weight.isnot(None),
                )
                .order_by(Wellness.date)
            )
        ).all()
        for dt, w in rows:
            weights[dt] = float(w)

    # Garmin bio_metrics (overrides wellness for same date)
    garmin_rows = await GarminBioMetrics.get_range(user_id, start_str, end_str)
    for r in garmin_rows:
        if r.weight_kg:
            weights[r.calendar_date] = r.weight_kg

    if not weights:
        return {"status": "no_data", "message": "No weight data found for the requested period."}

    sorted_dates = sorted(weights.keys())
    values = [weights[d] for d in sorted_dates]

    current = values[-1]
    avg = round(sum(values) / len(values), 1)
    min_w = round(min(values), 1)
    max_w = round(max(values), 1)

    # Slope: linear regression (kg per week)
    slope_per_week = 0.0
    direction = "stable"
    if len(values) >= 3:
        n = len(values)
        # Days from first data point
        first_date = date.fromisoformat(sorted_dates[0])
        x = [(date.fromisoformat(d) - first_date).days for d in sorted_dates]
        x_mean = sum(x) / n
        y_mean = sum(values) / n
        num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, values))
        den = sum((xi - x_mean) ** 2 for xi in x)
        if den > 0:
            slope_per_day = num / den
            slope_per_week = round(slope_per_day * 7, 2)
            if slope_per_week < -0.1:
                direction = "losing"
            elif slope_per_week > 0.1:
                direction = "gaining"

    result = {
        "status": "ok",
        "current_kg": current,
        "avg_kg": avg,
        "min_kg": min_w,
        "max_kg": max_w,
        "data_points": len(values),
        "period": {"from": sorted_dates[0], "to": sorted_dates[-1]},
        "trend_direction": direction,
        "trend_slope_kg_per_week": slope_per_week,
    }

    if target_kg > 0 and slope_per_week != 0:
        diff = target_kg - current
        if (diff < 0 and slope_per_week < 0) or (diff > 0 and slope_per_week > 0):
            weeks_to_target = diff / slope_per_week
            if weeks_to_target > 104:  # cap at 2 years
                result["target_kg"] = target_kg
                result["estimated_target_date"] = None
                result["target_note"] = "At current rate, target is >2 years away."
            else:
                target_date = ref + timedelta(weeks=weeks_to_target)
                result["target_kg"] = target_kg
                result["estimated_target_date"] = str(target_date)
        else:
            result["target_kg"] = target_kg
            result["estimated_target_date"] = None
            result["target_note"] = "Trend is moving away from target."
    elif target_kg > 0:
        result["target_kg"] = target_kg
        result["estimated_target_date"] = None
        result["target_note"] = "Not enough trend data to estimate."

    return result
