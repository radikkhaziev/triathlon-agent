from datetime import date

from data.intervals.dto import PlannedWorkoutDTO, WorkoutStepDTO

# ---------------------------------------------------------------------------
# Ramp test protocols as workout_doc steps
#
# Both protocols follow the DFA α1 method (Rogers et al. 2020-2023):
#   - α1 ≈ 1.0  → fully aerobic
#   - α1 = 0.75 → HRVT1 (LT1 / aerobic threshold)
#   - α1 = 0.50 → HRVT2 (LT2 / anaerobic threshold)
#   - α1 < 0.50 → anaerobic / VO2max
#
# Universal principles (per docs/RAMP_TEST_BIKE_SPEC.md §3):
#   1. Load is controlled (pace/power), HR is observed.
#   2. Anchor against current threshold (% threshold_pace / %FTP) — self-calibrating.
#   3. 3-minute steps for α1 stabilization (≥3 sliding windows per step).
#   4. 5% increment for resolution (3-5 bpm HR delta).
#   5. Top must penetrate HRVT2 — Run 115%, Bike 120% (calibration trap, see §5.2).
#   6. Cover both thresholds with margin: ≥2-3 points below HRVT1.
# ---------------------------------------------------------------------------

# Defaults used when the athlete has no threshold/FTP configured. These are
# average-amateur values — the warning string returned by the builders prompts
# the athlete to update sport-settings before relying on the result.
DEFAULT_THRESHOLD_PACE_SEC_PER_KM = 295.0  # 4:55/km
DEFAULT_BIKE_FTP_WATTS = 200.0


# Run ramp ladder: 8 steps × 3 min, 5% threshold-pace increments, 80% → 115%.
# Pace-driven (input), HR/DFA observed. Calibrated for `threshold_pace = pace at
# HRVT2` (Intervals.icu's `lthr`-aligned semantic, see drift detector in
# data/db/user.py). Step 100% sits exactly at HRVT2 by definition; 110-115%
# pushes α1 below 0.5 cleanly without forcing bail-out at unrealistic 130% paces.
_RUN_RAMP_PCT = [80, 85, 90, 95, 100, 105, 110, 115]


# Ride ramp ladder: 11 regular steps × 3 min (5% inc, 60→110%) + 1 final step
# × 4 min @ 120% («push to failure»). The 10% jump from step 11 (110%) to the
# final (120%) is intentional — calibration trap (see SPEC §5.2): if the
# athlete's FTP is undercalibrated, 110% may not penetrate the real HRVT2 and
# α1 won't cross 0.5; 120% guarantees it. After the first calibrated test
# updates FTP, the protocol self-corrects on subsequent runs.
_RIDE_RAMP_PCT_REGULAR = [60, 65, 70, 75, 80, 85, 90, 95, 100, 105, 110]
_RIDE_RAMP_PCT_FINAL = 120


