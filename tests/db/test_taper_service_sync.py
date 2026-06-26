"""Real-DB coverage for the sync taper resolver (TAPER_PLANNER_SPEC Phase 5).

The mock-based tests in ``tests/test_taper_service.py`` patch the I/O helpers,
so the literal sync SELECTs in ``_resolve_loads_sync`` /
``_resolve_peak_daily_load_sync`` are never exercised against Postgres — a typo
in those statements would slip past the suite. These round-trip through the test
DB. Added per code-review (2026-06-26).

These are SYNC tests on purpose: ``get_taper_plan_for_user_sync`` calls ``@dual``
ORM methods (``AthleteGoal.get_goal_dto``, ``recompute_today_loads_sync``) which
dispatch on event-loop presence. Under an ``async`` test they'd flip to the async
path and return coroutines; a plain ``def`` test has no running loop, so ``@dual``
takes the sync branch — exactly the dramatiq-worker context this code runs in."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from data.db import Activity, AthleteGoal, Wellness, get_sync_session
from data.intervals.dto import ActivityDTO
from data.taper_service import _resolve_loads_sync, _resolve_peak_daily_load_sync, get_taper_plan_for_user_sync
from tasks.dto import local_today


def _seed_wellness(user_id, dt, *, ctl, atl, sleep_score=80.0):
    with get_sync_session() as session:
        session.add(Wellness(user_id=user_id, date=dt.isoformat(), ctl=ctl, atl=atl, sleep_score=sleep_score))
        session.commit()


def _seed_goal(user_id, days_out, name="Ironman 70.3"):
    with get_sync_session() as session:
        session.add(
            AthleteGoal(
                id=1,
                user_id=user_id,
                category="RACE_A",
                event_name=name,
                event_date=local_today() + timedelta(days=days_out),
                sport_type="triathlon",
            )
        )
        session.commit()


def _seed_activities(user_id, n=20, tss=80.0):
    Activity.save_bulk(
        user_id,
        activities=[
            ActivityDTO(
                id=f"act{i}",
                start_date_local=local_today() - timedelta(days=i + 1),
                type="Run",
                icu_training_load=tss,
                moving_time=3600,
                average_hr=150.0,
            )
            for i in range(n)
        ],
    )


def test_resolve_peak_daily_load_sync_real_query():
    # Exercises the literal `get_sync_session` SELECT over Activity (the new SQL).
    _seed_activities(1, n=20, tss=80.0)
    peak, fallback = _resolve_peak_daily_load_sync(1, local_today(), ctl_now=50.0)
    assert fallback is False  # 20 days history > 14d minimum → not a fallback
    assert peak == 80.0  # best rolling-7d window median of 80 TSS/day


def test_resolve_peak_daily_load_sync_short_history_falls_back():
    _seed_activities(1, n=5, tss=90.0)  # only 5 days < 14d min
    peak, fallback = _resolve_peak_daily_load_sync(1, local_today(), ctl_now=55.0)
    assert (peak, fallback) == (55.0, True)


def test_resolve_loads_sync_wellness_fallback_real_query():
    # recompute_today_loads_sync → None drives the wellness-row fallback SELECT.
    _seed_wellness(1, local_today() - timedelta(days=2), ctl=55.0, atl=60.0)
    with patch("data.taper_service.recompute_today_loads_sync", return_value=None):
        assert _resolve_loads_sync(1) == (55.0, 60.0)


def test_resolve_loads_sync_no_data_returns_none():
    with patch("data.taper_service.recompute_today_loads_sync", return_value=None):
        assert _resolve_loads_sync(1) is None


def test_get_taper_plan_for_user_sync_end_to_end():
    # Full sync path against real DB: get_goal_dto + recompute_today_loads_sync +
    # the peak SELECT + build_taper_plan + envelope. No event loop → @dual sync.
    today = local_today()
    _seed_goal(1, days_out=14, name="Ironman 70.3 Italy")
    _seed_wellness(1, today - timedelta(days=1), ctl=60.0, atl=58.0)  # drives recompute
    _seed_activities(1, n=20, tss=80.0)

    result = get_taper_plan_for_user_sync(1)

    assert result["available"] is True
    assert result["days_to_race"] == 14
    assert result["race_distance_class"] == "long"  # inferred from "70.3"
    assert result["daily_targets"]  # 14d ≤ 21d → not early mode, targets populated
    assert result["inputs"]["ctl_now"] > 0


def test_get_taper_plan_for_user_sync_no_goal_refuses():
    assert get_taper_plan_for_user_sync(1)["reason"] == "no_future_race"
