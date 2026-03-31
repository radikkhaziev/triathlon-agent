"""MCP tools for post-activity HRV analysis (DFA alpha 1) — Level 2 pipeline."""

from datetime import date, timedelta

from sqlalchemy import select

from data.database import ActivityHrvRow, ActivityRow, get_session
from mcp_server.app import mcp


@mcp.tool()
async def get_activity_hrv(activity_id: str) -> dict:
    """Get DFA alpha 1 analysis for a specific activity.

    Returns DFA a1 summary (mean, warmup), quality metrics (artifact_pct, rr_count),
    detected thresholds (HRVT1/HRVT2 with HR/power/pace), Readiness (Ra %),
    Durability (Da %), and processing status.

    Only available for bike/run activities processed with chest strap HRM (BLE).
    Swim activities have no RR data.

    Note: Thresholds (HRVT1/HRVT2) are detected from DFA a1 vs HR relationship
    during activities with progressive intensity increase. HRVT1 (a1=0.75) corresponds
    to aerobic threshold, HRVT2 (a1=0.50) to anaerobic threshold.

    Args:
        activity_id: Intervals.icu activity ID (e.g. "i12345")
    """
    async with get_session() as session:
        activity = await session.get(ActivityRow, activity_id)
        if not activity or activity.user_id != 1:  # TODO: user_id from auth
            return {"error": f"Activity {activity_id} not found."}
        row = await session.get(ActivityHrvRow, activity_id)

    if not row:
        return {
            "error": f"No HRV analysis for activity {activity_id}. Either not processed yet or not a bike/run activity."
        }

    result = {
        "activity_id": activity_id,
        "date": activity.start_date_local if activity else None,
        "activity_type": row.activity_type,
        "processing_status": row.processing_status,
    }

    if row.processing_status != "processed":
        return result

    result["quality"] = {
        "hrv_quality": row.hrv_quality,
        "artifact_pct": row.artifact_pct,
        "rr_count": row.rr_count,
    }
    result["dfa_a1"] = {
        "mean": row.dfa_a1_mean,
        "warmup": row.dfa_a1_warmup,
    }

    if row.hrvt1_hr:
        result["thresholds"] = {
            "hrvt1_hr": row.hrvt1_hr,
            "hrvt1_power": row.hrvt1_power,
            "hrvt1_pace": row.hrvt1_pace,
            "hrvt2_hr": row.hrvt2_hr,
            "r_squared": row.threshold_r_squared,
            "confidence": row.threshold_confidence,
        }

    if row.ra_pct is not None:
        result["readiness_ra"] = {
            "ra_pct": row.ra_pct,
            "pa_today": row.pa_today,
            "status": "excellent" if row.ra_pct > 5 else "normal" if row.ra_pct > -5 else "under_recovered",
        }

    if row.da_pct is not None:
        result["durability_da"] = {
            "da_pct": row.da_pct,
            "status": (
                "excellent"
                if row.da_pct > 0
                else "normal" if row.da_pct > -5 else "fatigued" if row.da_pct > -15 else "overreached"
            ),
        }

    return result


