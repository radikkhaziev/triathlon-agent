"""Tests for mcp_server/tools/race_projection.py — error envelopes + auto-fill."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mcp_server.tools.race_projection import get_race_projection


def _mock_user_context(user_id: int = 1):
    return patch("mcp_server.tools.race_projection.get_current_user_id", return_value=user_id)


class TestErrorCases:
    @pytest.mark.asyncio
    async def test_no_race_date_and_no_goal_returns_no_race_date(self):
        with (
            _mock_user_context(),
            patch(
                "mcp_server.tools.race_projection.AthleteGoal.get_by_category",
                AsyncMock(return_value=None),
            ),
        ):
            result = await get_race_projection(mode="today")
        assert result["available"] is False
        assert result["reason"] == "no_race_date"

    @pytest.mark.asyncio
    async def test_invalid_race_date_format(self):
        with _mock_user_context():
            result = await get_race_projection(race_date="not-a-date")
        assert result["available"] is False
        assert result["reason"] == "invalid_race_date"

    @pytest.mark.asyncio
    async def test_race_date_in_past(self):
        past = (date.today() - timedelta(days=1)).isoformat()
        with _mock_user_context():
            result = await get_race_projection(race_date=past)
        assert result["available"] is False
        assert result["reason"] == "race_date_in_past"

    @pytest.mark.asyncio
    async def test_no_distance_provided(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        with _mock_user_context():
            result = await get_race_projection(race_date=future)
        assert result["available"] is False
        assert result["reason"] == "no_distance"


class TestAutoFillRaceDate:
    @pytest.mark.asyncio
    async def test_uses_race_a_goal_when_empty(self):
        future = date.today() + timedelta(days=30)
        goal = SimpleNamespace(event_date=future)
        envelope = {"splits": {"run": {"pred": 300}}, "not_available": [], "warnings": []}
        with (
            _mock_user_context(),
            patch(
                "mcp_server.tools.race_projection.AthleteGoal.get_by_category",
                AsyncMock(return_value=goal),
            ),
            patch(
                "mcp_server.tools.race_projection.predict_splits_with_ci",
                AsyncMock(return_value=envelope),
            ) as predict_mock,
        ):
            result = await get_race_projection(mode="today", race_distance_run_m=21000)

        # predict_splits_with_ci called with goal's event_date
        assert predict_mock.call_args.kwargs["race_date"] == future.isoformat()
        assert result["available"] is True


class TestSuccessPath:
    @pytest.mark.asyncio
    async def test_returns_predict_envelope_with_available_true(self):
        future = (date.today() + timedelta(days=60)).isoformat()
        envelope = {
            "mode": "race_day",
            "race_date": future,
            "days_to_race": 60,
            "splits": {"run": {"pred": 320, "ci_low": 305, "ci_high": 340, "total_sec": 6720}},
            "not_available": [],
            "warnings": [],
            "projected_ctl": 72.0,
        }
        with (
            _mock_user_context(),
            patch(
                "mcp_server.tools.race_projection.predict_splits_with_ci",
                AsyncMock(return_value=envelope),
            ),
        ):
            result = await get_race_projection(
                mode="race_day",
                race_date=future,
                race_distance_run_m=21000,
                target_hr_run=150,
            )
        assert result["available"] is True
        assert result["splits"]["run"]["pred"] == 320

    @pytest.mark.asyncio
    async def test_all_models_missing_returns_not_trained(self):
        future = (date.today() + timedelta(days=60)).isoformat()
        envelope = {
            "splits": {},
            "not_available": ["run"],
            "below_acceptance": [],
            "warnings": ["race_run model not trained"],
        }
        with (
            _mock_user_context(),
            patch(
                "mcp_server.tools.race_projection.predict_splits_with_ci",
                AsyncMock(return_value=envelope),
            ),
        ):
            result = await get_race_projection(race_date=future, race_distance_run_m=21000)
        assert result["available"] is False
        assert result["reason"] == "model_not_trained"

    @pytest.mark.asyncio
    async def test_below_acceptance_returns_distinct_reason(self):
        """Quality-gated models surface as `model_below_acceptance`, not
        `model_not_trained` — Claude can communicate «модель калибруется»
        instead of «не существует»."""
        future = (date.today() + timedelta(days=60)).isoformat()
        envelope = {
            "splits": {},
            "not_available": [],
            "below_acceptance": ["run"],
            "warnings": ["race_run model below acceptance floor"],
        }
        with (
            _mock_user_context(),
            patch(
                "mcp_server.tools.race_projection.predict_splits_with_ci",
                AsyncMock(return_value=envelope),
            ),
        ):
            result = await get_race_projection(race_date=future, race_distance_run_m=21000)
        assert result["available"] is False
        assert result["reason"] == "model_below_acceptance"
