"""Tests for race-plan compliance (PR3, spec §14).

Three layers:
- ORM CRUD round-trip (real Postgres via test_session conftest).
- Pure-function unit tests for the three metric computers.
- Integration test for ``compute_compliance`` end-to-end with seeded
  RacePlan / Race / Activity / ActivityDetail rows.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest  # noqa: F401  — pytest-asyncio collects via marker auto-discovery

from data.db import Activity, ActivityDetail, AthleteGoal, Race, RacePlan, RacePlanCompliance, User, get_session
from data.intervals.dto import ActivityDTO

PLAN_PAYLOAD = {
    "plan": {
        "warmup": "10 min easy.",
        "legs": [
            {
                "leg": "swim",
                "distance": "1.9 km",
                "pacing": {"low": "2:00/100m", "target": "1:55/100m", "cap": "1:50/100m"},
                "hr_ceiling_bpm": 155,
            },
            {
                "leg": "bike",
                "distance": "90 km",
                "pacing": {"low": "180W", "target": "210W", "cap": "240W"},
                "hr_ceiling_bpm": 165,
            },
        ],
        "fueling": {"carbs_g_per_hour": 75, "notes": "Gel every 25 min."},
        "transitions": [],
        "contingencies": [
            {"scenario": "heat", "plan": "Slow 5%."},
            {"scenario": "cramp", "plan": "Walk + salt."},
            {"scenario": "off-pace", "plan": "Drop low."},
        ],
    },
    "race": {"id": 1, "name": "Drina Trail"},
    "confidence_tier": "final",
    "model_version": "v1",
}


async def _seed_user(*, user_id: int = 1) -> None:
    """Add a User if not already present (conftest seeds id=1 by default)."""
    async with get_session() as session:
        existing = await session.get(User, user_id)
        if existing is None:
            session.add(User(id=user_id, chat_id=str(user_id), role="athlete"))
            await session.commit()


async def _seed_goal(*, goal_id: int = 1, user_id: int = 1) -> int:
    await _seed_user(user_id=user_id)
    async with get_session() as session:
        session.add(
            AthleteGoal(
                id=goal_id,
                user_id=user_id,
                category="RACE_A",
                event_name="Drina Trail",
                event_date=date.today() + timedelta(days=7),
                sport_type="triathlon",
            )
        )
        await session.commit()
        return goal_id


# ---------------------------------------------------------------------------
# ORM CRUD
# ---------------------------------------------------------------------------


class TestRacePlanComplianceCRUD:
    async def test_save_for_leg_and_get_round_trip(self):
        await _seed_goal()
        plan = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PLAN_PAYLOAD)

        row = await RacePlanCompliance.save_for_leg(
            user_id=1,
            race_plan_id=plan.id,
            race_id=None,
            leg_name="swim",
            hr_compliance_pct=Decimal("87.5"),
            band_compliance_pct=Decimal("60.0"),
            fueling_compliance_pct=None,
            leg_duration_sec=1800,
            notes="test",
        )
        assert row.id is not None
        assert row.leg_name == "swim"
        assert row.hr_compliance_pct == Decimal("87.50")
        assert row.computed_at is not None

        all_rows = await RacePlanCompliance.get_for_race_plan(plan.id, user_id=1)
        assert len(all_rows) == 1
        assert all_rows[0].id == row.id

    async def test_get_for_race_plan_is_user_scoped(self):
        """Cross-tenant read returns []  — defense-in-depth even though
        race_plan_id alone implies a single tenant."""
        await _seed_goal()
        plan = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PLAN_PAYLOAD)
        await RacePlanCompliance.save_for_leg(
            user_id=1,
            race_plan_id=plan.id,
            race_id=None,
            leg_name="run",
            hr_compliance_pct=None,
            band_compliance_pct=None,
            fueling_compliance_pct=None,
            leg_duration_sec=None,
            notes=None,
        )
        # Seed a second user
        await _seed_user(user_id=2)
        # Read claiming to be user 2 — should see nothing
        cross_tenant = await RacePlanCompliance.get_for_race_plan(plan.id, user_id=2)
        assert cross_tenant == []

    async def test_save_accepts_floats(self):
        """Numeric column accepts float input — coerced to Decimal on storage."""
        await _seed_goal()
        plan = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PLAN_PAYLOAD)
        row = await RacePlanCompliance.save_for_leg(
            user_id=1,
            race_plan_id=plan.id,
            race_id=None,
            leg_name="bike",
            hr_compliance_pct=42.5,  # float
            band_compliance_pct=33.33,
            fueling_compliance_pct=100.0,
            leg_duration_sec=3600,
            notes=None,
        )
        assert row.hr_compliance_pct == Decimal("42.50")

    async def test_cascade_on_race_plan_delete(self):
        """ON DELETE CASCADE — deleting the plan removes its compliance rows."""
        from sqlalchemy import delete, select

        await _seed_goal()
        plan = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PLAN_PAYLOAD)
        await RacePlanCompliance.save_for_leg(
            user_id=1,
            race_plan_id=plan.id,
            race_id=None,
            leg_name="swim",
            hr_compliance_pct=None,
            band_compliance_pct=None,
            fueling_compliance_pct=None,
            leg_duration_sec=None,
            notes=None,
        )
        async with get_session() as session:
            await session.execute(delete(RacePlan).where(RacePlan.id == plan.id))
            await session.commit()

        # Compliance row gone too.
        async with get_session() as session:
            remaining = (
                await session.execute(select(RacePlanCompliance).where(RacePlanCompliance.race_plan_id == plan.id))
            ).all()
        assert remaining == []


# ---------------------------------------------------------------------------
# Pure-function unit tests
# ---------------------------------------------------------------------------


class TestHrCompliancePure:
    def test_returns_100_when_avg_below_ceiling(self):
        from data.race_plan_compliance_service import _compute_hr_compliance

        leg = {"hr_ceiling_bpm": 165}
        assert _compute_hr_compliance(leg, 150.0) == Decimal("100.00")

    def test_returns_100_when_avg_equals_ceiling(self):
        from data.race_plan_compliance_service import _compute_hr_compliance

        # ≤ ceiling: equality is compliant.
        assert _compute_hr_compliance({"hr_ceiling_bpm": 165}, 165.0) == Decimal("100.00")

    def test_returns_0_when_avg_above_ceiling(self):
        from data.race_plan_compliance_service import _compute_hr_compliance

        assert _compute_hr_compliance({"hr_ceiling_bpm": 165}, 175.0) == Decimal("0.00")

    def test_returns_none_when_ceiling_missing(self):
        from data.race_plan_compliance_service import _compute_hr_compliance

        assert _compute_hr_compliance({}, 150.0) is None

    def test_returns_none_when_avg_missing(self):
        from data.race_plan_compliance_service import _compute_hr_compliance

        assert _compute_hr_compliance({"hr_ceiling_bpm": 165}, None) is None


class TestBandCompliancePure:
    def test_power_inside_band(self):
        from data.race_plan_compliance_service import _compute_band_compliance

        leg = {"pacing": {"low": "180W", "target": "210W", "cap": "240W"}}
        ad = ActivityDetail(activity_id="i1", normalized_power=200)
        assert _compute_band_compliance(leg, ad) == Decimal("100.00")

    def test_power_above_cap(self):
        from data.race_plan_compliance_service import _compute_band_compliance

        leg = {"pacing": {"low": "180W", "target": "210W", "cap": "240W"}}
        ad = ActivityDetail(activity_id="i1", normalized_power=260)  # over cap
        assert _compute_band_compliance(leg, ad) == Decimal("0.00")

    def test_power_below_low(self):
        from data.race_plan_compliance_service import _compute_band_compliance

        leg = {"pacing": {"low": "180W", "target": "210W", "cap": "240W"}}
        ad = ActivityDetail(activity_id="i1", normalized_power=150)  # under low
        assert _compute_band_compliance(leg, ad) == Decimal("0.00")

    def test_pace_inside_band(self):
        """Pace 5:00/km between 5:30/km (low) and 4:30/km (cap) — compliant.
        ActivityDetail.pace is m/s in Intervals; 5:00/km == 1000/300 = 3.33 m/s."""
        from data.race_plan_compliance_service import _compute_band_compliance

        leg = {"pacing": {"low": "5:30/km", "target": "5:00/km", "cap": "4:30/km"}}
        ad = ActivityDetail(activity_id="i1", pace=1000.0 / 300.0)  # 5:00/km
        assert _compute_band_compliance(leg, ad) == Decimal("100.00")

    def test_unparseable_corridor_returns_none(self):
        from data.race_plan_compliance_service import _compute_band_compliance

        leg = {"pacing": {"low": "easy", "target": "tempo", "cap": "threshold"}}
        ad = ActivityDetail(activity_id="i1", normalized_power=200)
        assert _compute_band_compliance(leg, ad) is None

    def test_mixed_unit_corridor_returns_none(self):
        from data.race_plan_compliance_service import _compute_band_compliance

        leg = {"pacing": {"low": "5:30/km", "target": "210W", "cap": "240W"}}
        ad = ActivityDetail(activity_id="i1", normalized_power=200)
        assert _compute_band_compliance(leg, ad) is None

    def test_missing_activity_data_returns_none(self):
        from data.race_plan_compliance_service import _compute_band_compliance

        leg = {"pacing": {"low": "180W", "target": "210W", "cap": "240W"}}
        # ActivityDetail with no power value
        ad = ActivityDetail(activity_id="i1")
        assert _compute_band_compliance(leg, ad) is None


class TestFuelingCompliancePure:
    def test_actual_meets_plan(self):
        from data.race_plan_compliance_service import _compute_fueling_compliance

        # 75 g/hr plan, 75 g consumed in 3600s (1h) → 75 g/hr actual → 100%.
        result = _compute_fueling_compliance({"plan": {"fueling": {"carbs_g_per_hour": 75}}}, 75, 3600)
        assert result == Decimal("100.00")

    def test_actual_below_plan(self):
        """Half the planned rate → 50% compliance."""
        from data.race_plan_compliance_service import _compute_fueling_compliance

        # 75 g/hr plan, 37.5 g/hr actual (75 g over 2h) → 50%.
        result = _compute_fueling_compliance({"plan": {"fueling": {"carbs_g_per_hour": 75}}}, 75, 7200)
        assert result == Decimal("50.00")

    def test_actual_over_plan_capped_at_100(self):
        """Over-fueling → still 100% (metric is "did you hit it", not bonus)."""
        from data.race_plan_compliance_service import _compute_fueling_compliance

        # 75 g/hr plan, 200 g consumed in 1h → 200 g/hr actual.
        result = _compute_fueling_compliance({"plan": {"fueling": {"carbs_g_per_hour": 75}}}, 200, 3600)
        assert result == Decimal("100.00")

    def test_returns_none_when_consumed_missing(self):
        from data.race_plan_compliance_service import _compute_fueling_compliance

        result = _compute_fueling_compliance({"plan": {"fueling": {"carbs_g_per_hour": 75}}}, None, 3600)
        assert result is None

    def test_returns_none_when_plan_carbs_missing(self):
        from data.race_plan_compliance_service import _compute_fueling_compliance

        result = _compute_fueling_compliance({"plan": {"fueling": {}}}, 75, 3600)
        assert result is None


# ---------------------------------------------------------------------------
# compute_compliance integration
# ---------------------------------------------------------------------------


async def _seed_activity_with_detail(
    *,
    activity_id: str = "act-1",
    user_id: int = 1,
    avg_hr: float | None = 150.0,
    moving_time: int = 14400,  # 4h
    normalized_power: int | None = 200,
) -> str:
    """Seed an Activity + ActivityDetail. Returns activity_id."""
    await Activity.save_bulk(
        user_id,
        activities=[
            ActivityDTO(
                id=activity_id,
                start_date_local=date.today() - timedelta(days=1),
                type="Run",
                icu_training_load=120.0,
                moving_time=moving_time,
                average_hr=avg_hr,
            )
        ],
    )
    async with get_session() as session:
        session.add(ActivityDetail(activity_id=activity_id, normalized_power=normalized_power))
        await session.commit()
    return activity_id


class TestComputeComplianceIntegration:
    async def test_returns_empty_when_plan_not_found(self):
        from data.race_plan_compliance_service import compute_compliance

        await _seed_user()
        rows = await compute_compliance(race_plan_id=99999, race_id=None, user_id=1)
        assert rows == []

    async def test_returns_empty_for_cross_tenant_plan(self):
        from data.race_plan_compliance_service import compute_compliance

        await _seed_goal()
        plan = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PLAN_PAYLOAD)
        await _seed_user(user_id=2)
        rows = await compute_compliance(race_plan_id=plan.id, race_id=None, user_id=2)
        assert rows == []

    async def test_persists_one_row_per_leg(self):
        from data.race_plan_compliance_service import compute_compliance

        await _seed_goal()
        plan = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PLAN_PAYLOAD)
        rows = await compute_compliance(race_plan_id=plan.id, race_id=None, user_id=1)
        # Plan has 2 legs (swim + bike) — one compliance row each.
        assert len(rows) == 2
        assert {r.leg_name for r in rows} == {"swim", "bike"}
        # No race_id → all metrics NULL except notes
        for r in rows:
            assert r.hr_compliance_pct is None
            assert r.band_compliance_pct is None
            assert r.fueling_compliance_pct is None
            assert "PR3 fallback" in r.notes

    async def test_full_pipeline_with_race_and_activity(self):
        from data.race_plan_compliance_service import compute_compliance

        await _seed_goal()
        plan = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PLAN_PAYLOAD)
        activity_id = await _seed_activity_with_detail(
            avg_hr=150.0,  # below ceilings 155 (swim) and 165 (bike)
            normalized_power=200,  # inside bike band 180-240W
            moving_time=14400,  # 4h
        )
        # Race row links plan-id to activity, with carbs_consumed_g for fueling.
        async with get_session() as session:
            session.add(
                Race(
                    user_id=1,
                    activity_id=activity_id,
                    name="Drina Trail",
                    race_type="A",
                    carbs_consumed_g=200,  # 200g over 4h = 50 g/hr (vs 75 plan = 66.7%)
                )
            )
            await session.commit()

        # Get the race id back.
        from sqlalchemy import select

        async with get_session() as session:
            race_row = (await session.execute(select(Race).where(Race.activity_id == activity_id))).scalar_one()

        rows = await compute_compliance(race_plan_id=plan.id, race_id=race_row.id, user_id=1)
        assert len(rows) == 2
        by_leg = {r.leg_name: r for r in rows}
        # swim: HR 150 ≤ 155 ceiling → 100%; band uses pace, ActivityDetail has no pace → None
        assert by_leg["swim"].hr_compliance_pct == Decimal("100.00")
        assert by_leg["swim"].band_compliance_pct is None
        # bike: HR 150 ≤ 165 → 100%; power 200 inside [180, 240] → 100%
        assert by_leg["bike"].hr_compliance_pct == Decimal("100.00")
        assert by_leg["bike"].band_compliance_pct == Decimal("100.00")
        # Fueling cloned to all legs: 50/75 = 66.67% rounded
        assert by_leg["swim"].fueling_compliance_pct == Decimal("66.67")
        assert by_leg["bike"].fueling_compliance_pct == Decimal("66.67")
        # leg_duration_sec is whole-activity duration in PR3
        assert by_leg["swim"].leg_duration_sec == 14400


class TestRaceCarbsConsumedGColumn:
    """Smoke test that the new ``Race.carbs_consumed_g`` column exists and
    accepts NULL + integer values. Defends against a future migration drop
    that would silently break the fueling compute path."""

    async def test_round_trip_null(self):
        from sqlalchemy import select

        await _seed_user()
        await Activity.save_bulk(
            1,
            activities=[
                ActivityDTO(
                    id="r-null",
                    start_date_local=date.today(),
                    type="Run",
                    icu_training_load=80.0,
                    moving_time=3600,
                    average_hr=145.0,
                )
            ],
        )
        async with get_session() as session:
            session.add(Race(user_id=1, activity_id="r-null", name="Test"))
            await session.commit()
            row = (await session.execute(select(Race).where(Race.activity_id == "r-null"))).scalar_one()
        assert row.carbs_consumed_g is None

    async def test_round_trip_integer(self):
        from sqlalchemy import select

        await _seed_user()
        await Activity.save_bulk(
            1,
            activities=[
                ActivityDTO(
                    id="r-int",
                    start_date_local=date.today(),
                    type="Run",
                    icu_training_load=80.0,
                    moving_time=3600,
                    average_hr=145.0,
                )
            ],
        )
        async with get_session() as session:
            session.add(Race(user_id=1, activity_id="r-int", name="Test", carbs_consumed_g=180))
            await session.commit()
            row = (await session.execute(select(Race).where(Race.activity_id == "r-int"))).scalar_one()
        assert row.carbs_consumed_g == 180


# Suppress unused-import warning from datetime/timezone when no test happens
# to need them; they're consumed only by ActivityDetail.computed_at default.
assert datetime is not None and timezone is not None
