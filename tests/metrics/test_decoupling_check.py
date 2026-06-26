"""Tests for the Base Building Protocol decoupling check (issue #157).

Two layers: (1) deterministic, DB-free tests of the pure core — the classifier
and the row → values → verdict pipeline; (2) DB-backed tests of the live wired
path — `decoupling_check_sync` (sync query + tenant scoping) and the
`_base_building_report_line` morning-report injection — which seed the test DB.
"""

from datetime import date, timedelta

from data.db import Activity, ActivityDetail, User, get_session
from data.intervals.dto import ActivityDTO
from data.metrics import _decoupling_result, _valid_decoupling_values, classify_decoupling, decoupling_check_sync
from tasks.actors.reports import _base_building_report_line


def _row(dt: str, decoupling: float, *, sport: str = "Ride", is_race: bool = False):
    """A valid-for-decoupling steady-state effort with the given drift.

    Tuple shape mirrors the fetch twins:
    (start_date_local, type, moving_time, variability_index, hr_zone_times,
     decoupling, is_race).
    """
    moving_time = 3600 if sport == "Ride" else 2700  # bike >= 60min, run >= 45min
    return (dt, sport, moving_time, 1.00, [1800, 1500, 200, 100, 0], decoupling, is_race)


# ---------------------------------------------------------------------------
# classify_decoupling — the deterministic chronic/acute/normal call
# ---------------------------------------------------------------------------


class TestClassifyDecoupling:
    def test_chronic_three_red(self):
        assert classify_decoupling([12.0, 13.0, 11.0]) == "chronic"

    def test_chronic_two_of_three_red(self):
        assert classify_decoupling([12.0, 4.0, 11.0]) == "chronic"

    def test_acute_one_red(self):
        assert classify_decoupling([4.0, 12.0, 3.0]) == "acute"

    def test_normal_no_red(self):
        assert classify_decoupling([4.0, 6.0, 3.0]) == "normal"

    def test_insufficient_two_values(self):
        assert classify_decoupling([12.0, 13.0]) == "insufficient_data"

    def test_insufficient_empty(self):
        assert classify_decoupling([]) == "insufficient_data"

    def test_red_boundary_inclusive(self):
        """10.0 is yellow, not red — mirrors decoupling_status."""
        assert classify_decoupling([10.0, 10.0, 10.0]) == "normal"
        assert classify_decoupling([10.1, 10.1, 4.0]) == "chronic"

    def test_negative_drift_counts_as_red(self):
        """abs() — a big pulse drop is graded like a big rise."""
        assert classify_decoupling([-12.0, -11.0, 3.0]) == "chronic"

    def test_only_last_three_weighed(self):
        """Older reds outside the 3-effort window don't trigger chronic."""
        assert classify_decoupling([99.0, 99.0, 4.0, 5.0, 3.0]) == "normal"


# ---------------------------------------------------------------------------
# _valid_decoupling_values — row filtering + ordering
# ---------------------------------------------------------------------------


class TestValidDecouplingValues:
    def test_orders_oldest_to_newest(self):
        rows = [
            _row("2026-06-03T07:00:00", 11.0),
            _row("2026-06-01T07:00:00", 4.0),
            _row("2026-06-02T07:00:00", 7.0),
        ]
        assert _valid_decoupling_values(rows, "bike") == [4.0, 7.0, 11.0]

    def test_excludes_races(self):
        rows = [
            _row("2026-06-01T07:00:00", 4.0),
            _row("2026-06-02T07:00:00", 18.0, is_race=True),  # max effort, not a base signal
        ]
        assert _valid_decoupling_values(rows, "bike") == [4.0]

    def test_filters_by_sport_group(self):
        rows = [
            _row("2026-06-01T07:00:00", 4.0, sport="Ride"),
            _row("2026-06-02T07:00:00", 9.0, sport="Run"),
        ]
        assert _valid_decoupling_values(rows, "bike") == [4.0]
        assert _valid_decoupling_values(rows, "run") == [9.0]

    def test_drops_invalid_efforts(self):
        """Interval session (high VI) is not a steady-state base signal."""
        interval = ("2026-06-02T07:00:00", "Ride", 3600, 1.30, [3000, 500, 100, 0, 0], 8.0, False)
        rows = [_row("2026-06-01T07:00:00", 4.0), interval]
        assert _valid_decoupling_values(rows, "bike") == [4.0]


# ---------------------------------------------------------------------------
# _decoupling_result — verdict envelope (stateless, no transition flag)
# ---------------------------------------------------------------------------


class TestDecouplingResult:
    def test_chronic_envelope(self):
        res = _decoupling_result("bike", [4.0, 12.0, 11.0])
        assert res["status"] == "chronic"
        assert res["sport"] == "bike"
        assert res["values"] == [4.0, 12.0, 11.0]
        assert res["valid_count"] == 3

    def test_values_clipped_to_window_and_rounded(self):
        res = _decoupling_result("run", [3.0, 4.16, 5.24, 6.38])
        assert res["values"] == [4.2, 5.2, 6.4]  # last 3, rounded
        assert res["valid_count"] == 4

    def test_recovery_is_stateless_not_a_transition(self):
        """No `just_deactivated` flag: the same value list always yields the same
        verdict, so a recovered trend just reads as acute/normal — the report
        relies on the chronic banner ceasing, not a one-time announcement."""
        res = _decoupling_result("bike", [12.0, 11.0, 4.0, 3.0])
        assert res["status"] == "acute"
        assert "just_deactivated" not in res

    def test_insufficient_data_empty(self):
        res = _decoupling_result("swim", [])
        assert res["status"] == "insufficient_data"
        assert res["values"] == []
        assert res["valid_count"] == 0


