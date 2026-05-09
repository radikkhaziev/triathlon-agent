"""Tests for `GET /api/athlete/goals` — Settings list-view endpoint (#323 Strand C).

Differs from `tests/api/test_athlete_goal.py` (which covers PATCH):
this one covers the read endpoint that returns ALL active future goals,
while PATCH still operates on a single ``goal_id``. The router's job is to
delegate to ``AthleteGoal.get_goals_for_settings`` and shape the DTO into
plain JSON; the ORM helper has its own DB-level coverage.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routers.athlete import list_athlete_goals
from data.db.dto import AthleteGoalDTO


def _user(user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        role="athlete",
        is_active=True,
        athlete_id="i001",
        language="ru",
    )


def _dto(
    *,
    id: int,
    category: str,
    event_name: str,
    event_date_str: str,
    sport_type: str = "triathlon",
    ctl_target: float | None = None,
    per_sport_targets: dict | None = None,
) -> AthleteGoalDTO:
    return AthleteGoalDTO(
        id=id,
        category=category,
        event_name=event_name,
        event_date=date.fromisoformat(event_date_str),
        sport_type=sport_type,
        ctl_target=ctl_target,
        per_sport_targets=per_sport_targets,
    )


class TestListAthleteGoals:
    @pytest.mark.asyncio
    async def test_empty_list_returns_empty_array(self):
        with patch(
            "api.routers.athlete.AthleteGoal.get_goals_for_settings",
            AsyncMock(return_value=[]),
        ):
            out = await list_athlete_goals(user=_user())
        assert out == {"goals": []}

    @pytest.mark.asyncio
    async def test_single_goal_serialized(self):
        dto = _dto(
            id=1,
            category="RACE_A",
            event_name="Ironman 70.3",
            event_date_str="2026-09-15",
            sport_type="triathlon",
            ctl_target=80.0,
            per_sport_targets={"swim": 18.0, "ride": 40.0, "run": 30.0},
        )
        with patch(
            "api.routers.athlete.AthleteGoal.get_goals_for_settings",
            AsyncMock(return_value=[dto]),
        ):
            out = await list_athlete_goals(user=_user())
        assert out == {
            "goals": [
                {
                    "id": 1,
                    "category": "RACE_A",
                    "event_name": "Ironman 70.3",
                    "event_date": "2026-09-15",
                    "sport_type": "triathlon",
                    "ctl_target": 80.0,
                    "per_sport_targets": {"swim": 18.0, "ride": 40.0, "run": 30.0},
                },
            ],
        }

    @pytest.mark.asyncio
    async def test_multiple_goals_preserve_order(self):
        """Router does NOT re-sort — preserves whatever order the ORM helper
        returned. The DB-level test asserts ASC ordering; here we just verify
        the router doesn't reshuffle."""
        dtos = [
            _dto(id=1, category="RACE_B", event_name="Olympic", event_date_str="2026-06-01"),
            _dto(id=2, category="RACE_A", event_name="Ironman", event_date_str="2026-09-15"),
        ]
        with patch(
            "api.routers.athlete.AthleteGoal.get_goals_for_settings",
            AsyncMock(return_value=dtos),
        ):
            out = await list_athlete_goals(user=_user())
        names = [g["event_name"] for g in out["goals"]]
        assert names == ["Olympic", "Ironman"]

    @pytest.mark.asyncio
    async def test_uses_data_user_id_for_resolution(self):
        """`get_data_user_id` resolves the DB user_id (handles demo → owner
        rewrite, or returns user.id otherwise). Verify the helper is called
        with the resolved value, not raw ``user.id``."""
        with (
            patch(
                "api.routers.athlete.AthleteGoal.get_goals_for_settings",
                AsyncMock(return_value=[]),
            ) as helper_mock,
            patch("api.routers.athlete.get_data_user_id", return_value=42),
        ):
            await list_athlete_goals(user=_user(user_id=99))

        helper_mock.assert_awaited_once()
        # First positional arg = user_id; the resolver picked 42, not 99
        assert helper_mock.await_args.args[0] == 42

    def test_get_endpoint_uses_require_viewer_not_require_athlete(self):
        """Regression guard for #323 Strand C C1: GET must use ``require_viewer``,
        not ``require_athlete`` (which would 403 demo sessions).

        Direct unit-level calls to ``list_athlete_goals(user=...)`` bypass
        FastAPI's dependency-injection chain, so this test wires the actual
        route through TestClient + ``dependency_overrides``. If a future diff
        accidentally reverts the dep, the ``require_athlete`` override raises
        and the assert below trips.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.deps import require_athlete, require_viewer
        from api.routers.athlete import router

        app = FastAPI()
        app.include_router(router)

        sentinel_user = SimpleNamespace(id=1, role="demo", is_active=True, athlete_id=None)
        app.dependency_overrides[require_viewer] = lambda: sentinel_user
        app.dependency_overrides[require_athlete] = lambda: pytest.fail(
            "GET /api/athlete/goals must depend on require_viewer, not require_athlete"
        )

        with patch(
            "api.routers.athlete.AthleteGoal.get_goals_for_settings",
            AsyncMock(return_value=[]),
        ):
            client = TestClient(app)
            resp = client.get("/api/athlete/goals")
        assert resp.status_code == 200
        assert resp.json() == {"goals": []}

    @pytest.mark.asyncio
    async def test_demo_role_can_read_goals(self):
        """Demo session reads goals (#323 Strand C C1 fix). The endpoint uses
        ``require_viewer``, not ``require_athlete``, so demo isn't 403'd here.
        Demo's read-only tour shows owner's goals via ``get_data_user_id``
        rewrite — same as profile / wellness data on other read endpoints."""
        demo = SimpleNamespace(
            id=999,
            role="demo",
            is_active=True,
            athlete_id=None,
            language="en",
        )
        dto = _dto(id=1, category="RACE_A", event_name="Owner's race", event_date_str="2026-09-15")
        with (
            patch(
                "api.routers.athlete.AthleteGoal.get_goals_for_settings",
                AsyncMock(return_value=[dto]),
            ),
            # Demo's get_data_user_id returns the owner's id (not the demo's)
            patch("api.routers.athlete.get_data_user_id", return_value=1),
        ):
            out = await list_athlete_goals(user=demo)

        # Demo sees the goal — no 403, no empty list
        assert len(out["goals"]) == 1
        assert out["goals"][0]["event_name"] == "Owner's race"
