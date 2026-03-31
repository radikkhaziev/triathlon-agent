"""MCP tools for HRV analysis data."""

from sqlalchemy import select

from data.database import HrvAnalysisRow, WellnessRow, get_session
from mcp_server.app import mcp


def _cv_verdict(cv: float | None) -> str | None:
    if cv is None:
        return None
    if cv < 5:
        return "very_stable"
    if cv < 10:
        return "normal"
    return "unstable"


def _swc_verdict(today_val: float | None, baseline_60d: float | None, swc: float | None) -> str | None:
    if not today_val or not baseline_60d or not swc:
        return None
    delta = today_val - baseline_60d
    if abs(delta) < swc:
        return "within_noise"
    return "significant_improvement" if delta > 0 else "significant_decline"


@mcp.tool()
async def get_hrv_analysis(date: str, algorithm: str = "") -> dict:
    """Get HRV (RMSSD) analysis for a given date.

    Returns dual-algorithm HRV status, baselines, bounds, SWC, CV, and trend.
    Both Flatt & Esco and AIEndurance algorithms are always computed.
    Flatt & Esco compares today vs 7-day mean (fast response).
    AIEndurance compares 7-day mean vs 60-day mean (chronic fatigue detection).

    Status: green (above upper bound) = full load, yellow (between bounds) = monitor,
    red (below lower bound) = reduce intensity.

    Args:
        date: Date in YYYY-MM-DD format
        algorithm: Optional — "flatt_esco" or "ai_endurance". If empty, returns both.
    """
    async with get_session() as session:
        result = await session.execute(
            select(WellnessRow).where(WellnessRow.user_id == 1, WellnessRow.date == date)  # TODO: per-user
        )
        row = result.scalar_one_or_none()
        hrv_today = float(row.hrv) if row and row.hrv else None

        algorithms = [algorithm] if algorithm else ["flatt_esco", "ai_endurance"]
        result = {"date": date, "hrv_today": hrv_today}

        for algo in algorithms:
            hrv_row = await session.get(HrvAnalysisRow, (1, date, algo))  # TODO: user_id from auth
            if not hrv_row:
                result[algo] = {"status": "insufficient_data"}
                continue

            delta_pct = None
            if hrv_today and hrv_row.rmssd_7d and hrv_row.rmssd_7d > 0:
                delta_pct = round((hrv_today - hrv_row.rmssd_7d) / hrv_row.rmssd_7d * 100, 1)

            result[algo] = {
                "status": hrv_row.status,
                "mean_7d": hrv_row.rmssd_7d,
                "sd_7d": hrv_row.rmssd_sd_7d,
                "mean_60d": hrv_row.rmssd_60d,
                "sd_60d": hrv_row.rmssd_sd_60d,
                "delta_pct": delta_pct,
                "lower_bound": hrv_row.lower_bound,
                "upper_bound": hrv_row.upper_bound,
                "swc": hrv_row.swc,
                "swc_verdict": _swc_verdict(hrv_today, hrv_row.rmssd_60d, hrv_row.swc),
                "cv_7d": hrv_row.cv_7d,
                "cv_verdict": _cv_verdict(hrv_row.cv_7d),
                "days_available": hrv_row.days_available,
                "trend": (
                    {
                        "direction": hrv_row.trend_direction,
                        "slope": hrv_row.trend_slope,
                        "r_squared": hrv_row.trend_r_squared,
                    }
                    if hrv_row.trend_direction
                    else None
                ),
            }

    return result
