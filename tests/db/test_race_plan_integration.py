"""Integration tests for RacePlan against a real Postgres test DB.

Complements the mock-based ``test_race_plan.py`` (SQL-shape + user_id scoping)
with end-to-end coverage of the partial unique index ``uq_race_plans_goal_day``
and the ``IntegrityError`` fallback path in the race-plan service.

The mock tests can't exercise the index (they don't actually round-trip through
Postgres). Without these tests the IntegrityError-handling branch in
``data/race_plan_service.py:build_race_plan`` would be dead-untested — added
in response to code-review M2a (2026-05-09).
"""

from datetime import date, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from data.db import AthleteGoal, RacePlan, get_session

PAYLOAD = {
    "plan": {"warmup": "10 min easy", "legs": []},
    "race": {"id": 1, "name": "Drina Trail"},
    "confidence_tier": "mid",
    "model_version": "v1-2026-05-09",
}


async def _seed_goal(*, goal_id: int = 1, user_id: int = 1) -> int:
    """Insert an AthleteGoal so RacePlan.goal_id FK resolves. Returns its id."""
    async with get_session() as session:
        goal = AthleteGoal(
            id=goal_id,
            user_id=user_id,
            category="RACE_A",
            event_name="Drina Trail",
            event_date=date.today() + timedelta(days=30),
            sport_type="triathlon",
        )
        session.add(goal)
        await session.commit()
        return goal.id


class TestPartialUniqueIndex:
    """``uq_race_plans_goal_day`` enforces (goal_id, UTC day) uniqueness when
    goal_id IS NOT NULL — partial index leaves NULL goal_id undeduplicated.
    These tests exercise the actual index, not a mock."""

    async def test_second_insert_same_goal_same_day_raises_integrity_error(self):
        goal_id = await _seed_goal()
        # First insert OK.
        await RacePlan.save(user_id=1, goal_id=goal_id, model_version="v1", payload=PAYLOAD)
        # Second insert same (goal_id, today UTC) → unique violation.
        with pytest.raises(IntegrityError):
            await RacePlan.save(user_id=1, goal_id=goal_id, model_version="v1", payload=PAYLOAD)

    async def test_two_inserts_with_null_goal_id_both_succeed(self):
        """Partial index condition is ``WHERE goal_id IS NOT NULL`` — NULLs
        are not deduplicated, allowing ad-hoc plans without a goal anchor."""
        await RacePlan.save(user_id=1, goal_id=None, model_version="v1", payload=PAYLOAD)
        # Second NULL-goal insert: also OK, no IntegrityError.
        await RacePlan.save(user_id=1, goal_id=None, model_version="v1", payload=PAYLOAD)
        # Verify both rows landed.
        async with get_session() as session:
            from sqlalchemy import func, select

            count = (
                await session.execute(select(func.count()).select_from(RacePlan).where(RacePlan.goal_id.is_(None)))
            ).scalar()
        assert count == 2

    async def test_different_goals_same_day_both_succeed(self):
        """Partial index keys on (goal_id, day) — different goals on the same
        day each get their own row."""
        goal_a = await _seed_goal(goal_id=1)
        goal_b = await _seed_goal(goal_id=2)
        await RacePlan.save(user_id=1, goal_id=goal_a, model_version="v1", payload=PAYLOAD)
        await RacePlan.save(user_id=1, goal_id=goal_b, model_version="v1", payload=PAYLOAD)
        # No IntegrityError: implicit pass.

    async def test_save_returns_row_with_loaded_scalars_post_session_close(self):
        """M1 follow-up: returned row's loaded scalars (id, model_version,
        payload, generated_at) must remain accessible after the @with_session
        wrapper closes the session — ``expire_on_commit=False`` + the explicit
        ``session.refresh(row)`` in ``save()`` guarantee this."""
        goal_id = await _seed_goal()
        row = await RacePlan.save(user_id=1, goal_id=goal_id, model_version="v1", payload=PAYLOAD)
        # Access scalars AFTER the wrapper has closed its session — should not
        # raise DetachedInstanceError.
        assert row.id is not None
        assert row.model_version == "v1"
        assert row.payload == PAYLOAD
        assert row.generated_at is not None
        assert row.user_id == 1
        assert row.goal_id == goal_id


