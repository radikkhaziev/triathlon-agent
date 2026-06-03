"""Tests for ``compute_activity_comparison`` — the deterministic "this session
vs your norm" block on the activity detail page (no AI, no migration).

Pool contract: same sport, non-race, duration ±30%, IF ±12, last 120d, min 3
members. Below the floor the function returns ``available=False`` with a reason
so the webapp hides the block rather than rendering a 1-sample "norm".
"""

from datetime import date, timedelta

from data.db import Activity, ActivityDetail, get_session
from data.intervals.dto import ActivityDTO
from mcp_server.tools.progress import compute_activity_comparison


async def _seed_run(
    aid: str,
    *,
    dt: date,
    moving_time: int = 3000,
    average_hr: float = 145.0,
    intensity_factor: float = 75.0,
    efficiency_factor: float = 2.10,
    decoupling: float = 5.0,
    variability_index: float = 1.02,
    pace: float = 3.3,
    is_race: bool = False,
    user_id: int = 1,
) -> tuple[Activity, ActivityDetail]:
    """Seed one run (Activity + ActivityDetail) that clears the decoupling gate
    (VI ≤ 1.10, ≥45min, >70% Z1+Z2). Returns the (activity, detail) pair so a
    caller can use it as the reference session."""
    dto = ActivityDTO(
        id=aid,
        start_date_local=dt,
        type="Run",
        moving_time=moving_time,
        average_hr=average_hr,
    )
    dto.is_race = is_race
    await Activity.save_bulk(user_id, activities=[dto])

    async with get_session() as session:
        detail = ActivityDetail(
            activity_id=aid,
            intensity_factor=intensity_factor,
            variability_index=variability_index,
            efficiency_factor=efficiency_factor,
            decoupling=decoupling,
            pace=pace,
            hr_zone_times=[1200, 1400, 200, 150, 50],  # 86% Z1+Z2
        )
        session.add(detail)
        await session.commit()
        activity = await session.get(Activity, aid)
        detail = await session.get(ActivityDetail, aid)
        session.expunge(activity)
        session.expunge(detail)
    return activity, detail


async def _seed_pool(n: int, *, base: date, **kwargs) -> None:
    """Seed `n` similar runs on consecutive prior days."""
    for i in range(n):
        await _seed_run(f"p{i}", dt=base - timedelta(days=i + 2), **kwargs)


async def _seed_ride(
    aid: str,
    *,
    dt: date,
    moving_time: int = 3600,
    average_hr: float = 150.0,
    intensity_factor: float = 80.0,
    efficiency_factor: float = 2.00,
    decoupling: float = 4.0,
    variability_index: float = 1.05,
    normalized_power: int = 230,
    is_race: bool = False,
    user_id: int = 1,
) -> tuple[Activity, ActivityDetail]:
    """Seed one ride (Activity + ActivityDetail). Ride uses `normalized_power`
    instead of `pace`; clears the decoupling gate (VI ≤ 1.10, ≥60min bike,
    >70% Z1+Z2)."""
    dto = ActivityDTO(
        id=aid,
        start_date_local=dt,
        type="Ride",
        moving_time=moving_time,
        average_hr=average_hr,
    )
    dto.is_race = is_race
    await Activity.save_bulk(user_id, activities=[dto])

    async with get_session() as session:
        detail = ActivityDetail(
            activity_id=aid,
            intensity_factor=intensity_factor,
            variability_index=variability_index,
            efficiency_factor=efficiency_factor,
            decoupling=decoupling,
            normalized_power=normalized_power,
            hr_zone_times=[1400, 1600, 300, 200, 100],  # 83% Z1+Z2, sums to 3600
        )
        session.add(detail)
        await session.commit()
        activity = await session.get(Activity, aid)
        detail = await session.get(ActivityDetail, aid)
        session.expunge(activity)
        session.expunge(detail)
    return activity, detail


async def _seed_ride_pool(n: int, *, base: date, **kwargs) -> None:
    """Seed `n` similar rides on consecutive prior days."""
    for i in range(n):
        await _seed_ride(f"rp{i}", dt=base - timedelta(days=i + 2), **kwargs)


class TestPoolGate:
    async def test_thin_pool_unavailable(self):
        """Two similar sessions < min pool of 3 → available=False/thin_pool."""
        today = date.today()
        ref_act, ref_det = await _seed_run("ref", dt=today)
        await _seed_pool(2, base=today)

        out = await compute_activity_comparison(1, ref_act, ref_det)

        assert out["available"] is False
        assert out["reason"] == "thin_pool"
        assert out["pool_n"] == 2

    async def test_no_similar_when_duration_far(self):
        """Pool exists but durations are outside ±30% → no_similar (pre-IF)."""
        today = date.today()
        ref_act, ref_det = await _seed_run("ref", dt=today, moving_time=3000)
        # 6000s is +100%, well outside 0.7–1.3 × 3000 = 2100–3900.
        await _seed_pool(4, base=today, moving_time=6000)

        out = await compute_activity_comparison(1, ref_act, ref_det)

        assert out["available"] is False
        assert out["reason"] == "no_similar"

    async def test_if_tolerance_filters_pool(self):
        """Sessions within duration but IF off by > 12 are dropped → thin_pool."""
        today = date.today()
        ref_act, ref_det = await _seed_run("ref", dt=today, intensity_factor=75.0)
        # IF 95 is 20 points away (> _CMP_IF_TOL of 12) — duration matches.
        await _seed_pool(4, base=today, intensity_factor=95.0)

        out = await compute_activity_comparison(1, ref_act, ref_det)

        assert out["available"] is False
        assert out["reason"] == "thin_pool"
        assert out["pool_n"] == 0

    async def test_unsupported_when_no_if(self):
        """No reference IF → unsupported (can't anchor the pool)."""
        today = date.today()
        ref_act, ref_det = await _seed_run("ref", dt=today, intensity_factor=75.0)
        ref_det.intensity_factor = None

        out = await compute_activity_comparison(1, ref_act, ref_det)

        assert out["available"] is False
        assert out["reason"] == "unsupported"

    async def test_swim_unsupported(self):
        """Swim has no decoupling/EF contract here → unsupported."""
        today = date.today()
        ref_act, ref_det = await _seed_run("ref", dt=today)
        ref_act.type = "Swim"

        out = await compute_activity_comparison(1, ref_act, ref_det)

        assert out["available"] is False
        assert out["reason"] == "unsupported"


