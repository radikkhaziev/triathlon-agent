"""Workout adaptation engine (ATP Phase 2).

Parses HumanGo workout descriptions, evaluates adaptation constraints,
and produces modified PlannedWorkout for Intervals.icu.
"""

import logging
import re

from data.db import AthleteThresholdsDTO
from data.intervals.dto import RecoveryScoreDTO, WorkoutStepDTO

logger = logging.getLogger(__name__)

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
# Run pace — HumanGo emits sec/km for run intervals (e.g. `low: 6:33 per km`).
# Distinct regex from Swim (`per 100 meters`) to avoid cross-sport false matches.
# Word boundary after `km` excludes false positives like `per kmh` (theoretical
# but HumanGo never emits this; defensive only).
_PACE_LOW_KM = re.compile(r"low:\s*(\d+):(\d+)\s*per\s*km\b", re.IGNORECASE)
_PACE_HIGH_KM = re.compile(r"high:\s*(\d+):(\d+)\s*per\s*km\b", re.IGNORECASE)

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

    # TSB override — strongest constraint. Fires only in the `risk` zone
    # (TSB < -30) per the 5-band model; see `data/utils.py:tsb_zone`.
    if tsb < -30:
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


# ---------------------------------------------------------------------------
# HumanGo → Intervals.icu structured-steps enrichment
# (see docs/HUMANGO_ENRICHMENT_SPEC.md)
# ---------------------------------------------------------------------------

_HUMANGO_SIGNATURE = "View on HumanGo"


def is_humango_event(description: str | None, workout_doc: dict | None) -> bool:
    """Decide whether a calendar event came from HumanGo and is ready for enrichment.

    Three AND'd checks; any negative → caller must skip the event:

    1. ``View on HumanGo`` signature in the description (unique to HumanGo's
       shared-calendar push; no other integration writes this string).
    2. ``==========`` separator present — defensive: HumanGo's «rest day» /
       RPE-only entries carry the View-link but no structured blocks, so
       parsing would yield an empty step list.
    3. ``workout_doc.steps`` empty / absent — idempotency. If we (or anyone)
       already populated structured steps, don't overwrite.
    """
    if not description or _HUMANGO_SIGNATURE not in description:
        return False
    if "==========" not in description:
        return False
    if workout_doc and workout_doc.get("steps"):
        return False
    return True


def _humango_hr_pct(low_bpm: float, high_bpm: float, lthr: int) -> dict | None:
    """Convert HumanGo absolute HR (bpm low/high) to ``%lthr`` corridor.

    Round-trip: pushed ``%lthr`` × athlete's LTHR ≈ HumanGo's original bpm
    (within ±1 bpm rounding). Watches see the original corridor verbatim
    after Intervals.icu FIT export.
    """
    if not lthr or lthr <= 0:
        return None
    start = round(low_bpm / lthr * 100)
    end = round(high_bpm / lthr * 100)
    return {"units": "%lthr", "start": start, "end": end}


def _humango_power_pct(low_w: float, high_w: float, ftp: int) -> dict | None:
    """Convert HumanGo absolute power (W low/high) to ``%ftp`` corridor."""
    if not ftp or ftp <= 0:
        return None
    start = round(low_w / ftp * 100)
    end = round(high_w / ftp * 100)
    return {"units": "%ftp", "start": start, "end": end}


def _humango_pace_pct(low_sec: int, high_sec: int, threshold_sec: float) -> dict | None:
    """Convert a HumanGo pace corridor (low=slower / high=faster) to ``%pace``.

    Unit-agnostic — caller passes matching units (Swim: sec/100m vs CSS;
    Run: sec/km vs threshold_pace_run). Intervals' ``%pace`` is a velocity
    ratio (100 = threshold velocity, faster = higher %). HumanGo's «low»
    pace is slower (more sec per unit distance), so it maps to the LOWER
    velocity-ratio bound; «high» pace is faster, maps to the HIGHER bound.
    Result: ``start < end`` preserves the corridor direction.
    """
    if not threshold_sec or threshold_sec <= 0:
        return None
    if not low_sec or not high_sec:
        return None
    start = round(threshold_sec / low_sec * 100)  # slower → lower velocity %
    end = round(threshold_sec / high_sec * 100)  # faster → higher velocity %
    return {"units": "%pace", "start": start, "end": end}


