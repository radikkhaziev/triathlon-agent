"""Race-execution plan service — business logic for generating and validating
structured race plans.

Extracted from ``mcp_server/tools/races.py`` (PR2.1, 2026-05-09) so that both
the MCP tool (``generate_race_plan``) and the REST endpoint
(``POST /api/race-plan/generate``) call the same code path. The MCP tool stays
a thin wrapper that resolves ``user_id`` from contextvars; the REST endpoint
resolves user from auth deps. Both forward to ``build_race_plan(user_id, …)``
here.

Spec: ``docs/RACE_PLAN_SPEC.md``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config import settings
from data.db import (
    Activity,
    AthleteGoal,
    AthleteSettings,
    FitnessProjection,
    Race,
    RacePlan,
    User,
    Wellness,
    get_session,
)
from data.db.tracking import ApiUsageDaily
from data.db.user_fact import UserFact
from data.redis_client import get_redis
from tasks.dto import local_today

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------

# Tag the model + prompt revision that produced a payload so we can reason
# about plan provenance later (and decide when to regenerate stale rows).
# Bump on prompt or schema changes.
RACE_PLAN_MODEL_VERSION = "v1-2026-05-09"


# Whitelist of user_facts topics that meaningfully shape a race plan. Spec §4
# enrichment: list_active(user_id) returns ALL active facts; we MUST filter to
# this set before injecting into the system prompt — otherwise irrelevant facts
# ("dog name = Rex") leak into coaching context. Whitelist > blacklist because
# Phase 2 extractor may produce facts with arbitrary topics; race plan only
# reads from its own set. See architect feedback 2026-05-09.
RACE_PLAN_FACT_TOPICS: frozenset[str] = frozenset(
    {
        "injury",
        "gi",
        "nutrition",
        "equipment",
        "pacing",
        "heat_response",
        "race_history",
        "recovery_pattern",
    }
)


# ---------------------------------------------------------------------------
#  JSON schema for forced tool_use
# ---------------------------------------------------------------------------

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
                "required": ["leg", "pacing"],
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
                        "description": (
                            "Pacing corridor low/target/cap. Units appropriate to the leg (min/km, W, min/100m)."
                        ),
                        "required": ["low", "target", "cap"],
                        "properties": {
                            "low": {"type": "string"},
                            "target": {"type": "string"},
                            "cap": {"type": "string"},
                        },
                    },
                    "hr_ceiling_bpm": {
                        "type": "integer",
                        "minimum": 80,
                        "maximum": 220,
                        "description": "Maximum HR for this leg in bpm. Omit for transitions.",
                    },
                    "notes": {
                        "type": "string",
                        "maxLength": 200,
                        "description": (
                            "1-2 sentence executional cue tied to the athlete's data. "
                            "HARD CAP 200 chars (~25 words) — athlete reads this on a phone "
                            "between legs, prose-blocks don't survive race-day attention."
                        ),
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
                    "minimum": 30,
                    "maximum": 120,
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
            "maxItems": 5,
            "description": (
                "3-5 contingency plans. Default trio (heat / cramp / off-pace) is a starting "
                "point, not a quota — pick what's actually relevant to this race's distance, "
                "discipline, and conditions if provided. Heat at 15°C is wasted attention."
            ),
            "items": {
                "type": "object",
                "required": ["scenario", "plan"],
                "properties": {
                    "scenario": {"type": "string", "description": "heat / cramp / off-pace / gi / mech / etc."},
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


# ---------------------------------------------------------------------------
#  System prompt
# ---------------------------------------------------------------------------

_RACE_PLAN_SYSTEM_PROMPT_TEMPLATE = (
    "You are an experienced {sport_role} writing an execution plan for an "
    "athlete's upcoming A-race. The athlete will read this on race morning. "
    "Keep the tone calm, specific, and grounded in their actual data. Bias "
    "toward conservative, defensible advice — a wrong pacing call breaks the race.\n\n"
    "Rules:\n"
    "1. Pacing corridor must be low/target/cap, not a single number. Target sits "
    "  inside the athlete's last-6-week training band; cap is the do-not-exceed "
    "  ceiling tied to threshold/zones.\n"
    "2. HR ceilings come from the athlete's zones (lthr / max_hr) when provided. "
    "  Do not invent HR ceilings if zones are missing — omit the field.\n"
    "3. Fueling 60-90 g/hr unless the athlete has explicit higher gut-training "
    "  evidence in the activity log.\n"
    "4. Transitions only for triathlon/duathlon/aquathlon races.\n"
    "5. Provide 3-5 contingencies. Default trio (heat / cramp / off-pace) is a "
    "  starting point, NOT a quota — pick what's actually relevant to this race's "
    "  distance, discipline, and conditions if provided. Heat scenario when "
    "  expected_temp_c < 18 is wasted attention; mech-failure for swim-only is "
    "  nonsense.\n"
    "6. **Race conditions (when ``race_conditions`` is in context):** flat course "
    "  (elevation_gain_m < 200) → bike cap nudges toward the upper end of the FTP "
    "  band (75-78%). Hilly (>500m gain on 70.3 distance, scale up for IM) → cap "
    "  drops 3-5% to leave headroom for climbs. Hot (expected_temp_c ≥ 25) → upgrade "
    "  the heat contingency, drop pacing 3-5%, raise sodium target. Cool (<10°C) → "
    "  warmup gets longer + clothing-strategy contingency.\n"
    "7. **Bike→Run constraint (triathlon only):** bike NP cap MUST be calibrated "
    "  to the run goal. Strong run goal → bike cap 75-78% FTP. Conservative run "
    "  goal → bike cap 70-72% FTP. Independent leg corridors that would cumulatively "
    "  destroy the run are unacceptable — the run is where finish-time is realised.\n"
    "8. **Negative-split run in triathlon:** for the run leg in a triathlon, "
    "  hr_ceiling_bpm in the FIRST 1/3 must NOT exceed Z2-high. Marathon-segment of "
    "  IM is where the race is won or lost — heroic-start plans break the race.\n"
    "9. Respond in {response_language} (BCP-47). Keep technical terms (W, bpm, "
    "  min/km, FTP) in English — they're sport universals. Athletes read on a phone; "
    "  sentences short.\n"
    "10. Reply ONLY by calling the submit_race_plan tool. Do not produce prose "
    "  outside the tool call.\n"
)


# ---------------------------------------------------------------------------
#  Pure helpers (no I/O — used by build_race_plan + tests)
# ---------------------------------------------------------------------------


def _resolve_confidence_tier(days_to_race: int) -> str:
    """Map days-to-race onto a confidence tier (replaces binary preliminary).

    Cutoffs (per RACE_PLAN_SPEC §3): ``final <7d``, ``late [7,14)``,
    ``mid [14,60)``, ``early ≥60``. The 200-day upper bound documented in the
    spec is enforced by ``build_race_plan`` (gate refuses before reaching
    here), so this resolver doesn't clamp — anything ≥60 returns ``early``.
    The server stamps the tier — Claude doesn't pick. UI renders 4 different
    warnings; intermediate tiers (mid/early) tell the athlete that corridors
    will tighten closer to race day.
    """
    if days_to_race < 7:
        return "final"
    if days_to_race < 14:
        return "late"
    if days_to_race < 60:
        return "mid"
    return "early"


def _resolve_coach_role(sport_type: str | None) -> str:
    """Map athlete-goal sport_type onto a system-prompt coach role.

    Without this, the prompt was hardcoded as 'triathlon and endurance coach'
    even for run-only / cycling-only / swim-only goals. Mismatch is small but
    visible; matters more once we ship to users beyond the owner.
    """
    sport = (sport_type or "").lower()
    if sport in {"triathlon", "duathlon", "aquathlon"}:
        return "triathlon and endurance coach"
    if sport == "run":
        return "running coach"
    if sport == "ride":
        return "cycling coach"
    if sport == "swim":
        return "swim coach"
    return "endurance coach"  # fitness / other / unknown


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
        for a in sorted(activities, key=lambda x: x.start_date_local or "", reverse=True)
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


def _summarize_user_facts(facts: list[Any]) -> list[dict[str, Any]]:
    """Filter user_facts to RACE_PLAN_FACT_TOPICS and emit prompt-ready dicts.

    Returns ``[]`` if athlete has no race-relevant facts — Claude works fine
    without them, just falls back to averaged-population assumptions.
    """
    out: list[dict[str, Any]] = []
    for f in facts:
        if f.topic not in RACE_PLAN_FACT_TOPICS:
            continue
        out.append(
            {
                "topic": f.topic,
                "fact": f.fact,
                "expires_at": f.expires_at.isoformat() if f.expires_at else None,
            }
        )
    return out


def _summarize_races(rows: list[tuple[Race, str, str | None]]) -> list[dict[str, Any]]:
    """Compact prior-race snapshot for the prompt.

    Spec §4 Phase 2.5 enrichment: Race history is the single best predictor
    of next-race pacing. Hand Claude finish/RPE/race-day fitness so corridors
    are anchored to actual prior performance, not hallucinated from training
    summaries alone. ``activity_date`` and ``activity_type`` come from the
    join in ``Race.get_recent_for_user`` to avoid N+1 lookups.
    """
    out: list[dict[str, Any]] = []
    for race, activity_date, activity_type in rows:
        out.append(
            {
                "name": race.name,
                "date": activity_date,
                "activity_type": activity_type,
                "race_type": race.race_type,
                "distance_m": race.distance_m,
                "finish_time_sec": race.finish_time_sec,
                "goal_time_sec": race.goal_time_sec,
                "placement": race.placement,
                "rpe": race.rpe,
                "elevation_gain_m": race.elevation_gain_m,
                "weather": race.weather,
                "race_day_ctl": race.race_day_ctl,
                "race_day_atl": race.race_day_atl,
                "race_day_tsb": race.race_day_tsb,
                "race_day_hrv_status": race.race_day_hrv_status,
                "race_day_recovery_score": race.race_day_recovery_score,
                "notes": race.notes,
            }
        )
    return out


# Pace string forms we'll attempt to parse for corridor sanity checks. Anything
# we can't parse cleanly is *skipped* (validator stays defensive — false rejects
# break a plan that's otherwise fine).
_PACE_RE = re.compile(r"^\s*(\d+):(\d{2})\s*/\s*(km|100m|mi)\s*$", re.IGNORECASE)
_POWER_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*w\s*$", re.IGNORECASE)


def _parse_corridor_value(s: Any) -> tuple[float, str] | None:
    """Parse a pacing-corridor entry to (effort, unit_kind).

    ``effort`` is normalized so larger == harder (we negate pace seconds; we
    leave power as-is). ``unit_kind`` lets us reject mixed-unit corridors.
    Returns ``None`` for unparseable input — caller skips the ordering check.
    """
    if not isinstance(s, str):
        return None
    pace = _PACE_RE.match(s)
    if pace:
        secs = int(pace.group(1)) * 60 + int(pace.group(2))
        return (-float(secs), f"pace_{pace.group(3).lower()}")
    power = _POWER_RE.match(s)
    if power:
        return (float(power.group(1).replace(",", ".")), "power")
    return None


_DISTANCE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(km|m|mi)\s*$", re.IGNORECASE)

# Canonical race distances + goal-time floors for inference from event_name.
# Distances in metres; goal_time_sec is a CONSERVATIVE floor — used for
# leg-duration plausibility check (an athlete can't realistically beat this
# even at world-class pace, so any leg implying more time than this is wrong).
# Match order matters: more specific ("70.3" / "ironman 70.3") before "ironman".
_CANONICAL_RACES: tuple[tuple[str, float, int], ...] = (
    ("70.3", 113_000, 14_400),  # Half IM — sub-4h is elite floor
    ("half ironman", 113_000, 14_400),
    ("half-ironman", 113_000, 14_400),
    ("140.6", 226_000, 28_800),  # Full IM — sub-8h is pro floor
    ("ironman", 226_000, 28_800),
    ("olympic", 51_500, 7_200),  # Olympic tri — sub-2h elite floor
    ("sprint triathlon", 25_750, 3_600),
    ("sprint", 25_750, 3_600),
    ("marathon", 42_195, 7_200),  # sub-2h elite floor
    ("half marathon", 21_098, 3_600),
    ("half-marathon", 21_098, 3_600),
)


def _parse_distance_to_m(s: Any) -> float | None:
    """Parse a human distance string ("1.5 km", "800 m", "21.1 km") to metres.
    Returns None for unparseable input."""
    if not isinstance(s, str):
        return None
    m = _DISTANCE_RE.match(s)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    if unit == "km":
        return value * 1000.0
    if unit == "mi":
        return value * 1609.344
    return value  # "m"


def _infer_race_distance_and_floor(event_name: str | None) -> tuple[float | None, int | None]:
    """Infer (race_total_m, goal_time_floor_sec) from a canonical race name.

    Returns (None, None) for unknown events. Used by validator to enable
    distance-sum and leg-duration sanity checks without needing the user to
    type race-total manually. Heuristic; bias toward NOT matching (false
    positive on distance check is worse than skipping the check).
    """
    if not event_name:
        return (None, None)
    name_lower = event_name.lower()
    for keyword, total_m, floor_sec in _CANONICAL_RACES:
        if keyword in name_lower:
            return (total_m, floor_sec)
    return (None, None)


def _validate_race_plan(
    plan: dict[str, Any],
    *,
    athlete_max_hr: int | None,
    race_total_m: float | None = None,
    goal_time_floor_sec: int | None = None,
) -> list[str]:
    """Defensive post-generation checks the JSON schema can't enforce.

    Returns a list of human-readable error strings (empty == valid). Used to
    refuse persistence when the model produced a structurally valid but
    physiologically nonsensical plan (e.g. cap < target < low for a pace
    corridor, HR ceiling above the athlete's documented max).

    Optional kwargs ``race_total_m`` and ``goal_time_floor_sec`` enable the
    distance-sum and leg-duration plausibility checks. Caller infers them via
    ``_infer_race_distance_and_floor(event_name)`` for canonical races; passes
    None for ad-hoc events (those checks are then skipped, not failed).
    """
    errors: list[str] = []

    legs = plan.get("legs") or []
    for idx, leg in enumerate(legs):
        leg_name = leg.get("leg") or f"#{idx}"

        # Corridor ordering: low < target < cap in effort space.
        # All-prose corridors (e.g. "easy/threshold/hard") skip the ordering
        # check — Claude legitimately uses prose for unstructured legs. But a
        # MIX of numeric + prose escapes both ordering AND unit checks, which
        # is exactly the malformed pattern the validator exists to catch
        # (e.g. low="5:30/km", target="5:00/km", cap="threshold pace").
        pacing = leg.get("pacing") or {}
        parsed = [_parse_corridor_value(pacing.get(k)) for k in ("low", "target", "cap")]
        parsed_count = sum(1 for p in parsed if p is not None)
        if 0 < parsed_count < 3:
            errors.append(f"leg {leg_name}: corridor mixes numeric with free-form values")
        elif parsed_count == 3:
            units = {u for _, u in parsed}  # type: ignore[misc]
            if len(units) > 1:
                errors.append(f"leg {leg_name}: corridor mixes units {units}")
            else:
                vals = [v for v, _ in parsed]  # type: ignore[misc]
                if not (vals[0] < vals[1] < vals[2]):
                    errors.append(f"leg {leg_name}: corridor not low<target<cap")

        # HR ceiling sanity vs athlete max.
        hr = leg.get("hr_ceiling_bpm")
        if isinstance(hr, int) and athlete_max_hr is not None and hr > athlete_max_hr + 5:
            errors.append(f"leg {leg_name}: hr_ceiling_bpm {hr} exceeds athlete max+5 ({athlete_max_hr + 5})")

    # ---------- Sum of leg distances ≈ race total ----------
    if race_total_m is not None and race_total_m > 0:
        leg_distances = [_parse_distance_to_m(leg.get("distance")) for leg in legs]
        valid_distances = [d for d in leg_distances if d is not None]
        if valid_distances:
            total = sum(valid_distances)
            tolerance = max(500.0, race_total_m * 0.05)
            if abs(total - race_total_m) > tolerance:
                errors.append(
                    f"sum of leg distances ({total:.0f}m) deviates from race total "
                    f"({race_total_m:.0f}m) by more than {tolerance:.0f}m"
                )

    # ---------- Fueling × duration sanity ----------
    if goal_time_floor_sec is not None and goal_time_floor_sec > 0:
        fueling = plan.get("fueling") or {}
        carbs_per_hour = fueling.get("carbs_g_per_hour")
        if isinstance(carbs_per_hour, (int, float)) and carbs_per_hour > 0:
            duration_h = goal_time_floor_sec / 3600
            total_carbs = carbs_per_hour * duration_h
            if not (100 <= total_carbs <= 1500):
                errors.append(
                    f"fueling × duration sanity: {carbs_per_hour}g/hr × "
                    f"{duration_h:.1f}h = {total_carbs:.0f}g (expected 100-1500g)"
                )
            elif duration_h >= 4 and carbs_per_hour < 50:
                errors.append(
                    f"fueling × duration sanity: {carbs_per_hour}g/hr is below the "
                    f"50g/hr floor for races ≥4h ({duration_h:.1f}h) — race-day starvation"
                )

    # ---------- Per-leg duration plausibility ----------
    if goal_time_floor_sec is not None and goal_time_floor_sec > 0:
        SWIM_S_PER_M = 0.90  # 1:30 per 100m
        BIKE_S_PER_M = 0.144  # 25 km/h
        RUN_S_PER_M = 0.240  # 4:00 per km
        for leg in legs:
            d = _parse_distance_to_m(leg.get("distance"))
            if d is None:
                continue
            leg_lower = (leg.get("leg") or "").lower()
            if "swim" in leg_lower:
                implied = d * SWIM_S_PER_M
            elif "bike" in leg_lower or "ride" in leg_lower or "cycl" in leg_lower:
                implied = d * BIKE_S_PER_M
            elif "run" in leg_lower:
                implied = d * RUN_S_PER_M
            else:
                continue  # T1/T2/segment-N — no per-sport pace to check against
            if implied > goal_time_floor_sec:
                errors.append(
                    f"leg {leg.get('leg')}: distance {d:.0f}m implies "
                    f"≥{implied / 3600:.1f}h at conservative pace, exceeds race "
                    f"floor {goal_time_floor_sec / 3600:.1f}h"
                )

    return errors


def _athlete_max_hr(zones_rows: list[AthleteSettings]) -> int | None:
    """Highest documented max_hr across the athlete's per-sport zone rows."""
    candidates = [s.max_hr for s in zones_rows if getattr(s, "max_hr", None)]
    return max(candidates) if candidates else None


# ---------------------------------------------------------------------------
#  Main entry point
# ---------------------------------------------------------------------------


# Per-day cap on force_regen calls per (user_id, goal_id). Limit is INCLUSIVE
# in the gate check (``existing_regen_count >= LIMIT``): with limit=1 the first
# regen is allowed, the second refused. With limit=3 the 3rd regen is allowed
# but the 4th refused. See review L6 (2026-05-09).
RACE_PLAN_REGEN_DAILY_LIMIT = 1

# Per-day cap on dry_run calls per user (cost guard, security review secH1
# 2026-05-09). dry_run intentionally bypasses the regen rate-limit (preview
# shouldn't consume a slot — spec §7), but each dry_run still costs Claude
# tokens. Without this gate an authenticated athlete could loop dry_run
# requests and burn ~7K tokens per call. 5/day per user is enough for
# legitimate "preview → decide → commit" UX (1-2 previews max in practice);
# malicious flooding is refused. Tracked in Redis with 25h TTL — fail-open
# if Redis is unavailable (preserves dev workflow without Redis).
RACE_PLAN_DRY_RUN_DAILY_LIMIT = 5


async def _check_dry_run_quota(user_id: int) -> tuple[bool, int | None]:
    """Per-user, per-UTC-day dry_run quota (security review secH1).

    Returns ``(allowed, retry_after_sec)``. When Redis is unavailable the
    check fails OPEN (returns ``(True, None)``) — preserves dev environments
    without Redis at the cost of leaving the abuse vector open in that
    config. In production Redis is always up.

    Counter uses ``INCR`` + ``EXPIRE`` 25h on first hit (slightly > day so
    midnight rollover doesn't drop the key while we're still on day N).
    """
    client = get_redis()
    if client is None:
        return (True, None)

    today_iso = datetime.now(timezone.utc).date().isoformat()
    key = f"race_plan:dry_run:{user_id}:{today_iso}"
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, 25 * 3600)
        if count > RACE_PLAN_DRY_RUN_DAILY_LIMIT:
            now = datetime.now(timezone.utc)
            tomorrow = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
            return (False, max(1, int((tomorrow - now).total_seconds())))
        return (True, None)
    except Exception:
        logger.warning("dry_run quota check failed (Redis error) — fail-open", exc_info=True)
        return (True, None)


