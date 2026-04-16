import zoneinfo
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, select

from api.deps import get_data_user_id, require_viewer
from bot.formatter import STATUS_EMOJI, get_category_display, get_recommendation_text
from config import settings
from data.db import HrvAnalysis, RhrAnalysis, User, Wellness, get_session
from data.utils import extract_sport_ctl

router = APIRouter()


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


def _format_sleep_duration(secs: int | None, language: str = "ru") -> str | None:
    if not secs:
        return None
    h, m = divmod(secs // 60, 60)
    if language == "en":
        return f"{h}h {m}m" if h else f"{m}m"
    return f"{h}ч {m}м" if h else f"{m}м"


def _hrv_block(hrv_row, hrv_today: float | None) -> dict:
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


async def _build_wellness_response(row, target_date: date, user_id: int, language: str = "ru") -> dict:
    target_str = str(target_date)

    category = row.recovery_category or "moderate"
    emoji, title = get_category_display(category, language)
    recommendation_text = get_recommendation_text(row.recovery_recommendation or "", language)

    hrv_flatt = await HrvAnalysis.get(user_id=user_id, dt=target_str, algorithm="flatt_esco")
    hrv_aie = await HrvAnalysis.get(user_id=user_id, dt=target_str, algorithm="ai_endurance")
    hrv_today = float(row.hrv) if row.hrv else None

    rhr_row = await RhrAnalysis.get(user_id=user_id, dt=target_str)

    tsb = round(row.ctl - row.atl, 1) if row.ctl is not None and row.atl is not None else None
    sport_ctl = extract_sport_ctl(row.sport_info)

    return {
        "date": target_str,
        "has_data": True,
        "recovery": {
            "score": row.recovery_score,
            "category": category,
            "emoji": emoji,
            "title": title,
            "recommendation": recommendation_text,
            "readiness_score": row.readiness_score,
            "readiness_level": row.readiness_level,
        },
        "hrv": {
            "primary_algorithm": settings.HRV_ALGORITHM,
            "flatt_esco": _hrv_block(hrv_flatt, hrv_today),
            "ai_endurance": _hrv_block(hrv_aie, hrv_today),
        },
        "rhr": _rhr_block(rhr_row),
        "sleep": {
            "score": row.sleep_score,
            "quality": row.sleep_quality,
            "duration": _format_sleep_duration(row.sleep_secs, language),
            "duration_secs": row.sleep_secs,
        },
        "training_load": {
            "ctl": row.ctl,
            "atl": row.atl,
            "tsb": tsb,
            "ramp_rate": row.ramp_rate,
            "sport_ctl": sport_ctl,
        },
        "body": {
            "weight": row.weight,
            "body_fat": row.body_fat,
            "vo2max": row.vo2max,
            "steps": row.steps,
        },
        "stress": {
            "ess_today": row.ess_today,
            "banister_recovery": row.banister_recovery,
        },
        "ai_recommendation": row.ai_recommendation,
        "updated_at": row.updated.isoformat() if row.updated else None,
    }


async def _wellness_has_prev(target_date: date, user_id: int) -> bool:
    target_str = str(target_date)
    async with get_session() as session:
        result = await session.execute(select(exists().where(Wellness.date < target_str, Wellness.user_id == user_id)))
        return result.scalar_one()


@router.get("/api/report")
async def morning_report(user: User = Depends(require_viewer)) -> dict:
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()
    today_str = str(today)
    uid = get_data_user_id(user)
    row = await Wellness.get(uid, today)

    if row is None:
        return {"date": today_str, "has_data": False, "role": user.role}

    result = await _build_wellness_response(row, today, uid, language=user.language)
    result["role"] = user.role
    return result


@router.get("/api/wellness-day")
async def wellness_day(
    dt: str = Query(default="", alias="date", description="Date YYYY-MM-DD, default=today"),
    user: User = Depends(require_viewer),
) -> dict:
    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    today = datetime.now(tz).date()

    if dt:
        try:
            target = date.fromisoformat(dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    else:
        target = today

    if target > today:
        target = today

    target_str = str(target)
    uid = get_data_user_id(user)
    row = await Wellness.get(uid, target)
    has_prev = await _wellness_has_prev(target, uid)
    has_next = target < today

    if row is None:
        return {
            "date": target_str,
            "has_data": False,
            "is_today": target == today,
            "has_prev": has_prev,
            "has_next": has_next,
            "role": user.role,
        }

    result = await _build_wellness_response(row, target, uid, language=user.language)
    result["is_today"] = target == today
    result["has_prev"] = has_prev
    result["has_next"] = has_next
    result["role"] = user.role
    return result
