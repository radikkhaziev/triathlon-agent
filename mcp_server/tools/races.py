"""MCP tools for race tagging, analytics, and future-race creation."""

import logging
from datetime import date, timedelta
from typing import Literal

import httpx
from sqlalchemy import select

from data.db import Activity, AthleteGoal, Race, Wellness, get_session
from data.intervals.client import IntervalsAsyncClient
from data.intervals.dto import EventExDTO
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id
from mcp_server.sentry import sentry_tool

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = ("RACE_A", "RACE_B", "RACE_C")


def _fmt_time(secs: int | None) -> str | None:
    if not secs:
        return None
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_pace(secs_km: float | None) -> str | None:
    if not secs_km:
        return None
    m, s = divmod(int(secs_km), 60)
    return f"{m}:{s:02d}/km"


@mcp.tool()
async def get_races(days_back: int = 365, sport: str = "") -> dict:
    """Get race history with fitness context, results, and activity metrics."""
    user_id = get_current_user_id()
    end = date.today()
    start = end - timedelta(days=days_back)

    races = await Race.get_range(user_id, str(start), str(end))

    # Batch-fetch all activities
    act_map: dict = {}
    if races:
        async with get_session() as session:
            act_ids = [r.activity_id for r in races]
            rows = (
                (await session.execute(select(Activity).where(Activity.id.in_(act_ids), Activity.user_id == user_id)))
                .scalars()
                .all()
            )
            act_map = {a.id: a for a in rows}

    if sport:
        sport_lower = sport.lower()
        races = [
            r
            for r in races
            if (act_map.get(r.activity_id) and (act_map[r.activity_id].type or "").lower() == sport_lower)
        ]

    entries = []
    for r in races:
        act = act_map.get(r.activity_id)

        entry = {
            "date": act.start_date_local if act else None,
            "name": r.name,
            "race_type": r.race_type,
            "sport": act.type if act else None,
            "distance_km": round(r.distance_m / 1000, 1) if r.distance_m else None,
            "finish_time": _fmt_time(r.finish_time_sec),
            "finish_time_sec": r.finish_time_sec,
            "goal_time": _fmt_time(r.goal_time_sec),
            "avg_pace": _fmt_pace(r.avg_pace_sec_km),
            "placement": r.placement,
            "surface": r.surface,
            "weather": r.weather,
            "rpe": r.rpe,
            "notes": r.notes,
            "fitness_context": {
                "ctl": r.race_day_ctl,
                "atl": r.race_day_atl,
                "tsb": r.race_day_tsb,
                "hrv_status": r.race_day_hrv_status,
                "recovery_score": r.race_day_recovery_score,
                "weight": r.race_day_weight,
            },
            "activity": {
                "id": r.activity_id,
                "duration_min": act.moving_time // 60 if act and act.moving_time else None,
                "avg_hr": act.average_hr if act else None,
                "tss": act.icu_training_load if act else None,
            },
        }
        entries.append(entry)

    return {"count": len(entries), "races": entries}