@mcp.tool()
async def get_thresholds_history(sport: str = "", days_back: int = 90) -> dict:
    """Get HRVT1/HRVT2 threshold trend over recent activities.

    Tracks how aerobic (HRVT1, DFA a1=0.75) and anaerobic (HRVT2, DFA a1=0.50)
    thresholds change over time. Useful for monitoring fitness progression —
    rising HRVT1 HR means improved aerobic capacity.

    Note: Thresholds are only detected from activities with progressive intensity
    (ramp-style or gradually increasing effort). Steady-state activities don't
    produce threshold data.

    Args:
        sport: Filter by sport: "bike" or "run". Empty = all.
        days_back: How many days to look back (default 90).
    """
    cutoff = str(date.today() - timedelta(days=days_back))

    _SPORT_TYPES = {
        "bike": ("Ride", "VirtualRide", "GravelRide", "MountainBikeRide"),
        "run": ("Run", "VirtualRun", "TrailRun"),
    }

    async with get_session() as session:
        query = (
            select(ActivityHrvRow, ActivityRow.start_date_local)
            .join(ActivityRow, ActivityRow.id == ActivityHrvRow.activity_id)
            .where(ActivityRow.user_id == 1)  # TODO: per-user
            .where(ActivityRow.start_date_local >= cutoff)
            .where(ActivityHrvRow.hrvt1_hr.isnot(None))
            .order_by(ActivityRow.start_date_local.asc())
        )
        if sport.lower() in _SPORT_TYPES:
            query = query.where(ActivityHrvRow.activity_type.in_(_SPORT_TYPES[sport.lower()]))

        result = await session.execute(query)
        rows = result.all()

    if not rows:
        return {
            "count": 0,
            "thresholds": [],
            "message": "No threshold data found. Thresholds require activities with progressive intensity increase.",
        }

    thresholds = []
    for r, activity_date in rows:
        entry = {
            "date": activity_date,
            "activity_id": r.activity_id,
            "activity_type": r.activity_type,
            "hrvt1_hr": r.hrvt1_hr,
            "hrvt2_hr": r.hrvt2_hr,
            "confidence": r.threshold_confidence,
        }
        if r.hrvt1_power:
            entry["hrvt1_power"] = r.hrvt1_power
        if r.hrvt1_pace:
            entry["hrvt1_pace"] = r.hrvt1_pace
        thresholds.append(entry)

    return {
        "count": len(thresholds),
        "days_back": days_back,
        "thresholds": thresholds,
    }


@mcp.tool()
async def get_readiness_history(sport: str = "", days_back: int = 30) -> dict:
    """Get Readiness (Ra) trend over recent activities.

    Ra compares warmup power/pace at a fixed DFA a1 level against 14-day baseline.
    Ra > +5%: excellent readiness, -5..+5%: normal, < -5%: under-recovered.

    This is a pre-workout readiness indicator — if Ra is consistently negative,
    the athlete may be accumulating fatigue and needs more recovery.

    Args:
        sport: Filter by sport: "bike" or "run". Empty = all.
        days_back: How many days to look back (default 30).
    """
    cutoff = str(date.today() - timedelta(days=days_back))

    _SPORT_TYPES = {
        "bike": ("Ride", "VirtualRide", "GravelRide", "MountainBikeRide"),
        "run": ("Run", "VirtualRun", "TrailRun"),
    }

    async with get_session() as session:
        query = (
            select(ActivityHrvRow, ActivityRow.start_date_local)
            .join(ActivityRow, ActivityRow.id == ActivityHrvRow.activity_id)
            .where(ActivityRow.user_id == 1)  # TODO: per-user
            .where(ActivityRow.start_date_local >= cutoff)
            .where(ActivityHrvRow.ra_pct.isnot(None))
            .order_by(ActivityRow.start_date_local.asc())
        )
        if sport.lower() in _SPORT_TYPES:
            query = query.where(ActivityHrvRow.activity_type.in_(_SPORT_TYPES[sport.lower()]))

        result = await session.execute(query)
        rows = result.all()

    if not rows:
        return {
            "count": 0,
            "readiness": [],
            "message": "No readiness data yet. Needs at least 2 weeks of activity data to establish baseline.",
        }

    readiness = []
    for r, activity_date in rows:
        status = "excellent" if r.ra_pct > 5 else "normal" if r.ra_pct > -5 else "under_recovered"
        readiness.append(
            {
                "date": activity_date,
                "activity_id": r.activity_id,
                "activity_type": r.activity_type,
                "ra_pct": r.ra_pct,
                "pa_today": r.pa_today,
                "status": status,
            }
        )

    return {
        "count": len(readiness),
        "days_back": days_back,
        "readiness": readiness,
    }