async def build_race_plan(
    user_id: int,
    *,
    goal_id: int | None = None,
    dry_run: bool = False,
    force_regen: bool = False,
    race_conditions: dict[str, float] | None = None,
) -> dict:
    """Generate a structured race-execution plan for an upcoming A-race.

    Single source of truth used by both the MCP tool ``generate_race_plan``
    and the REST endpoint ``POST /api/race-plan/generate``. ``user_id`` is
    resolved by the caller (contextvars for MCP, auth dep for REST).

    The function refuses to generate a plan when:
      - the athlete has fewer than 6 distinct activities in the last 6 weeks
        (no evidence to calibrate the pacing corridor), OR
      - the race is more than 200 days away (the fitness projection has
        decayed too far for the corridor to be defensible).

    Plans are tagged with a ``confidence_tier`` enum so the surface can warn
    the athlete how settled the corridor is: ``final`` <7d, ``late`` 7-14d,
    ``mid`` 14-60d, ``early`` 60-200d (replaces the old binary preliminary
    flag — see RACE_PLAN_SPEC §3).

    Parameters:
      user_id: athlete id from contextvars (MCP) or auth dep (REST).
      goal_id: athlete_goals.id — usually omitted; defaults to RACE_A.
      dry_run: True → return the generated payload only, do NOT persist.
      force_regen: True → bypass idempotency pre-check, run a fresh Claude
        call, and overwrite the existing row in-place (preserve id). Subject
        to a per-day rate limit (``RACE_PLAN_REGEN_DAILY_LIMIT``) tracked in
        ``payload.regen_count_today``. Returns ``{"error": "rate limit", ...}``
        with ``retry_after_sec`` if the limit is hit. Spec §7.
      race_conditions: optional ``{elevation_gain_m, expected_temp_c}`` dict.
        Surfaces in the prompt context so Claude tightens the corridor for
        the actual course (flat → bike cap closer to 78% FTP; hot → heat
        contingency upgraded). PR2.5 / spec §3 Phase 2.5 schema extension.
        Caller is the REST endpoint (web UI form) or the MCP tool kwarg.
    """
    # ---------- 1. Resolve goal ----------
    if goal_id is not None:
        async with get_session() as session:
            goal = (
                await session.execute(
                    select(AthleteGoal).where(
                        AthleteGoal.id == goal_id,
                        AthleteGoal.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
        if goal is None:
            return {"error": f"Goal {goal_id} not found for this athlete."}
    else:
        goal = await AthleteGoal.get_by_category(user_id, "RACE_A")
        if goal is None:
            return {
                "error": ("No active RACE_A goal — set one with /race or suggest_race before generating a race plan.")
            }

    # ---------- 1.5 dry_run quota gate (cost guard, secH1) ----------
    # dry_run intentionally bypasses the regen rate-limit (preview shouldn't
    # consume a slot — see §7), but each dry_run still spends ~7K Claude
    # tokens. Per-user 5/day Redis counter prevents loop-abuse without
    # blocking legitimate "preview → decide → commit" UX. Fires AFTER goal
    # resolve so unauthorised goal probes are still rejected by the
    # cross-tenant guard upstream — no information leak in the order.
    if dry_run:
        allowed, retry_after_sec = await _check_dry_run_quota(user_id)
        if not allowed:
            return {
                "error": (
                    f"rate limit: dry-run quota exhausted ({RACE_PLAN_DRY_RUN_DAILY_LIMIT}/day per user). "
                    "Wait for the next UTC day or commit a non-dry-run generation."
                ),
                "retry_after_sec": retry_after_sec,
            }

    # Idempotency pre-check + force_regen rate-limit gate.
    #
    # Default flow (force_regen=False): if today's row exists, return it
    # without Claude call (cost guard). Skipped for dry_run — callers may
    # want to preview a regenerated plan without persisting.
    #
    # Force-regen flow (force_regen=True, PR2.3): bypass the idempotent
    # return AND check the per-day rate limit (regen_count_today in payload).
    # Limit enforced BEFORE Claude call so a maxed-out user doesn't burn
    # tokens on a doomed regen. dry_run skips the rate-limit check entirely
    # (preview shouldn't consume a slot). See spec §7.
    existing_today: RacePlan | None = None
    existing_regen_count = 0
    if not dry_run:
        existing_today = await RacePlan.get_today_for_goal(goal.id, user_id=user_id)
        if existing_today is not None:
            existing_regen_count = (existing_today.payload or {}).get("regen_count_today", 0)
            if not force_regen:
                # Default idempotent return — no Claude call.
                return {
                    "id": existing_today.id,
                    "dry_run": False,
                    "confidence_tier": (existing_today.payload or {}).get("confidence_tier", "mid"),
                    "model_version": existing_today.model_version,
                    "payload": existing_today.payload,
                    "note": "Plan already generated today — returning the existing row.",
                }
            # force_regen=True path: rate-limit check.
            if existing_regen_count >= RACE_PLAN_REGEN_DAILY_LIMIT:
                now = datetime.now(timezone.utc)
                tomorrow_midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
                retry_after_sec = max(1, int((tomorrow_midnight - now).total_seconds()))
                return {
                    "error": (
                        f"rate limit: {existing_regen_count} regen(s) already used today "
                        f"(limit {RACE_PLAN_REGEN_DAILY_LIMIT}/day). Next available at the "
                        "start of the next UTC day."
                    ),
                    "retry_after_sec": retry_after_sec,
                    "next_available_at": tomorrow_midnight.isoformat(),
                }

    today = local_today()
    days_to_race = (goal.event_date - today).days

    if days_to_race > 200:
        return {
            "error": (
                f"Race is {days_to_race} days away (>200). The fitness projection "
                "isn't reliable that far out — re-run within ~6 months of race day."
            ),
            "race_date": str(goal.event_date),
            "days_to_race": days_to_race,
        }

    # ---------- 2. Pull 6 weeks of activities ----------
    six_weeks_ago = today - timedelta(weeks=6)
    activities, _last_synced = await Activity.get_range(user_id, six_weeks_ago, today)

    if len(activities) < 6:
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
                select(Wellness).where(Wellness.user_id == user_id).order_by(Wellness.date.desc()).limit(1)
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

    confidence_tier = _resolve_confidence_tier(days_to_race)

    # ---------- 4. Build prompt context ----------
    # User row pulled here for language pass-through to system prompt. Without
    # this, response language was guessed by Claude from data vibe — fragile.
    user_row = await User.get_by_id(user_id)
    response_language = (user_row.language if user_row else None) or "en"

    # Personal race history — single highest-ROI predictor of pacing per spec
    # §4 enrichment. Recency window 18 months: athlete 2 years ago at FTP 240W
    # vs today 285W has stale pacing. Cold-start fallback (drop the recency
    # filter) keeps the signal alive for newcomers; we tag it ``stale`` so
    # Claude weighs it accordingly.
    race_history_window_start = today - timedelta(days=18 * 30)
    recent_races = await Race.get_recent_for_user(
        user_id, sport_type=goal.sport_type, since=race_history_window_start, limit=5
    )
    race_history_stale = False
    if not recent_races:
        recent_races = await Race.get_recent_for_user(user_id, sport_type=goal.sport_type, limit=5)
        race_history_stale = bool(recent_races)

    # User facts (whitelist topics) — long-term memory injection per spec §4.
    # ``UserFact.list_active`` returns all active topics; whitelist filtering
    # happens in ``_summarize_user_facts`` so generic facts (dog name, etc.)
    # don't leak into the coaching prompt.
    all_active_facts = await UserFact.list_active(user_id)
    relevant_facts = _summarize_user_facts(all_active_facts)

    discipline = (goal.sport_type or "").lower()
    is_tri = discipline in {"triathlon", "duathlon", "aquathlon"}
    sport_role = _resolve_coach_role(goal.sport_type)

    # Goal name clamped to defend against prompt-injection via athlete-controlled
    # event_name (set by ``suggest_race`` MCP tool). Validator + JSON schema are
    # the deeper backstops; the clamp is the cheap first line.
    event_name_safe = (goal.event_name or "")[:100]

    context = {
        "race": {
            "id": goal.id,
            "name": event_name_safe,
            "date": race_day_str,
            "days_to_race": days_to_race,
            "discipline": goal.sport_type,
            "is_triathlon": is_tri,
            "ctl_target": goal.ctl_target,
            "confidence_tier": confidence_tier,
        },
        "today": today_snapshot,
        "race_day_projection": race_day_projection,
        "zones_by_sport": _summarize_zones(zones_rows),
        "training_last_6_weeks": _summarize_activities(activities),
        "race_history": {
            "stale": race_history_stale,
            "recency_window_months": None if race_history_stale else 18,
            "sport_filter": goal.sport_type,
            "races": _summarize_races(recent_races),
        },
        "user_facts": relevant_facts,
        # Optional course/weather hints from UI form OR auto-inherited from a
        # past Race row (PR2.5 / spec §3 + §11.10). Only emitted when present
        # so the prompt stays compact for ad-hoc events without conditions.
        "race_conditions": race_conditions if race_conditions else None,
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
    system_prompt = _RACE_PLAN_SYSTEM_PROMPT_TEMPLATE.format(
        sport_role=sport_role,
        response_language=response_language,
    )
    try:
        resp = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system_prompt,
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
    except Exception:
        logger.exception("build_race_plan: Claude call failed for user %d", user_id)
        return {"error": "Plan generation failed — please retry."}

    # Track token cost regardless of whether the response is structurally valid:
    # the validator and tool_use checks below can still reject, but the tokens
    # were already spent. Match the bot/agent.py increment shape.
    try:
        usage = resp.usage
        await ApiUsageDaily.increment(
            user_id=user_id,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )
    except Exception:
        logger.warning("build_race_plan: failed to track token usage for user %d", user_id, exc_info=True)

    plan_input: dict[str, Any] | None = None
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_race_plan":
            plan_input = dict(block.input) if block.input else None
            break

    if not plan_input:
        logger.warning("build_race_plan: model did not call submit_race_plan, stop_reason=%s", resp.stop_reason)
        return {"error": "Model did not return a structured plan. Try again."}

    # Defensive post-generation pass — schema can't enforce per-athlete HR or
    # corridor monotonicity. Refuse persistence on validation failure so we
    # don't ship a nonsensical plan to the athlete.
    # ``race_total_m`` / ``goal_time_floor_sec`` inferred from event_name for
    # canonical races (Ironman 70.3 / marathon / etc.); None for ad-hoc events,
    # which simply skips the distance-sum + leg-duration checks.
    race_total_m, goal_time_floor_sec = _infer_race_distance_and_floor(goal.event_name)
    validation_errors = _validate_race_plan(
        plan_input,
        athlete_max_hr=_athlete_max_hr(zones_rows),
        race_total_m=race_total_m,
        goal_time_floor_sec=goal_time_floor_sec,
    )
    if validation_errors:
        logger.warning(
            "build_race_plan: validation rejected plan for user %d, goal %d: %s",
            user_id,
            goal.id,
            "; ".join(validation_errors),
        )
        return {"error": "Generated plan failed validation — please retry."}

    # ``regen_count_today`` lives in the JSONB payload (no schema migration
    # needed). Counter resets implicitly each new UTC day because new rows on
    # day N+1 start from 0 — the day-N row stays at its final count but is no
    # longer surfaced by ``get_today_for_goal``. force_regen on a fresh-INSERT
    # day shouldn't burn the slot, so first force_regen of a new day starts
    # at count=1 rather than count=0+increment-on-next.
    will_regen_in_place = existing_today is not None and force_regen
    # On initial INSERT (not regen) we explicitly write ``regen_count_today=0``
    # rather than relying on the reader's ``.get(..., 0)`` default. Reason: if
    # a future change distinguishes "never regenerated" from "regenerated and
    # reset", a stored 0 vs missing key carry different semantics. Cheaper to
    # always write the field than to retrofit later (review N1, 2026-05-09).
    new_regen_count = (existing_regen_count + 1) if will_regen_in_place else 0
    payload: dict[str, Any] = {
        "plan": plan_input,
        "race": context["race"],
        "confidence_tier": confidence_tier,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": RACE_PLAN_MODEL_VERSION,
        "regen_count_today": new_regen_count,
    }

    # ---------- 6. Persist or short-circuit ----------
    if dry_run:
        return {
            "id": None,
            "dry_run": True,
            "confidence_tier": confidence_tier,
            "model_version": RACE_PLAN_MODEL_VERSION,
            "payload": payload,
        }

    if will_regen_in_place:
        # Force-regen path: in-place UPDATE preserves row id (Telegram deep-links
        # stay stable) and bumps generated_at + payload + model_version.
        # No IntegrityError risk — UPDATE-by-pk doesn't touch the unique index.
        row = await RacePlan.update_in_place(
            existing_today.id,
            user_id=user_id,
            model_version=RACE_PLAN_MODEL_VERSION,
            payload=payload,
        )
        if row is None:
            # Defensive: existing_today disappeared between pre-check and UPDATE
            # (e.g. concurrent goal deletion cascading SET NULL). Fall back to
            # an INSERT — better to write a fresh row than swallow the regen.
            logger.warning(
                "build_race_plan: existing_today vanished during regen for user %d, goal %d",
                user_id,
                goal.id,
            )
        else:
            return {
                "id": row.id,
                "dry_run": False,
                "confidence_tier": confidence_tier,
                "model_version": row.model_version,
                "payload": row.payload,
                "note": f"Plan regenerated in place (regen {new_regen_count}/{RACE_PLAN_REGEN_DAILY_LIMIT} today).",
            }

    try:
        row = await RacePlan.save(
            user_id=user_id,
            goal_id=goal.id,
            model_version=RACE_PLAN_MODEL_VERSION,
            payload=payload,
        )
    except IntegrityError:
        # Unique-violation on (goal_id, day): a parallel call beat us between
        # the pre-check and the insert. Return today's row so callers get the
        # same idempotent shape.
        logger.info("build_race_plan: race condition on uq_race_plans_goal_day — using existing row")
        existing = await RacePlan.get_today_for_goal(goal.id, user_id=user_id)
        if existing is not None:
            return {
                "id": existing.id,
                "dry_run": False,
                "confidence_tier": confidence_tier,
                "model_version": existing.model_version,
                "payload": existing.payload,
                "note": "Plan already generated today — returning the existing row.",
            }
        return {"error": "Plan persistence failed — please retry."}

    return {
        "id": row.id,
        "dry_run": False,
        "confidence_tier": confidence_tier,
        "model_version": row.model_version,
        "payload": row.payload,
    }
