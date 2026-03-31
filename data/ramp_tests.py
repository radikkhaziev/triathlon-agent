"""Ramp test protocols and threshold analysis (ATP Phase 4).

Provides ramp test workout generation, threshold freshness check,
and threshold drift detection.
"""

import logging
from datetime import date

from sqlalchemy import select

from config import settings
from data.database import ActivityHrvRow, ActivityRow, get_session
from data.models import PlannedWorkout, WorkoutStep

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ramp test protocols as workout_doc steps
# ---------------------------------------------------------------------------

# Ride: 6 steps from 60% to 103% FTP, 5 min each
RAMP_STEPS_RIDE = [
    WorkoutStep(text="Warm-up", duration=600, power={"units": "%ftp", "value": 60}),
    WorkoutStep(text="Step 1", duration=300, power={"units": "%ftp", "value": 65}),
    WorkoutStep(text="Step 2", duration=300, power={"units": "%ftp", "value": 73}),
    WorkoutStep(text="Step 3", duration=300, power={"units": "%ftp", "value": 80}),
    WorkoutStep(text="Step 4", duration=300, power={"units": "%ftp", "value": 88}),
    WorkoutStep(text="Step 5", duration=300, power={"units": "%ftp", "value": 95}),
    WorkoutStep(text="Step 6", duration=300, power={"units": "%ftp", "value": 103}),
    WorkoutStep(text="Cool-down", duration=600, power={"units": "%ftp", "value": 55}),
]

# Run: 5 steps from 70% to 100% LTHR, 5 min each
RAMP_STEPS_RUN = [
    WorkoutStep(text="Warm-up", duration=600, hr={"units": "%lthr", "value": 65}),
    WorkoutStep(text="Step 1", duration=300, hr={"units": "%lthr", "value": 70}),
    WorkoutStep(text="Step 2", duration=300, hr={"units": "%lthr", "value": 78}),
    WorkoutStep(text="Step 3", duration=300, hr={"units": "%lthr", "value": 85}),
    WorkoutStep(text="Step 4", duration=300, hr={"units": "%lthr", "value": 92}),
    WorkoutStep(text="Step 5", duration=300, hr={"units": "%lthr", "value": 100}),
    WorkoutStep(text="Cool-down", duration=600, hr={"units": "%lthr", "value": 60}),
]

RAMP_PROTOCOLS = {
    "Ride": RAMP_STEPS_RIDE,
    "Run": RAMP_STEPS_RUN,
}


def create_ramp_test(sport: str, target_date: date, days_since: int = 0) -> PlannedWorkout:
    """Create a ramp test PlannedWorkout for pushing to Intervals.icu.

    Args:
        sport: "Ride" or "Run"
        target_date: Date for the ramp test
        days_since: Days since last valid threshold (for rationale)
    """
    if sport not in RAMP_PROTOCOLS:
        raise ValueError(f"Ramp test not supported for {sport}. Only Ride and Run.")

    steps = RAMP_PROTOCOLS[sport]
    total_min = sum(s.duration for s in steps) // 60

    return PlannedWorkout(
        sport=sport,
        name=f"Ramp Test ({sport})",
        steps=list(steps),
        duration_minutes=total_min,
        rationale=(
            f"HRVT1/HRVT2 thresholds are {days_since} days old. "
            "Chest strap required (optical sensor not suitable for DFA). "
            "Hold steady effort for each 5-min step."
        ),
        target_date=target_date,
        suffix="generated",
    )


# ---------------------------------------------------------------------------
# Threshold freshness
# ---------------------------------------------------------------------------


