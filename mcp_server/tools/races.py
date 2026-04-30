"""MCP tools for race tagging, analytics, and future-race creation."""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

import anthropic
import httpx
from sqlalchemy import select

from config import settings
from data.db import (
    Activity,
    AthleteGoal,
    AthleteSettings,
    FitnessProjection,
    Race,
    RacePlan,
    Wellness,
    get_session,
)
from data.intervals.client import IntervalsAsyncClient
from data.intervals.dto import EventExDTO
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id
from mcp_server.sentry import sentry_tool

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = ("RACE_A", "RACE_B", "RACE_C")

# Tag the model + prompt revision that produced a payload so we can
# reason about plan provenance later (and decide when to regenerate stale
# rows). Bump on prompt or schema changes.
RACE_PLAN_MODEL_VERSION = "v0-2026-04-30"


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
    existing_name: str | None,
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
    is_update = existing_date is not None
    header_icon = "♻️ Update" if is_update else "🏁 Preview"
    lines.append(f"{header_icon}: {name} — {category}")
    # When there are multiple RACE_A (or B/C) upcoming, get_by_category picks
    # the nearest one — show which existing race is being overwritten so the
    # athlete can catch a mis-targeted update before confirming.
    if is_update and existing_name and existing_name != name:
        lines.append(f"🔁 Replaces: {existing_name}")
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

    Idempotency key is ``intervals_event_id`` — an athlete may have multiple active
    RACE_A / RACE_B / RACE_C in a season (e.g. two A-races: Ironman 70.3 in September
    + Oceanlava in October). This tool targets the **nearest upcoming** race in the
    given category: if one exists it is updated, otherwise a new event is created
    alongside any far-future races already on the calendar. The preview ("🔁 Replaces:
    …" vs. "🆕 New race") makes the branch explicit so the athlete can confirm.

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
    existing_name = existing_goal.event_name if existing_goal else None

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
            existing_name=existing_name,
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

    # include_past=True so a stale is_active=True row left over after the race
    # date passed (see AthleteGoal.upsert_from_intervals — post-race sync
    # reactivates rows) is still reachable here.
    existing_goal = await AthleteGoal.get_by_category(user_id, category, include_past=True)
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

    # 2) Soft-delete locally — scope by id so the row shown in preview and
    # sent to Intervals is exactly the row deactivated here. Picking by
    # (user_id, category) alone can diverge when the user has multiple
    # active races in the same category (e.g. stale past row + upcoming).
    try:
        await AthleteGoal.deactivate_by_id(existing_goal.id, user_id)
    except Exception as e:
        # Intervals succeeded but local failed — rare but leaves the user in an
        # inconsistent state that next sync won't auto-recover (event is gone
        # upstream). Warn so the user can retry.
        logger.exception("delete_race_goal: local deactivate failed after Intervals delete")
        return f"⚠️ Deleted from Intervals.icu but local cleanup failed: {e}. " "Retry to reconcile."

    return f"🗑️ {category} deleted: {event_name}."


# ---------------------------------------------------------------------------
# Race execution plan: generate_race_plan
# ---------------------------------------------------------------------------


# JSON Schema for the structured race plan Claude must produce. Used as the
# input schema for a forced tool_use call so the model returns parsed JSON
# instead of free-form prose we'd have to parse.
_RACE_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["warmup", "legs", "fueling", "contingencies"],
    "properties": {
        "warmup": {
            "type": "string",
            "description": "Pre-race warmup protocol, 2-4 sentences, sport-specific.",
        },
        "legs": {
            "type": "array",
            "minItems": 1,
            "description": (
                "Per-leg execution. Triathlon: swim, T1, bike, T2, run. "
                "Single-sport: one entry covering the whole distance, or split "
                "into segments (e.g. fast/cruise/finish for a marathon)."
            ),
            "items": {
                "type": "object",
                "required": ["leg"],
                "properties": {
                    "leg": {
                        "type": "string",
                        "description": "Leg name: swim / T1 / bike / T2 / run / segment-1 / etc.",
                    },
                    "distance": {
                        "type": "string",
                        "description": "Human-readable distance, e.g. '1.5 km', '40 km', '21.1 km'.",
                    },
                    "pacing": {
                        "type": "object",
                        "description": "Pacing corridor low/target/cap. Units appropriate to the leg (min/km, W, min/100m).",
                        "properties": {
                            "low": {"type": "string"},
                            "target": {"type": "string"},
                            "cap": {"type": "string"},
                        },
                    },
                    "hr_ceiling_bpm": {
                        "type": "integer",
                        "description": "Maximum HR for this leg in bpm. Omit for transitions.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "1-2 sentence executional cue tied to the athlete's data.",
                    },
                },
            },
        },
        "fueling": {
            "type": "object",
            "required": ["carbs_g_per_hour"],
            "properties": {
                "carbs_g_per_hour": {
                    "type": "integer",
                    "description": "Target carb intake g/hr. Conservative band 60-90 unless gut-trained.",
                },
                "fluid_ml_per_hour": {"type": "integer"},
                "sodium_mg_per_hour": {"type": "integer"},
                "notes": {
                    "type": "string",
                    "description": "Cadence notes (e.g. 'gel every 25 min, sip every 10 min').",
                },
            },
        },
        "transitions": {
            "type": "array",
            "description": "Tri-only. T1/T2 checklists. Empty for single-sport races.",
            "items": {
                "type": "object",
                "required": ["name", "checklist"],
                "properties": {
                    "name": {"type": "string", "description": "T1 / T2"},
                    "checklist": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "target_time_sec": {"type": "integer"},
                },
            },
        },
        "contingencies": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "description": "Exactly three contingency plans: heat, cramp, off-pace (in any order).",
            "items": {
                "type": "object",
                "required": ["scenario", "plan"],
                "properties": {
                    "scenario": {"type": "string", "description": "heat / cramp / off-pace / equipment / etc."},
                    "plan": {"type": "string", "description": "What to do, 1-3 sentences."},
                },
            },
        },
        "headline": {
            "type": "string",
            "description": "One-sentence race-day mantra grounded in the athlete's data.",
        },
    },
}


