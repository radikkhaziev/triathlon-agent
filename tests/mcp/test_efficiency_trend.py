"""Regression tests for ``compute_efficiency_trend``.

Covers the race-exclusion contract relied on by:
  - ``/api/bike-readiness`` (BIKE_READINESS_SPEC §3.3, §7)
  - ``/api/progress``
  - the ``get_efficiency_trend`` MCP tool

The function pre-filters activities before the bulk-detail fetch; race-effort
(`is_race=True`) is peak load, not a training-base signal, so all three
callers want it dropped before the decoupling median / weekly EF are computed.
"""

from datetime import date, timedelta

from data.db import Activity, ActivityDetail, AthleteSettings, get_session
from data.intervals.dto import ActivityDTO
from mcp_server.tools.progress import compute_efficiency_trend


async def _seed_bike_thresholds(user_id: int = 1) -> None:
    """LTHR = 153 → Z2 (68–83% LTHR) = 104–127 bpm. Test rides set avg_hr=115
    so they clear the Z2 gate under ``strict_filter=True``."""
    await AthleteSettings.upsert(user_id=user_id, sport="Ride", lthr=153)


async def _seed_bike_ride(
    aid: str,
    *,
    dt: date,
    is_race: bool = False,
    decoupling: float = 4.0,
    user_id: int = 1,
) -> None:
    """Seed one bike activity + a matching ActivityDetail that clears every
    `is_valid_for_decoupling` gate (VI ≤ 1.10, ≥60min ride, >70% Z1+Z2,
    decoupling not NULL) and `_is_z2` (avg_hr inside 68–83% LTHR)."""
    dto = ActivityDTO(
        id=aid,
        start_date_local=dt,
        type="Ride",
        moving_time=4200,  # 70 min, > 60-min bike floor
        average_hr=115.0,  # inside Z2 (104–127) for LTHR=153
    )
    dto.is_race = is_race
    await Activity.save_bulk(user_id, activities=[dto])

    async with get_session() as session:
        session.add(
            ActivityDetail(
                activity_id=aid,
                variability_index=1.02,
                efficiency_factor=2.10,
                decoupling=decoupling,
                hr_zone_times=[1200, 2400, 400, 150, 50],  # 86% in Z1+Z2
                pace=2.5,
            )
        )
        await session.commit()


class TestRaceExclusion:
    """Spec BIKE_READINESS_SPEC §3.3 + §7 — races out of decoupling + EF trend."""

    async def test_race_dropped_from_decoupling_trend(self):
        """A bike race with elevated decoupling must not poison the median.

        Without the fix the 12% race-day decoupling would flip the
        last-5 median above 5% and force the Durability traffic-light yellow.
        """
        await _seed_bike_thresholds()
        # Three healthy rides (decoupling ≈ 4%), then a race with 12% drift.
        today = date.today()
        for i, aid in enumerate(["r1", "r2", "r3"]):
            await _seed_bike_ride(aid, dt=today - timedelta(days=i + 2), decoupling=4.0)
        await _seed_bike_ride("race", dt=today - timedelta(days=1), is_race=True, decoupling=12.0)

        result = await compute_efficiency_trend(user_id=1, sport="bike", days_back=84, strict_filter=True)

        # Single-sport request → flat dict (not nested under "bike").
        trend = result["decoupling_trend"]
        assert trend["last_n"] == 3, "race must be dropped before the last-5 median"
        assert 12.0 not in trend["values"]
        assert trend["median"] == 4.0
        assert trend["status"] == "green"

    async def test_race_dropped_from_weekly_ef(self):
        """A bike race's EF must not land in any weekly bucket — `weekly`
        feeds the EFChart and the supplementary EF-trend signal."""
        await _seed_bike_thresholds()
        today = date.today()
        await _seed_bike_ride("nrm", dt=today - timedelta(days=3), decoupling=4.0)
        await _seed_bike_ride("rac", dt=today - timedelta(days=2), is_race=True, decoupling=4.0)

        result = await compute_efficiency_trend(user_id=1, sport="bike", days_back=84, strict_filter=True)

        # Race id must not appear among the per-activity entries either.
        ids = [a["id"] for a in result["activities"]]
        assert "rac" not in ids
        assert "nrm" in ids
        # Weekly aggregate exists (single non-race ride) but is built only
        # from the non-race; sessions count == 1 confirms isolation.
        assert sum(w["sessions"] for w in result["weekly"]) == 1

    async def test_baseline_non_race_included(self):
        """Sanity: the same fixture without `is_race=True` IS counted —
        ensures the assertions above flunk the test if race exclusion
        accidentally turns into blanket exclusion."""
        await _seed_bike_thresholds()
        today = date.today()
        await _seed_bike_ride("plain", dt=today - timedelta(days=1), decoupling=4.0)

        result = await compute_efficiency_trend(user_id=1, sport="bike", days_back=84, strict_filter=True)

        assert result["data_points"] == 1
        assert result["decoupling_trend"]["last_n"] == 1
        assert result["decoupling_trend"]["values"] == [4.0]
