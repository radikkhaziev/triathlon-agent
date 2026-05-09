"""Unit tests for `data.sport_map.resolve_race_sport_type` — race-goal enum.

Covers the `String → enum` mapping that runs at every webhook sync and
`suggest_race` write. A drift here means new races land with the wrong
`sport_type` and the prompt / Settings UI shows misleading info.
"""

from __future__ import annotations

import pytest

from data.sport_map import RACE_SPORT_TYPES, resolve_race_sport_type


class TestResolveRaceSportType:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            # Canonical enum — direct match
            ("triathlon", "triathlon"),
            ("duathlon", "duathlon"),
            ("aquathlon", "aquathlon"),
            ("run", "run"),
            ("ride", "ride"),
            ("swim", "swim"),
            ("fitness", "fitness"),
            # Capitalization-insensitive (Claude often sends "Triathlon")
            ("Triathlon", "triathlon"),
            ("RUN", "run"),
            ("  Ride  ", "ride"),
            # Intervals.icu raw activity types — alias resolution
            ("Bike", "ride"),
            ("Cycling", "ride"),
            ("VirtualRide", "ride"),
            ("Running", "run"),
            ("TrailRun", "run"),
            ("VirtualRun", "run"),
            ("Swimming", "swim"),
            ("OpenWaterSwim", "swim"),
        ],
    )
    def test_known_mappings(self, raw: str, expected: str) -> None:
        assert resolve_race_sport_type(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_empty_falls_back_to_fitness(self, raw: str | None) -> None:
        """Schema is NOT NULL — never return None / empty."""
        assert resolve_race_sport_type(raw) == "fitness"

    @pytest.mark.parametrize(
        "raw",
        ["WeightTraining", "Yoga", "Workout", "Hike", "Marathon", "Half Marathon", "garbage"],
    )
    def test_unknown_falls_back_to_fitness(self, raw: str) -> None:
        """Unknown Intervals types or race-name free-form ('Marathon', 'Ultra')
        → fitness bucket. The resolver does NOT parse race-name vocabulary —
        Claude is expected to pass canonical Intervals types via the
        ``suggest_race`` docstring contract."""
        assert resolve_race_sport_type(raw) == "fitness"

    def test_returns_value_in_enum(self) -> None:
        """Every output must be one of the canonical 7 values, or the
        Settings dropdown / DB CHECK constraint will reject."""
        for raw in ["triathlon", "Run", "Bike", None, "garbage", "OpenWaterSwim"]:
            assert resolve_race_sport_type(raw) in RACE_SPORT_TYPES


def test_pydantic_literal_matches_resolver_enum() -> None:
    """The race-goal sport_type enum is declared in 4 places (RACE_SPORT_TYPES
    frozenset, Pydantic Literal, TypeScript union, dropdown options). This test
    asserts the two Python sources stay in lock-step — adding a value to one
    without the other would silently 422 every PATCH from the UI dropdown.

    See #323 Strand D code-review M2 finding for context. Frontend duplication
    (TS / dropdown) is enforced by code review only — TS can't be introspected
    from Python at test time.
    """
    from typing import get_args, get_type_hints

    from api.dto import AthleteGoalPatchRequest

    hints = get_type_hints(AthleteGoalPatchRequest)
    # Field type is `Literal[...] | None` → Union[Literal[...], None]; pull the
    # Literal out (it's the non-NoneType arg).
    literal = next(a for a in get_args(hints["sport_type"]) if a is not type(None))
    assert set(get_args(literal)) == RACE_SPORT_TYPES
