import hashlib
import hmac
from datetime import date
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException

from bot.formatter import CATEGORY_DISPLAY, RECOMMENDATION_TEXT, STATUS_EMOJI
from config import settings
from data.database import get_hrv_analysis, get_rhr_analysis, get_wellness
from data.utils import extract_sport_ctl

router = APIRouter()


def verify_telegram_init_data(init_data: str, bot_token: str) -> bool:
    parsed = parse_qs(init_data)
    received_hash = parsed.pop("hash", [None])[0]
    if not received_hash:
        return False

    data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_hash, received_hash)


def _verify_request(authorization: str | None) -> None:
    bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    if not bot_token:
        return
    if not authorization or not verify_telegram_init_data(authorization, bot_token):
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")


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


@router.get("/api/report")
async def morning_report(authorization: str | None = Header(default=None)) -> dict:
    """Full morning report data for the Mini App report page."""
    _verify_request(authorization)
    today = date.today()
    today_str = str(today)
    row = await get_wellness(today)

    if row is None:
        return {"date": today_str, "has_data": False}

    # Recovery
    category = row.recovery_category or "moderate"
    emoji, title = CATEGORY_DISPLAY.get(category, ("⚪", "СТАТУС НЕИЗВЕСТЕН"))
    recommendation_text = RECOMMENDATION_TEXT.get(row.recovery_recommendation or "", row.recovery_recommendation or "")

    # HRV — both algorithms
    hrv_flatt = await get_hrv_analysis(today_str, "flatt_esco")
    hrv_aie = await get_hrv_analysis(today_str, "ai_endurance")
    hrv_today = float(row.hrv) if row.hrv else None

    # RHR
    rhr_row = await get_rhr_analysis(today_str)

    # Training load
    tsb = round(row.ctl - row.atl, 1) if row.ctl is not None and row.atl is not None else None

    # Per-sport CTL from sport_info JSON
    sport_ctl = extract_sport_ctl(row.sport_info)

    return {
        "date": today_str,
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
    }
