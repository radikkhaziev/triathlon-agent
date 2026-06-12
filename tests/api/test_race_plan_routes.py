"""Tests for /api/race-plan endpoints (PR2.2).

GET returns latest persisted plan or 404; POST forwards to build_race_plan
service and maps {error: ...} dicts to appropriate HTTP statuses. Auth split:
GET = require_viewer (demo can browse), POST = require_athlete (mutation).
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401  — pytest-asyncio collects via marker auto-discovery
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import require_athlete, require_viewer
from api.routers.race_plan import router as race_plan_router
from data.db import AthleteGoal, RacePlan, get_session


def _build_client(role: str = "owner") -> AsyncClient:
    """ASGI test client with auth deps overridden to a stub user_id=1."""
    test_app = FastAPI()
    test_app.include_router(race_plan_router)

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.role = role
    mock_user.is_active = True
    mock_user.athlete_id = "12345"
    test_app.dependency_overrides[require_viewer] = lambda: mock_user
    test_app.dependency_overrides[require_athlete] = lambda: mock_user
    return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")


def _build_viewer_only_client() -> AsyncClient:
    """Client where ``require_viewer`` resolves but ``require_athlete`` 403s.

    Mimics what happens for a demo-role user: GET endpoints accept them
    (read-only browse), POST /generate refuses with 403. Without this fixture
    the auth split documented in api/routers/race_plan.py is asserted only
    by inspection. See review L5 (2026-05-09)."""
    from fastapi import HTTPException

    test_app = FastAPI()
    test_app.include_router(race_plan_router)

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.role = "demo"
    mock_user.is_active = True
    mock_user.athlete_id = None  # demo lacks Intervals.icu
    test_app.dependency_overrides[require_viewer] = lambda: mock_user

    def _no_athlete():
        raise HTTPException(status_code=403, detail="Read-only demo mode")

    test_app.dependency_overrides[require_athlete] = _no_athlete
    return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")


async def _seed_goal(*, goal_id: int = 1, user_id: int = 1) -> int:
    """Insert an AthleteGoal so race-plan FK + ownership check pass."""
    async with get_session() as session:
        session.add(
            AthleteGoal(
                id=goal_id,
                user_id=user_id,
                category="RACE_A",
                event_name="Drina Trail",
                event_date=date.today() + timedelta(days=30),
                sport_type="triathlon",
            )
        )
        await session.commit()
        return goal_id


# ---------------------------------------------------------------------------
# GET /api/race-plan
# ---------------------------------------------------------------------------


class TestGetRacePlan:
    async def test_returns_404_when_goal_does_not_exist(self):
        client = _build_client()
        async with client as c:
            resp = await c.get("/api/race-plan", params={"goal_id": 999})
        assert resp.status_code == 404
        assert "Goal 999 not found" in resp.json()["detail"]

    async def test_returns_404_when_goal_belongs_to_another_user(self):
        """Cross-tenant ownership check — the SELECT is scoped by user_id, so a
        goal belonging to user 2 surfaces as 'not found' (no existence leak)."""
        # Seed second user via raw User insert (test_session fixture only seeds id=1).
        from data.db import User

        async with get_session() as session:
            session.add(User(id=2, chat_id="other", role="athlete"))
            await session.commit()
        await _seed_goal(goal_id=42, user_id=2)

        client = _build_client()  # acts as user 1
        async with client as c:
            resp = await c.get("/api/race-plan", params={"goal_id": 42})
        assert resp.status_code == 404
        assert "Goal 42 not found" in resp.json()["detail"]

    async def test_returns_404_when_goal_exists_but_no_plan_yet(self):
        await _seed_goal(goal_id=1)

        client = _build_client()
        async with client as c:
            resp = await c.get("/api/race-plan", params={"goal_id": 1})
        assert resp.status_code == 404
        assert "No plan generated yet" in resp.json()["detail"]

    async def test_returns_latest_plan(self):
        await _seed_goal(goal_id=1)
        # Seed two plans; expect the latest one (highest generated_at) returned.
        # Our save() commits today UTC; since unique index is (goal_id, day) we
        # can't write two for today — write ONE and verify shape.
        await RacePlan.save(
            user_id=1,
            goal_id=1,
            model_version="v1-test",
            payload={
                "plan": {"warmup": "10 min easy", "legs": []},
                "race": {"id": 1, "name": "Drina Trail"},
                "confidence_tier": "mid",
            },
        )

        client = _build_client()
        async with client as c:
            resp = await c.get("/api/race-plan", params={"goal_id": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert body["model_version"] == "v1-test"
        assert body["confidence_tier"] == "mid"  # surfaced from payload
        assert body["payload"]["plan"]["warmup"] == "10 min easy"
        # `race` snapshot is stripped from payload by `_format_plan_response`.
        assert "race" not in body["payload"]
        assert body["generated_at"] is not None

    async def test_returns_400_when_goal_id_missing(self):
        """FastAPI Query(...) with required marker → 422 from validation."""
        client = _build_client()
        async with client as c:
            resp = await c.get("/api/race-plan")
        assert resp.status_code == 422

    async def test_demo_gets_stub_without_ai_payload(self):
        """Demo sessions must never receive the plan's AI free-text — the
        endpoint returns a `demo_stub` envelope with an empty payload and the
        frontend renders a canned sample (docs/DEMO_PUBLIC_ACCESS_SPEC.md
        Phase 2)."""
        await _seed_goal(goal_id=1)
        await RacePlan.save(
            user_id=1,
            goal_id=1,
            model_version="v1-test",
            payload={
                "plan": {"warmup": "разогрев с личным контекстом", "legs": []},
                "confidence_tier": "high",
            },
        )

        client = _build_client(role="demo")
        async with client as c:
            resp = await c.get("/api/race-plan", params={"goal_id": 1})

        assert resp.status_code == 200
        body = resp.json()
        assert body["demo_stub"] is True
        assert body["payload"] == {}
        assert body["confidence_tier"] == "high"
        assert "разогрев" not in resp.text


# ---------------------------------------------------------------------------
# POST /api/race-plan/generate
# ---------------------------------------------------------------------------


class TestGenerateEndpoint:
    """Endpoint forwards to build_race_plan(user_id=auth.user.id, ...) — we
    patch the service to return canned shapes and verify HTTP mapping."""

    async def test_passes_user_id_from_auth_not_body(self):
        """Multi-tenant invariant: user_id MUST come from auth, never from
        the request body. Even if someone POSTs {goal_id, user_id} the
        service is called with user_id from the auth-resolved User object."""
        client = _build_client()
        captured: dict = {}

        async def fake_build(*, user_id, goal_id=None, dry_run=False, force_regen=False, race_conditions=None):
            captured["user_id"] = user_id
            captured["goal_id"] = goal_id
            captured["dry_run"] = dry_run
            captured["force_regen"] = force_regen
            return {"id": 7, "dry_run": dry_run, "confidence_tier": "mid", "model_version": "v1", "payload": {}}

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                # Pretend a malicious client tries to inject user_id — Pydantic
                # GenerateRequest model doesn't have that field, so it's ignored.
                resp = await c.post(
                    "/api/race-plan/generate",
                    json={"goal_id": 42, "dry_run": True, "user_id": 999},
                )

        assert resp.status_code == 200
        # user_id from auth (mock_user.id=1), NOT from body
        assert captured["user_id"] == 1
        assert captured["goal_id"] == 42
        assert captured["dry_run"] is True

    async def test_passthrough_success_response(self):
        client = _build_client()

        async def fake_build(*, user_id, goal_id=None, dry_run=False, force_regen=False, race_conditions=None):
            return {
                "id": 99,
                "dry_run": False,
                "confidence_tier": "late",
                "model_version": "v1-test",
                "payload": {"plan": {"warmup": "..."}, "confidence_tier": "late"},
            }

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == 99
        assert body["confidence_tier"] == "late"

    async def test_maps_goal_not_found_to_404(self):
        client = _build_client()

        async def fake_build(**_):
            return {"error": "Goal 99 not found for this athlete."}

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={"goal_id": 99})
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]["error"]

    async def test_maps_no_active_race_a_to_404(self):
        client = _build_client()

        async def fake_build(**_):
            return {
                "error": ("No active RACE_A goal — set one with /race or suggest_race before generating a race plan.")
            }

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={})
        assert resp.status_code == 404

    async def test_maps_too_far_out_to_400(self):
        client = _build_client()

        async def fake_build(**_):
            return {
                "error": (
                    "Race is 250 days away (>200). The fitness projection isn't reliable that far out — "
                    "re-run within ~6 months of race day."
                ),
                "race_date": "2027-01-15",
                "days_to_race": 250,
            }

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={})
        assert resp.status_code == 400
        assert resp.json()["detail"]["days_to_race"] == 250

    async def test_maps_too_few_activities_to_400(self):
        client = _build_client()

        async def fake_build(**_):
            return {
                "error": (
                    "Only 2 activities in the last 6 weeks — not enough training history "
                    "to calibrate a pacing corridor. Sync Intervals.icu and try again."
                ),
                "activity_count": 2,
            }

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={})
        assert resp.status_code == 400
        assert resp.json()["detail"]["activity_count"] == 2

    async def test_maps_claude_failure_to_502(self):
        client = _build_client()

        async def fake_build(**_):
            return {"error": "Plan generation failed — please retry."}

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={})
        assert resp.status_code == 502

    async def test_maps_no_tool_use_block_to_502(self):
        client = _build_client()

        async def fake_build(**_):
            return {"error": "Model did not return a structured plan. Try again."}

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={})
        assert resp.status_code == 502

    async def test_maps_validation_failure_to_502(self):
        client = _build_client()

        async def fake_build(**_):
            return {"error": "Generated plan failed validation — please retry."}

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={})
        assert resp.status_code == 502

    async def test_maps_unknown_error_to_400(self):
        """Fall-through: unrecognised service errors get 400 (client should
        retry / report) rather than masking with 500."""
        client = _build_client()

        async def fake_build(**_):
            return {"error": "Some new failure mode we haven't classified yet."}

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={})
        assert resp.status_code == 400

    async def test_force_regen_passes_through(self):
        """``force_regen=True`` in body must reach build_race_plan as kwarg."""
        client = _build_client()
        captured: dict = {}

        async def fake_build(*, user_id, goal_id=None, dry_run=False, force_regen=False, race_conditions=None):
            captured["force_regen"] = force_regen
            return {"id": 7, "dry_run": False, "confidence_tier": "mid", "model_version": "v1", "payload": {}}

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={"force_regen": True})

        assert resp.status_code == 200
        assert captured["force_regen"] is True

    async def test_maps_rate_limit_to_429_with_retry_after_header(self):
        """``rate limit`` error → 429 + Retry-After header per RFC 6585.
        UI uses the header to display 'Next regen in HH:MM'."""
        client = _build_client()

        async def fake_build(**_):
            return {
                "error": "rate limit: 1 regen(s) already used today (limit 1/day). Next available …",
                "retry_after_sec": 36000,  # 10 hours
                "next_available_at": "2026-05-10T00:00:00+00:00",
            }

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={"force_regen": True})

        assert resp.status_code == 429
        assert resp.headers.get("retry-after") == "36000"
        body = resp.json()
        assert body["detail"]["retry_after_sec"] == 36000
        assert "next_available_at" in body["detail"]


class TestAuthSplit:
    """Ensure GET=require_viewer / POST=require_athlete split actually holds.

    Without these tests, swapping auth deps on either route would silently
    change the contract — demo users gaining mutation rights or athletes
    losing read access. See review L5 (2026-05-09)."""

    async def test_demo_viewer_can_get_race_plan(self):
        """Demo can browse — same contract as the rest of the dashboard."""
        client = _build_viewer_only_client()
        async with client as c:
            # No goal seeded → 404, but the auth dep MUST resolve (no 403/401).
            resp = await c.get("/api/race-plan", params={"goal_id": 1})
        assert resp.status_code == 404, "GET should reach handler then 404 on missing goal, not be blocked by auth"

    async def test_demo_viewer_blocked_from_post_generate(self):
        """POST mutates + costs Claude tokens → require_athlete refuses demo."""
        client = _build_viewer_only_client()
        async with client as c:
            resp = await c.post("/api/race-plan/generate", json={})
        assert resp.status_code == 403
        assert "demo" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# PR2.5: race_conditions passthrough + inheritable-conditions endpoint
# ---------------------------------------------------------------------------


class TestRaceConditionsPassthrough:
    """``race_conditions`` Pydantic model in body forwarded to build_race_plan
    as a clean dict (None fields stripped). Bound validation reject malformed
    input early so the service never sees garbage. PR2.5."""

    async def test_forwards_race_conditions_dict_with_none_fields_stripped(self):
        client = _build_client()
        captured: dict = {}

        async def fake_build(*, user_id, goal_id=None, dry_run=False, force_regen=False, race_conditions=None):
            captured["race_conditions"] = race_conditions
            return {"id": 7, "dry_run": False, "confidence_tier": "mid", "model_version": "v1", "payload": {}}

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post(
                    "/api/race-plan/generate",
                    json={
                        "race_conditions": {"elevation_gain_m": 850, "expected_temp_c": None},
                    },
                )

        assert resp.status_code == 200
        # None field stripped, only the populated one reaches the service.
        assert captured["race_conditions"] == {"elevation_gain_m": 850.0}

    async def test_omits_race_conditions_when_body_field_absent(self):
        """Backward compat: existing clients (and pre-PR2.5 frontends) that
        don't send race_conditions still work — service gets None."""
        client = _build_client()
        captured: dict = {}

        async def fake_build(*, user_id, goal_id=None, dry_run=False, force_regen=False, race_conditions=None):
            captured["race_conditions"] = race_conditions
            return {"id": 7, "dry_run": False, "confidence_tier": "mid", "model_version": "v1", "payload": {}}

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post("/api/race-plan/generate", json={})

        assert resp.status_code == 200
        assert captured["race_conditions"] is None

    async def test_omits_race_conditions_when_all_fields_none(self):
        """Empty-after-strip → None passed (not {}), so service's truthy check
        works the same as 'field absent'."""
        client = _build_client()
        captured: dict = {}

        async def fake_build(*, user_id, goal_id=None, dry_run=False, force_regen=False, race_conditions=None):
            captured["race_conditions"] = race_conditions
            return {"id": 7, "dry_run": False, "confidence_tier": "mid", "model_version": "v1", "payload": {}}

        with patch("api.routers.race_plan.build_race_plan", side_effect=fake_build):
            async with client as c:
                resp = await c.post(
                    "/api/race-plan/generate",
                    json={"race_conditions": {"elevation_gain_m": None, "expected_temp_c": None}},
                )

        assert resp.status_code == 200
        assert captured["race_conditions"] is None

    async def test_rejects_negative_elevation(self):
        """Pydantic ge=0 catches typo (negative elevation is meaningless)."""
        client = _build_client()
        async with client as c:
            resp = await c.post(
                "/api/race-plan/generate",
                json={"race_conditions": {"elevation_gain_m": -100}},
            )
        assert resp.status_code == 422

    async def test_rejects_implausible_temperature(self):
        """Bound -50..60 catches Fahrenheit-as-Celsius unit mix-ups."""
        client = _build_client()
        async with client as c:
            resp = await c.post(
                "/api/race-plan/generate",
                json={"race_conditions": {"expected_temp_c": 90}},  # 90°C is volcanic
            )
        assert resp.status_code == 422