def build_ramp_steps_run(
    threshold_pace_sec_per_km: float | None = None,
) -> tuple[list[WorkoutStepDTO], list[str]]:
    """Build the Run ramp ladder relative to the athlete's threshold pace.

    Uses ``%pace`` units (velocity ratio, 100 = threshold). Intervals.icu
    converts to absolute pace using the athlete's sport-settings threshold,
    Garmin displays the resulting pace target on the watch.

    Returns ``(steps, warnings)``. Warnings surface when threshold_pace is
    missing (default applied) or the top step crosses common treadmill caps —
    the consumer is expected to forward them into the workout rationale so the
    athlete can act on them before the test.
    """
    warnings: list[str] = []
    if threshold_pace_sec_per_km is None or threshold_pace_sec_per_km <= 0:
        threshold_pace_sec_per_km = DEFAULT_THRESHOLD_PACE_SEC_PER_KM
        warnings.append(
            f"⚠️ Run threshold pace not configured in Intervals.icu. The protocol "
            f"emits {{units: '%pace'}} steps; without a configured Run threshold, "
            f"Intervals.icu cannot resolve them to absolute pace and Garmin will "
            f"render no pace target on the watch. Default "
            f"{int(DEFAULT_THRESHOLD_PACE_SEC_PER_KM)}s/km (4:55/km — average "
            f"amateur) is used here only for the local treadmill-cap warning "
            f"below; the actual on-watch behavior depends on configuring "
            f"Intervals.icu Run sport-settings before the test."
        )

    steps: list[WorkoutStepDTO] = [
        WorkoutStepDTO(text="Warm-up", duration=600, hr={"units": "%lthr", "start": 70}),
    ]
    for i, pct in enumerate(_RUN_RAMP_PCT, start=1):
        steps.append(
            WorkoutStepDTO(
                text=f"Step {i} ({pct}% threshold)",
                duration=180,
                pace={"units": "%pace", "start": pct},
            )
        )
    steps.append(
        WorkoutStepDTO(text="Cool-down", duration=420, hr={"units": "%lthr", "start": 70}),
    )

    # Treadmill cap warning — last step at 115% × threshold_speed (km/h).
    threshold_speed_kmh = 3600.0 / threshold_pace_sec_per_km
    top_speed_kmh = threshold_speed_kmh * (_RUN_RAMP_PCT[-1] / 100)
    if top_speed_kmh > 18.0:
        warnings.append(
            f"⚠️ Top step pace ≈ {top_speed_kmh:.1f} km/h — most home treadmills cap at 18-20 km/h. "
            "If yours can't sustain it, the test ends naturally at the speed cap and the DFA fit "
            "uses the points up to bail-out."
        )

    return steps, warnings


def build_ramp_steps_ride(
    bike_ftp_watts: float | None = None,
) -> tuple[list[WorkoutStepDTO], list[str]]:
    """Build the Ride ramp ladder anchored on the athlete's FTP.

    Returns ``(steps, warnings)``. WU is split into two phases (50% Z1 ease-in
    → 60% Z2 build) so the athlete eases into the work range. Steps 1-11 are
    uniform 5% increments at 3 min each; the final step is a deliberate 10%
    jump to 120% for 4 min — long enough to produce one valid α1 window
    (~60-90 sec) even if the rider can't hold the full 4 min.
    """
    warnings: list[str] = []
    if bike_ftp_watts is None or bike_ftp_watts <= 0:
        bike_ftp_watts = DEFAULT_BIKE_FTP_WATTS
        warnings.append(
            f"⚠️ Bike FTP not configured in Intervals.icu. The protocol emits "
            f"{{units: '%ftp'}} steps; without a configured FTP, Intervals.icu "
            f"cannot resolve them to absolute watts and the trainer's ERG mode "
            f"will fall back to whatever Intervals shows. Default "
            f"{int(DEFAULT_BIKE_FTP_WATTS)}W is referenced here only for the "
            f"informational warning; configure Intervals.icu Ride sport-settings "
            f"before the test for accurate targets."
        )

    steps: list[WorkoutStepDTO] = [
        WorkoutStepDTO(text="Warm-up easy", duration=300, power={"units": "%ftp", "start": 50}),
        WorkoutStepDTO(text="Warm-up build", duration=300, power={"units": "%ftp", "start": 60}),
    ]
    for i, pct in enumerate(_RIDE_RAMP_PCT_REGULAR, start=1):
        steps.append(
            WorkoutStepDTO(
                text=f"Step {i} ({pct}% FTP)",
                duration=180,
                power={"units": "%ftp", "start": pct},
            )
        )
    steps.append(
        WorkoutStepDTO(
            text=f"Step {len(_RIDE_RAMP_PCT_REGULAR) + 1} ({_RIDE_RAMP_PCT_FINAL}% FTP) — push to failure",
            duration=240,
            power={"units": "%ftp", "start": _RIDE_RAMP_PCT_FINAL},
        )
    )
    steps.append(
        WorkoutStepDTO(text="Cool-down", duration=600, power={"units": "%ftp", "start": 50}),
    )

    return steps, warnings


# ---------------------------------------------------------------------------
# Workout description templates — baked into the rationale so Garmin/Intervals
# UI shows full equipment + pacing + failure-signal guidance to the athlete.
# Spec §6.
# ---------------------------------------------------------------------------

