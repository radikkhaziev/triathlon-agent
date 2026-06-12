"""REST endpoints for race-execution plans.

Backs the Goal-tab UI (PR2.4): ``GET`` returns the latest persisted plan for a
goal (or 404), ``POST /generate`` synchronously calls ``build_race_plan`` and
returns the freshly generated payload. Both share the service module
(``data.race_plan_service``) with the MCP tool ``generate_race_plan`` — the
single source of truth for plan-generation business logic.

Auth split (matches the rest of the dashboard):
- ``GET`` — ``require_viewer``: demo accounts can browse the owner's plan
  (read-only is the demo contract).
- ``POST /generate`` — ``require_athlete``: mutation, costs Claude tokens, and
  needs Intervals.icu data — both reasons demo can't fire it.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_data_user_id, is_demo, require_athlete, require_viewer
from data.db import AthleteGoal, Race, RacePlan, User, get_session
from data.race_plan_service import build_race_plan

router = APIRouter()


def _format_plan_response(row: RacePlan) -> dict:
    """Shape the JSON for ``GET /api/race-plan`` and the post-generate response.

    Pull ``confidence_tier`` out of the payload to the top level so the UI
    doesn't have to dig into the JSONB blob to render the badge. Strip the
    inner ``race`` snapshot and ``regen_count_today`` — the service writes
    them as goal-deletion / rate-limit bookkeeping; UI reads only the inner
    ``plan`` block plus the top-level fields above.
    """
    payload = dict(row.payload or {})
    payload.pop("race", None)
    payload.pop("regen_count_today", None)
    return {
        "model_version": row.model_version,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "confidence_tier": payload.get("confidence_tier", "mid"),
        "payload": payload,
    }


@router.get("/api/race-plan")
async def get_race_plan(
    goal_id: int = Query(..., description="athlete_goals.id — the goal to fetch the latest plan for"),
    user: User = Depends(require_viewer),
) -> dict:
    """Latest plan for ``goal_id``, or 404 if nothing generated yet.

    Verifies goal ownership in SQL (``WHERE id=? AND user_id=?``) before
    reading the plan, mirroring the defensive scoping in
    ``build_race_plan`` step 1 — a leaked goal_id can't surface a plan that
    belongs to another tenant.
    """
    uid = get_data_user_id(user)

    async with get_session() as session:
        goal = (
            await session.execute(select(AthleteGoal).where(AthleteGoal.id == goal_id, AthleteGoal.user_id == uid))
        ).scalar_one_or_none()
    if goal is None:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    row = await RacePlan.get_latest_for_race(goal_id, user_id=uid)
    if row is None:
        raise HTTPException(status_code=404, detail="No plan generated yet")

    if is_demo(user):
        # The payload's free-text (headline / warmup / leg notes / contingencies)
        # is AI-generated from private athlete context — never serialize it to
        # demo. The frontend skips this fetch entirely for demo sessions and
        # renders a canned sample; this branch is defense-in-depth.
        # See docs/DEMO_PUBLIC_ACCESS_SPEC.md Phase 2.
        return {
            "model_version": row.model_version,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
            "confidence_tier": (row.payload or {}).get("confidence_tier", "mid"),
            "demo_stub": True,
            "payload": {},
        }

    return _format_plan_response(row)


class RaceConditions(BaseModel):
    """Optional course/weather hints (PR2.5 / spec §3 Phase 2.5 schema).

    Both fields optional — UI may submit just one (e.g. elevation known but
    forecast not yet available). Bounds are sanity floors/ceilings: negative
    elevation → reject (typo guard), temperatures clamped to plausible-on-Earth
    range to catch unit-mix-ups (Fahrenheit accidentally entered as Celsius).
    """

    elevation_gain_m: float | None = Field(
        default=None, ge=0, le=15000, description="Total positive elevation in metres"
    )
    expected_temp_c: float | None = Field(
        default=None, ge=-50, le=60, description="Forecast race-day air temperature in °C"
    )


class GenerateRequest(BaseModel):
    """Body for ``POST /api/race-plan/generate``.

    All fields optional: ``goal_id`` defaults to the athlete's RACE_A goal;
    ``dry_run`` skips persistence (UI uses it to preview without writing);
    ``force_regen`` bypasses the idempotent same-day return and runs a fresh
    Claude call, subject to the 1/day rate limit (PR2.3, spec §7);
    ``race_conditions`` surfaces course/weather to Claude (PR2.5).
    """

    goal_id: int | None = Field(default=None, description="Specific goal; omit for RACE_A")
    dry_run: bool = Field(default=False, description="Generate but don't persist")
    force_regen: bool = Field(default=False, description="In-place regenerate (rate-limited 1/day)")
    race_conditions: RaceConditions | None = Field(
        default=None, description="Optional course/weather hints (elevation_gain_m, expected_temp_c)"
    )


# Maps service-error message fragments to HTTP status codes. Service returns
# {"error": "..."} dicts; we want REST-shaped responses with appropriate codes
# so the React surface can branch on status (404 vs 400 vs 429 vs 502 vs 500).
# Order matters — first match wins. ``rate limit`` is handled separately so we
# can attach a ``Retry-After`` header (see ``generate_race_plan_endpoint``).
_ERROR_STATUS_MAP: tuple[tuple[str, int], ...] = (
    ("rate limit", 429),  # force_regen daily quota exhausted
    ("not found for this athlete", 404),  # cross-tenant goal_id or missing
    ("No active RACE_A goal", 404),  # default-goal lookup miss
    # Full phrase used (not just "200") to avoid matching unrelated literals
    # like "HTTP 200" in future upstream-error wording. See review L7.
    ("days away (>200)", 400),  # gate: race too far out
    ("activities in the last 6 weeks", 400),  # gate: not enough training
    ("ANTHROPIC_API_KEY", 500),  # server config issue
    ("Plan generation failed", 502),  # Claude call failed
    ("Model did not return", 502),  # Claude returned non-tool_use
    ("failed validation", 502),  # validator caught nonsensical plan
    ("persistence failed", 500),  # DB write failed after IntegrityError fallback
)


def _http_status_for_service_error(message: str) -> int:
    """Translate a service ``{"error": <msg>}`` into an HTTP status code.
    Falls through to 400 for unrecognised messages — generic client error
    rather than 500 (the service has already returned a clean string)."""
    for fragment, status in _ERROR_STATUS_MAP:
        if fragment in message:
            return status
    return 400


@router.post("/api/race-plan/generate")
async def generate_race_plan_endpoint(
    req: GenerateRequest,
    user: User = Depends(require_athlete),
) -> dict:
    """Synchronously generate a plan (Claude call: 5-15s).

    Request body fields are forwarded to ``build_race_plan(user_id=...)``;
    ``user_id`` comes from auth, NEVER from the body — same multi-tenant
    invariant the MCP tool relies on (``get_current_user_id`` from contextvars).

    Service returns either ``{"error": ...}`` (mapped to HTTP via
    ``_ERROR_STATUS_MAP``) or a ``{id, dry_run, confidence_tier, ...}`` dict
    that we pass through unchanged. POST is intentionally synchronous — the
    UI's loading spinner spans the whole 5-15s; queueing this would only buy
    complexity without UX benefit at owner-scale.
    """
    uid = get_data_user_id(user)
    # Strip None fields from race_conditions before forwarding — Pydantic emits
    # explicit ``None`` for unset optionals, and the service treats an empty
    # dict the same as "nothing supplied" (see context-build conditional).
    rc_dict: dict[str, float] | None = None
    if req.race_conditions is not None:
        rc_dict = req.race_conditions.model_dump(exclude_none=True) or None

    out = await build_race_plan(
        user_id=uid,
        goal_id=req.goal_id,
        dry_run=req.dry_run,
        force_regen=req.force_regen,
        race_conditions=rc_dict,
    )

    if "error" in out:
        status = _http_status_for_service_error(out["error"])
        # 429 needs Retry-After header per RFC 6585. Service emits
        # ``retry_after_sec`` for the rate-limit case; default to 1h if absent
        # (defensive — the service always sets it for the rate-limit error).
        headers: dict[str, str] | None = None
        if status == 429:
            headers = {"Retry-After": str(out.get("retry_after_sec", 3600))}
        raise HTTPException(status_code=status, detail=out, headers=headers)

    return out


@router.get("/api/race-plan/inheritable-conditions")
async def inheritable_conditions(
    goal_id: int = Query(..., description="Goal whose sport_type narrows the search"),
    user: User = Depends(require_viewer),
) -> dict:
    """Past races whose conditions can pre-fill the conditions form (PR2.5 / spec §11.10).

    Filters by ``goal.sport_type`` — same-sport history is the only relevant
    signal (a 70.3 athlete doesn't want to inherit conditions from a 5km park
    run). Returns up to 5 rows. UI surfaces this as a dropdown above the
    conditions inputs; choosing an entry pre-populates ``elevation_gain_m`` /
    ``expected_temp_c`` defaults that the user can still edit.

    Why a UI selector and not name-matching: ``"Oceanlava 2024"`` vs
    ``"OceanLava Montenegro 2024"`` is fragile, and false-inherit from an
    unrelated race is worse than no inherit. Explicit selection lets the
    athlete confirm intent.
    """
    uid = get_data_user_id(user)

    async with get_session() as session:
        goal = (
            await session.execute(select(AthleteGoal).where(AthleteGoal.id == goal_id, AthleteGoal.user_id == uid))
        ).scalar_one_or_none()
    if goal is None:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    rows = await Race.get_recent_for_user(uid, sport_type=goal.sport_type, limit=5)
    return {
        "races": [
            {
                "id": race.id,
                "name": race.name,
                "date": activity_date,
                "elevation_gain_m": race.elevation_gain_m,
                "weather": race.weather,
            }
            for race, activity_date, _activity_type in rows
        ],
    }