async def get_threshold_freshness_data(sport: str = "") -> dict:
    """Check how fresh HRVT1/HRVT2 thresholds are.

    Returns dict with days_since, last_date, hrvt1_hr, hrvt2_hr per sport.
    """
    async with get_session() as session:
        query = (
            select(
                ActivityRow.type,
                ActivityRow.start_date_local,
                ActivityHrvRow.hrvt1_hr,
                ActivityHrvRow.hrvt2_hr,
            )
            .join(ActivityHrvRow, ActivityRow.id == ActivityHrvRow.activity_id)
            .where(ActivityRow.user_id == 1)  # TODO: per-user
            .where(ActivityHrvRow.processing_status == "processed")
            .where(ActivityHrvRow.hrvt1_hr.isnot(None))
        )
        if sport:
            query = query.where(ActivityRow.type == sport)
        query = query.order_by(ActivityRow.start_date_local.desc()).limit(5)

        result = await session.execute(query)
        rows = result.all()

    if not rows:
        return {
            "status": "no_data",
            "sport": sport or "all",
            "days_since": None,
            "last_date": None,
            "last_hrvt1": None,
            "last_hrvt2": None,
            "recent_tests": [],
        }

    last_date_str = rows[0][1]
    last_date = date.fromisoformat(last_date_str) if last_date_str else None
    days_since = (date.today() - last_date).days if last_date else None

    return {
        "status": "stale" if days_since and days_since > 21 else "fresh",
        "sport": sport or "all",
        "days_since": days_since,
        "last_date": str(last_date) if last_date else None,
        "last_hrvt1": rows[0][2],
        "last_hrvt2": rows[0][3],
        "recent_tests": [
            {
                "sport": r[0],
                "date": str(r[1]),
                "hrvt1_hr": r[2],
                "hrvt2_hr": r[3],
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Threshold drift detection
# ---------------------------------------------------------------------------


async def detect_threshold_drift() -> dict | None:
    """Compare recent HRVT1 values with config LTHR to detect drift.

    Returns drift info if >5% divergence found across 2+ tests,
    or None if no significant drift.
    """
    async with get_session() as session:
        # Get last 3 valid HRVT1 values for each sport
        result_ride = await session.execute(
            select(ActivityHrvRow.hrvt1_hr, ActivityHrvRow.hrvt1_power)
            .join(ActivityRow, ActivityRow.id == ActivityHrvRow.activity_id)
            .where(ActivityRow.user_id == 1)  # TODO: per-user
            .where(ActivityHrvRow.processing_status == "processed")
            .where(ActivityHrvRow.hrvt1_hr.isnot(None))
            .where(ActivityRow.type.in_(["Ride", "VirtualRide"]))
            .order_by(ActivityRow.start_date_local.desc())
            .limit(3)
        )
        ride_rows = result_ride.all()

        result_run = await session.execute(
            select(ActivityHrvRow.hrvt1_hr)
            .join(ActivityRow, ActivityRow.id == ActivityHrvRow.activity_id)
            .where(ActivityRow.user_id == 1)  # TODO: per-user
            .where(ActivityHrvRow.processing_status == "processed")
            .where(ActivityHrvRow.hrvt1_hr.isnot(None))
            .where(ActivityRow.type.in_(["Run", "VirtualRun", "TrailRun"]))
            .order_by(ActivityRow.start_date_local.desc())
            .limit(3)
        )
        run_rows = result_run.all()

    alerts = []

    # Check ride HRVT1 vs config LTHR_BIKE
    if len(ride_rows) >= 2:
        avg_hrvt1 = sum(r[0] for r in ride_rows) / len(ride_rows)
        config_lthr = settings.ATHLETE_LTHR_BIKE
        pct_diff = (avg_hrvt1 - config_lthr) / config_lthr * 100
        if abs(pct_diff) > 5:
            alerts.append(
                {
                    "sport": "Ride",
                    "metric": "LTHR",
                    "measured_avg": round(avg_hrvt1),
                    "config_value": config_lthr,
                    "diff_pct": round(pct_diff, 1),
                    "tests_count": len(ride_rows),
                    "message": (
                        f"HRVT1 stable at {round(avg_hrvt1)} bpm ({len(ride_rows)} tests). "
                        f"Current LTHR Bike: {config_lthr} bpm ({pct_diff:+.1f}%). "
                        "Consider updating LTHR."
                    ),
                }
            )

    # Check run HRVT1 vs config LTHR_RUN
    if len(run_rows) >= 2:
        avg_hrvt1 = sum(r[0] for r in run_rows) / len(run_rows)
        config_lthr = settings.ATHLETE_LTHR_RUN
        pct_diff = (avg_hrvt1 - config_lthr) / config_lthr * 100
        if abs(pct_diff) > 5:
            alerts.append(
                {
                    "sport": "Run",
                    "metric": "LTHR",
                    "measured_avg": round(avg_hrvt1),
                    "config_value": config_lthr,
                    "diff_pct": round(pct_diff, 1),
                    "tests_count": len(run_rows),
                    "message": (
                        f"HRVT1 stable at {round(avg_hrvt1)} bpm ({len(run_rows)} tests). "
                        f"Current LTHR Run: {config_lthr} bpm ({pct_diff:+.1f}%). "
                        "Consider updating LTHR."
                    ),
                }
            )

    return {"alerts": alerts} if alerts else None


# ---------------------------------------------------------------------------
# Should we suggest a ramp test?
# ---------------------------------------------------------------------------


async def should_suggest_ramp(
    recovery_score: float,
    recovery_category: str,
    tsb: float,
) -> str | None:
    """Check if a ramp test should be suggested today.

    Returns sport ("Ride" or "Run") if ramp should be suggested, None otherwise.
    """
    # Must be in good shape
    if recovery_category not in ("good", "excellent") or recovery_score < 70:
        return None
    if tsb < -10:
        return None

    # Check freshness for both sports
    for sport in ("Ride", "Run"):
        data = await get_threshold_freshness_data(sport)
        if data["status"] == "no_data":
            return sport  # never tested → suggest
        if data["days_since"] and data["days_since"] > 21:
            return sport  # stale → suggest

    return None
