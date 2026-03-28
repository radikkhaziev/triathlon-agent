import hashlib
import hmac
import json
import logging
import zoneinfo
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import exists, func, select

from api.auth import create_jwt, verify_code, verify_jwt
from bot.formatter import CATEGORY_DISPLAY, RECOMMENDATION_TEXT, STATUS_EMOJI
from bot.scheduler import daily_metrics_job, scheduled_workouts_job, sync_activities_job
from config import settings
from data.database import (
    ActivityDetailRow,
    ActivityHrvRow,
    ActivityRow,
    ScheduledWorkoutRow,
    WellnessRow,
    get_activities_range,
    get_hrv_analysis,
    get_rhr_analysis,
    get_scheduled_workouts_range,
    get_session,
    get_wellness,
)
from data.utils import extract_sport_ctl, format_duration, serialize_activity_details, serialize_activity_hrv

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_and_parse_init_data(init_data: str, bot_token: str) -> dict | None:
    """Verify Telegram initData HMAC and return parsed fields, or None if invalid."""
    parsed = parse_qs(init_data)
    received_hash = parsed.pop("hash", [None])[0]
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None
    return parsed


def _get_user_role(authorization: str | None) -> str:
    """Determine user role from Telegram initData or JWT Bearer token.

    Supports two auth methods:
    - Telegram Mini App: Authorization header contains raw initData
    - Desktop JWT: Authorization header contains "Bearer <jwt>"

    Returns: "owner", "viewer", or "anonymous".
    """
    if not authorization:
        return "anonymous"

    # Method 1: JWT Bearer token (desktop login)
    if authorization.startswith("Bearer "):
        jwt_token = authorization[7:]
        chat_id = verify_jwt(jwt_token)
        if chat_id and chat_id == str(settings.TELEGRAM_CHAT_ID):
            return "owner"
        if chat_id:
            return "viewer"
        return "anonymous"

    # Method 2: Telegram initData (Mini App)
    bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    if not bot_token:
        return "anonymous"

    parsed = _verify_and_parse_init_data(authorization, bot_token)
    if parsed is None:
        return "anonymous"

    user_json = parsed.get("user", [None])[0]
    if not user_json:
        return "anonymous"

    try:
        user = json.loads(user_json)
    except (json.JSONDecodeError, TypeError):
        return "anonymous"

    user_id = str(user.get("id", ""))
    if user_id == str(settings.TELEGRAM_CHAT_ID):
        return "owner"
    return "viewer"


def _require_viewer(authorization: str | None) -> str:
    """Require at least viewer role. Returns role string."""
    role = _get_user_role(authorization)
    if role == "anonymous":
        raise HTTPException(status_code=401, detail="Telegram authorization required")
    return role


def _require_owner(authorization: str | None) -> None:
    """Require owner role."""
    role = _get_user_role(authorization)
    if role == "anonymous":
        raise HTTPException(status_code=401, detail="Telegram authorization required")
    if role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")


def _cv_verdict(cv: float | None) -> str | None:
    if cv is None:
        return None
    if cv < 5:
        return "высокая"
    if cv < 10:
        return "нормальная"
    return "нестабильная"


def _swc_verdict(today_val: float | None, baseline_60d: float | None, swc: float | None) -> str | None:
    if not today_val or not baseline_60d or not swc:
        return None
    delta = today_val - baseline_60d
    if abs(delta) < swc:
        return "в пределах шума"
    if delta > 0:
        return "значимое улучшение"
    return "значимое снижение"