class TestInheritableConditionsEndpoint:
    """GET /api/race-plan/inheritable-conditions — dropdown source for the
    "inherit from previous race" UX (spec §11.10). Sport-filtered,
    user-scoped, capped at 5 rows."""

    async def test_returns_404_when_goal_does_not_exist(self):
        client = _build_client()
        async with client as c:
            resp = await c.get("/api/race-plan/inheritable-conditions", params={"goal_id": 999})
        assert resp.status_code == 404

    async def test_returns_404_when_goal_belongs_to_another_user(self):
        from data.db import User

        async with get_session() as session:
            session.add(User(id=2, chat_id="other", role="athlete"))
            await session.commit()
        await _seed_goal(goal_id=42, user_id=2)

        client = _build_client()
        async with client as c:
            resp = await c.get("/api/race-plan/inheritable-conditions", params={"goal_id": 42})
        assert resp.status_code == 404

    async def test_returns_empty_when_no_past_races(self):
        await _seed_goal(goal_id=1)
        client = _build_client()
        async with client as c:
            resp = await c.get("/api/race-plan/inheritable-conditions", params={"goal_id": 1})
        assert resp.status_code == 200
        assert resp.json() == {"races": []}

    async def test_returns_inheritable_fields_for_recent_races(self):
        from data.db import Activity, Race
        from data.intervals.dto import ActivityDTO

        await _seed_goal(goal_id=1)
        # Seed an activity + Race row with conditions populated.
        ref = date.today() - timedelta(days=30)
        await Activity.save_bulk(
            1,
            activities=[
                ActivityDTO(
                    id="act_prev",
                    start_date_local=ref,
                    type="Run",  # tri goal accepts any type — see Race.get_recent_for_user
                    icu_training_load=80.0,
                    moving_time=3600,
                    average_hr=150.0,
                )
            ],
        )
        async with get_session() as session:
            session.add(
                Race(
                    user_id=1,
                    activity_id="act_prev",
                    name="Oceanlava 2024",
                    elevation_gain_m=1200.0,
                    weather="sunny, 24°C",
                )
            )
            await session.commit()

        client = _build_client()
        async with client as c:
            resp = await c.get("/api/race-plan/inheritable-conditions", params={"goal_id": 1})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["races"]) == 1
        race = body["races"][0]
        assert race["name"] == "Oceanlava 2024"
        assert race["elevation_gain_m"] == 1200.0
        assert race["weather"] == "sunny, 24°C"
        assert race["date"] == ref.isoformat()
