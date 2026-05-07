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


# Run ramp ladder: 10 steps × 3 min, 5% threshold-pace increments, 85% → 130%.
# Pace-driven (input), HR/DFA observed. Final step ≈ 130% threshold pace —
# clearly above LT2, drives DFA a1 below 0.5 (HRVT2) for trained athletes.
_RUN_RAMP_PCT = [85, 90, 95, 100, 105, 110, 115, 120, 125, 130]


def build_ramp_steps_run(threshold_pace_sec_per_km: float | None = None) -> list[WorkoutStepDTO]:
    """Build the Run ramp ladder relative to the athlete's threshold pace.

    Uses ``%pace`` units (velocity ratio, 100 = threshold). Intervals.icu
    converts to absolute pace using the athlete's sport-settings threshold,
    Garmin displays the resulting pace target on the watch.

    The ``threshold_pace_sec_per_km`` parameter is currently unused (kept for
    future fallback if ``%pace`` ever proves unreliable); athlete-relative
    scaling is delegated to Intervals.icu.
    """
    _ = threshold_pace_sec_per_km  # reserved for future fallback to s/km
    steps: list[WorkoutStepDTO] = [
        WorkoutStepDTO(text="Warm-up", duration=600, hr={"units": "%lthr", "value": 70}),
    ]
    for i, pct in enumerate(_RUN_RAMP_PCT, start=1):
        steps.append(
            WorkoutStepDTO(
                text=f"Step {i} ({pct}% threshold)",
                duration=180,
                pace={"units": "%pace", "value": pct},
            )
        )
    steps.append(
        WorkoutStepDTO(text="Cool-down", duration=600, hr={"units": "%lthr", "value": 70}),
    )
    return steps


def create_ramp_test(
    sport: str,
    target_date: date,
    days_since: int = 0,
    threshold_pace: float | None = None,
) -> PlannedWorkoutDTO:
    """Create a ramp test PlannedWorkout for pushing to Intervals.icu.

    Args:
        sport: "Ride" or "Run"
        target_date: Date for the ramp test
        days_since: Days since last valid threshold (for rationale)
        threshold_pace: Athlete's Run threshold pace in s/km. Required for Run
            (falls back to default with a calibration warning); ignored for Ride.
    """
    if sport == "Ride":
        steps = list(RAMP_STEPS_RIDE)
        rationale_extra = ""
    elif sport == "Run":
        steps = build_ramp_steps_run(threshold_pace)
        rationale_extra = (
            ""
            if threshold_pace
            else (
                " Threshold pace not set in Intervals.icu — %pace targets won't render on the "
                "watch correctly; calibrate by setting your Run threshold there first."
            )
        )
    else:
        raise ValueError(f"Ramp test not supported for {sport}. Only Ride and Run.")

    total_min = sum(s.duration for s in steps) // 60

    rationale = (
        f"HRVT1/HRVT2 thresholds are {days_since} days old. "
        "Chest strap required (optical sensor not suitable for DFA). "
        "Hold steady effort for each step."
    )
    if sport == "Run":
        rationale += (
            " Treadmill or perfectly flat course required — protocol is pace-driven, "
            "outdoor terrain/wind makes pace targets unreliable. "
            "Final step is near-max effort; if you can't hold pace, the test ends — "
            "DFA fit takes the points up to the bail-out."
        )
    rationale += rationale_extra

    return PlannedWorkoutDTO(
        sport=sport,
        name=f"Ramp Test ({sport})",
        steps=steps,
        duration_minutes=total_min,
        rationale=rationale,
        target_date=target_date,
    )