_RACE_PLAN_SYSTEM_PROMPT = (
    "You are an experienced triathlon and endurance coach writing an "
    "execution plan for an athlete's upcoming A-race. The athlete will read "
    "this on race morning. Keep the tone calm, specific, and grounded in "
    "their actual data. Bias toward conservative, defensible advice — a wrong "
    "pacing call breaks the race.\n\n"
    "Rules:\n"
    "1. Pacing corridor must be low/target/cap, not a single number. Target sits "
    "  inside the athlete's last-6-week training band; cap is the do-not-exceed "
    "  ceiling tied to threshold/zones.\n"
    "2. HR ceilings come from the athlete's zones (lthr / max_hr) when provided. "
    "  Do not invent HR ceilings if zones are missing — omit the field.\n"
    "3. Fueling 60-90 g/hr unless the athlete has explicit higher gut-training "
    "  evidence in the activity log.\n"
    "4. Transitions only for triathlon/duathlon/aquathlon races.\n"
    "5. Provide exactly three contingencies covering heat, cramp, and off-pace.\n"
    "6. Use the athlete's primary language conservatively — keep technical terms "
    "  in English. Athletes read on a phone; sentences short.\n"
    "7. Reply ONLY by calling the submit_race_plan tool. Do not produce prose "
    "  outside the tool call.\n"
)


