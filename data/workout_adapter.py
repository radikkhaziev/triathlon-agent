"""Workout adaptation engine (ATP Phase 2).

Parses HumanGo workout descriptions, evaluates adaptation constraints,
and produces modified PlannedWorkout for Intervals.icu.
"""

import re

from data.intervals.dto import RecoveryScoreDTO, WorkoutStepDTO

# ---------------------------------------------------------------------------
# Constants: zone boundaries (HR as % LTHR, Power as % FTP)
# ---------------------------------------------------------------------------

# Zone upper bounds — zone N means target <= ZONE_UPPER[N] % of threshold
ZONE_UPPER = {1: 72, 2: 82, 3: 87, 4: 92, 5: 100}

# Map absolute HR/power to approximate zone
# These are mid-zone values used for rough classification


def _hr_to_zone(low_bpm: float, high_bpm: float, lthr: int) -> int:
    """Estimate zone from HR range and LTHR."""
    mid_pct = ((low_bpm + high_bpm) / 2) / lthr * 100
    for z in range(1, 6):
        if mid_pct <= ZONE_UPPER[z]:
            return z
    return 5


def _power_to_zone(low_w: float, high_w: float, ftp: float) -> int:
    """Estimate zone from power range and FTP."""
    mid_pct = ((low_w + high_w) / 2) / ftp * 100
    # Power zones differ from HR zones — use standard 7-zone model simplified to 5
    if mid_pct <= 55:
        return 1
    if mid_pct <= 75:
        return 2
    if mid_pct <= 90:
        return 3
    if mid_pct <= 105:
        return 4
    return 5


# ---------------------------------------------------------------------------
# Parser: HumanGo description → WorkoutStep list
# ---------------------------------------------------------------------------

_SEPARATOR = re.compile(r"={10,}")
_REPEAT = re.compile(r"repeat\s+(\d+)\s+times", re.IGNORECASE)
_DURATION_FULL = re.compile(r"duration:\s*(?:(\d+)\s*min)?(?:\s*(\d+)\s*sec)?", re.IGNORECASE)
_DISTANCE = re.compile(r"distance:\s*(\d+)\s*meters", re.IGNORECASE)
_POWER_LOW = re.compile(r"low:\s*([\d.]+)\s*W", re.IGNORECASE)
_POWER_HIGH = re.compile(r"high:\s*([\d.]+)\s*W", re.IGNORECASE)
_HR_LOW = re.compile(r"low:\s*([\d.]+)\s*bpm", re.IGNORECASE)
_HR_HIGH = re.compile(r"high:\s*([\d.]+)\s*bpm", re.IGNORECASE)
_PACE_LOW = re.compile(r"low:\s*(\d+):(\d+)\s*per\s*100\s*meters", re.IGNORECASE)
_PACE_HIGH = re.compile(r"high:\s*(\d+):(\d+)\s*per\s*100\s*meters", re.IGNORECASE)

STEP_TYPES = {"warmup", "interval", "recovery", "cooldown", "rest"}


def parse_humango_description(description: str) -> list[WorkoutStepDTO]:
    """Parse HumanGo workout description into structured WorkoutStep list.

    HumanGo format uses ====== separators between steps, with:
    - Step type: warmup/interval/recovery/cooldown/rest
    - Duration or distance
    - Target: power (W), heart rate (bpm), or pace (per 100m)
    - Optional repeat groups: "======= repeat N times ====="
    """
    if not description:
        return []

    # Split into blocks by separator lines
    blocks = _split_into_blocks(description)

    # Parse blocks into steps, handling repeat groups
    steps: list[WorkoutStepDTO] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]

        # Check for repeat marker
        repeat_match = _REPEAT.search(block)
        if repeat_match:
            reps = int(repeat_match.group(1))
            # Collect interval + recovery steps (not warmup/cooldown)
            sub_steps: list[WorkoutStepDTO] = []
            i += 1
            while i < len(blocks):
                if _REPEAT.search(blocks[i]):
                    break
                step = _parse_block(blocks[i])
                if step:
                    # cooldown after repeat belongs to outer level
                    step_type = step.text.lower().replace("-", "")
                    if step_type in ("cooldown", "cool down"):
                        break
                    sub_steps.append(step)
                i += 1
            if sub_steps:
                first_type = sub_steps[0].text if sub_steps else "Intervals"
                steps.append(
                    WorkoutStepDTO(
                        text=f"{reps}x {first_type}",
                        reps=reps,
                        steps=sub_steps,
                    )
                )
            continue

        step = _parse_block(block)
        if step:
            steps.append(step)
        i += 1

    return steps


def _split_into_blocks(description: str) -> list[str]:
    """Split description by ====== separators, returning non-empty blocks."""
    # Remove everything before first separator (intro text, HumanGo link)
    parts = _SEPARATOR.split(description)
    blocks = []
    for part in parts:
        text = part.strip()
        if text and not text.startswith("View on HumanGo"):
            blocks.append(text)
    return blocks


