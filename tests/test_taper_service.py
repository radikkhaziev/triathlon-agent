"""Tests for data/taper_service.py — refusal gates + input resolution."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from data.taper_service import (
    _distance_class_from_name,
    _resolve_loads,
    _resolve_peak_daily_load,
    get_taper_plan_for_user,
    get_taper_plan_for_user_sync,
)

TODAY = date(2026, 6, 12)


def _patches(
    goal_dto=None,
    goals=None,
    loads=(60.0, 65.0),
    peak=(85.0, False),
):
    return (
        patch("data.taper_service.local_today", return_value=TODAY),
        patch("data.taper_service.AthleteGoal.get_goal_dto", AsyncMock(return_value=goal_dto)),
        patch("data.taper_service.AthleteGoal.get_all", AsyncMock(return_value=goals or [])),
        patch("data.taper_service._resolve_loads", AsyncMock(return_value=loads)),
        patch("data.taper_service._resolve_peak_daily_load", AsyncMock(return_value=peak)),
    )


def _goal(days_out: int = 14, name: str = "Test race", goal_id: int = 7, active: bool = True):
    return SimpleNamespace(
        id=goal_id,
        event_name=name,
        event_date=TODAY + timedelta(days=days_out),
        is_active=active,
    )


async def _call(**kwargs):
    ctx = _patches(**{k: kwargs.pop(k) for k in list(kwargs) if k in ("goal_dto", "goals", "loads", "peak")})
    with ctx[0], ctx[1], ctx[2], ctx[3], ctx[4]:
        return await get_taper_plan_for_user(1, **kwargs)


class TestRefusalGates:
    @pytest.mark.asyncio
    async def test_no_goal_no_race_date(self):
        result = await _call()
        assert result["available"] is False
        assert result["reason"] == "no_future_race"

    @pytest.mark.asyncio
    async def test_invalid_race_date(self):
        result = await _call(race_date="not-a-date")
        assert result["available"] is False
        assert result["reason"] == "invalid_race_date"

    @pytest.mark.asyncio
    async def test_race_date_in_past(self):
        result = await _call(race_date="2026-06-01")
        assert result["available"] is False
        assert result["reason"] == "race_date_in_past"

    @pytest.mark.asyncio
    async def test_unknown_goal_id(self):
        result = await _call(goals=[_goal(goal_id=7)], goal_id=99)
        assert result["available"] is False
        assert result["reason"] == "goal_not_found"

    @pytest.mark.asyncio
    async def test_inactive_goal_id(self):
        result = await _call(goals=[_goal(goal_id=7, active=False)], goal_id=7)
        assert result["available"] is False
        assert result["reason"] == "goal_not_found"

    @pytest.mark.asyncio
    async def test_invalid_distance_class(self):
        result = await _call(race_date="2026-06-26", race_distance_class="ultra")
        assert result["available"] is False
        assert result["reason"] == "invalid_distance_class"

    @pytest.mark.asyncio
    async def test_no_wellness_data(self):
        result = await _call(race_date="2026-06-26", loads=None)
        assert result["available"] is False
        assert result["reason"] == "no_wellness_data"


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_plan_from_primary_goal(self):
        result = await _call(goal_dto=_goal(days_out=14))
        assert result["available"] is True
        assert result["days_to_race"] == 14
        assert result["confidence"] == "ok"
        assert result["race_distance_class"] == "standard"
        # Dates serialised to ISO strings for the envelope
        assert isinstance(result["taper_start_date"], str)
        assert all(isinstance(t["date"], str) for t in result["daily_targets"])
        assert result["inputs"] == {"ctl_now": 60.0, "atl_now": 65.0, "peak_daily_load": 85.0}

    @pytest.mark.asyncio
    async def test_goal_id_resolution(self):
        result = await _call(goals=[_goal(goal_id=7, days_out=12, name="Ironman 70.3 Italy")], goal_id=7)
        assert result["available"] is True
        assert result["event_name"] == "Ironman 70.3 Italy"
        assert result["race_distance_class"] == "long"  # inferred from name

    @pytest.mark.asyncio
    async def test_explicit_class_overrides_heuristic(self):
        result = await _call(
            goals=[_goal(goal_id=7, name="Ironman 70.3 Italy")], goal_id=7, race_distance_class="short"
        )
        assert result["race_distance_class"] == "short"

    @pytest.mark.asyncio
    async def test_race_date_override_without_goal(self):
        result = await _call(race_date="2026-06-26")
        assert result["available"] is True
        assert result["event_name"] is None

    @pytest.mark.asyncio
    async def test_peak_fallback_adds_warning(self):
        result = await _call(goal_dto=_goal(), peak=(60.0, True))
        assert result["available"] is True
        assert "peak_load_fallback_ctl" in result["warnings"]

    @pytest.mark.asyncio
    async def test_early_mode_passthrough(self):
        result = await _call(goal_dto=_goal(days_out=45))
        assert result["confidence"] == "early"
        assert result["daily_targets"] == []
        assert result["projected_race_day"] is None


def _activity(day: date, tss: float):
    return SimpleNamespace(icu_training_load=tss, start_date_local=day.isoformat())


def _patch_activities(activities):
    return patch("data.taper_service.Activity.get_range", AsyncMock(return_value=(activities, None)))


class TestResolvePeakDailyLoad:
    @pytest.mark.asyncio
    async def test_best_week_median_wins(self):
        # 42 days of 50 TSS/day with one 100 TSS/day week (today-20..today-14):
        # the rolling-7d max lands on that week, its median is 100.
        acts = []
        for i in range(1, 43):
            day = TODAY - timedelta(days=i)
            tss = 100.0 if 14 <= i <= 20 else 50.0
            acts.append(_activity(day, tss))
        with _patch_activities(acts):
            peak, fallback = await _resolve_peak_daily_load(1, TODAY, ctl_now=60.0)
        assert peak == 100.0
        assert fallback is False

    @pytest.mark.asyncio
    async def test_ctl_floor_when_above_peak_week(self):
        acts = [_activity(TODAY - timedelta(days=i), 50.0) for i in range(1, 43)]
        with _patch_activities(acts):
            peak, fallback = await _resolve_peak_daily_load(1, TODAY, ctl_now=120.0)
        assert peak == 120.0
        assert fallback is False

    @pytest.mark.asyncio
    async def test_short_history_falls_back_to_ctl(self):
        acts = [_activity(TODAY - timedelta(days=i), 80.0) for i in range(1, 11)]  # 10 days only
        with _patch_activities(acts):
            peak, fallback = await _resolve_peak_daily_load(1, TODAY, ctl_now=55.0)
        assert peak == 55.0
        assert fallback is True

    @pytest.mark.asyncio
    async def test_no_activities_falls_back_to_ctl(self):
        with _patch_activities([]):
            peak, fallback = await _resolve_peak_daily_load(1, TODAY, ctl_now=55.0)
        assert (peak, fallback) == (55.0, True)

    @pytest.mark.asyncio
    async def test_loadless_and_datetime_rows_handled(self):
        # icu_training_load=None rows are skipped; datetime-suffixed
        # start_date_local strings slice down to the date.
        acts = [_activity(TODAY - timedelta(days=i), 60.0) for i in range(1, 43)]
        acts.append(SimpleNamespace(icu_training_load=None, start_date_local=TODAY.isoformat()))
        acts.append(SimpleNamespace(icu_training_load=40.0, start_date_local=f"{TODAY - timedelta(days=3)}T07:30:00"))
        with _patch_activities(acts):
            peak, fallback = await _resolve_peak_daily_load(1, TODAY, ctl_now=50.0)
        assert fallback is False
        assert peak == 60.0  # median of the best week stays 60 (100 TSS day is a single outlier)


class TestResolveLoads:
    @pytest.mark.asyncio
    async def test_deplanned_loads_preferred(self):
        with patch("data.taper_service.recompute_today_loads", AsyncMock(return_value=(55.0, 60.0, -5.0))):
            assert await _resolve_loads(1) == (55.0, 60.0)

    @pytest.mark.asyncio
    async def test_wellness_fallback(self):
        row = SimpleNamespace(ctl=50.0, atl=45.0)

        @asynccontextmanager
        async def fake_session():
            yield SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(first=lambda: row)))

        with (
            patch("data.taper_service.recompute_today_loads", AsyncMock(return_value=None)),
            patch("data.taper_service.get_session", fake_session),
        ):
            assert await _resolve_loads(1) == (50.0, 45.0)

    @pytest.mark.asyncio
    async def test_no_data_returns_none(self):
        @asynccontextmanager
        async def fake_session():
            yield SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(first=lambda: None)))

        with (
            patch("data.taper_service.recompute_today_loads", AsyncMock(return_value=None)),
            patch("data.taper_service.get_session", fake_session),
        ):
            assert await _resolve_loads(1) is None


class TestDistanceClassHeuristic:
    def test_markers(self):
        assert _distance_class_from_name("Ironman 70.3 Koper") == "long"
        assert _distance_class_from_name("Белградский марафон") == "long"
        assert _distance_class_from_name("Sprint triathlon") == "short"
        assert _distance_class_from_name("parkrun Ada Ciganlija") == "short"
        assert _distance_class_from_name("Olympic distance tri") == "standard"
        assert _distance_class_from_name(None) == "standard"


class TestSyncResolver:
    """get_taper_plan_for_user_sync — sync twin for the morning-report actor
    (Phase 5). Primary-goal-only; same gate shape + envelope as the async path."""

    @staticmethod
    def _call(goal_dto=None, loads=(60.0, 65.0), peak=(85.0, False)):
        with (
            patch("data.taper_service.local_today", return_value=TODAY),
            patch("data.taper_service.AthleteGoal.get_goal_dto", return_value=goal_dto),
            patch("data.taper_service._resolve_loads_sync", return_value=loads),
            patch("data.taper_service._resolve_peak_daily_load_sync", return_value=peak),
        ):
            return get_taper_plan_for_user_sync(1)

    def test_no_future_race(self):
        assert self._call(goal_dto=None)["reason"] == "no_future_race"

    def test_race_in_past(self):
        assert self._call(goal_dto=_goal(days_out=-3))["reason"] == "race_date_in_past"

    def test_no_wellness_data(self):
        assert self._call(goal_dto=_goal(), loads=None)["reason"] == "no_wellness_data"

    def test_no_training_history(self):
        assert self._call(goal_dto=_goal(), peak=(0.0, True))["reason"] == "no_training_history"

    def test_happy_path_infers_class_from_name(self):
        result = self._call(goal_dto=_goal(days_out=14, name="Ironman 70.3 Italy"))
        assert result["available"] is True
        assert result["days_to_race"] == 14
        assert result["race_distance_class"] == "long"
        assert isinstance(result["taper_start_date"], str)
        assert all(isinstance(t["date"], str) for t in result["daily_targets"])

    @pytest.mark.asyncio
    async def test_parity_with_async(self):
        # Identical resolved inputs → byte-identical envelope. This is the
        # "can't drift" guarantee: both paths share `_build_envelope`.
        goal = _goal(days_out=14, name="Test race")
        sync_res = self._call(goal_dto=goal)
        async_res = await _call(goal_dto=goal)  # same defaults: loads (60,65), peak (85,False)
        assert sync_res == async_res
