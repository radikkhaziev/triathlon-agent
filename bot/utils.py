"""Helper functions for bot scheduler — no Telegram/Bot dependencies."""

import asyncio
import logging
from datetime import date, timedelta

from ai.claude_agent import ClaudeAgent
from config import settings
from data.database import (
    ActivityDetailRow,
    ActivityHrvRow,
    ActivityRow,
    AiWorkoutRow,
    HrvAnalysisRow,
    RhrAnalysisRow,
    ScheduledWorkoutRow,
    TrainingLogRow,
)
from data.intervals_client import IntervalsClient
from data.models import RecoveryScore, ScheduledWorkout
from data.ramp_tests import create_ramp_test, get_threshold_freshness_data, should_suggest_ramp
from data.workout_adapter import adapt_workout

logger = logging.getLogger(__name__)

# Canonical sport name → Intervals.icu activity type
_CANONICAL_TO_TYPE = {"swim": "Swim", "bike": "Ride", "run": "Run"}


def enrich_sport_info(wellness, sport_ctl: dict[str, float]) -> None:
    """Merge per-sport CTL into wellness.sport_info before persistence."""
    existing_info = list(wellness.sport_info) if wellness.sport_info else []
    existing_types = {(e.get("type") or "").lower(): i for i, e in enumerate(existing_info)}

    for canonical, ctl_val in sport_ctl.items():
        if ctl_val < 0:
            continue
        iv_type = _CANONICAL_TO_TYPE[canonical]
        iv_type_lower = iv_type.lower()
        if iv_type_lower in existing_types:
            existing_info[existing_types[iv_type_lower]]["ctl"] = ctl_val
        else:
            existing_info.append({"type": iv_type, "ctl": ctl_val})

    if existing_info:
        wellness.sport_info = existing_info


async def get_latest_ra(dt: date) -> float | None:
    """Get the latest Ra (readiness) from yesterday's DFA analysis."""
    yesterday = dt - timedelta(days=1)
    hrv_analyses = await ActivityHrvRow.get_for_date(yesterday)
    for h in hrv_analyses:
        if h.ra_pct is not None:
            return h.ra_pct
    return None


async def compute_max_zone(activity_id: str, sport: str | None = None) -> str | None:
    """Determine the zone where the athlete spent the most time.

    Returns "Z1".."Z5" or None if no zone data available.
    Priority: Ride→power_zone_times, Swim→pace_zone_times, else→hr_zone_times.
    """
    detail = await ActivityDetailRow.get(activity_id)
    if not detail:
        return None

    zones = None
    if sport == "Ride" and detail.power_zone_times:
        zones = detail.power_zone_times
    elif sport == "Swim" and detail.pace_zone_times:
        zones = detail.pace_zone_times

    if not zones and detail.hr_zone_times:
        zones = detail.hr_zone_times

    if not zones:
        return None

    # Intervals.icu returns 6+ elements: [below_z1, z1, z2, z3, z4, z5]
    # or 5 elements: [z1, z2, z3, z4, z5]
    if len(zones) >= 6:
        zone_values = zones[1:6]
    elif len(zones) == 5:
        zone_values = zones[:5]
    else:
        return None

    if all(v == 0 for v in zone_values):
        return None

    # Tie-break: prefer lower zone (Z2 over Z4 at equal time)
    max_idx = min(range(len(zone_values)), key=lambda i: (-zone_values[i], i))
    return f"Z{max_idx + 1}"


def detect_compliance(log, activity) -> str:
    """Detect which plan variant the athlete followed."""
    if log.source == "none":
        return "unplanned"

    actual_dur = activity.moving_time or 0

    # Check adapted match
    if log.adapted_duration_sec:
        adapted_ratio = actual_dur / log.adapted_duration_sec if log.adapted_duration_sec else 0
        if 0.7 <= adapted_ratio <= 1.3:
            return "followed_adapted"

    # Check original match
    if log.original_duration_sec:
        original_ratio = actual_dur / log.original_duration_sec if log.original_duration_sec else 0
        if 0.7 <= original_ratio <= 1.3:
            if log.source == "ai":
                return "followed_ai"
            return "followed_original"

    return "modified"


