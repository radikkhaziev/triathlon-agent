"""Dashboard API routes — mock data for visual preview.

These endpoints serve the dashboard.html frontend with realistic mock data.
Replace with real DB queries when ready.
"""

import random
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from api.deps import require_viewer
from data.db import User

router = APIRouter()

# --- Helpers ---


def _date_range(days_back: int, end: date | None = None) -> list[str]:
    end = end or date(2026, 3, 25)
    return [str(end - timedelta(days=days_back - 1 - i)) for i in range(days_back)]


def _generate_load_series(days: int) -> dict:
    """Generate realistic CTL/ATL/TSB + per-sport CTL time series."""
    dates = _date_range(days)
    rng = random.Random(42)

    # Start values
    ctl, atl = 52.0, 58.0
    ctl_swim, ctl_bike, ctl_run = 8.0, 22.0, 14.0

    ctl_arr, atl_arr, tsb_arr = [], [], []
    swim_arr, bike_arr, run_arr = [], [], []

    for i in range(days):
        # Simulate daily TSS with weekly periodicity (rest on Mon)
        day_of_week = date.fromisoformat(dates[i]).weekday()
        if day_of_week == 0:  # Monday rest
            tss = rng.uniform(0, 20)
        elif day_of_week == 6:  # Sunday long
            tss = rng.uniform(80, 140)
        else:
            tss = rng.uniform(40, 100)

        # Gradual build over time
        tss *= 1 + i / days * 0.3

        ctl = ctl + (tss - ctl) / 42
        atl = atl + (tss - atl) / 7
        tsb = ctl - atl

        ctl_arr.append(round(ctl, 1))
        atl_arr.append(round(atl, 1))
        tsb_arr.append(round(tsb, 1))

        # Per-sport CTL (simplified)
        sport_tss = {"swim": 0, "bike": 0, "run": 0}
        sport = rng.choice(["swim", "bike", "bike", "run", "run"])
        sport_tss[sport] = tss

        ctl_swim = ctl_swim + (sport_tss["swim"] - ctl_swim) / 42
        ctl_bike = ctl_bike + (sport_tss["bike"] - ctl_bike) / 42
        ctl_run = ctl_run + (sport_tss["run"] - ctl_run) / 42

        swim_arr.append(round(ctl_swim, 1))
        bike_arr.append(round(ctl_bike, 1))
        run_arr.append(round(ctl_run, 1))

    return {
        "dates": dates,
        "ctl": ctl_arr,
        "atl": atl_arr,
        "tsb": tsb_arr,
        "ctl_swim": swim_arr,
        "ctl_bike": bike_arr,
        "ctl_run": run_arr,
    }


# Cache the 84-day series so /api/training-load is consistent across calls
_LOAD_84 = _generate_load_series(84)


def _generate_activities(days: int) -> list[dict]:
    """Generate realistic activity log."""
    rng = random.Random(123)
    activities = []
    today = date(2026, 3, 25)

    sport_config = {
        "swimming": {"tss_range": (30, 55), "prob": 0.3},
        "cycling": {"tss_range": (50, 130), "prob": 0.4},
        "running": {"tss_range": (40, 90), "prob": 0.3},
    }

    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        dow = d.weekday()

        if dow == 0:  # Monday — rest or easy swim
            if rng.random() < 0.4:
                activities.append({"date": str(d), "sport": "swimming", "tss": rng.randint(25, 35)})
            continue

        # 1-2 activities per day
        n_activities = 1 if rng.random() < 0.7 else 2
        for _ in range(n_activities):
            sport = rng.choices(
                list(sport_config.keys()),
                weights=[c["prob"] for c in sport_config.values()],
            )[0]
            lo, hi = sport_config[sport]["tss_range"]
            # Sunday = long
            if dow == 6:
                lo, hi = int(lo * 1.3), int(hi * 1.3)
            activities.append({"date": str(d), "sport": sport, "tss": rng.randint(lo, hi)})

    return activities


def _generate_recovery_series(days: int) -> dict:
    """Generate realistic recovery score + RMSSD series."""
    dates = _date_range(days)
    rng = random.Random(77)

    recovery = []
    hrv = []

    for i in range(days):
        day_of_week = date.fromisoformat(dates[i]).weekday()
        # Recovery tends to be higher after rest days, lower after hard training days
        if day_of_week == 0:  # Monday — post-rest
            rec = rng.uniform(75, 92)
            hrv_val = rng.uniform(52, 60)
        elif day_of_week in (5, 6):  # Weekend — post-hard-session
            rec = rng.uniform(45, 68)
            hrv_val = rng.uniform(40, 50)
        else:
            rec = rng.uniform(60, 85)
            hrv_val = rng.uniform(45, 56)

        # Slight upward trend over the period
        rec += i / days * 5
        hrv_val += i / days * 3

        recovery.append(round(min(100, rec), 0))
        hrv.append(round(hrv_val, 1))

    return {"dates": dates, "recovery": recovery, "hrv": hrv}


_RECOVERY_21 = _generate_recovery_series(21)


# --- Endpoints ---