@mcp.tool()
async def tag_race(
    activity_id: str,
    name: str,
    race_type: str = "C",
    distance_m: float | None = None,
    finish_time_sec: int | None = None,
    goal_time_sec: int | None = None,
    placement: int | None = None,
    placement_total: int | None = None,
    surface: str | None = None,
    weather: str | None = None,
    rpe: int | None = None,
    notes: str | None = None,
) -> dict:
    """Tag activity as race. Auto-fills fitness context from wellness data."""
    user_id = get_current_user_id()

    async with get_session() as session:
        activity = (
            await session.execute(select(Activity).where(Activity.id == activity_id, Activity.user_id == user_id))
        ).scalar_one_or_none()

        if not activity:
            return {"error": f"Activity {activity_id} not found."}

        activity.is_race = True
        activity.sub_type = "RACE"

        # Auto-fill fitness context
        wellness = (
            await session.execute(
                select(Wellness).where(
                    Wellness.user_id == user_id,
                    Wellness.date == activity.start_date_local,
                )
            )
        ).scalar_one_or_none()

        # Calculate pace
        avg_pace = None
        if distance_m and activity.moving_time and distance_m > 0:
            avg_pace = round(activity.moving_time / (distance_m / 1000), 1)

        if not finish_time_sec and activity.moving_time:
            finish_time_sec = activity.moving_time

        # Check existing race
        existing = (await session.execute(select(Race).where(Race.activity_id == activity_id))).scalar_one_or_none()

        race_data = {
            "user_id": user_id,
            "activity_id": activity_id,
            "name": name,
            "race_type": race_type,
            "distance_m": distance_m,
            "finish_time_sec": finish_time_sec,
            "goal_time_sec": goal_time_sec,
            "placement": placement,
            "placement_total": placement_total,
            "surface": surface,
            "weather": weather,
            "avg_pace_sec_km": avg_pace,
            "rpe": rpe,
            "notes": notes,
            "race_day_ctl": wellness.ctl if wellness else None,
            "race_day_atl": wellness.atl if wellness else None,
            "race_day_tsb": (wellness.ctl - wellness.atl) if wellness and wellness.ctl and wellness.atl else None,
            "race_day_recovery_score": wellness.recovery_score if wellness else None,
            "race_day_weight": wellness.weight if wellness else None,
        }

        if existing:
            for k, v in race_data.items():
                if v is not None:
                    setattr(existing, k, v)
        else:
            session.add(Race(**race_data))

        await session.commit()

    return {
        "status": "tagged",
        "activity_id": activity_id,
        "name": name,
        "race_type": race_type,
        "distance_km": round(distance_m / 1000, 1) if distance_m else None,
        "finish_time": _fmt_time(finish_time_sec),
        "avg_pace": _fmt_pace(avg_pace),
        "fitness_context": {
            "ctl": race_data["race_day_ctl"],
            "tsb": race_data["race_day_tsb"],
            "recovery": race_data["race_day_recovery_score"],
        },
    }


@mcp.tool()
async def update_race(
    activity_id: str,
    name: str | None = None,
    race_type: str | None = None,
    distance_m: float | None = None,
    finish_time_sec: int | None = None,
    goal_time_sec: int | None = None,
    placement: int | None = None,
    placement_total: int | None = None,
    surface: str | None = None,
    weather: str | None = None,
    rpe: int | None = None,
    notes: str | None = None,
) -> dict:
    """Update race details (placement, notes, RPE, weather, etc.)."""
    user_id = get_current_user_id()

    async with get_session() as session:
        race = (
            await session.execute(select(Race).where(Race.activity_id == activity_id, Race.user_id == user_id))
        ).scalar_one_or_none()

        if not race:
            return {"error": f"No race found for activity {activity_id}."}

        updates = {}
        for field, value in [
            ("name", name),
            ("race_type", race_type),
            ("distance_m", distance_m),
            ("finish_time_sec", finish_time_sec),
            ("goal_time_sec", goal_time_sec),
            ("placement", placement),
            ("placement_total", placement_total),
            ("surface", surface),
            ("weather", weather),
            ("rpe", rpe),
            ("notes", notes),
        ]:
            if value is not None:
                setattr(race, field, value)
                updates[field] = value

        # Recalculate pace if distance changed
        if distance_m:
            act = (await session.execute(select(Activity.moving_time).where(Activity.id == activity_id))).scalar()
            if act and distance_m > 0:
                race.avg_pace_sec_km = round(act / (distance_m / 1000), 1)
                updates["avg_pace"] = _fmt_pace(race.avg_pace_sec_km)

        await session.commit()

    return {"status": "updated", "activity_id": activity_id, "updates": updates}


# ---------------------------------------------------------------------------
# Future-race creation: suggest_race / delete_race_goal
# ---------------------------------------------------------------------------


