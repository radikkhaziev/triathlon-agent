from datetime import date

from data.intervals.dto import PlannedWorkoutDTO, WorkoutStepDTO

# ---------------------------------------------------------------------------
# Ramp test protocols as workout_doc steps
# ---------------------------------------------------------------------------

# Ride: 6 steps from 60% to 103% FTP, 5 min each
RAMP_STEPS_RIDE = [
    WorkoutStepDTO(text="Warm-up", duration=600, power={"units": "%ftp", "value": 60}),
    WorkoutStepDTO(text="Step 1", duration=300, power={"units": "%ftp", "value": 65}),
    WorkoutStepDTO(text="Step 2", duration=300, power={"units": "%ftp", "value": 73}),
    WorkoutStepDTO(text="Step 3", duration=300, power={"units": "%ftp", "value": 80}),
    WorkoutStepDTO(text="Step 4", duration=300, power={"units": "%ftp", "value": 88}),
    WorkoutStepDTO(text="Step 5", duration=300, power={"units": "%ftp", "value": 95}),
    WorkoutStepDTO(text="Step 6", duration=300, power={"units": "%ftp", "value": 103}),
    WorkoutStepDTO(text="Cool-down", duration=600, power={"units": "%ftp", "value": 55}),
]

# Run: 5 steps from 70% to 100% LTHR, 5 min each
RAMP_STEPS_RUN = [
    WorkoutStepDTO(text="Warm-up", duration=600, hr={"units": "%lthr", "value": 65}),
    WorkoutStepDTO(text="Step 1", duration=300, hr={"units": "%lthr", "value": 70}),
    WorkoutStepDTO(text="Step 2", duration=300, hr={"units": "%lthr", "value": 78}),
    WorkoutStepDTO(text="Step 3", duration=300, hr={"units": "%lthr", "value": 85}),
    WorkoutStepDTO(text="Step 4", duration=300, hr={"units": "%lthr", "value": 92}),
    WorkoutStepDTO(text="Step 5", duration=300, hr={"units": "%lthr", "value": 100}),
    WorkoutStepDTO(text="Cool-down", duration=600, hr={"units": "%lthr", "value": 60}),
]

RAMP_PROTOCOLS = {
    "Ride": RAMP_STEPS_RIDE,
    "Run": RAMP_STEPS_RUN,
}


def create_ramp_test(sport: str, target_date: date, days_since: int = 0) -> PlannedWorkoutDTO:
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

    return PlannedWorkoutDTO(
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
    )