def _humango_target_for_step(
    text_block: str,
    sport: str,
    thresholds: AthleteThresholdsDTO,
) -> tuple[str | None, dict | None]:
    """Pick the right target type for a HumanGo step block based on sport + content.

    Returns ``(target_key, target_dict)`` where ``target_key`` is one of
    ``"hr"``/``"power"``/``"pace"`` or ``None`` when no parseable target is
    found OR the relevant threshold is missing. ``target_dict`` follows the
    ``{units: "%X", start, end}`` schema validated in
    ``WORKOUT_ABSOLUTE_TARGETS_SPEC.md`` §12 Attempt 3b.
    """
    if sport == "Ride":
        low_m = _POWER_LOW.search(text_block)
        high_m = _POWER_HIGH.search(text_block)
        if low_m and high_m:
            # Audit: if a Ride block ever carries BOTH power and HR, prefer
            # power but log the existence so we can sample real frequency.
            if _HR_LOW.search(text_block) and _HR_HIGH.search(text_block):
                logger.info("HumanGo Ride block has both power and HR targets — preferring power")
            target = _humango_power_pct(float(low_m.group(1)), float(high_m.group(1)), thresholds.ftp or 0)
            return ("power", target) if target else (None, None)
        # Fall through to HR (some HumanGo rides emit HR, not power)

    if sport in ("Run", "Ride"):
        low_m = _HR_LOW.search(text_block)
        high_m = _HR_HIGH.search(text_block)
        if low_m and high_m:
            lthr = thresholds.lthr_run if sport == "Run" else thresholds.lthr_bike
            target = _humango_hr_pct(float(low_m.group(1)), float(high_m.group(1)), lthr or 0)
            return ("hr", target) if target else (None, None)

    # Run pace targets — HumanGo regularly emits run intervals with sec/km
    # corridors (e.g. tempo / threshold sessions). Without this branch, such
    # blocks come through target-less and watches alert on nothing. The
    # `%pace` schema is the same as Swim — only the threshold differs
    # (`threshold_pace_run` is sec/km, not sec/100m). HR check above takes
    # precedence when both signals are present in the block (HR is the more
    # universal Run target; pace breaks when GPS is poor or treadmill).
    if sport == "Run":
        low_m = _PACE_LOW_KM.search(text_block)
        high_m = _PACE_HIGH_KM.search(text_block)
        if low_m and high_m:
            low_sec = int(low_m.group(1)) * 60 + int(low_m.group(2))
            high_sec = int(high_m.group(1)) * 60 + int(high_m.group(2))
            target = _humango_pace_pct(low_sec, high_sec, thresholds.threshold_pace_run or 0)
            return ("pace", target) if target else (None, None)

    if sport == "Swim":
        low_m = _PACE_LOW.search(text_block)
        high_m = _PACE_HIGH.search(text_block)
        if low_m and high_m:
            low_sec = int(low_m.group(1)) * 60 + int(low_m.group(2))
            high_sec = int(high_m.group(1)) * 60 + int(high_m.group(2))
            target = _humango_pace_pct(low_sec, high_sec, thresholds.css or 0)
            return ("pace", target) if target else (None, None)

    return None, None


def _humango_parse_block_for_enrichment(
    block: str, sport: str, thresholds: AthleteThresholdsDTO
) -> WorkoutStepDTO | None:
    """Parse a single HumanGo block into a corridor-schema ``WorkoutStepDTO``.

    Mirrors ``_parse_block`` for step-type / duration / distance extraction
    but emits the production ``{units, start, end}`` target shape instead of
    the legacy ``{units, value, low, high}`` shape used by compliance code.
    """
    lines = [line.strip() for line in block.split("\n") if line.strip()]
    if not lines:
        return None

    step_type = ""
    for line in lines:
        low = line.lower()
        if low in STEP_TYPES:
            step_type = low
            break
    if not step_type:
        return None

    text_block = "\n".join(lines)

    duration = 0
    dur_match = _DURATION_FULL.search(text_block)
    if dur_match:
        mins = int(dur_match.group(1) or 0)
        secs = int(dur_match.group(2) or 0)
        duration = mins * 60 + secs

    distance = 0
    dist_match = _DISTANCE.search(text_block)
    if dist_match:
        distance = int(dist_match.group(1))

    target_key, target = _humango_target_for_step(text_block, sport, thresholds)

    display_names = {
        "warmup": "Warm-up",
        "interval": "Interval",
        "recovery": "Recovery",
        "cooldown": "Cool-down",
        "rest": "Rest",
    }

    return WorkoutStepDTO(
        text=display_names.get(step_type, step_type.capitalize()),
        duration=duration if duration > 0 else 0,
        distance=distance if distance > 0 else None,
        hr=target if target_key == "hr" else None,
        power=target if target_key == "power" else None,
        pace=target if target_key == "pace" else None,
    )


