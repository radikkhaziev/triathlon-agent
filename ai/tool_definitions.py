"""Tool definitions and handlers for Claude tool-use API (MCP Phase 2).

MORNING_TOOLS — tool definitions (name, description, input_schema) for messages.create().
TOOL_HANDLERS — maps tool name to async handler function.

Handlers call data/database and data/metrics functions directly (not through MCP layer).
"""

import logging
from datetime import date as date_module
from datetime import timedelta

from sqlalchemy import select

from config import settings
from data.database import (
    ActivityHrvRow,
    ActivityRow,
    HrvAnalysisRow,
    RhrAnalysisRow,
    ScheduledWorkoutRow,
    WellnessRow,
    get_iqos_daily,
    get_iqos_range,
    get_mood_checkins,
    get_session,
    get_training_log_range,
)
from data.ramp_tests import detect_threshold_drift, get_threshold_freshness_data
from data.utils import extract_sport_ctl, format_duration

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions (Anthropic tool-use format)
# ---------------------------------------------------------------------------

MORNING_TOOLS = [
    # --- Core tools (recommended sequence) ---
    {
        "name": "get_recovery",
        "description": (
            "Get composite recovery score and training recommendation for a date. "
            "Recovery score (0-100) combines: RMSSD 35%, Banister 25%, RHR 20%, Sleep 20%. "
            "Categories: excellent >85, good 70-85, moderate 40-70, low <40."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_hrv_analysis",
        "description": (
            "Get HRV analysis with dual-algorithm baselines. "
            "Returns status (green/yellow/red), 7d/60d means, bounds, CV, SWC, trend. "
            "Algorithm: 'flatt_esco' or 'ai_endurance'. Empty = both."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "algorithm": {
                    "type": "string",
                    "description": "Algorithm: 'flatt_esco', 'ai_endurance', or empty for both",
                },
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_rhr_analysis",
        "description": (
            "Get resting heart rate analysis with baselines. "
            "Inverted vs HRV: elevated RHR = red. "
            "Returns status (green/yellow/red), today vs 7d/30d/60d means, bounds, trend."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_training_load",
        "description": (
            "Get CTL/ATL/TSB and per-sport CTL for a given date. "
            "All values from Intervals.icu (tau_CTL=42d, tau_ATL=7d). "
            "TSB zones: >+10 under-training, -10..+10 optimal, -10..-25 productive overreach, <-25 overtraining risk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_scheduled_workouts",
        "description": (
            "Get planned workouts from Intervals.icu calendar for a date. "
            "Returns workout name, sport type, duration, and description with interval structure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "days_ahead": {
                    "type": "integer",
                    "description": "Days ahead to include (0 = single day). Default: 0",
                },
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_goal_progress",
        "description": (
            "Get race goal progress — overall and per-sport CTL vs targets. "
            "Shows event name, date, weeks remaining, and percentage of target CTL achieved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_activity_hrv",
        "description": (
            "Get DFA alpha 1 analysis for activities on a given date. "
            "Returns Ra (readiness %), Da (durability %), HRVT1/HRVT2 thresholds, quality."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["date"],
        },
    },
    # --- Optional tools (Claude calls when suspicious data) ---
    {
        "name": "get_wellness_range",
        "description": (
            "Get wellness data for a date range. "
            "Useful for trend analysis — returns daily wellness records with recovery, HRV, sleep."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["from_date", "to_date"],
        },
    },
    {
        "name": "get_activities",
        "description": (
            "Get completed activities for a date range. "
            "Returns sport type, training load (TSS), duration, DFA availability."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_date": {"type": "string", "description": "End date YYYY-MM-DD. Default: today"},
                "days_back": {"type": "integer", "description": "Days to look back. Default: 7"},
            },
        },
    },
    {
        "name": "get_training_log",
        "description": (
            "Get training log with pre-workout context, actual data, and post-outcome. "
            "Shows compliance, adaptation, and recovery response."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_back": {"type": "integer", "description": "Days to look back. Default: 14"},
            },
        },
    },
    {
        "name": "get_threshold_freshness",
        "description": (
            "Check how fresh HRVT1/HRVT2 thresholds are. "
            "Thresholds older than 21 days are stale. Returns last test date and drift alerts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "description": "Filter: 'Ride' or 'Run'. Empty = all"},
            },
        },
    },
    {
        "name": "get_readiness_history",
        "description": (
            "Get Readiness (Ra) trend over recent activities. "
            "Ra > +5%: excellent, -5..+5%: normal, < -5%: under-recovered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "description": "Filter: 'bike' or 'run'. Empty = all"},
                "days_back": {"type": "integer", "description": "Days to look back. Default: 30"},
            },
        },
    },
    {
        "name": "get_mood_checkins",
        "description": (
            "Get mood check-ins for a date range. " "Returns energy, mood, anxiety, social ratings (1-5) and notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Reference date YYYY-MM-DD. Default: today"},
                "days_back": {"type": "integer", "description": "Days to look back. Default: 7"},
            },
        },
    },
    {
        "name": "get_iqos_sticks",
        "description": (
            "Get IQOS stick count for a day or range. "
            "Use days_back=0 for single day, >0 for range with totals and average."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_date": {"type": "string", "description": "Date YYYY-MM-DD. Default: today"},
                "days_back": {"type": "integer", "description": "0 = single day, 7 = week. Default: 0"},
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wellness_to_dict(row: WellnessRow) -> dict:
    return {
        "date": row.id,
        "ctl": row.ctl,
        "atl": row.atl,
        "ramp_rate": row.ramp_rate,
        "sport_info": row.sport_info,
        "weight": row.weight,
        "resting_hr": row.resting_hr,
        "hrv": row.hrv,
        "sleep_secs": row.sleep_secs,
        "sleep_score": row.sleep_score,
        "sleep_quality": row.sleep_quality,
        "ess_today": row.ess_today,
        "banister_recovery": row.banister_recovery,
        "recovery_score": row.recovery_score,
        "recovery_category": row.recovery_category,
        "recovery_recommendation": row.recovery_recommendation,
        "readiness_score": row.readiness_score,
        "readiness_level": row.readiness_level,
    }


def _hrv_row_to_dict(row: HrvAnalysisRow) -> dict:
    return {
        "algorithm": row.algorithm,
        "status": row.status,
        "rmssd_7d": row.rmssd_7d,
        "rmssd_sd_7d": row.rmssd_sd_7d,
        "rmssd_60d": row.rmssd_60d,
        "lower_bound": row.lower_bound,
        "upper_bound": row.upper_bound,
        "cv_7d": row.cv_7d,
        "swc": row.swc,
        "days_available": row.days_available,
        "trend_direction": row.trend_direction,
        "trend_slope": row.trend_slope,
    }


def _tsb_zone(tsb: float | None) -> str | None:
    if tsb is None:
        return None
    if tsb > 10:
        return "under_training"
    if tsb >= -10:
        return "optimal"
    if tsb >= -25:
        return "productive_overreach"
    return "overtraining_risk"


def _pct(current: float | None, target: float) -> float | None:
    if current is None or target <= 0:
        return None
    return round(current / target * 100, 1)


# ---------------------------------------------------------------------------
# Handlers — thin async wrappers calling DB/metrics directly
# ---------------------------------------------------------------------------


async def handle_get_recovery(date: str) -> dict:
    async with get_session() as session:
        row = await session.get(WellnessRow, date)
    if not row:
        return {"error": f"No data for {date}"}

    sleep_duration = None
    if row.sleep_secs:
        h, m = divmod(row.sleep_secs // 60, 60)
        sleep_duration = f"{h}h {m}m" if h else f"{m}m"

    return {
        "date": date,
        "score": row.recovery_score,
        "category": row.recovery_category,
        "recommendation": row.recovery_recommendation,
        "readiness_score": row.readiness_score,
        "readiness_level": row.readiness_level,
        "sleep_score": row.sleep_score,
        "sleep_duration": sleep_duration,
        "ess_today": row.ess_today,
        "banister_recovery": row.banister_recovery,
    }


async def handle_get_hrv_analysis(date: str, algorithm: str = "") -> dict:
    async with get_session() as session:
        if algorithm:
            row = await session.get(HrvAnalysisRow, (date, algorithm))
            if not row:
                return {"error": f"No HRV data for {date} ({algorithm})"}
            return {"date": date, **_hrv_row_to_dict(row)}
        else:
            flatt = await session.get(HrvAnalysisRow, (date, "flatt_esco"))
            aie = await session.get(HrvAnalysisRow, (date, "ai_endurance"))
            result: dict = {"date": date}
            if flatt:
                result["flatt_esco"] = _hrv_row_to_dict(flatt)
            if aie:
                result["ai_endurance"] = _hrv_row_to_dict(aie)
            if not flatt and not aie:
                result["error"] = f"No HRV data for {date}"
            return result


async def handle_get_rhr_analysis(date: str) -> dict:
    async with get_session() as session:
        row = await session.get(RhrAnalysisRow, date)
    if not row:
        return {"error": f"No RHR data for {date}"}
    return {
        "date": date,
        "status": row.status,
        "rhr_today": row.rhr_today,
        "rhr_7d": row.rhr_7d,
        "rhr_sd_7d": row.rhr_sd_7d,
        "rhr_30d": row.rhr_30d,
        "rhr_sd_30d": row.rhr_sd_30d,
        "rhr_60d": row.rhr_60d,
        "lower_bound": row.lower_bound,
        "upper_bound": row.upper_bound,
        "cv_7d": row.cv_7d,
    }


async def handle_get_training_load(date: str) -> dict:
    async with get_session() as session:
        row = await session.get(WellnessRow, date)
    if not row:
        return {"error": f"No data for {date}"}

    tsb = round(row.ctl - row.atl, 1) if row.ctl is not None and row.atl is not None else None
    sport_ctl = extract_sport_ctl(row.sport_info)

    return {
        "date": date,
        "ctl": row.ctl,
        "atl": row.atl,
        "tsb": tsb,
        "ramp_rate": row.ramp_rate,
        "sport_ctl": sport_ctl,
        "interpretation": {
            "tsb_zone": _tsb_zone(tsb),
            "ramp_safe": row.ramp_rate <= 7 if row.ramp_rate else None,
        },
    }


async def handle_get_scheduled_workouts(date: str, days_ahead: int = 0) -> dict:
    start = date_module.fromisoformat(date)
    end = start + timedelta(days=days_ahead)

    async with get_session() as session:
        rows = (
            (
                await session.execute(
                    select(ScheduledWorkoutRow)
                    .where(ScheduledWorkoutRow.start_date_local >= str(start))
                    .where(ScheduledWorkoutRow.start_date_local <= str(end))
                    .order_by(ScheduledWorkoutRow.start_date_local)
                )
            )
            .scalars()
            .all()
        )

    workouts = []
    for r in rows:
        workouts.append(
            {
                "date": r.start_date_local,
                "type": r.type,
                "name": r.name,
                "category": r.category,
                "duration": format_duration(r.moving_time),
                "duration_secs": r.moving_time,
                "description": r.description,
            }
        )

    return {"count": len(workouts), "from": str(start), "to": str(end), "workouts": workouts}


async def handle_get_goal_progress() -> dict:
    today = date_module.today()
    days_remaining = (settings.GOAL_EVENT_DATE - today).days
    weeks_remaining = round(days_remaining / 7, 1)

    async with get_session() as session:
        result = await session.execute(
            select(WellnessRow).where(WellnessRow.ctl.isnot(None)).order_by(WellnessRow.id.desc()).limit(1)
        )
        row = result.scalar_one_or_none()

    current_ctl = row.ctl if row else None
    sport_ctl = extract_sport_ctl(row.sport_info) if row else {"swim": None, "bike": None, "run": None}

    return {
        "event": settings.GOAL_EVENT_NAME,
        "event_date": str(settings.GOAL_EVENT_DATE),
        "days_remaining": days_remaining,
        "weeks_remaining": weeks_remaining,
        "overall": {
            "current_ctl": current_ctl,
            "target_ctl": settings.GOAL_CTL_TARGET,
            "pct": _pct(current_ctl, settings.GOAL_CTL_TARGET),
        },
        "swim": {
            "current_ctl": sport_ctl["swim"],
            "target_ctl": settings.GOAL_SWIM_CTL_TARGET,
            "pct": _pct(sport_ctl["swim"], settings.GOAL_SWIM_CTL_TARGET),
        },
        "bike": {
            "current_ctl": sport_ctl["bike"],
            "target_ctl": settings.GOAL_BIKE_CTL_TARGET,
            "pct": _pct(sport_ctl["bike"], settings.GOAL_BIKE_CTL_TARGET),
        },
        "run": {
            "current_ctl": sport_ctl["run"],
            "target_ctl": settings.GOAL_RUN_CTL_TARGET,
            "pct": _pct(sport_ctl["run"], settings.GOAL_RUN_CTL_TARGET),
        },
    }


async def handle_get_activity_hrv(date: str) -> dict:
    dt = date_module.fromisoformat(date)
    async with get_session() as session:
        rows = (
            (
                await session.execute(
                    select(ActivityHrvRow)
                    .where(ActivityHrvRow.date == str(dt))
                    .where(ActivityHrvRow.processing_status == "processed")
                )
            )
            .scalars()
            .all()
        )

    if not rows:
        return {"date": date, "count": 0, "activities": []}

    activities = []
    for r in rows:
        entry: dict = {
            "activity_id": r.activity_id,
            "activity_type": r.activity_type,
            "dfa_a1_mean": r.dfa_a1_mean,
            "hrv_quality": r.hrv_quality,
        }
        if r.hrvt1_hr:
            entry["hrvt1_hr"] = r.hrvt1_hr
            entry["hrvt1_power"] = r.hrvt1_power
            entry["hrvt1_pace"] = r.hrvt1_pace
        if r.ra_pct is not None:
            entry["ra_pct"] = r.ra_pct
        if r.da_pct is not None:
            entry["da_pct"] = r.da_pct
        activities.append(entry)

    return {"date": date, "count": len(activities), "activities": activities}


async def handle_get_wellness_range(from_date: str, to_date: str) -> dict:
    async with get_session() as session:
        result = await session.execute(
            select(WellnessRow).where(WellnessRow.id >= from_date, WellnessRow.id <= to_date).order_by(WellnessRow.id)
        )
        rows = result.scalars().all()

    if not rows:
        return {"error": f"No data for range {from_date} to {to_date}", "count": 0}

    return {
        "from_date": from_date,
        "to_date": to_date,
        "count": len(rows),
        "data": [_wellness_to_dict(r) for r in rows],
    }


async def handle_get_activities(target_date: str = "", days_back: int = 7) -> dict:
    end = date_module.fromisoformat(target_date) if target_date else date_module.today()
    start = end - timedelta(days=days_back)

    async with get_session() as session:
        rows = (
            (
                await session.execute(
                    select(ActivityRow)
                    .where(ActivityRow.start_date_local >= str(start))
                    .where(ActivityRow.start_date_local <= str(end))
                    .order_by(ActivityRow.start_date_local.desc())
                )
            )
            .scalars()
            .all()
        )

        hrv_map = {}
        if rows:
            activity_ids = [r.id for r in rows]
            hrv_rows = (
                (await session.execute(select(ActivityHrvRow).where(ActivityHrvRow.activity_id.in_(activity_ids))))
                .scalars()
                .all()
            )
            hrv_map = {h.activity_id: h for h in hrv_rows}

    activities = []
    for r in rows:
        entry = {
            "id": r.id,
            "date": r.start_date_local,
            "type": r.type,
            "training_load": r.icu_training_load,
            "duration": format_duration(r.moving_time),
            "duration_secs": r.moving_time,
        }
        hrv = hrv_map.get(r.id)
        if hrv and hrv.processing_status == "processed":
            entry["has_hrv_analysis"] = True
            entry["dfa_a1_mean"] = hrv.dfa_a1_mean
        else:
            entry["has_hrv_analysis"] = False
        activities.append(entry)

    total_load = sum(a["training_load"] or 0 for a in activities)

    return {
        "count": len(activities),
        "from": str(start),
        "to": str(end),
        "total_training_load": round(total_load, 1),
        "activities": activities,
    }


async def handle_get_training_log(days_back: int = 14) -> dict:
    rows = await get_training_log_range(days_back=days_back)

    entries = []
    for r in rows:
        entry = {
            "date": r.date,
            "sport": r.sport,
            "source": r.source,
            "original_name": r.original_name,
            "adapted_name": r.adapted_name,
            "adaptation_reason": r.adaptation_reason,
            "pre": {
                "recovery": r.pre_recovery_score,
                "category": r.pre_recovery_category,
                "hrv_status": r.pre_hrv_status,
                "hrv_delta": r.pre_hrv_delta_pct,
                "tsb": r.pre_tsb,
                "sleep": r.pre_sleep_score,
            },
            "compliance": r.compliance,
        }
        if r.compliance:
            entry["actual"] = {
                "activity_id": r.actual_activity_id,
                "duration_min": (r.actual_duration_sec // 60) if r.actual_duration_sec else None,
                "avg_hr": r.actual_avg_hr,
                "tss": r.actual_tss,
            }
        if r.post_recovery_score is not None:
            entry["post"] = {
                "recovery": r.post_recovery_score,
                "recovery_delta": r.recovery_delta,
            }
        entries.append(entry)

    return {"count": len(entries), "entries": entries}


async def handle_get_threshold_freshness(sport: str = "") -> dict:
    data = await get_threshold_freshness_data(sport)
    drift = await detect_threshold_drift()
    result = {**data}
    if drift and drift["alerts"]:
        result["drift_alerts"] = drift["alerts"]
    return result


async def handle_get_readiness_history(sport: str = "", days_back: int = 30) -> dict:
    cutoff = str(date_module.today() - timedelta(days=days_back))

    _SPORT_TYPES = {
        "bike": ("Ride", "VirtualRide", "GravelRide", "MountainBikeRide"),
        "run": ("Run", "VirtualRun", "TrailRun"),
    }

    async with get_session() as session:
        query = (
            select(ActivityHrvRow)
            .where(ActivityHrvRow.date >= cutoff)
            .where(ActivityHrvRow.ra_pct.isnot(None))
            .order_by(ActivityHrvRow.date.asc())
        )
        if sport.lower() in _SPORT_TYPES:
            query = query.where(ActivityHrvRow.activity_type.in_(_SPORT_TYPES[sport.lower()]))

        rows = (await session.execute(query)).scalars().all()

    readiness = []
    for r in rows:
        status = "excellent" if r.ra_pct > 5 else "normal" if r.ra_pct > -5 else "under_recovered"
        readiness.append(
            {
                "date": r.date,
                "activity_type": r.activity_type,
                "ra_pct": r.ra_pct,
                "status": status,
            }
        )

    return {"count": len(readiness), "days_back": days_back, "readiness": readiness}


async def handle_get_mood_checkins(date_str: str | None = None, days_back: int = 7) -> dict:
    checkins = await get_mood_checkins(target_date=date_str, days_back=days_back)

    ref = date_module.fromisoformat(date_str) if date_str else date_module.today()
    from_date = ref - timedelta(days=days_back - 1)

    return {
        "checkins": [
            {
                "timestamp": row.timestamp.isoformat(),
                "energy": row.energy,
                "mood": row.mood,
                "anxiety": row.anxiety,
                "social": row.social,
                "note": row.note,
            }
            for row in checkins
        ],
        "count": len(checkins),
        "period": {"from": str(from_date), "to": str(ref)},
    }


async def handle_get_iqos_sticks(target_date: str = "", days_back: int = 0) -> dict:
    ref = date_module.fromisoformat(target_date) if target_date else date_module.today()

    if days_back == 0:
        row = await get_iqos_daily(ref)
        return {"date": str(ref), "count": row.count if row else 0}

    rows = await get_iqos_range(target_date=str(ref), days_back=days_back)
    from_date = ref - timedelta(days=days_back - 1)
    rows_by_date = {r.date: r.count for r in rows}
    total = sum(rows_by_date.values())

    return {
        "period": {"from": str(from_date), "to": str(ref)},
        "total": total,
        "days_with_data": len(rows),
        "average_per_day": round(total / max(len(rows), 1), 1),
        "daily": [{"date": r.date, "count": r.count} for r in rows],
    }


# ---------------------------------------------------------------------------
# Handler dispatch map
# ---------------------------------------------------------------------------

# Chat tools — copy of MORNING_TOOLS (not alias) to allow adding chat-only tools later
CHAT_TOOLS = [*MORNING_TOOLS]

TOOL_HANDLERS = {
    "get_recovery": handle_get_recovery,
    "get_hrv_analysis": handle_get_hrv_analysis,
    "get_rhr_analysis": handle_get_rhr_analysis,
    "get_training_load": handle_get_training_load,
    "get_scheduled_workouts": handle_get_scheduled_workouts,
    "get_goal_progress": handle_get_goal_progress,
    "get_activity_hrv": handle_get_activity_hrv,
    "get_wellness_range": handle_get_wellness_range,
    "get_activities": handle_get_activities,
    "get_training_log": handle_get_training_log,
    "get_threshold_freshness": handle_get_threshold_freshness,
    "get_readiness_history": handle_get_readiness_history,
    "get_mood_checkins": handle_get_mood_checkins,
    "get_iqos_sticks": handle_get_iqos_sticks,
}
