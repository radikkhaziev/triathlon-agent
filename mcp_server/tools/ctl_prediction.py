"""MCP tool — CTL target ETA prediction.

Thin wrapper around :func:`data.metrics.project_ctl_target` so the morning
report (this tool) and the webapp Goal-tab (`/api/dashboard/goal-progress`)
share one projection formula — polyfit-based linear regression on the last
14-ish CTL points. Before the consolidation each path had its own slope
estimator (endpoint-difference here vs polyfit there), so the same goal
showed two slightly different ETAs depending on where you looked.

Response shape kept stable (``estimated_date`` / ``ramp_rate_per_week`` /
``confidence``) — Claude's morning prompt formats it directly into the
«достигнешь 75 CTL к …» line; changing keys would silently break that.
"""

from datetime import date as date_type

from sqlalchemy import select

from data.db import Wellness, get_session
from data.metrics import project_ctl_target
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


def _extract_sport_ctl(sport_info: list[dict] | None, sport: str) -> float | None:
    if not sport_info:
        return None
    sport_lower = sport.lower()
    for entry in sport_info:
        if entry.get("type", "").lower() == sport_lower:
            return entry.get("ctl")
    return None


@mcp.tool()
async def predict_ctl(target_ctl: float, sport: str = "") -> dict:
    """Predict when CTL will reach ``target_ctl`` at the current ramp rate.

    ``sport`` filters to a single discipline (``run`` / ``ride`` / ``swim``);
    reads per-sport CTL from ``wellness.sport_info``. Returns ``{error}`` on
    insufficient history (<2 days or <7-day span), ``{note}`` when the target
    is already met or CTL is flat/declining, otherwise the full ETA envelope.
    """
    user_id = get_current_user_id()
    today = date_type.today()

    async with get_session() as session:
        rows = (
            await session.execute(
                select(Wellness.date, Wellness.ctl, Wellness.sport_info)
                .where(Wellness.user_id == user_id, Wellness.ctl.isnot(None))
                .order_by(Wellness.date.desc())
                .limit(15)
            )
        ).all()

    if len(rows) < 2:
        return {"error": "Not enough CTL data (need at least 2 days)."}

    # Build (date, ctl) series for the shared projector. Order ascending so
    # the projector's own internal sort is a no-op.
    series: list[tuple[date_type, float]] = []
    if sport:
        for dt, _ctl, sport_info in reversed(rows):
            sport_ctl = _extract_sport_ctl(sport_info, sport)
            if sport_ctl is not None:
                series.append((date_type.fromisoformat(dt), float(sport_ctl)))
        if not series:
            return {"error": f"No CTL data for sport '{sport}'."}
    else:
        series = [(date_type.fromisoformat(dt), float(ctl)) for dt, ctl, _ in reversed(rows)]

    current_ctl = series[-1][1]
    days_span = (series[-1][0] - series[0][0]).days

    projection = project_ctl_target(series, target_ctl, today)
    if projection is None:
        # project_ctl_target returns None only when target ≤ 0; predict_ctl's
        # signature types target as float so this is defensive — Claude could
        # pass 0 through tool-use though, so we keep a typed error.
        return {"error": "target_ctl must be > 0."}

    reason = projection["reason"]
    ramp_per_week = projection["ramp_per_week"]

    if reason == "insufficient_data":
        return {"error": "Not enough history for ramp rate (need 7+ days)."}

    base = {
        "current_ctl": round(current_ctl, 1),
        "target_ctl": target_ctl,
        "sport": sport or "total",
        "ramp_rate_per_week": ramp_per_week,
    }

    if reason == "already_at_target":
        return {**base, "estimated_date": None, "note": "Target already reached!"}

    if reason in ("flat", "declining"):
        return {
            **base,
            "estimated_date": None,
            "note": "CTL is declining or flat — target cannot be reached at current rate.",
        }

    # Happy path — project_ctl_target returned a date.
    projected_iso = projection["projected_date"]
    projected = date_type.fromisoformat(projected_iso)
    weeks_to_target = (projected - today).days / 7

    confidence = "high" if days_span >= 14 else "medium"
    if ramp_per_week is not None and ramp_per_week > 7:
        confidence = "low"

    return {
        **base,
        "data_days": days_span,
        "estimated_weeks": round(weeks_to_target, 1),
        "estimated_date": projected_iso,
        "confidence": confidence,
    }
