"""Unit tests for `bot.prompts._render_goals_block` — issue #323 Strand D.

Pure renderer over a list[AthleteGoalDTO]; no DB / async. Three shapes:
zero / one / two — see `_render_goals_block` docstring for the contract.
"""

from __future__ import annotations

from datetime import date

from bot.prompts import _render_goals_block
from data.db.dto import AthleteGoalDTO


def _dto(name: str, dt: date, sport: str = "triathlon") -> AthleteGoalDTO:
    return AthleteGoalDTO(event_name=name, event_date=dt, sport_type=sport)


class TestRenderGoalsBlock:
    def test_no_goals_renders_default_line(self) -> None:
        out = _render_goals_block([])
        assert out == "- Goal: не задана"

    def test_single_goal_renders_one_liner(self) -> None:
        out = _render_goals_block([_dto("Marathon", date(2026, 9, 1), "run")])
        assert out == "- Goal: Marathon (2026-09-01, run)"

    def test_two_goals_renders_focused_block(self) -> None:
        out = _render_goals_block(
            [
                _dto("Ironman 70.3", date(2026, 9, 15), "triathlon"),
                _dto("Olympic Distance", date(2026, 6, 1), "triathlon"),
            ]
        )
        # Header tells Claude how to weight the two
        assert "focus on RACE_A" in out
        # Both events present with the canonical labels
        assert "RACE_A: Ironman 70.3 (2026-09-15, triathlon)" in out
        assert "Nearest: Olympic Distance (2026-06-01, triathlon)" in out

    def test_sport_type_renders_in_one_liner(self) -> None:
        """sport_type pinned in the rendered string — verifies Strand D
        actually surfaces the new field to Claude (the whole reason for D)."""
        out = _render_goals_block([_dto("Half Marathon", date(2026, 7, 1), "run")])
        assert ", run)" in out

    def test_two_goals_each_carries_sport_type(self) -> None:
        out = _render_goals_block(
            [
                _dto("RACE_A event", date(2026, 12, 1), "triathlon"),
                _dto("Tune-up 10K", date(2026, 6, 1), "run"),
            ]
        )
        assert "triathlon" in out
        assert "run" in out
