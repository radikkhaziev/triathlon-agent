"""Dramatiq actors — athlete settings (sync thresholds, update zones)."""

import logging
from datetime import date, timedelta

import dramatiq
from pydantic import validate_call

from data.db import AthleteGoal, AthleteSettings, User, UserDTO
from data.intervals.client import IntervalsSyncClient
from data.intervals.dto import ScheduledWorkoutDTO, SportSettingsDTO
from tasks.dto import DateDTO

from ..tools import TelegramTool

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_send_zones_notification(user: UserDTO, updated: list[str]):
    """Send Telegram notification about updated zones."""
    tg = TelegramTool(user=user)
    if updated:
        msg = "✅ Зоны обновлены:\n" + "\n".join(updated)
    else:
        msg = "ℹ️ Drift не обнаружен, зоны актуальны"
    tg.send_message(text=msg)


@dramatiq.actor(queue_name="default")
@validate_call
def actor_sync_athlete_settings(
    user: UserDTO,
    sport_settings: list[SportSettingsDTO] | None = None,
):
    """Sync sport settings from Intervals.icu → athlete_settings table.

    If ``sport_settings`` is provided (e.g. from webhook payload), skips the API call.
    """
    if sport_settings is None:
        with IntervalsSyncClient.for_user(user) as client:
            all_settings = client.list_sport_settings()
    else:
        all_settings = sport_settings

    for ss in all_settings:
        # Map types to primary sport: ["Ride", "VirtualRide", ...] → "Ride"
        primary = ss.types[0] if ss.types else None
        if primary not in ("Ride", "Run", "Swim"):
            continue

        # Convert threshold_pace from m/s (Intervals.icu API) to seconds:
        # Swim → sec/100m, Run → sec/km
        pace = ss.threshold_pace
        if pace and pace > 0:
            if primary == "Swim":
                pace = round(100 / pace, 1)  # m/s → sec/100m
            elif primary == "Run":
                pace = round(1000 / pace, 1)  # m/s → sec/km

        AthleteSettings.upsert(
            user_id=user.id,
            sport=primary,
            lthr=ss.lthr,
            max_hr=ss.max_hr,
            ftp=ss.ftp,
            threshold_pace=pace,
            pace_units=ss.pace_units,
            hr_zones=ss.hr_zones,
            hr_zone_names=ss.hr_zone_names,
            power_zones=ss.power_zones,
            power_zone_names=ss.power_zone_names,
            pace_zones=ss.pace_zones,
            pace_zone_names=ss.pace_zone_names,
        )
        logger.info("Synced athlete_settings %s for user %d", primary, user.id)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_send_goal_notification(
    user: UserDTO,
    event_name: str,
    event_date: DateDTO,
    category: str,
):
    """Notify user about a new goal synced from Intervals."""
    tg = TelegramTool(user=user)
    tg.send_message(text=f"🏁 Новая цель: {event_name} ({category}), {event_date.isoformat()}")


@dramatiq.actor(queue_name="default")
@validate_call
def actor_sync_athlete_goals(user: UserDTO):
    """Sync RACE_A/B/C events from Intervals.icu → athlete_goals table."""
    today = date.today()
    # Intervals.icu API drops category-filtered results unless newest is set explicitly —
    # reproduced on 2026-04-20 with user 5's RACE_A "Drina trail" 2026-05-05: without newest,
    # `?category=RACE_A` returns []; with newest ~2 years out it returns the event.
    newest = today + timedelta(days=2 * 365)

    existing_ids = {g.intervals_event_id for g in AthleteGoal.get_all(user.id)}
    new_event: ScheduledWorkoutDTO | None = None
    new_category: str | None = None

    with IntervalsSyncClient.for_user(user) as client:
        for category in ("RACE_A", "RACE_B", "RACE_C"):
            events: list[ScheduledWorkoutDTO] = client.get_events(oldest=today, newest=newest, category=category)
            if not events:
                continue
            event = events[0]
            if event.id not in existing_ids:
                new_event = event
                new_category = category
            AthleteGoal.upsert_from_intervals(
                user_id=user.id,
                category=category,
                event_name=event.name or f"{category} event",
                event_date=event.start_date_local,
                intervals_event_id=event.id,
            )
            logger.info("Synced goal %s for user %d: %s %s", category, user.id, event.name, event.start_date_local)

    if new_event:
        _actor_send_goal_notification.send(
            user=user,
            event_name=new_event.name or new_category,
            event_date=new_event.start_date_local,
            category=new_category,
        )


@dramatiq.actor(queue_name="default")
@validate_call
def actor_update_zones(user: UserDTO):
    """Read threshold drift, update athlete_settings + Intervals.icu, notify user."""
    drift = User.detect_threshold_drift(user_id=user.id)
    if not drift:
        logger.info("No threshold drift for user %d", user.id)
        return

    updated: list[str] = []

    with IntervalsSyncClient.for_user(user) as client:
        for alert in drift.alerts:
            new_lthr = alert.measured_avg
            old_lthr = alert.config_value
            sport = alert.sport

            AthleteSettings.upsert(user_id=user.id, sport=sport, lthr=new_lthr)
            client.update_sport_settings(sport, {"lthr": new_lthr})

            updated.append(f"LTHR {sport}: {old_lthr} → {new_lthr} bpm")
            logger.info("Updated LTHR %s for user %d: %d → %d", sport, user.id, old_lthr, new_lthr)

    _actor_send_zones_notification.send(user, updated)