_TRIATHLON_DISCIPLINES = {
    "Triathlon": ["Swim", "Ride", "Run"],
    "Duathlon": ["Run", "Ride", "Run"],
    "Aquathlon": ["Swim", "Run"],
}


def _format_preview(
    *,
    name: str,
    category: str,
    event_date: date,
    sport: str | None,
    distance_m: float | None,
    description: str,
    ctl_target: float | None,
    current_ctl: float | None,
    existing_date: date | None,
    today: date,
) -> str:
    """Render a human-readable dry-run preview for the confirm button.

    MCP tools are language-agnostic — Claude reads this preview from the
    tool result and paraphrases it into the athlete's language before
    replying. Keep wording concise and consistent (all English) so the
    model doesn't mix languages downstream.
    """
    days_to_race = (event_date - today).days
    date_str = f"{event_date} ({days_to_race}d)" if days_to_race >= 0 else str(event_date)

    lines: list[str] = []
    header_icon = "♻️ Update" if existing_date else "🏁 Preview"
    lines.append(f"{header_icon}: {name} — {category}")
    if existing_date and existing_date != event_date:
        lines.append(f"📅 Was: {existing_date} → Now: {date_str}")
    else:
        lines.append(f"📅 Date: {date_str}")
    if sport:
        sport_line = f"🏃 Sport: {sport}"
        if distance_m:
            dist_km = distance_m / 1000
            sport_line += f", {dist_km:.1f} km" if dist_km >= 1 else f", {int(distance_m)} m"
        lines.append(sport_line)
    elif distance_m:
        dist_km = distance_m / 1000
        lines.append(f"📏 Distance: {dist_km:.1f} km")

    if ctl_target is not None:
        ctl_line = f"🎯 Peak CTL: {ctl_target:.0f}"
        if current_ctl is not None and days_to_race > 0:
            gap = ctl_target - current_ctl
            weeks = max(days_to_race / 7, 0.1)
            ramp = gap / weeks
            ctl_line += f" (current {current_ctl:.0f}, ramp {ramp:+.1f} TSS/wk)"
            if ramp > 7:
                ctl_line += " ⚠️ aggressive (>7 TSS/wk)"
        lines.append(ctl_line)

    if description:
        lines.append(f"📝 {description}")

    lines.append("")
    action = "update" if existing_date else "submit"
    lines.append(f"Call suggest_race with dry_run=False or tap «Submit to Intervals» to {action}.")
    return "\n".join(lines)