def _format_sleep_duration(secs: int | None) -> str | None:
    if not secs:
        return None
    h, m = divmod(secs // 60, 60)
    return f"{h}ч {m}м" if h else f"{m}м"


def _hrv_block(hrv_row, hrv_today: float | None) -> dict:
    """Build HRV section for a single algorithm."""
    if not hrv_row:
        return {"status": "insufficient_data", "status_emoji": "⚪"}

    delta_pct = None
    if hrv_today and hrv_row.rmssd_7d and hrv_row.rmssd_7d > 0:
        delta_pct = round((hrv_today - hrv_row.rmssd_7d) / hrv_row.rmssd_7d * 100, 1)

    return {
        "status": hrv_row.status,
        "status_emoji": STATUS_EMOJI.get(hrv_row.status, "⚪"),
        "today": hrv_today,
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


def _rhr_block(rhr_row) -> dict:
    """Build RHR section."""
    if not rhr_row:
        return {"status": "insufficient_data", "status_emoji": "⚪"}

    delta_30d = None
    if rhr_row.rhr_today and rhr_row.rhr_30d:
        delta_30d = round(rhr_row.rhr_today - rhr_row.rhr_30d, 1)

    return {
        "status": rhr_row.status,
        "status_emoji": STATUS_EMOJI.get(rhr_row.status, "⚪"),
        "today": rhr_row.rhr_today,
        "mean_7d": rhr_row.rhr_7d,
        "sd_7d": rhr_row.rhr_sd_7d,
        "mean_30d": rhr_row.rhr_30d,
        "sd_30d": rhr_row.rhr_sd_30d,
        "mean_60d": rhr_row.rhr_60d,
        "sd_60d": rhr_row.rhr_sd_60d,
        "delta_30d": delta_30d,
        "lower_bound": rhr_row.lower_bound,
        "upper_bound": rhr_row.upper_bound,
        "cv_7d": rhr_row.cv_7d,
        "cv_verdict": _cv_verdict(rhr_row.cv_7d),
        "days_available": rhr_row.days_available,
        "trend": (
            {
                "direction": rhr_row.trend_direction,
                "slope": rhr_row.trend_slope,
                "r_squared": rhr_row.trend_r_squared,
            }
            if rhr_row.trend_direction
            else None
        ),
    }


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Auth endpoints (desktop login via one-time code)
# ---------------------------------------------------------------------------


@router.post("/api/auth/verify-code")
async def auth_verify_code(body: dict) -> dict:
    """Verify a one-time code from /web bot command and return JWT."""
    code = str(body.get("code", "")).strip()
    if not code:
        raise HTTPException(status_code=400, detail="Code is required")

    chat_id = verify_code(code)
    if not chat_id:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    token = create_jwt(chat_id)
    role = "owner" if chat_id == str(settings.TELEGRAM_CHAT_ID) else "viewer"
    return {"token": token, "role": role, "expires_in_days": settings.JWT_EXPIRY_DAYS}


@router.get("/api/auth/me")
async def auth_me(authorization: str | None = Header(default=None)) -> dict:
    """Check current auth status."""
    role = _get_user_role(authorization)
    return {"role": role, "authenticated": role != "anonymous"}


async def _build_wellness_response(row, target_date: date) -> dict:
    """Build the wellness data payload shared by morning_report and wellness_day."""
    target_str = str(target_date)

    # Recovery
    category = row.recovery_category or "moderate"
    emoji, title = CATEGORY_DISPLAY.get(category, ("⚪", "СТАТУС НЕИЗВЕСТЕН"))
    recommendation_text = RECOMMENDATION_TEXT.get(row.recovery_recommendation or "", row.recovery_recommendation or "")

    # HRV — both algorithms
    hrv_flatt = await get_hrv_analysis(target_str, "flatt_esco")
    hrv_aie = await get_hrv_analysis(target_str, "ai_endurance")
    hrv_today = float(row.hrv) if row.hrv else None

    # RHR
    rhr_row = await get_rhr_analysis(target_str)

    # Training load
    tsb = round(row.ctl - row.atl, 1) if row.ctl is not None and row.atl is not None else None

    # Per-sport CTL from sport_info JSON
    sport_ctl = extract_sport_ctl(row.sport_info)

    return {
        "date": target_str,
        "has_data": True,
        # --- Recovery ---
        "recovery": {
            "score": row.recovery_score,
            "category": category,
            "emoji": emoji,
            "title": title,
            "recommendation": recommendation_text,
            "readiness_score": row.readiness_score,
            "readiness_level": row.readiness_level,
        },
        # --- HRV (both algorithms) ---
        "hrv": {
            "primary_algorithm": settings.HRV_ALGORITHM,
            "flatt_esco": _hrv_block(hrv_flatt, hrv_today),
            "ai_endurance": _hrv_block(hrv_aie, hrv_today),
        },
        # --- Resting HR ---
        "rhr": _rhr_block(rhr_row),
        # --- Sleep ---
        "sleep": {
            "score": row.sleep_score,
            "quality": row.sleep_quality,
            "duration": _format_sleep_duration(row.sleep_secs),
            "duration_secs": row.sleep_secs,
        },
        # --- Training load ---
        "training_load": {
            "ctl": row.ctl,
            "atl": row.atl,
            "tsb": tsb,
            "ramp_rate": row.ramp_rate,
            "sport_ctl": sport_ctl,
        },
        # --- Body ---
        "body": {
            "weight": row.weight,
            "body_fat": row.body_fat,
            "vo2max": row.vo2max,
            "steps": row.steps,
        },
        # --- ESS / Banister ---
        "stress": {
            "ess_today": row.ess_today,
            "banister_recovery": row.banister_recovery,
        },
        # --- AI ---
        "ai_recommendation": row.ai_recommendation,
        "ai_recommendation_gemini": row.ai_recommendation_gemini,
    }


async def _wellness_has_prev(target_date: date) -> bool:
    """Check if wellness records exist before the target date."""
    target_str = str(target_date)
    async with get_session() as session:
        result = await session.execute(select(exists().where(WellnessRow.id < target_str)))
        return result.scalar_one()


@router.get("/api/report")
async def morning_report(authorization: str | None = Header(default=None)) -> dict:
    """Full morning report data for the Mini App report page."""
    role = _require_viewer(authorization)
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()
    today_str = str(today)
    row = await get_wellness(today)

    if row is None:
        return {"date": today_str, "has_data": False, "role": role}

    result = await _build_wellness_response(row, today)
    result["role"] = role
    return result


# ---------------------------------------------------------------------------
# Wellness Day (arbitrary date, navigable)
# ---------------------------------------------------------------------------


@router.get("/api/wellness-day")
async def wellness_day(
    dt: str = Query(default="", alias="date", description="Date YYYY-MM-DD, default=today"),
    authorization: str | None = Header(default=None),
) -> dict:
    """Full wellness data for a specific date (navigable by day)."""
    role = _require_viewer(authorization)
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()

    if dt:
        try:
            target = date.fromisoformat(dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    else:
        target = today

    # Don't allow future dates
    if target > today:
        target = today

    target_str = str(target)
    row = await get_wellness(target)
    has_prev = await _wellness_has_prev(target)
    has_next = target < today

    if row is None:
        return {
            "date": target_str,
            "has_data": False,
            "is_today": target == today,
            "has_prev": has_prev,
            "has_next": has_next,
            "role": role,
        }

    result = await _build_wellness_response(row, target)
    result["is_today"] = target == today
    result["has_prev"] = has_prev
    result["has_next"] = has_next
    result["role"] = role
    return result


# ---------------------------------------------------------------------------
# Scheduled Workouts
# ---------------------------------------------------------------------------

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@router.get("/api/scheduled-workouts")
async def scheduled_workouts(
    week_offset: int = Query(default=0, ge=-52, le=52),
    authorization: str | None = Header(default=None),
) -> dict:
    """Weekly training plan (Mon-Sun) with navigation."""
    role = _require_viewer(authorization)
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()

    # Monday of current week + offset
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    sunday = monday + timedelta(days=6)

    workouts, last_synced_at = await get_scheduled_workouts_range(monday, sunday)

    # Group by date
    by_date: dict[str, list] = {}
    for w in workouts:
        by_date.setdefault(w.start_date_local, []).append(w)

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        d_str = str(d)
        day_workouts = []
        for w in by_date.get(d_str, []):
            day_workouts.append(
                {
                    "id": w.id,
                    "type": w.type,
                    "name": w.name,
                    "category": w.category,
                    "duration": format_duration(w.moving_time),
                    "duration_secs": w.moving_time,
                    "distance_km": w.distance,
                    "description": w.description,
                }
            )
        days.append(
            {
                "date": d_str,
                "weekday": _WEEKDAYS[i],
                "workouts": day_workouts,
            }
        )

    # Check if data exists beyond this week (for navigation limits)
    next_monday = monday + timedelta(weeks=1)
    prev_sunday = monday - timedelta(days=1)

    async with get_session() as session:
        has_next_result = await session.execute(
            select(func.count())
            .select_from(ScheduledWorkoutRow)
            .where(ScheduledWorkoutRow.start_date_local >= str(next_monday))
        )
        has_next = has_next_result.scalar_one() > 0

        has_prev_result = await session.execute(
            select(func.count())
            .select_from(ScheduledWorkoutRow)
            .where(ScheduledWorkoutRow.start_date_local <= str(prev_sunday))
        )
        has_prev = has_prev_result.scalar_one() > 0

    return {
        "week_start": str(monday),
        "week_end": str(sunday),
        "week_offset": week_offset,
        "today": str(today),
        "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
        "has_prev": has_prev,
        "has_next": has_next,
        "role": role,
        "days": days,
    }


@router.post("/api/jobs/sync-workouts")
async def job_sync_workouts(authorization: str | None = Header(default=None)) -> dict:
    """Trigger scheduled workouts sync (owner only)."""
    _require_owner(authorization)

    try:
        await scheduled_workouts_job()
    except Exception:
        logger.exception("sync-workouts job failed")
        raise HTTPException(status_code=502, detail="Sync failed — Intervals.icu may be unavailable")

    async with get_session() as session:
        result = await session.execute(select(func.max(ScheduledWorkoutRow.last_synced_at)))
        last_synced_at = result.scalar_one_or_none()
        count_result = await session.execute(
            select(func.count())
            .select_from(ScheduledWorkoutRow)
            .where(ScheduledWorkoutRow.last_synced_at == last_synced_at)
        )
        synced_count = count_result.scalar_one() if last_synced_at else 0

    return {
        "status": "ok",
        "synced_count": synced_count,
        "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
    }


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


@router.get("/api/activities-week")
async def activities_week(
    week_offset: int = Query(default=0, ge=-52, le=52),
    authorization: str | None = Header(default=None),
) -> dict:
    """Weekly completed activities (Mon-Sun) with navigation."""
    role = _require_viewer(authorization)
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()

    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    sunday = monday + timedelta(days=6)

    activities, last_synced_at = await get_activities_range(monday, sunday)

    by_date: dict[str, list] = {}
    for a in activities:
        by_date.setdefault(a.start_date_local, []).append(a)

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        d_str = str(d)
        day_activities = []
        for a in by_date.get(d_str, []):
            day_activities.append(
                {
                    "id": a.id,
                    "type": a.type,
                    "moving_time": a.moving_time,
                    "duration": format_duration(a.moving_time),
                    "icu_training_load": round(a.icu_training_load, 1) if a.icu_training_load is not None else None,
                    "average_hr": round(a.average_hr) if a.average_hr is not None else None,
                }
            )
        days.append(
            {
                "date": d_str,
                "weekday": _WEEKDAYS[i],
                "activities": day_activities,
            }
        )

    # Check if activities exist before this week (for navigation limits)
    prev_sunday = monday - timedelta(days=1)

    async with get_session() as session:
        prev_result = await session.execute(select(exists().where(ActivityRow.start_date_local <= str(prev_sunday))))
        has_prev = prev_result.scalar_one()

    return {
        "week_start": str(monday),
        "week_end": str(sunday),
        "week_offset": week_offset,
        "today": str(today),
        "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
        "has_prev": has_prev,
        "role": role,
        "days": days,
    }


@router.post("/api/jobs/sync-activities")
async def job_sync_activities(authorization: str | None = Header(default=None)) -> dict:
    """Trigger activity sync (owner only)."""
    _require_owner(authorization)

    try:
        await sync_activities_job()
    except Exception:
        logger.exception("sync-activities job failed")
        raise HTTPException(status_code=502, detail="Sync failed — Intervals.icu may be unavailable")

    async with get_session() as session:
        result = await session.execute(select(func.max(ActivityRow.last_synced_at)))
        last_synced_at = result.scalar_one_or_none()
        count_result = await session.execute(
            select(func.count()).select_from(ActivityRow).where(ActivityRow.last_synced_at == last_synced_at)
        )
        synced_count = count_result.scalar_one() if last_synced_at else 0

    return {
        "status": "ok",
        "synced_count": synced_count,
        "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
    }


@router.post("/api/jobs/sync-wellness")
async def job_sync_wellness(authorization: str | None = Header(default=None)) -> dict:
    """Trigger wellness sync for today (owner only)."""
    _require_owner(authorization)

    try:
        await daily_metrics_job()
    except Exception:
        logger.exception("sync-wellness job failed")
        raise HTTPException(status_code=502, detail="Sync failed")

    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today_str = str(datetime.now(tz).date())
    row = await get_wellness(datetime.now(tz).date())

    return {
        "status": "ok",
        "date": today_str,
        "has_data": row is not None,
    }


# ---------------------------------------------------------------------------
# Activity Details
# ---------------------------------------------------------------------------


@router.get("/api/activity/{activity_id}/details")
async def activity_details(
    activity_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    """Full activity details: summary + extended stats + DFA HRV analysis."""
    _require_viewer(authorization)

    if not activity_id or not activity_id.startswith("i") or not activity_id[1:].isdigit():
        raise HTTPException(status_code=400, detail="Invalid activity ID format")

    async with get_session() as session:
        activity = await session.get(ActivityRow, activity_id)
        if activity is None:
            raise HTTPException(status_code=404, detail="Activity not found")

        detail = await session.get(ActivityDetailRow, activity_id)
        hrv = await session.get(ActivityHrvRow, activity_id)

    return {
        "activity_id": activity.id,
        "type": activity.type,
        "date": activity.start_date_local,
        "moving_time": activity.moving_time,
        "duration": format_duration(activity.moving_time),
        "icu_training_load": round(activity.icu_training_load, 1) if activity.icu_training_load is not None else None,
        "average_hr": round(activity.average_hr) if activity.average_hr is not None else None,
        "details": serialize_activity_details(detail) if detail else None,
        "hrv": serialize_activity_hrv(hrv) if hrv and hrv.processing_status == "processed" else None,
    }
