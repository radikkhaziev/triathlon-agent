"""Unit tests for `render_race_goal_resource` — body of the `athlete://goal`
MCP resource. Pure renderer over `list[AthleteGoalDTO]` — no DB, no MCP.

Closes Copilot review #325 finding: previous `if g.ctl_target:` truthy check
silently dropped legitimate `CTL=0` values (full taper / injury recovery
target). Now uses `is not None` so `0` renders.
"""

from __future__ import annotations

from datetime import date

from data.db.dto import AthleteGoalDTO
from mcp_server.resources.athlete_profile import render_race_goal_resource


def _dto(
    *,
    name: str = "Marathon",
    dt: date = date(2026, 9, 1),
    sport: str = "run",
    ctl_target: float | None = None,
    per_sport_targets: dict | None = None,
) -> AthleteGoalDTO:
    return AthleteGoalDTO(
        event_name=name,
        event_date=dt,
        sport_type=sport,
        ctl_target=ctl_target,
        per_sport_targets=per_sport_targets,
    )


class TestRenderRaceGoalResource:
    def test_no_goals(self) -> None:
        assert render_race_goal_resource([]) == "No active goal set."

    def test_single_goal_minimal(self) -> None:
        out = render_race_goal_resource([_dto()])
        assert "Marathon" in out
        assert "Date: 2026-09-01" in out
        assert "Sport: run" in out
        # No CTL line when ctl_target is None
        assert "CTL Target" not in out

    def test_ctl_zero_renders(self) -> None:
        """The Copilot-flagged regression: `CTL=0` MUST appear in output.
        Truthy check `if g.ctl_target:` skipped it; `is not None` renders it."""
        out = render_race_goal_resource([_dto(ctl_target=0)])
        assert "CTL Target (total): 0" in out

    def test_per_sport_zero_renders(self) -> None:
        """Same invariant for per-sport CTL=0 — full taper sport-block."""
        out = render_race_goal_resource([_dto(per_sport_targets={"swim": 0, "ride": 0, "run": 0})])
        assert "CTL Target (swim): 0" in out
        assert "CTL Target (ride): 0" in out
        assert "CTL Target (run): 0" in out

    def test_per_sport_none_skipped(self) -> None:
        """`None` per-sport target legitimately means «not set», skip it."""
        out = render_race_goal_resource([_dto(per_sport_targets={"swim": None, "ride": 35.0})])
        assert "CTL Target (swim)" not in out
        assert "CTL Target (ride): 35.0" in out

    def test_two_goals_emit_race_a_and_nearest(self) -> None:
        out = render_race_goal_resource(
            [
                _dto(name="Ironman 70.3", dt=date(2026, 9, 15), sport="triathlon"),
                _dto(name="Olympic", dt=date(2026, 6, 1), sport="triathlon"),
            ]
        )
        assert "RACE_A: Ironman 70.3" in out
        assert "Nearest: Olympic" in out

    def test_ctl_positive_renders(self) -> None:
        out = render_race_goal_resource([_dto(ctl_target=80.0)])
        assert "CTL Target (total): 80.0" in out
