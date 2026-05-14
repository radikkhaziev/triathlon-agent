"""Dashboard API routes — mock data for visual preview.

These endpoints serve the dashboard.html frontend with realistic mock data.
Replace with real DB queries when ready.
"""

import random
from datetime import date, timedelta

from fastapi import APIRouter, Depends

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
    ctl_swim, ctl_ride, ctl_run = 8.0, 22.0, 14.0

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
        ctl_ride = ctl_ride + (sport_tss["bike"] - ctl_ride) / 42
        ctl_run = ctl_run + (sport_tss["run"] - ctl_run) / 42

        swim_arr.append(round(ctl_swim, 1))
        bike_arr.append(round(ctl_ride, 1))
        run_arr.append(round(ctl_run, 1))

    return {
        "dates": dates,
        "ctl": ctl_arr,
        "atl": atl_arr,
        "tsb": tsb_arr,
        "ctl_swim": swim_arr,
        "ctl_ride": bike_arr,
        "ctl_run": run_arr,
    }


# Cache the 84-day series so /api/training-load is consistent across calls
_LOAD_84 = _generate_load_series(84)


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


# --- Job trigger stubs ---


@router.post("/api/jobs/morning-report", status_code=202)
async def job_morning_report(user: User = Depends(require_viewer)) -> dict:
    """Trigger morning report generation (stub)."""
    return {
        "status": "accepted",
        "job": "morning-report",
        "message": "Mock: would run scheduler_wellness_job(run_ai=True)",
    }