def humango_to_intervals_steps(
    description: str,
    sport: str,
    thresholds: AthleteThresholdsDTO,
) -> list[WorkoutStepDTO] | None:
    """Parse a HumanGo workout description and emit Intervals.icu-ready steps.

    Returns ``None`` when the caller must skip pushing — either the converter
    cannot run, or it ran but found nothing pushable:
    - Sport not in ``{Run, Ride, Swim}`` (HumanGo doesn't push these structured).
    - Threshold for the sport is missing / zero (LTHR or threshold_pace_run for
      Run, FTP or LTHR for Ride, CSS for Swim) — cold-start athlete, see SPEC §6.
    - Description parsed cleanly but yielded zero steps (defensive — in the
      normal call path ``is_humango_event`` already gates on the ``==========``
      separator, so empty-but-valid is unreachable from the actor).

    A non-empty ``list[WorkoutStepDTO]`` is returned only when there's actually
    something to push.
    """
    if sport not in ("Run", "Ride", "Swim"):
        logger.info("HumanGo enrichment skipped: sport=%s not in {Run, Ride, Swim}", sport)
        return None

    # Cold-start guard: each sport needs at least one usable threshold.
    # Run accepts either LTHR (for HR-driven blocks) or threshold_pace_run
    # (for pace-driven blocks) — empirically HumanGo emits one or the other
    # per workout, not both.
    if sport == "Run" and not (
        (thresholds.lthr_run and thresholds.lthr_run > 0)
        or (thresholds.threshold_pace_run and thresholds.threshold_pace_run > 0)
    ):
        logger.info("HumanGo enrichment skipped: missing LTHR and threshold_pace for Run")
        return None
    if sport == "Ride" and not (
        (thresholds.ftp and thresholds.ftp > 0) or (thresholds.lthr_bike and thresholds.lthr_bike > 0)
    ):
        logger.info("HumanGo enrichment skipped: missing FTP and LTHR for Ride")
        return None
    if sport == "Swim" and not (thresholds.css and thresholds.css > 0):
        logger.info("HumanGo enrichment skipped: missing CSS for Swim")
        return None

    blocks = _split_into_blocks(description)
    steps: list[WorkoutStepDTO] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        repeat_match = _REPEAT.search(block)
        if repeat_match:
            reps = int(repeat_match.group(1))
            sub_steps: list[WorkoutStepDTO] = []
            i += 1
            while i < len(blocks):
                if _REPEAT.search(blocks[i]):
                    break
                sub = _humango_parse_block_for_enrichment(blocks[i], sport, thresholds)
                if sub:
                    step_kind = sub.text.lower().replace("-", "")
                    if step_kind in ("cooldown", "cool down"):
                        break  # cooldown belongs to outer level
                    sub_steps.append(sub)
                i += 1
            if sub_steps:
                # HumanGo sometimes emits `repeat 1 times` to wrap a single
                # interval+recovery pair. A single-rep group adds zero value
                # in the Intervals UI / Garmin watch view — flatten it to
                # plain sequential steps so the calendar reads cleanly.
                if reps == 1:
                    steps.extend(sub_steps)
                else:
                    first_kind = sub_steps[0].text
                    steps.append(WorkoutStepDTO(text=f"{reps}x {first_kind}", reps=reps, steps=sub_steps))
            continue

        step = _humango_parse_block_for_enrichment(block, sport, thresholds)
        if step:
            steps.append(step)
        i += 1

    if not steps:
        return None

    # Fail-closed guard: cold-start check at function entry verifies «at least
    # one threshold present», but a mismatched-pair case can still produce
    # target-less steps — e.g. athlete has `lthr_run` set but HumanGo emits
    # pace-only Run blocks (or symmetric: only `threshold_pace_run` set,
    # description carries HR). The granular threshold check inside
    # `_humango_target_for_step` returns `None` for the missing pair, but the
    # step still gets emitted (with `hr=power=pace=None`). Pushing such steps
    # would lock out future re-enrichment via `is_humango_event`'s idempotency
    # guard (which only checks `workout_doc.steps` non-empty, not target presence).
    # Drop the whole workout so the next sync tick can retry once thresholds
    # are updated.
    if all(_step_is_target_less(s) for s in steps):
        logger.info(
            "HumanGo enrichment skipped: parsed %d step(s) but none carry "
            "hr/power/pace — likely description/threshold sport-target mismatch",
            len(steps),
        )
        return None

    return steps


def _step_is_target_less(s: WorkoutStepDTO) -> bool:
    """A step has no actionable target. For repeat groups, check sub-steps."""
    if s.reps and s.steps:
        return all(_step_is_target_less(sub) for sub in s.steps)
    return s.hr is None and s.power is None and s.pace is None