@mcp.tool()
@sentry_tool
async def suggest_race(
    name: str,
    category: Literal["RACE_A", "RACE_B", "RACE_C"],
    dt: str,
    sport: str = "",
    distance_m: float | None = None,
    description: str = "",
    ctl_target: float | None = None,
    dry_run: bool = False,
) -> str:
    """Create or update a future race event in Intervals.icu (RACE_A/B/C) and mirror it into
    athlete_goals. Dry-run returns a preview string; the bot replays the exact same input with
    dry_run=False on user confirmation — do NOT call dry_run=False yourself.

    Idempotency is (user_id, category) — one active RACE_A / RACE_B / RACE_C per athlete.
    Repeated calls with a new `dt` move the existing race (update_event), they do NOT
    create a second row.

    Parameters:
      name: race name, e.g. "Drina Trail", "Ironman 70.3 Belgrade".
      category: RACE_A (season goal) / RACE_B (tune-up) / RACE_C (fitness check). Ask if ambiguous.
      dt: ISO date "YYYY-MM-DD". Must be >= today.
      sport: Run / Ride / Swim / TrailRun / Triathlon / Duathlon / Aquathlon. Optional — inferred from name if obvious.
      distance_m: race distance in meters (optional).
      description: freeform notes — surface, weather, goal time.
      ctl_target: peak CTL on race day. Pass through only if the athlete named a number.
        Do not invent one — the preview will show current CTL so the athlete can decide.
      dry_run: True → preview only, no side-effects.
    """
    user_id = get_current_user_id()

    # --- Validation ---------------------------------------------------------
    if category not in _VALID_CATEGORIES:
        return f"Error: invalid category {category!r} — must be one of {', '.join(_VALID_CATEGORIES)}."

    try:
        event_date = date.fromisoformat(dt)
    except ValueError:
        return f"Error: invalid date {dt!r} — must be ISO format YYYY-MM-DD."

    today = date.today()
    if event_date < today:
        return f"Error: race date {dt} is in the past — use tag_race to log past races."

    # --- Lookup existing (idempotency key = user_id + category) ------------
    existing_goal = await AthleteGoal.get_by_category(user_id, category)
    existing_intervals_id = existing_goal.intervals_event_id if existing_goal else None
    existing_date = existing_goal.event_date if existing_goal else None

    # Current CTL for preview sanity-hints — newest wellness row
    current_ctl: float | None = None
    async with get_session() as session:
        row = (
            await session.execute(
                select(Wellness.ctl)
                .where(Wellness.user_id == user_id, Wellness.ctl.isnot(None))
                .order_by(Wellness.date.desc())
                .limit(1)
            )
        ).scalar()
        if row is not None:
            current_ctl = float(row)

    # --- Dry-run short-circuit ---------------------------------------------
    if dry_run:
        return _format_preview(
            name=name,
            category=category,
            event_date=event_date,
            sport=sport or None,
            distance_m=distance_m,
            description=description,
            ctl_target=ctl_target,
            current_ctl=current_ctl,
            existing_date=existing_date,
            today=today,
        )

    # --- Fallback idempotency: check Intervals calendar if local row missing.
    # Covers the recovery path where a previous attempt pushed the event to
    # Intervals but the local upsert failed — we pick up the existing event_id
    # and do an update instead of creating a duplicate.
    if existing_intervals_id is None:
        try:
            async with IntervalsAsyncClient.for_user(user_id) as client:
                remote_events = await client.get_events(oldest=event_date, newest=event_date, category=category)
                if remote_events:
                    picked = remote_events[0]
                    existing_intervals_id = picked.id
                    if len(remote_events) > 1:
                        logger.warning(
                            "suggest_race recovery: %d %s events on %s for user %d, picking id=%s name=%r — "
                            "others may be orphans from prior failures",
                            len(remote_events),
                            category,
                            event_date,
                            user_id,
                            existing_intervals_id,
                            getattr(picked, "name", None),
                        )
                    else:
                        logger.info(
                            "suggest_race recovery: using remote event id=%s name=%r for user %d",
                            existing_intervals_id,
                            getattr(picked, "name", None),
                            user_id,
                        )
        except Exception:
            logger.warning("suggest_race: fallback get_events failed", exc_info=True)
            # Non-fatal — proceed to create path.

    # --- Build Intervals payload -------------------------------------------
    disciplines = _TRIATHLON_DISCIPLINES.get(sport)
    payload = EventExDTO(
        category=category,
        type=sport or None,
        name=name,
        start_date_local=f"{event_date.isoformat()}T00:00:00",
        description=description or None,
        distance=distance_m,
    )

    # --- Push to Intervals -------------------------------------------------
    try:
        async with IntervalsAsyncClient.for_user(user_id) as client:
            if existing_intervals_id:
                result = await client.update_event(existing_intervals_id, payload)
                action = "updated"
            else:
                result = await client.create_event(payload)
                action = "created"
    except Exception as e:
        logger.exception("suggest_race: Intervals push failed for user %d", user_id)
        return f"Error pushing to Intervals.icu: {e}"

    intervals_event_id = result.id

    # --- Local upsert (handles race/reopen + backfill ctl_target) ----------
    try:
        goal = await AthleteGoal.upsert_from_intervals(
            user_id=user_id,
            category=category,
            event_name=name,
            event_date=event_date,
            intervals_event_id=intervals_event_id,
        )
    except Exception as e:
        # Intervals succeeded, DB didn't — idempotent retry will recover (§4.4).
        logger.exception("suggest_race: local upsert failed after Intervals push, event_id=%s", intervals_event_id)
        return (
            f"⚠️ Pushed to Intervals.icu (event {intervals_event_id}) but local save failed: {e}. "
            "Retry the same request to reconcile."
        )

    ctl_target_saved = True
    if ctl_target is not None:
        try:
            await AthleteGoal.set_ctl_target(goal.id, ctl_target, user_id=user_id)
        except Exception:
            logger.warning("suggest_race: set_ctl_target failed for goal %d", goal.id, exc_info=True)
            ctl_target_saved = False

    # --- Fill triathlon disciplines if applicable --------------------------
    if disciplines and goal.disciplines != disciplines:
        try:
            async with get_session() as session:
                fresh = await session.get(AthleteGoal, goal.id)
                if fresh is not None:
                    fresh.disciplines = disciplines
                    await session.commit()
        except Exception:
            logger.warning("suggest_race: disciplines backfill failed for goal %d", goal.id, exc_info=True)

    # --- Response ----------------------------------------------------------
    days_to_race = (event_date - today).days
    lines = [
        f"✅ {category} {action}: {name} — {event_date} ({days_to_race} дн).",
    ]
    if ctl_target is not None:
        if ctl_target_saved:
            lines.append(f"Peak CTL target: {ctl_target:.0f}.")
        else:
            lines.append(f"⚠️ Peak CTL target ({ctl_target:.0f}) failed to save — set it from /settings.")
    lines.append(f"Event: https://intervals.icu/event/{intervals_event_id}")
    return "\n".join(lines)