async def fetch_missing_details(intervals: IntervalsClient, activity_ids: list[str]) -> int:
    """Fetch and save activity details for IDs that lack an activity_details row.

    Returns count of details fetched.
    """
    existing_ids = await ActivityDetailRow.get_existing_ids(activity_ids)
    missing_ids = [aid for aid in activity_ids if aid not in existing_ids]
    if not missing_ids:
        return 0

    fetched = 0
    for i, aid in enumerate(missing_ids):
        try:
            detail = await intervals.get_activity_detail(aid)
            if detail is None:
                logger.debug("Activity %s not found (404), skipping", aid)
                continue

            try:
                intervals_data = await intervals.get_activity_intervals(aid)
            except Exception:
                logger.warning("Failed to fetch intervals for %s, saving detail only", aid)
                intervals_data = None

            await ActivityDetailRow.save(aid, detail, intervals_data)
            fetched += 1
            logger.debug("Fetched details for activity %s", aid)
        except Exception:
            logger.warning("Failed to fetch details for activity %s", aid, exc_info=True)

        if i < len(missing_ids) - 1:
            await asyncio.sleep(1)

    if fetched:
        logger.info("Fetched details for %d new activities", fetched)
    return fetched


# ---------------------------------------------------------------------------
# Training log lifecycle (pre / actual / post)
# ---------------------------------------------------------------------------


async def record_training_log_pre(wellness_row, dt: date) -> None:
    """Record pre-workout context in training_log for today."""
    existing = await TrainingLogRow.get_for_date(dt)
    if existing:
        return

    workouts = await ScheduledWorkoutRow.get_for_date(dt)
    ai_workouts = await AiWorkoutRow.get_for_date(dt)

    hrv_flatt = await HrvAnalysisRow.get(str(dt), "flatt_esco")
    rhr_row = await RhrAnalysisRow.get(str(dt))

    hrv_status = hrv_flatt.status if hrv_flatt else "insufficient_data"
    hrv_7d = hrv_flatt.rmssd_7d if hrv_flatt else 0
    hrv_today = float(wellness_row.hrv) if wellness_row.hrv else 0
    hrv_delta = ((hrv_today - hrv_7d) / hrv_7d * 100) if hrv_today and hrv_7d else 0.0
    tsb = (wellness_row.ctl - wellness_row.atl) if wellness_row.ctl and wellness_row.atl else 0
    ra = await get_latest_ra(dt)

    pre_kwargs = dict(
        pre_recovery_score=wellness_row.recovery_score,
        pre_recovery_category=wellness_row.recovery_category,
        pre_hrv_status=hrv_status,
        pre_hrv_delta_pct=round(hrv_delta, 1),
        pre_rhr_today=rhr_row.rhr_today if rhr_row else None,
        pre_rhr_status=rhr_row.status if rhr_row else None,
        pre_tsb=round(tsb, 1),
        pre_ctl=wellness_row.ctl,
        pre_atl=wellness_row.atl,
        pre_ra_pct=ra,
        pre_sleep_score=wellness_row.sleep_score,
    )

    if workouts:
        for w in workouts:
            adapted = next(
                (a for a in ai_workouts if a.sport == w.type and "adapted" in (a.rationale or "").lower()),
                None,
            )
            await TrainingLogRow.create(
                date=str(dt),
                sport=w.type,
                source="adapted" if adapted else "humango",
                original_name=w.name,
                original_description=(w.description or "")[:500],
                original_duration_sec=w.moving_time,
                adapted_name=adapted.name if adapted else None,
                adapted_description=adapted.description if adapted else None,
                adapted_duration_sec=(adapted.duration_minutes * 60) if adapted else None,
                adaptation_reason=adapted.rationale if adapted else None,
                **pre_kwargs,
            )
    elif ai_workouts:
        for a in ai_workouts:
            await TrainingLogRow.create(
                date=str(dt),
                sport=a.sport,
                source="ai",
                original_name=a.name,
                original_duration_sec=(a.duration_minutes or 0) * 60,
                **pre_kwargs,
            )
    else:
        await TrainingLogRow.create(
            date=str(dt),
            source="none",
            **pre_kwargs,
        )

    logger.info("Training log pre-context recorded for %s", dt)


