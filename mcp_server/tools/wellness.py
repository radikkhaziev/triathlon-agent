"""MCP tools for wellness data."""

from sqlalchemy import select

from data.db import Wellness, get_session
from data.metrics import recompute_today_loads
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id
from tasks.dto import local_today


def _row_to_dict(row: Wellness) -> dict:
    """Convert Wellness to a flat dict for MCP response."""
    return {
        "date": row.date,
        "ctl": row.ctl,
        "atl": row.atl,
        "ramp_rate": row.ramp_rate,
        "ctl_load": row.ctl_load,
        "atl_load": row.atl_load,
        "sport_info": row.sport_info,
        "weight": row.weight,
        "resting_hr": row.resting_hr,
        "hrv": row.hrv,
        "sleep_secs": row.sleep_secs,
        "sleep_score": row.sleep_score,
        "body_fat": row.body_fat,
        "vo2max": row.vo2max,
        "steps": row.steps,
        "ess_today": row.ess_today,
        "banister_recovery": row.banister_recovery,
        "recovery_score": row.recovery_score,
        "recovery_category": row.recovery_category,
        "recovery_recommendation": row.recovery_recommendation,
        "readiness_score": row.readiness_score,
        "readiness_level": row.readiness_level,
    }


async def _apply_today_loads_override(user_id: int, record: dict) -> None:
    """Replace `ctl`/`atl` in a wellness dict with the actual-only recompute.

    Intervals.icu bakes today's planned workouts into ctl/atl, so morning-time
    reads look as if today's session is already done. Mutates `record` in
    place. No-op if yesterday's wellness row is missing.
    """
    recomputed = await recompute_today_loads(user_id)
    if recomputed is None:
        return
    ctl, atl, _ = recomputed
    record["ctl"] = ctl
    record["atl"] = atl


@mcp.tool()
async def get_wellness(date: str) -> dict:
    """Get all wellness fields: CTL, ATL, HRV, sleep, body metrics, recovery score, readiness."""
    user_id = get_current_user_id()
    async with get_session() as session:
        result = await session.execute(select(Wellness).where(Wellness.user_id == user_id, Wellness.date == date))
        row = result.scalar_one_or_none()
    if not row:
        return {"error": f"No data for {date}"}
    record = _row_to_dict(row)
    if date == local_today().isoformat():
        await _apply_today_loads_override(user_id, record)
    return record


@mcp.tool()
async def get_wellness_range(from_date: str, to_date: str) -> dict:
    """Get wellness data for a date range (inclusive). Returns daily records list."""
    user_id = get_current_user_id()
    async with get_session() as session:
        result = await session.execute(
            select(Wellness)
            .where(Wellness.user_id == user_id, Wellness.date >= from_date, Wellness.date <= to_date)
            .order_by(Wellness.date)
        )
        rows = result.scalars().all()

    if not rows:
        return {"error": f"No data for range {from_date} to {to_date}", "count": 0}

    records = [_row_to_dict(r) for r in rows]
    today_iso = local_today().isoformat()
    for record in records:
        if record["date"] == today_iso:
            await _apply_today_loads_override(user_id, record)
            break

    return {
        "from_date": from_date,
        "to_date": to_date,
        "count": len(rows),
        "data": records,
    }