class TestMarkers:
    async def test_pool_returns_markers(self):
        """A 3-session pool yields available=True with the core markers."""
        today = date.today()
        ref_act, ref_det = await _seed_run("ref", dt=today, decoupling=11.0, efficiency_factor=2.10)
        await _seed_pool(3, base=today, decoupling=5.0, efficiency_factor=2.00)

        out = await compute_activity_comparison(1, ref_act, ref_det)

        assert out["available"] is True
        assert out["pool_n"] == 3
        keys = {m["key"] for m in out["markers"]}
        # decoupling (valid pool), ef, pace, avg_hr, vi all have ≥3 samples.
        assert {"decoupling", "ef", "pace", "avg_hr"} <= keys

    async def test_decoupling_band_worse(self):
        """Reference decoupling well above the pool median → band='worse'
        (lower-is-better marker, delta > 5% of norm)."""
        today = date.today()
        ref_act, ref_det = await _seed_run("ref", dt=today, decoupling=11.0)
        await _seed_pool(3, base=today, decoupling=5.0)

        out = await compute_activity_comparison(1, ref_act, ref_det)
        dec = next(m for m in out["markers"] if m["key"] == "decoupling")

        assert dec["band"] == "worse"
        assert dec["norm_median"] == 5.0
        assert dec["pool_n"] == 3

    async def test_ef_band_better(self):
        """Reference EF above the pool median → band='better' (higher-is-better)."""
        today = date.today()
        ref_act, ref_det = await _seed_run("ref", dt=today, efficiency_factor=2.40)
        await _seed_pool(3, base=today, efficiency_factor=2.00)

        out = await compute_activity_comparison(1, ref_act, ref_det)
        ef = next(m for m in out["markers"] if m["key"] == "ef")

        assert ef["band"] == "better"

    async def test_race_excluded_from_pool(self):
        """A race in-window must not count toward the pool — 2 normal + 1 race
        stays below the min-3 floor."""
        today = date.today()
        ref_act, ref_det = await _seed_run("ref", dt=today)
        await _seed_pool(2, base=today)
        await _seed_run("race", dt=today - timedelta(days=1), is_race=True)

        out = await compute_activity_comparison(1, ref_act, ref_det)

        assert out["available"] is False
        assert out["pool_n"] == 2

    async def test_race_reference_short_circuits(self):
        """A race *reference* returns reason='race' before any pool query — a
        race effort vs an easy-session norm is apples-to-oranges."""
        today = date.today()
        ref_act, ref_det = await _seed_run("ref", dt=today, is_race=True)
        await _seed_pool(4, base=today)

        out = await compute_activity_comparison(1, ref_act, ref_det)

        assert out["available"] is False
        assert out["reason"] == "race"
        assert out["pool_n"] == 0

    async def test_pace_in_sec_per_km_not_inverted(self):
        """`pace` stored as sec/km (e.g. 290 vs 320) is lower-is-better: a faster
        reference (lower sec/km) is `better`, not inverted by an m/s assumption."""
        today = date.today()
        # 290 sec/km is faster than the pool's 320 sec/km median.
        ref_act, ref_det = await _seed_run("ref", dt=today, pace=290.0)
        await _seed_pool(3, base=today, pace=320.0)

        out = await compute_activity_comparison(1, ref_act, ref_det)
        pace = next(m for m in out["markers"] if m["key"] == "pace")

        assert pace["band"] == "better"
        assert pace["norm_median"] == 320.0

    async def test_window_days_in_payload(self):
        """Available payload carries the server-side window so the UI header
        can't drift from `_CMP_WINDOW_DAYS`."""
        today = date.today()
        ref_act, ref_det = await _seed_run("ref", dt=today)
        await _seed_pool(3, base=today)

        out = await compute_activity_comparison(1, ref_act, ref_det)

        assert out["available"] is True
        assert out["window_days"] == 120


class TestRideMarkers:
    async def test_ride_np_marker_better(self):
        """Ride pool yields an `np` marker (not `pace`); reference NP above the
        pool median → band='better' (higher-is-better)."""
        today = date.today()
        ref_act, ref_det = await _seed_ride("ref", dt=today, normalized_power=260)
        await _seed_ride_pool(3, base=today, normalized_power=230)

        out = await compute_activity_comparison(1, ref_act, ref_det)

        assert out["available"] is True
        keys = {m["key"] for m in out["markers"]}
        assert "np" in keys
        assert "pace" not in keys  # pace is run-only
        np_marker = next(m for m in out["markers"] if m["key"] == "np")
        assert np_marker["band"] == "better"
        assert np_marker["norm_median"] == 230
