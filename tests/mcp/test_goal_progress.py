"""Tests for mcp_server/tools/goal.py — get_goal_progress multi-goal payload.

Issue #473: the tool used to return progress for a single (nearest) goal only,
so the coaching agent wrongly told the athlete a second A-race wasn't
registered. It must now return a `goals` array covering every active future
goal, with per-sport CTL targets read per goal row.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from data.db import AthleteGoal, User, Wellness, get_session
from mcp_server.tools.goal import get_goal_progress


async def _seed_goal(*, event_name, event_date, category, ctl_target, per_sport_targets, user_id=1):
    async with get_session() as session:
        session.add(
            AthleteGoal(
                user_id=user_id,
                category=category,
                event_name=event_name,
                event_date=event_date,
                sport_type="triathlon",
                ctl_target=ctl_target,
                per_sport_targets=per_sport_targets,
                is_active=True,
            )
        )
        await session.commit()


async def _seed_wellness():
    async with get_session() as session:
        session.add(
            Wellness(
                user_id=1,
                date="2026-07-24",
                ctl=70.0,
                updated=datetime.now(timezone.utc),
                sport_info=[
                    {"type": "swim", "ctl": 12.0},
                    {"type": "ride", "ctl": 40.0},
                    {"type": "run", "ctl": 22.0},
                ],
            )
        )
        await session.commit()


class TestGetGoalProgress:
    @pytest.mark.asyncio
    async def test_returns_all_active_future_goals(self, _test_db):
        await _seed_wellness()
        await _seed_goal(
            event_name="Belgrade Ironman 70.3",
            event_date=date(2026, 9, 13),
            category="RACE_A",
            ctl_target=80,
            per_sport_targets={"swim": 12, "ride": 44, "run": 24},
        )
        await _seed_goal(
            event_name="Oceanlava Olimpic",
            event_date=date(2026, 10, 11),
            category="RACE_A",
            ctl_target=85,
            per_sport_targets={"swim": 13, "ride": 47, "run": 25},
        )

        with patch("mcp_server.tools.goal.get_current_user_id", return_value=1):
            result = await get_goal_progress()

        goals = result["goals"]
        assert len(goals) == 2
        # Sorted by event_date ascending — Belgrade (Sep) first.
        assert goals[0]["event"] == "Belgrade Ironman 70.3"
        assert goals[1]["event"] == "Oceanlava Olimpic"
        # Targets are read per goal row, not from a global config.
        assert goals[0]["overall"]["target_ctl"] == 80
        assert goals[1]["overall"]["target_ctl"] == 85
        assert goals[0]["ride"]["target_ctl"] == 44
        assert goals[1]["ride"]["target_ctl"] == 47
        # Current CTL is the single athlete-wide snapshot, shared across goals.
        assert goals[0]["overall"]["current_ctl"] == 70.0
        assert goals[1]["overall"]["current_ctl"] == 70.0
        assert goals[0]["ride"]["current_ctl"] == 40.0

    @pytest.mark.asyncio
    async def test_goal_id_filter_narrows_to_one(self, _test_db):
        await _seed_wellness()
        await _seed_goal(
            event_name="Belgrade Ironman 70.3",
            event_date=date(2026, 9, 13),
            category="RACE_A",
            ctl_target=80,
            per_sport_targets={"swim": 12, "ride": 44, "run": 24},
        )
        await _seed_goal(
            event_name="Oceanlava Olimpic",
            event_date=date(2026, 10, 11),
            category="RACE_A",
            ctl_target=85,
            per_sport_targets={"swim": 13, "ride": 47, "run": 25},
        )

        all_goals = await AthleteGoal.get_goals_for_settings(1, date(2026, 7, 24))
        oceanlava_id = next(g.id for g in all_goals if g.event_name == "Oceanlava Olimpic")

        with patch("mcp_server.tools.goal.get_current_user_id", return_value=1):
            result = await get_goal_progress(goal_id=oceanlava_id)

        assert len(result["goals"]) == 1
        assert result["goals"][0]["event"] == "Oceanlava Olimpic"

    @pytest.mark.asyncio
    async def test_unknown_goal_id_returns_error(self, _test_db):
        await _seed_goal(
            event_name="Belgrade Ironman 70.3",
            event_date=date(2026, 9, 13),
            category="RACE_A",
            ctl_target=80,
            per_sport_targets={"swim": 12, "ride": 44, "run": 24},
        )
        with patch("mcp_server.tools.goal.get_current_user_id", return_value=1):
            result = await get_goal_progress(goal_id=99999)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_active_goals_returns_empty_array(self, _test_db):
        with patch("mcp_server.tools.goal.get_current_user_id", return_value=1):
            result = await get_goal_progress()
        assert result["goals"] == []
        assert "error" in result

    @pytest.mark.asyncio
    async def test_does_not_leak_other_tenant_goals(self, _test_db):
        """User 1 must never see (nor be able to target by goal_id) a goal
        owned by user 2 — tenant isolation (MULTI_TENANT_SECURITY_SPEC T1)."""
        async with get_session() as session:
            session.add(User(id=2, chat_id="test_user_2", role="viewer"))
            await session.commit()
        await _seed_goal(
            event_name="User2 Marathon",
            event_date=date(2026, 9, 13),
            category="RACE_A",
            ctl_target=80,
            per_sport_targets={"run": 30},
            user_id=2,
        )
        user2_goal_id = next(g.id for g in await AthleteGoal.get_goals_for_settings(2, date(2026, 7, 24)))

        with patch("mcp_server.tools.goal.get_current_user_id", return_value=1):
            listed = await get_goal_progress()
            targeted = await get_goal_progress(goal_id=user2_goal_id)

        assert listed["goals"] == []
        assert "error" in targeted