@router.get("/api/dashboard")
async def dashboard(user: User = Depends(require_viewer)) -> dict:
    """Today tab — readiness, metrics, training load, AI recommendation."""
    return {
        "has_data": True,
        "readiness_level": "green",
        "readiness_score": 78,
        "hrv_last": 52.3,
        "hrv_baseline": 48.1,
        "sleep_score": 82,
        "resting_hr": 46.0,
        "ctl": _LOAD_84["ctl"][-1],
        "atl": _LOAD_84["atl"][-1],
        "tsb": _LOAD_84["tsb"][-1],
        "ai_recommendation": (
            "🟢 Готовность хорошая. HRV +8.7% выше 7-дн базы (52.3 vs 48.1), "
            "RHR стабильный 46 уд/мин, сон 82/100.\n\n"
            "Сегодня запланирована велотренировка Z2 с интервалами темпо 2×10мин — "
            "подходит текущему состоянию. Держи пульс в Z2 (104-127), "
            "на темпо-блоках Z3 (127-144).\n\n"
            "CTL 62 → цель 75 к сентябрю, темп набора +1.2 TSS/нед — в графике. "
            "TSB -9 — оптимальная зона продуктивной нагрузки.\n\n"
            "До Ironman 70.3 — 25 недель. Плавание 60% от цели — "
            "добавь 1 тренировку в неделю для ускорения."
        ),
    }


@router.get("/api/training-load")
async def training_load(days: int = Query(default=84, le=365), user: User = Depends(require_viewer)) -> dict:
    """CTL/ATL/TSB + per-sport CTL time series."""
    if days >= 84:
        return _LOAD_84
    # Slice the tail
    return {k: v[-days:] if isinstance(v, list) else v for k, v in _LOAD_84.items()}


@router.get("/api/activities")
async def activities(days: int = Query(default=28, le=180), user: User = Depends(require_viewer)) -> dict:
    """Completed activities with sport and TSS."""
    return {"activities": _generate_activities(days)}


@router.get("/api/recovery-trend")
async def recovery_trend(days: int = Query(default=21, ge=1, le=90), user: User = Depends(require_viewer)) -> dict:
    """Recovery score + RMSSD trend over N days."""
    if days >= 21:
        return _RECOVERY_21
    return {k: v[-days:] if isinstance(v, list) else v for k, v in _RECOVERY_21.items()}


@router.get("/api/goal")
async def goal(user: User = Depends(require_viewer)) -> dict:
    """Race goal progress."""
    return {
        "event_name": "Ironman 70.3",
        "event_date": "2026-09-15",
        "weeks_remaining": 25,
        "overall_pct": 63,
        "swim_pct": 60,
        "swim_ctl": 9.0,
        "swim_target": 15,
        "bike_pct": 72,
        "bike_ctl": 25.2,
        "bike_target": 35,
        "run_pct": 55,
        "run_ctl": 13.8,
        "run_target": 25,
    }


@router.get("/api/weekly-summary")
async def weekly_summary(user: User = Depends(require_viewer)) -> dict:
    """This week's completed training summary by sport."""
    return {
        "week_start": "2026-03-23",
        "week_end": "2026-03-29",
        "by_sport": {
            "swimming": {"duration_sec": 3600, "distance_m": 3000, "tss": 42},
            "cycling": {"duration_sec": 12600, "distance_m": 85000, "tss": 158},
            "running": {"duration_sec": 5400, "distance_m": 11500, "tss": 88},
        },
    }


@router.get("/api/scheduled")
async def scheduled_workouts(days: int = Query(default=7, le=30), user: User = Depends(require_viewer)) -> dict:
    """Planned workouts for the next N days."""
    today = date(2026, 3, 25)
    workouts = [
        {"date": str(today), "sport": "cycling", "workout_name": "Endurance Z2 + 2×10min Tempo", "planned_tss": 85},
        {
            "date": str(today + timedelta(days=1)),
            "sport": "swimming",
            "workout_name": "Technique + 10×100m @CSS",
            "planned_tss": 42,
        },
        {
            "date": str(today + timedelta(days=1)),
            "sport": "running",
            "workout_name": "Easy Recovery 40min Z1",
            "planned_tss": 35,
        },
        {
            "date": str(today + timedelta(days=2)),
            "sport": "cycling",
            "workout_name": "Sweet Spot 3×12min",
            "planned_tss": 95,
        },
        {
            "date": str(today + timedelta(days=3)),
            "sport": "running",
            "workout_name": "Tempo 4×8min Z3",
            "planned_tss": 72,
        },
        {
            "date": str(today + timedelta(days=4)),
            "sport": "swimming",
            "workout_name": "Endurance 3km steady",
            "planned_tss": 45,
        },
        {
            "date": str(today + timedelta(days=5)),
            "sport": "cycling",
            "workout_name": "Long Ride Z2 3.5h",
            "planned_tss": 160,
        },
        {
            "date": str(today + timedelta(days=6)),
            "sport": "running",
            "workout_name": "Long Run Z2 1h30",
            "planned_tss": 105,
        },
    ]
    cutoff = str(today + timedelta(days=days))
    return {"workouts": [w for w in workouts if w["date"] < cutoff]}


# --- Job trigger stubs ---


@router.post("/api/jobs/morning-report", status_code=202)
async def job_morning_report(user: User = Depends(require_viewer)) -> dict:
    """Trigger morning report generation (stub)."""
    return {
        "status": "accepted",
        "job": "morning-report",
        "message": "Mock: would run scheduler_wellness_job(run_ai=True)",
    }


@router.post("/api/jobs/sync-wellness", status_code=202)
async def job_sync_wellness(user: User = Depends(require_viewer)) -> dict:
    """Trigger wellness sync (stub)."""
    return {"status": "accepted", "job": "sync-wellness", "message": "Mock: would run scheduler_wellness_job()"}