@mcp.tool()
@sentry_tool
async def delete_race_goal(category: Literal["RACE_A", "RACE_B", "RACE_C"]) -> str:
    """Delete a future race by priority category. Removes the event from Intervals.icu
    and soft-deletes the local athlete_goals row.

    Confirm intent with the athlete before calling — deletion is irreversible from the
    bot (they'd have to re-add via suggest_race). Idempotent: calling twice in a row
    returns an informational message on the second call, not an error.
    """
    user_id = get_current_user_id()

    if category not in _VALID_CATEGORIES:
        return f"Error: invalid category {category!r} — must be one of {', '.join(_VALID_CATEGORIES)}."

    existing_goal = await AthleteGoal.get_by_category(user_id, category)
    if existing_goal is None:
        return f"Nothing to delete — no active {category} goal."

    event_id = existing_goal.intervals_event_id
    event_name = existing_goal.event_name

    # 1) Remove from Intervals. 404 = event already gone upstream, continue with
    # local cleanup so the user can re-create on the same category. Any other
    # HTTP error or non-HTTP exception bails before we touch the DB.
    if event_id is not None:
        try:
            async with IntervalsAsyncClient.for_user(user_id) as client:
                await client.delete_event(event_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.info("delete_race_goal: Intervals event %s already gone, proceeding", event_id)
            else:
                logger.exception("delete_race_goal: Intervals delete failed for event %s", event_id)
                return f"Error deleting from Intervals.icu: HTTP {e.response.status_code}"
        except Exception as e:
            logger.exception("delete_race_goal: Intervals delete failed for event %s", event_id)
            return f"Error deleting from Intervals.icu: {e}"

    # 2) Soft-delete locally.
    try:
        await AthleteGoal.deactivate_by_category(user_id, category)
    except Exception as e:
        # Intervals succeeded but local failed — rare but leaves the user in an
        # inconsistent state that next sync won't auto-recover (event is gone
        # upstream). Warn so the user can retry.
        logger.exception("delete_race_goal: local deactivate failed after Intervals delete")
        return f"⚠️ Deleted from Intervals.icu but local cleanup failed: {e}. " "Retry to reconcile."

    return f"🗑️ {category} deleted: {event_name}."
