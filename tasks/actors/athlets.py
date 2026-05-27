"""Dramatiq actors — athlete settings (sync thresholds, update zones)."""

import logging
from datetime import timedelta

import dramatiq
from pydantic import validate_call

from bot.i18n import _, set_language
from data.db import AthleteGoal, AthleteSettings, User, UserDTO
from data.intervals.client import IntervalsAccessError, IntervalsSyncClient
from data.intervals.dto import ScheduledWorkoutDTO, SportSettingsDTO
from data.sport_map import resolve_race_sport_type
from tasks.dto import DateDTO, local_today
from tasks.formatter import format_pace

from ..tools import TelegramTool
from .common import is_user_dormant

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default")
@validate_call
def _actor_send_zones_notification(user: UserDTO, updated: list[str]):
    """Send Telegram notification about updated zones."""
    set_language(user.language or "ru")
    tg = TelegramTool(user=user)
    if updated:
        msg = f"✅ {_('Зоны обновлены')}:\n" + "\n".join(updated)
    else:
        msg = f"ℹ️ {_('Drift не обнаружен, зоны актуальны')}"
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
        try:
            with IntervalsSyncClient.for_user(user) as client:
                all_settings = client.list_sport_settings()
        except IntervalsAccessError as e:
            # 401 (token revoked) / 403 (scope revoked) / missing-creds — any of
            # them means we can't sync. Skip cleanly so Dramatiq doesn't
            # retry-loop. Note: the `try` wraps `for_user(...)` itself because
            # missing-creds raises *before* the context manager body runs.
            logger.info("Skipping settings sync for user %d: %s", user.id, e)
            return
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

        # MMP model only on Ride sport_settings — Run/Swim payloads omit the block.
        mmp = ss.mmp_model if primary == "Ride" else None

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
            critical_power=mmp.critical_power if mmp else None,
            w_prime=mmp.w_prime if mmp else None,
            p_max=mmp.p_max if mmp else None,
            mmp_ftp=mmp.ftp if mmp else None,
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
def actor_sync_athlete_goals(user: UserDTO, force_inactive: bool = False):
    """Sync RACE_A/B/C events from Intervals.icu → athlete_goals table.

    Athletes routinely have multiple races per category (e.g. two A-races in a
    season), so we upsert every event returned by Intervals, keyed on
    ``intervals_event_id``. Each genuinely new event triggers its own Telegram
    notification.

    Webhook-driven (CALENDAR_UPDATED) — dormancy gate skips API quota for
    inactive users. CLI admin (`force_inactive=True`) bypasses.
    """
    if not force_inactive and is_user_dormant(user.id, "actor_sync_athlete_goals"):
        return

    today = local_today()
    # Intervals.icu API drops category-filtered results unless newest is set explicitly —
    # reproduced on 2026-04-20 with user 5's RACE_A "Drina trail" 2026-05-05: without newest,
    # `?category=RACE_A` returns []; with newest ~2 years out it returns the event.
    newest = today + timedelta(days=2 * 365)

    existing_ids = {g.intervals_event_id for g in AthleteGoal.get_all(user.id)}
    new_events: list[tuple[str, ScheduledWorkoutDTO]] = []

    try:
        with IntervalsSyncClient.for_user(user) as client:
            for category in ("RACE_A", "RACE_B", "RACE_C"):
                events: list[ScheduledWorkoutDTO] = client.get_events(oldest=today, newest=newest, category=category)
                for event in events:
                    if event.id not in existing_ids:
                        new_events.append((category, event))
                        # Guard against the same event.id appearing under more
                        # than one category — without this, a second pass would
                        # notify twice for the same race.
                        existing_ids.add(event.id)
                    AthleteGoal.upsert_from_intervals(
                        user_id=user.id,
                        category=category,
                        event_name=event.name or f"{category} event",
                        event_date=event.start_date_local,
                        intervals_event_id=event.id,
                        sport_type=resolve_race_sport_type(event.type),
                    )
                    logger.info(
                        "Synced goal %s for user %d: %s %s",
                        category,
                        user.id,
                        event.name,
                        event.start_date_local,
                    )
    except IntervalsAccessError as e:
        # CALENDAR scope revoked / no creds — nothing to sync, abort cleanly.
        logger.info("Skipping goal sync for user %d: %s", user.id, e)
        return

    for category, event in new_events:
        _actor_send_goal_notification.send(
            user=user,
            event_name=event.name or category,
            event_date=event.start_date_local,
            category=category,
        )


