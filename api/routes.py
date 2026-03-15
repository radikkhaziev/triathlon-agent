import hashlib
import hmac
from datetime import date, timedelta
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException

from config import settings
from data.database import (
    get_activities,
    get_daily_metrics,
    get_daily_metrics_range,
    get_scheduled_workouts_range,
    get_tss_history,
)

router = APIRouter()


def verify_telegram_init_data(init_data: str, bot_token: str) -> bool:
    parsed = parse_qs(init_data)
    received_hash = parsed.pop("hash", [None])[0]
    if not received_hash:
        return False

    data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed_hash, received_hash)


def _verify_request(authorization: str | None) -> None:
    bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    if not bot_token:
        return
    if not authorization or not verify_telegram_init_data(authorization, bot_token):
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/api/dashboard")
def dashboard(authorization: str | None = Header(default=None)) -> dict:
    _verify_request(authorization)
    today = date.today()
    row = get_daily_metrics(today)

    if row is None:
        return {"date": str(today), "has_data": False}

    return {
        "date": row.date,
        "has_data": True,
        "readiness_score": row.readiness_score,
        "readiness_level": row.readiness_level,
        "sleep_score": row.sleep_score,
        "hrv_last": row.hrv_last,
        "hrv_baseline": row.hrv_baseline,
        "body_battery": row.body_battery,
        "resting_hr": row.resting_hr,
        "ctl": row.ctl,
        "atl": row.atl,
        "tsb": row.tsb,
        "ctl_swim": row.ctl_swim,
        "ctl_bike": row.ctl_bike,
        "ctl_run": row.ctl_run,
        "ai_recommendation": row.ai_recommendation,
    }


@router.get("/api/training-load")
def training_load(
    days: int = 84,
    authorization: str | None = Header(default=None),
) -> dict:
    _verify_request(authorization)
    today = date.today()
    start = today - timedelta(days=days)
    rows = get_daily_metrics_range(start, today)

    return {
        "dates": [r.date for r in rows],
        "ctl": [r.ctl for r in rows],
        "atl": [r.atl for r in rows],
        "tsb": [r.tsb for r in rows],
    }


@router.get("/api/activities")
def activities_list(
    days: int = 28,
    authorization: str | None = Header(default=None),
) -> dict:
    _verify_request(authorization)
    today = date.today()
    start = today - timedelta(days=days)
    rows = get_activities(start, today)

    return {
        "activities": [
            {
                "activity_id": r.activity_id,
                "date": r.date,
                "sport": r.sport,
                "duration_sec": r.duration_sec,
                "distance_m": r.distance_m,
                "avg_hr": r.avg_hr,
                "tss": r.tss,
            }
            for r in rows
        ]
    }


@router.get("/api/goal")
def goal_progress(authorization: str | None = Header(default=None)) -> dict:
    _verify_request(authorization)
    event_date = settings.GOAL_EVENT_DATE
    weeks_remaining = max(0, (event_date - date.today()).days // 7)

    swim_target = settings.GOAL_SWIM_CTL_TARGET
    bike_target = settings.GOAL_BIKE_CTL_TARGET
    run_target = settings.GOAL_RUN_CTL_TARGET

    row = get_daily_metrics(date.today())
    ctl_swim = (row.ctl_swim or 0) if row else 0
    ctl_bike = (row.ctl_bike or 0) if row else 0
    ctl_run = (row.ctl_run or 0) if row else 0

    swim_pct = min(100, (ctl_swim / swim_target) * 100) if swim_target else 0
    bike_pct = min(100, (ctl_bike / bike_target) * 100) if bike_target else 0
    run_pct = min(100, (ctl_run / run_target) * 100) if run_target else 0

    return {
        "event_name": settings.GOAL_EVENT_NAME,
        "event_date": str(event_date),
        "weeks_remaining": weeks_remaining,
        "swim_pct": round(swim_pct, 1),
        "bike_pct": round(bike_pct, 1),
        "run_pct": round(run_pct, 1),
        "overall_pct": round((swim_pct + bike_pct + run_pct) / 3, 1),
    }


@router.get("/api/weekly-summary")
def weekly_summary(authorization: str | None = Header(default=None)) -> dict:
    _verify_request(authorization)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    rows = get_activities(week_start, today)

    by_sport: dict[str, dict] = {}
    for r in rows:
        sport = r.sport or "other"
        if sport not in by_sport:
            by_sport[sport] = {"duration_sec": 0, "distance_m": 0, "tss": 0, "count": 0}
        by_sport[sport]["duration_sec"] += r.duration_sec or 0
        by_sport[sport]["distance_m"] += r.distance_m or 0
        by_sport[sport]["tss"] += r.tss or 0
        by_sport[sport]["count"] += 1

    return {"week_start": str(week_start), "by_sport": by_sport}


@router.get("/api/scheduled")
def scheduled_workouts(
    days: int = 7,
    authorization: str | None = Header(default=None),
) -> dict:
    _verify_request(authorization)
    today = date.today()
    end = today + timedelta(days=days)
    rows = get_scheduled_workouts_range(today, end)

    return {
        "workouts": [
            {
                "date": r.scheduled_date,
                "sport": r.sport,
                "workout_name": r.workout_name,
                "description": r.description,
                "planned_tss": r.planned_tss,
            }
            for r in rows
        ]
    }