async def fill_training_log_post(wellness_row, dt: date) -> None:
    """Fill post-outcome for yesterday's training_log entry using today's wellness."""
    yesterday = dt - timedelta(days=1)
    unfilled = await TrainingLogRow.get_unfilled_post()
    targets = [r for r in unfilled if r.date == str(yesterday)]

    if not targets:
        return

    hrv_flatt = await HrvAnalysisRow.get(str(dt), "flatt_esco")
    hrv_7d = hrv_flatt.rmssd_7d if hrv_flatt else 0
    hrv_today = float(wellness_row.hrv) if wellness_row.hrv else 0
    hrv_delta = ((hrv_today - hrv_7d) / hrv_7d * 100) if hrv_today and hrv_7d else 0.0
    rhr_row = await RhrAnalysisRow.get(str(dt))
    ra = await get_latest_ra(dt)

    for log in targets:
        pre_score = log.pre_recovery_score or 0
        post_score = wellness_row.recovery_score or 0

        await TrainingLogRow.update(
            log.id,
            post_recovery_score=post_score,
            post_hrv_delta_pct=round(hrv_delta, 1),
            post_rhr_today=rhr_row.rhr_today if rhr_row else None,
            post_sleep_score=wellness_row.sleep_score,
            post_ra_pct=ra,
            recovery_delta=round(post_score - pre_score, 1),
        )

    logger.info("Training log post-outcome filled for %s (%d entries)", yesterday, len(targets))


async def fill_training_log_actual() -> None:
    """Fill actual workout data for training_log entries that have no compliance yet."""
    unfilled = await TrainingLogRow.get_unfilled_actual()
    logger.info("Training log actual: %d unfilled entries", len(unfilled))
    if not unfilled:
        return

    filled_count = 0
    for log in unfilled:
        log_date = date.fromisoformat(log.date)
        logger.info(
            "Training log #%d: date=%s sport=%s name=%s",
            log.id,
            log.date,
            log.sport,
            log.original_name,
        )
        activities = await ActivityRow.get_for_date(log_date)
        logger.info(
            "Training log #%d: found %d activities for %s: %s",
            log.id,
            len(activities),
            log.date,
            [(a.id, a.type, a.moving_time) for a in activities],
        )

        if not activities:
            logger.info("Training log #%d: no activities, marking skipped", log.id)
            await TrainingLogRow.update(log.id, compliance="skipped")
            filled_count += 1
            continue

        matched = None
        if log.sport:
            matched = next((a for a in activities if a.type == log.sport), None)
            logger.info(
                "Training log #%d: sport match '%s' → %s",
                log.id,
                log.sport,
                matched.id if matched else "no match",
            )
        if not matched:
            matched = activities[0]
            logger.info("Training log #%d: fallback to first activity %s", log.id, matched.id)

        compliance = detect_compliance(log, matched)
        logger.info(
            "Training log #%d: compliance=%s (activity=%s, sport=%s, duration=%s, hr=%s)",
            log.id,
            compliance,
            matched.id,
            matched.type,
            matched.moving_time,
            matched.average_hr,
        )

        max_zone = await compute_max_zone(matched.id, sport=matched.type)

        await TrainingLogRow.update(
            log.id,
            actual_activity_id=matched.id,
            actual_sport=matched.type,
            actual_duration_sec=matched.moving_time,
            actual_avg_hr=matched.average_hr,
            actual_tss=matched.icu_training_load,
            actual_max_zone_time=max_zone,
            compliance=compliance,
        )
        filled_count += 1

    if filled_count:
        logger.info("Training log actual filled: %d entries", filled_count)


# ---------------------------------------------------------------------------
# Workout generation / adaptation / ramp tests
# ---------------------------------------------------------------------------