# ---------------------------------------------------------------------------
# decoupling_check_sync — the live wired path (DB + tenant scoping)
# ---------------------------------------------------------------------------


async def _seed_valid_ride(aid: str, *, dt: date, decoupling: float, user_id: int, sport: str = "Ride") -> None:
    """One bike/run activity + ActivityDetail clearing every
    `is_valid_for_decoupling` gate (>=60min bike / >=45min run, VI ≤ 1.10,
    >70% Z1+Z2, decoupling not NULL)."""
    moving_time = 4200 if sport == "Ride" else 2800  # bike >= 60min, run >= 45min
    dto = ActivityDTO(id=aid, start_date_local=dt, type=sport, moving_time=moving_time, average_hr=115.0)
    dto.is_race = False
    await Activity.save_bulk(user_id, activities=[dto])
    async with get_session() as session:
        session.add(
            ActivityDetail(
                activity_id=aid,
                variability_index=1.02,
                decoupling=decoupling,
                hr_zone_times=[1200, 2400, 400, 150, 50],  # 86% Z1+Z2
                pace=2.5,
            )
        )
        await session.commit()


class TestDecouplingCheckSync:
    """Exercises the actual sync query the morning report calls — including the
    `user_id` WHERE scope (multi-tenant isolation)."""

    async def test_chronic_through_real_query(self):
        base = date(2026, 6, 1)
        for i, drift in enumerate([4.0, 12.0, 11.0]):  # 2 of 3 red
            await _seed_valid_ride(f"a{i}", dt=base + timedelta(days=i), decoupling=drift, user_id=1)
        res = decoupling_check_sync(1, "bike")
        assert res["status"] == "chronic"
        assert res["values"] == [4.0, 12.0, 11.0]  # oldest→newest ordering preserved

    async def test_tenant_isolation(self):
        """User 1's red drift must not leak into user 2's verdict."""
        async with get_session() as session:
            if not await session.get(User, 2):
                session.add(User(id=2, chat_id="test_user_2", role="viewer"))
                await session.commit()
        base = date(2026, 6, 1)
        for i, drift in enumerate([12.0, 13.0, 11.0]):  # user 1 chronic
            await _seed_valid_ride(f"u1_{i}", dt=base + timedelta(days=i), decoupling=drift, user_id=1)
        for i, drift in enumerate([4.0, 5.0, 3.0]):  # user 2 normal
            await _seed_valid_ride(f"u2_{i}", dt=base + timedelta(days=i), decoupling=drift, user_id=2)
        assert decoupling_check_sync(1, "bike")["status"] == "chronic"
        assert decoupling_check_sync(2, "bike")["status"] == "normal"

    async def test_insufficient_data_with_two_efforts(self):
        base = date(2026, 6, 1)
        for i, drift in enumerate([12.0, 13.0]):
            await _seed_valid_ride(f"b{i}", dt=base + timedelta(days=i), decoupling=drift, user_id=1)
        assert decoupling_check_sync(1, "bike")["status"] == "insufficient_data"

    async def test_chronic_stable_across_repeat_calls(self):
        """No new effort between mornings → identical verdict every run. Guards
        against a re-firing transition flag (the dropped `just_deactivated`)."""
        base = date(2026, 6, 1)
        for i, drift in enumerate([12.0, 11.0, 4.0, 3.0]):  # recovered to acute
            await _seed_valid_ride(f"c{i}", dt=base + timedelta(days=i), decoupling=drift, user_id=1)
        first = decoupling_check_sync(1, "bike")
        second = decoupling_check_sync(1, "bike")
        assert first == second  # stateless: same inputs, same output, no one-time signal


# ---------------------------------------------------------------------------
# _base_building_report_line — the morning-report injection (precedence + None)
# ---------------------------------------------------------------------------


class TestBaseBuildingReportLine:
    async def test_chronic_returns_banner_naming_sport(self):
        base = date(2026, 6, 1)
        for i, drift in enumerate([12.0, 13.0, 11.0]):
            await _seed_valid_ride(f"r{i}", dt=base + timedelta(days=i), decoupling=drift, user_id=1, sport="Run")
        line = _base_building_report_line(1)
        assert line is not None
        assert "активен (бег)" in line
        assert "Z2" in line

    async def test_names_both_sports_when_both_chronic(self):
        base = date(2026, 6, 1)
        for i, drift in enumerate([12.0, 13.0, 11.0]):
            await _seed_valid_ride(f"bk{i}", dt=base + timedelta(days=i), decoupling=drift, user_id=1, sport="Ride")
            await _seed_valid_ride(f"rn{i}", dt=base + timedelta(days=i), decoupling=drift, user_id=1, sport="Run")
        line = _base_building_report_line(1)
        assert "вело" in line and "бег" in line

    async def test_none_when_not_chronic(self):
        """One chronic-sport absent + the other normal → no injection."""
        base = date(2026, 6, 1)
        for i, drift in enumerate([4.0, 5.0, 3.0]):
            await _seed_valid_ride(f"n{i}", dt=base + timedelta(days=i), decoupling=drift, user_id=1, sport="Ride")
        assert _base_building_report_line(1) is None