def _parse_block(block: str) -> WorkoutStepDTO | None:
    """Parse a single block into a WorkoutStep."""
    lines = [line.strip() for line in block.split("\n") if line.strip()]
    if not lines:
        return None

    # Find step type from first meaningful line
    step_type = ""
    for line in lines:
        low = line.lower()
        if low in STEP_TYPES:
            step_type = low
            break

    if not step_type:
        # No recognized step type — skip (could be intro text)
        return None

    text_block = "\n".join(lines)

    # Parse duration
    duration = 0
    dur_match = _DURATION_FULL.search(text_block)
    if dur_match:
        mins = int(dur_match.group(1) or 0)
        secs = int(dur_match.group(2) or 0)
        duration = mins * 60 + secs

    # Parse distance (for swim)
    distance = 0
    dist_match = _DISTANCE.search(text_block)
    if dist_match:
        distance = int(dist_match.group(1))

    # Parse targets
    hr = _parse_hr_target(text_block)
    power = _parse_power_target(text_block)
    pace = _parse_pace_target(text_block)

    # Map step type to display name
    display_names = {
        "warmup": "Warm-up",
        "interval": "Interval",
        "recovery": "Recovery",
        "cooldown": "Cool-down",
        "rest": "Rest",
    }

    step = WorkoutStepDTO(
        text=display_names.get(step_type, step_type.capitalize()),
        duration=duration if duration > 0 else (distance // 2 if distance else 0),
        hr=hr,
        power=power,
        pace=pace,
    )
    return step


def _parse_hr_target(text: str) -> dict | None:
    """Parse heart rate target from block text."""
    if "heart rate:" not in text.lower():
        return None
    low_m = _HR_LOW.search(text)
    high_m = _HR_HIGH.search(text)
    if low_m and high_m:
        low = float(low_m.group(1))
        high = float(high_m.group(1))
        mid = int((low + high) / 2)
        return {"units": "bpm", "value": mid, "low": int(low), "high": int(high)}
    return None


def _parse_power_target(text: str) -> dict | None:
    """Parse power target from block text."""
    if "power:" not in text.lower():
        return None
    low_m = _POWER_LOW.search(text)
    high_m = _POWER_HIGH.search(text)
    if low_m and high_m:
        low = float(low_m.group(1))
        high = float(high_m.group(1))
        mid = int((low + high) / 2)
        return {"units": "watts", "value": mid, "low": int(low), "high": int(high)}
    return None


def _parse_pace_target(text: str) -> dict | None:
    """Parse swim pace target from block text."""
    if "pace:" not in text.lower():
        return None
    low_m = _PACE_LOW.search(text)
    high_m = _PACE_HIGH.search(text)
    if low_m and high_m:
        # low pace = slower, high pace = faster (confusing but HumanGo convention)
        low_secs = int(low_m.group(1)) * 60 + int(low_m.group(2))
        high_secs = int(high_m.group(1)) * 60 + int(high_m.group(2))
        mid_secs = (low_secs + high_secs) // 2
        return {"units": "sec_per_100m", "value": mid_secs, "low": low_secs, "high": high_secs}
    return None


# ---------------------------------------------------------------------------
# Zone estimation from parsed steps
# ---------------------------------------------------------------------------


def estimate_step_zone(step: WorkoutStepDTO, ftp: float = 233, lthr: int = 153) -> int:
    """Estimate the training zone of a step based on its targets."""
    if step.power and "low" in step.power and "high" in step.power:
        return _power_to_zone(step.power["low"], step.power["high"], ftp)
    if step.hr and "low" in step.hr and "high" in step.hr:
        return _hr_to_zone(step.hr["low"], step.hr["high"], lthr)
    # No target or pace-only — assume Z2
    return 2


def estimate_workout_max_zone(steps: list[WorkoutStepDTO], ftp: float = 233, lthr: int = 153) -> int:
    """Estimate the maximum zone reached in a workout."""
    max_zone = 1
    for step in steps:
        if step.steps:  # repeat group
            for sub in step.steps:
                z = estimate_step_zone(sub, ftp, lthr)
                max_zone = max(max_zone, z)
        else:
            z = estimate_step_zone(step, ftp, lthr)
            max_zone = max(max_zone, z)
    return max_zone


# ---------------------------------------------------------------------------
# Adaptation constraints
# ---------------------------------------------------------------------------


def compute_constraints(
    recovery: RecoveryScoreDTO,
    hrv_status: str,
    tsb: float,
    ra: float | None = None,
) -> tuple[int, float]:
    """Compute max allowed zone and duration factor based on athlete state.

    Returns (max_zone, duration_factor) where:
    - max_zone: 1-5, highest allowed training zone
    - duration_factor: 0.75-1.0, multiplier for workout duration
    """
    max_zone = 5
    duration_factor = 1.0

    score = recovery.score
    category = recovery.category

    # TSB override — strongest constraint
    if tsb < -25:
        max_zone = min(max_zone, 2)
        duration_factor = min(duration_factor, 0.80)

    # HRV yellow/red
    if hrv_status == "red":
        max_zone = min(max_zone, 2)
        duration_factor = min(duration_factor, 0.75)
    elif hrv_status == "yellow":
        max_zone = min(max_zone, 3)
        duration_factor = min(duration_factor, 0.90)

    # Recovery category
    if category == "low" or score < 40:
        max_zone = min(max_zone, 2)
        duration_factor = min(duration_factor, 0.75)
    elif category == "moderate" or score < 70:
        max_zone = min(max_zone, 2)
        duration_factor = min(duration_factor, 0.85)
    elif category == "good" and hrv_status != "green":
        max_zone = min(max_zone, 3)
        duration_factor = min(duration_factor, 0.90)
    # excellent + green → no constraints (max_zone=5, factor=1.0)

    # Ra consecutive decline
    if ra is not None and ra < -5:
        max_zone = min(max_zone, max_zone - 1) if max_zone > 1 else 1
        duration_factor = min(duration_factor, duration_factor - 0.05)

    return max_zone, max(duration_factor, 0.50)  # floor at 50%


# ---------------------------------------------------------------------------
# Adaptation decision
# ---------------------------------------------------------------------------


def needs_adaptation(
    steps: list[WorkoutStepDTO],
    max_zone: int,
    ftp: float = 233,
    lthr: int = 153,
) -> bool:
    """Check if the workout exceeds the allowed zone constraints."""
    workout_max = estimate_workout_max_zone(steps, ftp, lthr)
    return workout_max > max_zone