class TestServiceIntegrityErrorFallback:
    """The race-condition fallback in ``build_race_plan``:
    pre-check sees no row → Claude fires → INSERT loses to a parallel INSERT
    that won the unique index → IntegrityError caught → fallback returns the
    winning row. Without this test that branch is dead-untested."""

    async def test_fallback_returns_existing_row_when_insert_loses_race(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from data.race_plan_service import build_race_plan

        goal_id = await _seed_goal()

        # Pre-seed the row that the "parallel" call would have inserted before us.
        winning_row = await RacePlan.save(
            user_id=1,
            goal_id=goal_id,
            model_version="v-winner",
            payload={**PAYLOAD, "marker": "winner"},
        )

        # Patch RacePlan.get_today_for_goal to return None on the FIRST call
        # (idempotency pre-check sees nothing — simulates the race window) and
        # the real winning row on the SECOND call (the post-IntegrityError
        # fallback). RacePlan.save will then hit the unique index and raise.
        # Anthropic + dependencies stubbed so we don't actually call Claude.
        calls: list = []

        async def get_today_side_effect(goal_id_arg, *, user_id):
            calls.append(goal_id_arg)
            if len(calls) == 1:
                return None  # pre-check: pretend not-yet-saved
            return winning_row  # fallback: return the row that won the race

        # Stub out everything heavy: Activity, AthleteSettings, FitnessProjection,
        # Wellness, Anthropic. Goal resolves via the real DB.
        valid_plan = {
            "warmup": "10 min easy + 4×30s strides.",
            "legs": [
                {
                    "leg": "run",
                    "distance": "21.1 km",
                    "pacing": {"low": "5:30/km", "target": "5:10/km", "cap": "4:50/km"},
                    "hr_ceiling_bpm": 175,
                    "notes": "Hold target.",
                }
            ],
            "fueling": {"carbs_g_per_hour": 70, "notes": "Gel every 25 min."},
            "transitions": [],
            "contingencies": [
                {"scenario": "heat", "plan": "Slow 5%."},
                {"scenario": "cramp", "plan": "Walk + salt."},
                {"scenario": "off-pace", "plan": "Drop to low."},
            ],
            "headline": "Steady to km 16.",
        }

        from types import SimpleNamespace

        tool_block = SimpleNamespace(type="tool_use", name="submit_race_plan", input=valid_plan)
        anthropic_resp = SimpleNamespace(content=[tool_block], stop_reason="tool_use", usage=None)
        fake_client = MagicMock()
        fake_client.messages = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=anthropic_resp)

        # Activity rows: 8 to pass the >=6 floor.
        from data.db import Activity
        from data.intervals.dto import ActivityDTO

        await Activity.save_bulk(
            1,
            activities=[
                ActivityDTO(
                    id=f"act{i}",
                    start_date_local=date.today() - timedelta(days=i + 1),
                    type="Run",
                    icu_training_load=80.0,
                    moving_time=3600,
                    average_hr=150.0,
                )
                for i in range(8)
            ],
        )

        with (
            patch("data.race_plan_service.RacePlan.get_today_for_goal", side_effect=get_today_side_effect),
            patch("data.race_plan_service.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch("data.race_plan_service.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch("anthropic.AsyncAnthropic", MagicMock(return_value=fake_client)),
            patch("data.race_plan_service.settings") as fake_settings,
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            out = await build_race_plan(user_id=1, goal_id=goal_id)

        assert "error" not in out
        # Fallback returned the winning row, not a fresh one.
        assert out["id"] == winning_row.id
        assert out["payload"]["marker"] == "winner"
        assert "already generated today" in out.get("note", "").lower()
        # get_today_for_goal called twice: once pre-check, once post-IntegrityError fallback.
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# PR2.3: force_regen + rate-limit + in-place UPDATE
# ---------------------------------------------------------------------------


def _stub_anthropic(plan_input: dict):
    """Build a MagicMock that emulates anthropic.AsyncAnthropic returning a
    single tool_use block. Helper for the force_regen tests below."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    tool_block = SimpleNamespace(type="tool_use", name="submit_race_plan", input=plan_input)
    resp = SimpleNamespace(content=[tool_block], stop_reason="tool_use", usage=None)
    fake_client = MagicMock()
    fake_client.messages = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=resp)
    return MagicMock(return_value=fake_client)


_VALID_PLAN_INPUT = {
    "warmup": "10 min easy + 4×30s strides.",
    "legs": [
        {
            "leg": "run",
            "distance": "21.1 km",
            "pacing": {"low": "5:30/km", "target": "5:10/km", "cap": "4:50/km"},
            "hr_ceiling_bpm": 175,
            "notes": "Hold target.",
        }
    ],
    "fueling": {"carbs_g_per_hour": 70, "notes": "Gel every 25 min."},
    "transitions": [],
    "contingencies": [
        {"scenario": "heat", "plan": "Slow 5%."},
        {"scenario": "cramp", "plan": "Walk + salt."},
        {"scenario": "off-pace", "plan": "Drop to low."},
    ],
    "headline": "Steady to km 16.",
}


async def _seed_8_activities(user_id: int = 1) -> None:
    """8 Run activities to pass build_race_plan's <6 floor."""
    from data.db import Activity
    from data.intervals.dto import ActivityDTO

    await Activity.save_bulk(
        user_id,
        activities=[
            ActivityDTO(
                id=f"act{i}",
                start_date_local=date.today() - timedelta(days=i + 1),
                type="Run",
                icu_training_load=80.0,
                moving_time=3600,
                average_hr=150.0,
            )
            for i in range(8)
        ],
    )


class TestForceRegenAndRateLimit:
    """End-to-end coverage of the force_regen path: in-place UPDATE preserves
    id, regen_count_today increments, rate-limit gate refuses 2nd regen
    BEFORE Claude call, dry_run+force_regen bypasses rate-limit. Reviewed M1
    (2026-05-09)."""

    async def test_force_regen_happy_path_preserves_id_and_increments_counter(self):
        """First regen of the day: existing row UPDATED in place (same id),
        regen_count_today goes 0 → 1."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from data.race_plan_service import build_race_plan

        goal_id = await _seed_goal()
        await _seed_8_activities()
        # Pre-seed today's row with regen_count_today=0 (initial generation).
        original = await RacePlan.save(
            user_id=1,
            goal_id=goal_id,
            model_version="v-original",
            payload={**PAYLOAD, "regen_count_today": 0, "marker": "before-regen"},
        )

        with (
            patch("data.race_plan_service.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch("data.race_plan_service.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch("anthropic.AsyncAnthropic", _stub_anthropic(_VALID_PLAN_INPUT)),
            patch("data.race_plan_service.settings") as fake_settings,
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            out = await build_race_plan(user_id=1, goal_id=goal_id, force_regen=True)

        assert "error" not in out
        # Same id → in-place UPDATE, not DELETE+INSERT.
        assert out["id"] == original.id
        # Counter incremented.
        assert out["payload"]["regen_count_today"] == 1
        # Note surfaces the regen quota state.
        assert "regenerated in place" in out.get("note", "").lower()
        # Payload is fresh (model_version bumped from v-original).
        assert out["model_version"] != "v-original"

    async def test_force_regen_rate_limit_refuses_second_regen_without_claude_call(self):
        """Second regen of the same day → 429-equivalent service refusal,
        ``retry_after_sec`` populated, NO Claude call made (cost guard)."""
        from unittest.mock import patch

        from data.race_plan_service import build_race_plan

        goal_id = await _seed_goal()
        # Pre-seed with regen_count_today=1 (already at limit).
        await RacePlan.save(
            user_id=1,
            goal_id=goal_id,
            model_version="v-already-regenerated",
            payload={**PAYLOAD, "regen_count_today": 1},
        )

        # Anthropic patched but expected NOT called — the rate-limit gate must
        # short-circuit before we'd ever reach the Claude call.
        anthropic_factory = _stub_anthropic(_VALID_PLAN_INPUT)
        with patch("anthropic.AsyncAnthropic", anthropic_factory):
            out = await build_race_plan(user_id=1, goal_id=goal_id, force_regen=True)

        assert "error" in out
        assert "rate limit" in out["error"].lower()
        assert out["retry_after_sec"] > 0
        assert "next_available_at" in out
        # Cost guard: no Claude call made.
        anthropic_factory.assert_not_called()

    async def test_dry_run_with_force_regen_bypasses_rate_limit(self):
        """dry_run preview shouldn't consume the regen slot — even when the
        user is already at the daily limit, dry_run+force_regen returns a
        previewed payload without persisting and without rate-limit refusal."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from data.race_plan_service import build_race_plan

        goal_id = await _seed_goal()
        await _seed_8_activities()
        # Already at limit.
        await RacePlan.save(
            user_id=1,
            goal_id=goal_id,
            model_version="v-already-regenerated",
            payload={**PAYLOAD, "regen_count_today": 1},
        )

        with (
            patch("data.race_plan_service.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch("data.race_plan_service.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch("anthropic.AsyncAnthropic", _stub_anthropic(_VALID_PLAN_INPUT)),
            patch("data.race_plan_service.settings") as fake_settings,
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            out = await build_race_plan(user_id=1, goal_id=goal_id, dry_run=True, force_regen=True)

        # Returns a preview, NOT a rate-limit error.
        assert "error" not in out
        assert out["dry_run"] is True
        assert out["id"] is None  # not persisted
        # And the existing row's regen_count_today is unchanged (still 1).
        existing = await RacePlan.get_today_for_goal(goal_id, user_id=1)
        assert existing.payload["regen_count_today"] == 1


class TestUpdateInPlaceORM:
    """Direct integration tests for ``RacePlan.update_in_place`` — review N3
    (2026-05-09). Method was previously exercised only end-to-end via
    build_race_plan, leaving the user_id-scoping defense and the timestamp-
    advance behaviour without dedicated assertions. A regression that drops
    the ``cls.user_id == user_id`` clause would silently bypass tenant
    isolation; a regression that forgets to bump ``generated_at`` would break
    UTC-day idempotency without flagging."""

    async def test_returns_none_and_does_not_mutate_for_cross_tenant_user_id(self):
        from data.db import User

        # Goal + plan owned by user 1.
        goal_id = await _seed_goal(user_id=1)
        original = await RacePlan.save(
            user_id=1,
            goal_id=goal_id,
            model_version="v-original",
            payload={**PAYLOAD, "marker": "untouched"},
        )

        # Seed user 2 (the would-be attacker).
        async with get_session() as session:
            session.add(User(id=2, chat_id="other", role="athlete"))
            await session.commit()

        # Try to UPDATE user 1's row claiming to be user 2.
        result = await RacePlan.update_in_place(
            original.id,
            user_id=2,
            model_version="v-malicious",
            payload={**PAYLOAD, "marker": "tampered"},
        )

        # Must refuse silently (None) — defense-in-depth, no existence leak.
        assert result is None
        # And the original row must NOT have been mutated.
        async with get_session() as session:
            from sqlalchemy import select

            row = (await session.execute(select(RacePlan).where(RacePlan.id == original.id))).scalar_one()
        assert row.model_version == "v-original"
        assert row.payload["marker"] == "untouched"

    async def test_advances_generated_at(self):
        """generated_at MUST move forward on UPDATE — otherwise the row stays
        at its original timestamp and ``get_today_for_goal`` could mis-classify
        it across day boundaries."""
        import asyncio

        goal_id = await _seed_goal()
        original = await RacePlan.save(user_id=1, goal_id=goal_id, model_version="v1", payload=PAYLOAD)
        original_generated_at = original.generated_at
        # Sub-second sleep so the new timestamp is provably later.
        await asyncio.sleep(0.01)

        updated = await RacePlan.update_in_place(
            original.id,
            user_id=1,
            model_version="v2",
            payload={**PAYLOAD, "marker": "after-update"},
        )

        assert updated is not None
        assert updated.id == original.id  # in-place, same row
        assert updated.generated_at > original_generated_at
        assert updated.model_version == "v2"
        assert updated.payload["marker"] == "after-update"


class TestExistingTodayVanishedFallback:
    """When ``existing_today`` exists at pre-check but ``update_in_place``
    returns None (e.g. concurrent goal-deletion cascading SET NULL), the
    service falls back to a fresh INSERT rather than swallowing the regen.
    Review N2 (2026-05-09)."""

    async def test_falls_back_to_insert_when_update_in_place_returns_none(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from data.race_plan_service import build_race_plan

        goal_id = await _seed_goal()
        await _seed_8_activities()
        # Pre-seed existing-today so the regen path is taken.
        await RacePlan.save(
            user_id=1,
            goal_id=goal_id,
            model_version="v-original",
            payload={**PAYLOAD, "regen_count_today": 0},
        )

        # Patch update_in_place to simulate the row vanishing between
        # pre-check and UPDATE (returns None).
        update_in_place_mock = AsyncMock(return_value=None)
        # Patch the service's logger.warning directly — pytest's ``caplog``
        # fixture is sensitive to cross-test handler reconfiguration (sentry
        # init in api.server, FastAPI test-client setup, etc.) and was
        # silently dropping records when run in the broader suite.
        warnings_seen: list[str] = []
        original_warning = build_race_plan.__globals__["logger"].warning

        def capture_warning(msg, *args, **kwargs):
            warnings_seen.append(msg % args if args else msg)
            return original_warning(msg, *args, **kwargs)

        with (
            patch("data.race_plan_service.logger.warning", side_effect=capture_warning),
            patch("data.race_plan_service.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch("data.race_plan_service.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch("data.race_plan_service.RacePlan.update_in_place", update_in_place_mock),
            patch("anthropic.AsyncAnthropic", _stub_anthropic(_VALID_PLAN_INPUT)),
            patch("data.race_plan_service.settings") as fake_settings,
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            # The original (pre-seeded) row + the would-be UPDATE both target
            # today's UTC day, so a fresh INSERT will collide with the unique
            # index → IntegrityError fallback returns the original.
            out = await build_race_plan(user_id=1, goal_id=goal_id, force_regen=True)

        # Review L5: assert the "vanished mid-flight" warning fired so a
        # future refactor that drops the logger.warning() call is caught.
        assert any(
            "existing_today vanished" in msg for msg in warnings_seen
        ), f"expected 'existing_today vanished' warning, got: {warnings_seen}"

        # update_in_place was called (regen path entered) and returned None.
        update_in_place_mock.assert_called_once()
        # Service didn't swallow the regen — it tried to write. The
        # IntegrityError fallback then surfaced the existing-today row, which
        # is the safest possible recovery (no data loss, idempotent shape).
        assert "error" not in out
        assert "id" in out


class TestDryRunQuota:
    """Per-user dry_run rate-limit (security review secH1, 2026-05-09).
    Prevents authenticated cost-abuse loops via ``{dry_run: true}``."""

    async def test_dry_run_refused_when_redis_counter_over_limit(self):
        """Once Redis counter > LIMIT, the gate returns rate-limit error
        WITHOUT calling Claude (cost guard)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from data.race_plan_service import RACE_PLAN_DRY_RUN_DAILY_LIMIT, build_race_plan

        await _seed_goal()

        # Fake Redis: INCR returns LIMIT+1 (already over budget).
        fake_redis = MagicMock()
        fake_redis.incr = AsyncMock(return_value=RACE_PLAN_DRY_RUN_DAILY_LIMIT + 1)
        fake_redis.expire = AsyncMock()
        anthropic_factory = _stub_anthropic(_VALID_PLAN_INPUT)

        with (
            patch("data.race_plan_service.get_redis", return_value=fake_redis),
            patch("anthropic.AsyncAnthropic", anthropic_factory),
        ):
            out = await build_race_plan(user_id=1, goal_id=1, dry_run=True)

        assert "error" in out
        assert "dry-run quota" in out["error"].lower()
        assert out["retry_after_sec"] > 0
        # No Claude call — cost guard fires before message create.
        anthropic_factory.assert_not_called()

    async def test_dry_run_allowed_when_redis_counter_below_limit(self):
        """First few dry_run calls go through (counter ≤ limit)."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from data.race_plan_service import build_race_plan

        await _seed_goal()
        await _seed_8_activities()

        fake_redis = MagicMock()
        fake_redis.incr = AsyncMock(return_value=1)  # first call
        fake_redis.expire = AsyncMock()

        with (
            patch("data.race_plan_service.get_redis", return_value=fake_redis),
            patch("data.race_plan_service.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch("data.race_plan_service.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch("anthropic.AsyncAnthropic", _stub_anthropic(_VALID_PLAN_INPUT)),
            patch("data.race_plan_service.settings") as fake_settings,
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            out = await build_race_plan(user_id=1, goal_id=1, dry_run=True)

        # Got through to the dry_run preview return.
        assert "error" not in out
        assert out["dry_run"] is True
        # First INCR sets TTL.
        fake_redis.expire.assert_called_once()

    async def test_dry_run_fails_open_when_redis_unavailable(self):
        """Redis returning None (disabled / unreachable) → quota check
        skipped, dry_run proceeds. Documented trade-off — preserves dev
        environments without Redis at the cost of no rate-limit there."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from data.race_plan_service import build_race_plan

        await _seed_goal()
        await _seed_8_activities()

        with (
            patch("data.race_plan_service.get_redis", return_value=None),
            patch("data.race_plan_service.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch("data.race_plan_service.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch("anthropic.AsyncAnthropic", _stub_anthropic(_VALID_PLAN_INPUT)),
            patch("data.race_plan_service.settings") as fake_settings,
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            out = await build_race_plan(user_id=1, goal_id=1, dry_run=True)

        assert "error" not in out
        assert out["dry_run"] is True

    async def test_non_dry_run_skips_quota_check(self):
        """force_regen / first-time generation don't consume the dry_run
        counter — they have their own gates (regen_count_today)."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from data.race_plan_service import build_race_plan

        await _seed_goal()
        await _seed_8_activities()

        fake_redis = MagicMock()
        fake_redis.incr = AsyncMock(return_value=999)  # would block dry_run if called

        with (
            patch("data.race_plan_service.get_redis", return_value=fake_redis),
            patch("data.race_plan_service.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch("data.race_plan_service.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch("anthropic.AsyncAnthropic", _stub_anthropic(_VALID_PLAN_INPUT)),
            patch("data.race_plan_service.settings") as fake_settings,
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            # NOT dry_run — should proceed normally even with Redis at limit
            out = await build_race_plan(user_id=1, goal_id=1, dry_run=False)

        assert "error" not in out
        # INCR never called for non-dry_run path
        fake_redis.incr.assert_not_called()


class TestTaperBlock:
    """TAPER_PLANNER_SPEC Phase 3 — deterministic ``taper`` block in the race-plan
    payload. The block comes from the shared resolver, NOT the LLM, so it's
    asserted independently of the (mocked) Claude output. Omitted when the
    resolver refuses, mirroring the conditional ``race_conditions`` block."""

    async def test_taper_block_added_when_resolver_available(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from data.race_plan_service import build_race_plan

        goal_id = await _seed_goal()
        await _seed_8_activities()

        taper_envelope = {
            "available": True,
            "race_date": "2026-07-01",
            "taper_days": 14,
            "daily_targets": [{"date": "2026-06-20", "target_tss": 70}],
            "confidence": "ok",
        }

        with (
            patch("data.race_plan_service.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch("data.race_plan_service.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch("data.race_plan_service.get_taper_plan_for_user", AsyncMock(return_value=taper_envelope)),
            patch("anthropic.AsyncAnthropic", _stub_anthropic(_VALID_PLAN_INPUT)),
            patch("data.race_plan_service.settings") as fake_settings,
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            out = await build_race_plan(user_id=1, goal_id=goal_id, dry_run=True)

        assert "error" not in out
        # Deterministic block stored verbatim from the resolver envelope.
        assert out["payload"]["taper"] == taper_envelope

    async def test_taper_block_omitted_when_resolver_refuses(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from data.race_plan_service import build_race_plan

        goal_id = await _seed_goal()
        await _seed_8_activities()

        with (
            patch("data.race_plan_service.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch("data.race_plan_service.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch(
                "data.race_plan_service.get_taper_plan_for_user",
                AsyncMock(return_value={"available": False, "reason": "no_training_history"}),
            ),
            patch("anthropic.AsyncAnthropic", _stub_anthropic(_VALID_PLAN_INPUT)),
            patch("data.race_plan_service.settings") as fake_settings,
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            out = await build_race_plan(user_id=1, goal_id=goal_id, dry_run=True)

        assert "error" not in out
        assert "taper" not in out["payload"]

    async def test_taper_resolver_error_omits_block_not_plan(self):
        """A resolver exception (DB hiccup / logic bug) must NOT sink an
        otherwise-valid plan — the optional taper block is dropped, the plan
        survives. Guards the post-Claude failure mode (PR #468 review)."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from data.race_plan_service import build_race_plan

        goal_id = await _seed_goal()
        await _seed_8_activities()

        with (
            patch("data.race_plan_service.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch("data.race_plan_service.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch(
                "data.race_plan_service.get_taper_plan_for_user",
                AsyncMock(side_effect=RuntimeError("simulated resolver crash")),
            ),
            patch("anthropic.AsyncAnthropic", _stub_anthropic(_VALID_PLAN_INPUT)),
            patch("data.race_plan_service.settings") as fake_settings,
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            out = await build_race_plan(user_id=1, goal_id=goal_id, dry_run=True)

        assert "error" not in out  # plan still generated
        assert "taper" not in out["payload"]  # block omitted on resolver error