async def generate_and_push_workout(wellness_row, dt: date, bot=None) -> None:
    """Generate or adapt a workout and push it to Intervals.icu.

    Phase 1: if no planned workout → AI generates from scratch (suffix=generated)
    Phase 2: if planned workout exists → adapt if recovery requires it (suffix=adapted)
    """
    hrv_flatt = await HrvAnalysisRow.get(str(dt), "flatt_esco")
    rhr_row = await RhrAnalysisRow.get(str(dt))
    hrv_status = hrv_flatt.status if hrv_flatt else "insufficient_data"
    tsb = (wellness_row.ctl - wellness_row.atl) if wellness_row.ctl and wellness_row.atl else 0

    existing_workouts = await ScheduledWorkoutRow.get_for_date(dt)

    if existing_workouts:
        # Phase 2: try to adapt existing workout
        if wellness_row.recovery_category in ("excellent", "good") and hrv_status == "green":
            logger.info("No adaptation needed — recovery %s, HRV green", wellness_row.recovery_category)
            return

        recovery = RecoveryScore(
            score=wellness_row.recovery_score or 50,
            category=wellness_row.recovery_category or "moderate",
            recommendation=wellness_row.recovery_recommendation or "",
        )

        ra = await get_latest_ra(dt)

        for w_row in existing_workouts:
            if not w_row.description:
                continue
            original = ScheduledWorkout(
                id=w_row.id,
                start_date_local=dt,
                name=w_row.name,
                type=w_row.type,
                description=w_row.description,
                moving_time=w_row.moving_time,
            )
            workout = adapt_workout(
                original,
                recovery,
                hrv_status,
                tsb,
                ra,
                ftp=settings.ATHLETE_FTP,
                lthr=settings.ATHLETE_LTHR_RUN,
            )
            if workout:
                await push_workout(workout, dt, bot=bot)
                return

        logger.info("No adaptation needed for planned workouts on %s", dt)
        return

    # Phase 1: no planned workout → generate from scratch
    if wellness_row.recovery_category == "low":
        logger.info("Skipping AI workout generation — recovery is low for %s", dt)
        return

    hrv_aie = await HrvAnalysisRow.get(str(dt), "ai_endurance")
    agent = ClaudeAgent()
    workout = await agent.generate_workout(wellness_row, hrv_flatt, hrv_aie, rhr_row)

    if workout is None:
        logger.info("AI recommended rest day for %s", dt)
        return

    await push_workout(workout, dt, bot=bot)


async def push_workout(workout, dt: date, bot=None) -> None:
    """Push a PlannedWorkout to Intervals.icu and save to local DB."""
    existing = await AiWorkoutRow.get_by_external_id(workout.external_id)
    if existing and existing.status == "active":
        logger.info("Workout already exists: %s", workout.external_id)
        return

    intervals = IntervalsClient()
    event_data = workout.to_intervals_event()
    result = await intervals.create_event(event_data)
    intervals_id = result.get("id")

    await AiWorkoutRow.save(
        date_str=str(dt),
        sport=workout.sport,
        slot=workout.slot,
        external_id=workout.external_id,
        intervals_id=intervals_id,
        name=workout.name,
        description="; ".join(s.text for s in workout.steps if s.text),
        duration_minutes=workout.duration_minutes,
        target_tss=workout.target_tss,
        rationale=workout.rationale,
    )
    logger.info(
        "Workout pushed: AI: %s (%s) (%s, %d min) for %s",
        workout.name,
        workout.suffix,
        workout.sport,
        workout.duration_minutes,
        dt,
    )

    # Send Telegram notification
    if bot is not None:
        try:
            from bot.formatter import build_workout_pushed_message

            msg = build_workout_pushed_message(
                sport=workout.sport,
                name=workout.name,
                duration_minutes=workout.duration_minutes,
                target_tss=workout.target_tss,
                suffix=workout.suffix,
                intervals_id=intervals_id,
                athlete_id=settings.INTERVALS_ATHLETE_ID,
                target_date=dt,
            )
            await bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=msg)
        except Exception:
            logger.warning("Failed to send workout notification", exc_info=True)


async def maybe_suggest_ramp(wellness_row, dt: date) -> None:
    """Suggest a ramp test if thresholds are stale and athlete is ready."""
    tsb = (wellness_row.ctl - wellness_row.atl) if wellness_row.ctl and wellness_row.atl else 0
    sport = await should_suggest_ramp(
        recovery_score=wellness_row.recovery_score or 0,
        recovery_category=wellness_row.recovery_category or "moderate",
        tsb=tsb,
    )
    if not sport:
        return

    tomorrow = dt + timedelta(days=1)
    planned = await ScheduledWorkoutRow.get_for_date(tomorrow)
    if planned:
        logger.info("Skipping ramp suggestion — workout planned for %s", tomorrow)
        return

    upcoming = await AiWorkoutRow.get_upcoming(days_ahead=14)
    if any("Ramp Test" in (w.name or "") for w in upcoming):
        return

    freshness = await get_threshold_freshness_data(sport)
    days_since = freshness.get("days_since") or 0

    workout = create_ramp_test(sport, tomorrow, days_since)
    await push_workout(workout, tomorrow)
    logger.info("Ramp test suggested: %s on %s (thresholds %d days old)", sport, tomorrow, days_since)