def _summarize_activities(activities: list[Activity]) -> dict[str, Any]:
    """Compact 6w activity summary suitable for the prompt context.

    The full row list would balloon prompt tokens. We hand Claude per-sport
    aggregates plus a small recent-race-effort sample so it can ground the
    pacing corridor without seeing every workout.
    """
    by_sport: dict[str, dict[str, Any]] = {}
    for a in activities:
        sport = a.type or "Other"
        b = by_sport.setdefault(
            sport,
            {"count": 0, "total_minutes": 0, "total_tss": 0.0, "avg_hr_samples": []},
        )
        b["count"] += 1
        if a.moving_time:
            b["total_minutes"] += int(a.moving_time // 60)
        if a.icu_training_load:
            b["total_tss"] += float(a.icu_training_load)
        if a.average_hr:
            b["avg_hr_samples"].append(float(a.average_hr))

    summary: dict[str, Any] = {"weeks": 6, "total_count": len(activities), "by_sport": {}}
    for sport, b in by_sport.items():
        avg_hr = round(sum(b["avg_hr_samples"]) / len(b["avg_hr_samples"]), 1) if b["avg_hr_samples"] else None
        summary["by_sport"][sport] = {
            "count": b["count"],
            "total_minutes": b["total_minutes"],
            "total_tss": round(b["total_tss"], 1),
            "avg_hr": avg_hr,
        }

    # Recent race-pace efforts (long sessions or races) for pacing grounding.
    recent_efforts = [
        {
            "date": a.start_date_local,
            "sport": a.type,
            "minutes": int((a.moving_time or 0) // 60),
            "avg_hr": a.average_hr,
            "tss": a.icu_training_load,
            "is_race": bool(a.is_race),
        }
        for a in sorted(activities, key=lambda x: x.start_date_local, reverse=True)
        if (a.moving_time or 0) >= 60 * 60  # ≥60 min
    ][:8]
    summary["long_efforts_recent"] = recent_efforts
    return summary


def _summarize_zones(settings_rows: list[AthleteSettings]) -> dict[str, Any]:
    """Compact per-sport zones snapshot for the prompt."""
    out: dict[str, Any] = {}
    for s in settings_rows:
        out[s.sport] = {
            "lthr": s.lthr,
            "max_hr": s.max_hr,
            "ftp_w": s.ftp,
            "threshold_pace": s.threshold_pace,
            "pace_units": s.pace_units,
            "hr_zones_bpm": s.hr_zones,
            "power_zones_pct_ftp": s.power_zones,
            "pace_zones_pct_threshold": s.pace_zones,
        }
    return out


@mcp.tool()
@sentry_tool
async def generate_race_plan(race_id: int | None = None, dry_run: bool = False) -> dict:
    """Generate a structured race-execution plan for an upcoming A-race.

    Reads the athlete's RACE_A goal (or a specific goal by id), the last 6
    weeks of training, per-sport zones, and the race-day-projected fitness
    decay; calls Claude with that context and a forced JSON schema; persists
    the result as a row in ``race_plans``.

    The tool refuses to generate a plan when:
      - the athlete has fewer than 6 weeks of activities (we won't hallucinate
        a corridor without training evidence), OR
      - the race is more than 120 days away (the projection has decayed too
        far for the pacing corridor to be defensible).

    Plans for races 14-120 days out are tagged ``preliminary=True`` so the
    Telegram surface can warn the athlete that the corridor will tighten
    closer to race day.

    Parameters:
      race_id: athlete_goals.id — usually omitted; defaults to RACE_A.
        (Spelled ``race_id`` to match the issue spec; this is the goal id
        since the pre-race target lives in athlete_goals, not the post-race
        ``races`` table.)
      dry_run: True → return the generated payload only, do NOT persist.
    """
    user_id = get_current_user_id()

    # ---------- 1. Resolve goal ----------
    if race_id is not None:
        async with get_session() as session:
            goal = await session.get(AthleteGoal, race_id)
        if goal is None or goal.user_id != user_id:
            return {"error": f"Goal {race_id} not found for this athlete."}
    else:
        goal = await AthleteGoal.get_by_category(user_id, "RACE_A")
        if goal is None:
            return {
                "error": (
                    "No active RACE_A goal — set one with /race or suggest_race "
                    "before generating a race plan."
                )
            }

    today = date.today()
    days_to_race = (goal.event_date - today).days

    if days_to_race > 120:
        return {
            "error": (
                f"Race is {days_to_race} days away (>120). The fitness projection "
                "isn't reliable that far out — re-run within 4 months of race day."
            ),
            "race_date": str(goal.event_date),
            "days_to_race": days_to_race,
        }

    # ---------- 2. Pull 6 weeks of activities ----------
    six_weeks_ago = today - timedelta(weeks=6)
    activities, _last_synced = await Activity.get_range(user_id, six_weeks_ago, today)

    if len(activities) < 6:
        # Heuristic floor: at least 6 sessions across 6 weeks. The exact spec
        # says "<6 weeks of activities" — interpreted as "we have fewer than
        # 6 distinct training sessions on record over the last 6w", which is
        # the smallest signal that lets us calibrate a corridor.
        return {
            "error": (
                f"Only {len(activities)} activities in the last 6 weeks — not "
                "enough training history to calibrate a pacing corridor. Sync "
                "Intervals.icu and try again."
            ),
            "activity_count": len(activities),
        }

    # ---------- 3. Pull zones + race-day projection ----------
    zones_rows = await AthleteSettings.get_all(user_id)
    projection_rows = await FitnessProjection.get_projection(user_id)
    race_day_projection: dict[str, Any] | None = None
    race_day_str = str(goal.event_date)
    for row in projection_rows:
        if row.date == race_day_str:
            race_day_projection = {
                "date": row.date,
                "ctl": row.ctl,
                "atl": row.atl,
                "tsb": (row.ctl - row.atl) if (row.ctl is not None and row.atl is not None) else None,
                "ramp_rate": row.ramp_rate,
            }
            break

    # Latest wellness as today-anchor (current CTL/TSB give the model a sense
    # of where the athlete is right now, not just on race day).
    async with get_session() as session:
        wellness_row = (
            await session.execute(
                select(Wellness)
                .where(Wellness.user_id == user_id)
                .order_by(Wellness.date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    today_snapshot: dict[str, Any] | None = None
    if wellness_row is not None:
        today_snapshot = {
            "date": wellness_row.date,
            "ctl": wellness_row.ctl,
            "atl": wellness_row.atl,
            "tsb": (
                (wellness_row.ctl - wellness_row.atl)
                if (wellness_row.ctl is not None and wellness_row.atl is not None)
                else None
            ),
            "recovery_score": wellness_row.recovery_score,
        }

    preliminary = days_to_race > 14

    # ---------- 4. Build prompt context ----------
    discipline = (goal.sport_type or "").lower()
    is_tri = discipline in {"triathlon", "duathlon", "aquathlon"} or bool(goal.disciplines)

    context = {
        "race": {
            "id": goal.id,
            "name": goal.event_name,
            "date": race_day_str,
            "days_to_race": days_to_race,
            "discipline": goal.sport_type,
            "disciplines": goal.disciplines,
            "is_triathlon": is_tri,
            "ctl_target": goal.ctl_target,
            "preliminary": preliminary,
        },
        "today": today_snapshot,
        "race_day_projection": race_day_projection,
        "zones_by_sport": _summarize_zones(zones_rows),
        "training_last_6_weeks": _summarize_activities(activities),
    }

    user_message = (
        "Generate the race execution plan. Use ONLY this JSON context to "
        "ground the corridor. Do not invent zones or fueling values that are "
        "not supported by the data.\n\n"
        f"```json\n{json.dumps(context, default=str, indent=2)}\n```"
    )

    # ---------- 5. Call Claude with forced tool_use ----------
    api_key = settings.ANTHROPIC_API_KEY.get_secret_value() if settings.ANTHROPIC_API_KEY else ""
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY is not configured — cannot generate plan."}

    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3)
    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_RACE_PLAN_SYSTEM_PROMPT,
            tools=[
                {
                    "name": "submit_race_plan",
                    "description": "Submit the structured race execution plan.",
                    "input_schema": _RACE_PLAN_SCHEMA,
                }
            ],
            tool_choice={"type": "tool", "name": "submit_race_plan"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        logger.exception("generate_race_plan: Claude call failed for user %d", user_id)
        return {"error": f"Plan generation failed: {e}"}

    plan_input: dict[str, Any] | None = None
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_race_plan":
            plan_input = dict(block.input) if block.input else None
            break

    if not plan_input:
        logger.warning("generate_race_plan: model did not call submit_race_plan, stop_reason=%s", resp.stop_reason)
        return {"error": "Model did not return a structured plan. Try again."}

    payload: dict[str, Any] = {
        "plan": plan_input,
        "race": context["race"],
        "preliminary": preliminary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": RACE_PLAN_MODEL_VERSION,
    }

    # ---------- 6. Persist or short-circuit ----------
    if dry_run:
        return {
            "id": None,
            "dry_run": True,
            "preliminary": preliminary,
            "model_version": RACE_PLAN_MODEL_VERSION,
            "payload": payload,
        }

    try:
        row = await RacePlan.save(
            user_id=user_id,
            goal_id=goal.id,
            model_version=RACE_PLAN_MODEL_VERSION,
            payload=payload,
        )
    except Exception as e:
        # Most likely a unique-violation on (goal_id, day) — return today's
        # row instead of erroring, so callers get the same idempotent shape.
        logger.warning("generate_race_plan: save failed (%s) — falling back to today's row", e)
        existing = await RacePlan.get_today_for_goal(goal.id)
        if existing is not None:
            return {
                "id": existing.id,
                "dry_run": False,
                "preliminary": preliminary,
                "model_version": existing.model_version,
                "payload": existing.payload,
                "note": "Plan already generated today — returning the existing row.",
            }
        return {"error": f"Plan persistence failed: {e}"}

    return {
        "id": row.id,
        "dry_run": False,
        "preliminary": preliminary,
        "model_version": row.model_version,
        "payload": row.payload,
    }