_RUN_DESCRIPTION = (
    "RAMP TEST (DFA α1 method)\n\n"
    "EQUIPMENT:\n"
    "- Chest HR strap MANDATORY (optical sensors do not produce valid RR data for DFA)\n"
    "- Treadmill recommended; outdoor flat course as fallback\n"
    "- RR-interval recording enabled on watch\n\n"
    "WARM-UP (10 min, by feel):\n"
    "- Easy jog, build to ~70-75% LTHR\n\n"
    "RAMP (8 steps × 3 min, 80→115% threshold pace):\n"
    "- Hold each pace step for the full 3 minutes\n"
    "- DO NOT slow down to control HR — pace is the input, HR is observed\n"
    "- STOP when you cannot hold pace; remaining steps skipped (DFA fit uses points up to bail-out)\n\n"
    "PACING GUIDANCE:\n"
    "- Step 1 (80%) should feel almost trivially easy\n"
    "- Real test starts around Step 5-6 (100-105% threshold)\n"
    "- Final 2-3 steps (110-115%) are where you find your edge\n\n"
    "COOL-DOWN (7 min, by feel):\n"
    "- 1-2 min walk, then easy jog\n"
    "- HR falls naturally below 70% LTHR"
)

_RIDE_DESCRIPTION = (
    "RAMP TEST — BIKE (DFA α1 method)\n\n"
    "EQUIPMENT:\n"
    "- Chest HR strap MANDATORY\n"
    "- Smart trainer in ERG mode (power is target, you handle cadence)\n"
    "- Powerful fan + cold water + ventilation (cardiac drift dominates without cooling)\n"
    "- RR recording enabled\n\n"
    "WARM-UP (10 min, ERG):\n"
    "- 5 min @ 50% FTP, 5 min @ 60% FTP\n"
    "- Establish cadence 85-90 rpm — hold throughout the test\n\n"
    "RAMP (11 × 3 min @ 60-110% + 1 × 4 min @ 120%):\n"
    "- Cadence 85-90 rpm THROUGHOUT — drift adds noise to the DFA curve\n"
    "- ERG holds watts; you maintain cadence consistency\n"
    "- Drink every 10 min\n\n"
    "FINAL STEP (120% FTP, 4 min) — push to failure:\n"
    "- Ok to stop at 60-90 sec; one valid α1 window is enough\n"
    "- ERG lockout / cadence dropping below 70 rpm = end of test\n\n"
    "COOL-DOWN (10 min, ERG):\n"
    "- 50% FTP easy spin"
)


def create_ramp_test(
    sport: str,
    target_date: date,
    days_since: int = 0,
    threshold_pace: float | None = None,
    bike_ftp: float | None = None,
) -> PlannedWorkoutDTO:
    """Create a ramp test PlannedWorkout for pushing to Intervals.icu.

    Args:
        sport: "Ride" or "Run"
        target_date: Date for the ramp test
        days_since: Days since last valid threshold (for rationale)
        threshold_pace: Athlete's Run threshold pace in s/km. Optional — affects
            warnings only (Intervals.icu does the %pace → absolute pace conversion).
            None / ≤0 emits a calibration warning. Ignored for Ride.
        bike_ftp: Athlete's bike FTP in watts. Same rules as ``threshold_pace``,
            mirrored for the Ride path. Ignored for Run.
    """
    if sport == "Ride":
        steps, warnings = build_ramp_steps_ride(bike_ftp)
        description = _RIDE_DESCRIPTION
    elif sport == "Run":
        steps, warnings = build_ramp_steps_run(threshold_pace)
        description = _RUN_DESCRIPTION
    else:
        raise ValueError(f"Ramp test not supported for {sport}. Only Ride and Run.")

    total_min = sum(s.duration for s in steps) // 60

    header = (
        f"HRVT1/HRVT2 thresholds are {days_since} days old. "
        "Chest strap required (optical sensor not suitable for DFA)."
    )
    rationale_parts = [header, description]
    if warnings:
        rationale_parts.append("\n".join(warnings))

    return PlannedWorkoutDTO(
        sport=sport,
        name=f"Ramp Test ({sport})",
        steps=steps,
        duration_minutes=total_min,
        rationale="\n\n".join(rationale_parts),
        target_date=target_date,
    )