@dramatiq.actor(queue_name="default")
@validate_call
def actor_update_zones(user: UserDTO):
    """Read threshold drift, update athlete_settings + Intervals.icu, notify user.

    Handles three metrics — all push HRVT2-derived values (FTP/LTHR/threshold_pace
    semantically equal LT2 = HRVT2 per Coggan/Friel; the prior HRVT1 mapping
    misaligned every Intervals zone by ~13%):
      - LTHR (HR threshold) — Ride + Run, push as ``{"lthr": bpm}``
      - THRESHOLD_PACE (Run only) — sec/km in our DB, push to Intervals.icu as
        m/s (the API stores velocity, not pace) via ``{"threshold_pace": m_s}``.
      - FTP (Ride only) — push as ``{"ftp": watts}`` (added 2026-05-08, issue #313).
    """
    drift = User.detect_threshold_drift(user_id=user.id)
    if not drift:
        logger.info("No threshold drift for user %d", user.id)
        return

    updated: list[str] = []

    # Order: push to Intervals.icu first, persist locally only on success.
    # Reversed order would leave DB and API permanently disagreeing if a
    # Dramatiq retry sees the *new* DB value as `config` (drift collapses
    # to 0% → no alert → API never receives the push).
    try:
        with IntervalsSyncClient.for_user(user) as client:
            for alert in drift.alerts:
                sport = alert.sport
                new_value = alert.measured
                old_value = alert.config_value

                if new_value is None or new_value <= 0:
                    logger.warning(
                        "Skipping %s update for user %d: invalid measured=%r",
                        alert.metric,
                        user.id,
                        new_value,
                    )
                    continue

                if alert.metric == "LTHR":
                    client.update_sport_settings(sport, {"lthr": new_value})
                    AthleteSettings.upsert(user_id=user.id, sport=sport, lthr=new_value)
                    updated.append(f"LTHR {sport}: {old_value} → {new_value} bpm")
                    logger.info("Updated LTHR %s for user %d: %d → %d", sport, user.id, old_value, new_value)
                elif alert.metric == "FTP":
                    client.update_sport_settings(sport, {"ftp": new_value})
                    AthleteSettings.upsert(user_id=user.id, sport=sport, ftp=new_value)
                    updated.append(f"FTP {sport}: {old_value} → {new_value} W")
                    logger.info("Updated FTP %s for user %d: %d → %d", sport, user.id, old_value, new_value)
                elif alert.metric == "THRESHOLD_PACE":
                    # DB stores sec/km; Intervals.icu API expects m/s velocity.
                    m_per_s = round(1000 / new_value, 3)
                    client.update_sport_settings(sport, {"threshold_pace": m_per_s})
                    AthleteSettings.upsert(user_id=user.id, sport=sport, threshold_pace=float(new_value))
                    old_pace = format_pace(old_value) or f"{old_value} s/km"
                    new_pace = format_pace(new_value) or f"{new_value} s/km"
                    updated.append(f"Threshold pace {sport}: {old_pace} → {new_pace}")
                    logger.info(
                        "Updated threshold_pace %s for user %d: %d → %d s/km (%.3f m/s)",
                        sport,
                        user.id,
                        old_value,
                        new_value,
                        m_per_s,
                    )
                else:
                    logger.warning("Unknown drift metric %s for user %d, skipping", alert.metric, user.id)
    except IntervalsAccessError as e:
        # SETTINGS:WRITE scope revoked / no creds — drift stays in our DB but
        # we can't push to Intervals.icu. User has to reconnect to recover.
        logger.info("Skipping zone update for user %d: %s", user.id, e)
        return

    _actor_send_zones_notification.send(user, updated)
