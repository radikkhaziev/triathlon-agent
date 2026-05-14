"""Smoke tests for the real Load- and Goal-tab endpoints (END-12, END-14).

We only validate the contract the React Dashboard depends on:
- shape of the response,
- date window respected,
- filtering rules (NULL TSS dropped, non-bucketable sports dropped),
- per-user scoping.
"""

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import require_viewer
from api.routers.dashboard import router as dashboard_router
from data.db import Activity, ActivityDetail, AthleteGoal, User, Wellness, get_session
from data.intervals.dto import ActivityDTO

_FIXED_TODAY = date(2026, 4, 30)


@pytest.fixture(autouse=True)
def _freeze_today():
    """Pin the endpoint's notion of "today" so date-window assertions are stable."""
    with patch("api.routers.dashboard._today_local", return_value=_FIXED_TODAY):
        yield


@pytest.fixture
def client():
    test_app = FastAPI()
    test_app.include_router(dashboard_router)

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.role = "owner"
    mock_user.is_active = True
    test_app.dependency_overrides[require_viewer] = lambda: mock_user
    return AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test")


async def _seed_wellness(
    user_id: int,
    dt: date,
    *,
    ctl: float | None = 60.0,
    atl: float | None = 50.0,
    recovery_score: float | None = 75.0,
    hrv: float | None = 52.0,
) -> None:
    """Insert a wellness row directly. WellnessDTO.intervals_dict() only syncs
    Intervals.icu fields, but our endpoints also need ``recovery_score`` which
    is computed locally — so we bypass the DTO and write the model directly."""
    async with get_session() as session:
        session.add(
            Wellness(
                user_id=user_id,
                date=dt.isoformat(),
                ctl=ctl,
                atl=atl,
                recovery_score=recovery_score,
                hrv=hrv,
                updated=datetime.now(timezone.utc),
            )
        )
        await session.commit()


def _make_activity(
    *,
    aid: str,
    dt: date,
    sport: str = "Ride",
    tss: float | None = 80.0,
) -> ActivityDTO:
    return ActivityDTO(
        id=aid,
        start_date_local=dt,
        type=sport,
        icu_training_load=tss,
        moving_time=3600,
        average_hr=140.0,
    )


# ---------------------------------------------------------------------------
# /api/training-load
# ---------------------------------------------------------------------------


class TestTrainingLoad:
    async def test_returns_only_real_rows(self, client):
        # Seed 3 wellness rows in window, 1 row outside (older), 1 row missing CTL
        for offset in (0, 5, 10):
            await _seed_wellness(1, _FIXED_TODAY - timedelta(days=offset))
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=200))  # outside window
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=2), ctl=None, atl=None)

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"dates", "ctl", "atl", "tsb"}
        # 3 in-window rows + 1 NULL-CTL row dropped + 1 outside-window row dropped
        assert len(data["dates"]) == 3
        assert len(data["ctl"]) == 3
        assert len(data["atl"]) == 3
        assert len(data["tsb"]) == 3

    async def test_dates_within_window(self, client):
        for offset in range(5):
            await _seed_wellness(1, _FIXED_TODAY - timedelta(days=offset))

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        oldest_allowed = _FIXED_TODAY - timedelta(days=83)
        for d_str in data["dates"]:
            d = date.fromisoformat(d_str)
            assert oldest_allowed <= d <= _FIXED_TODAY

    async def test_dates_sorted_ascending(self, client):
        for offset in (0, 3, 1, 2):  # out of order
            await _seed_wellness(1, _FIXED_TODAY - timedelta(days=offset))

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        assert data["dates"] == sorted(data["dates"])

    async def test_tsb_is_ctl_minus_atl(self, client):
        await _seed_wellness(1, _FIXED_TODAY, ctl=60.0, atl=42.0)

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        assert data["tsb"][0] == 18.0  # 60.0 - 42.0

    async def test_no_per_sport_keys_in_load_response(self, client):
        """Per-sport CTL goes to GoalTab via END-12, not Load."""
        await _seed_wellness(1, _FIXED_TODAY)

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        assert "ctl_swim" not in data
        assert "ctl_ride" not in data
        assert "ctl_run" not in data

    async def test_empty_user_returns_empty_arrays(self, client):
        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        assert resp.status_code == 200
        assert resp.json() == {"dates": [], "ctl": [], "atl": [], "tsb": []}

    async def test_per_user_scoping(self, client):
        """Wellness rows for another user must not leak into the response."""
        # Create user 2, seed a wellness row, then call as user 1
        async with get_session() as session:
            session.add(User(id=2, chat_id="23456", role="athlete"))
            await session.commit()
        await _seed_wellness(2, _FIXED_TODAY)
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=1))

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        # Only user 1's row (yesterday) should appear
        assert len(data["dates"]) == 1
        assert data["dates"][0] == (_FIXED_TODAY - timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# /api/activities
# ---------------------------------------------------------------------------


class TestActivities:
    async def test_returns_correct_shape(self, client):
        await Activity.save_bulk(
            1,
            activities=[_make_activity(aid="i1", dt=_FIXED_TODAY, sport="Ride", tss=85.0)],
        )

        async with client as c:
            resp = await c.get("/api/activities?days=28")

        assert resp.status_code == 200
        data = resp.json()
        assert "activities" in data
        assert len(data["activities"]) == 1
        a = data["activities"][0]
        assert set(a.keys()) == {"date", "sport", "tss"}
        assert a["sport"] == "cycling"
        assert a["tss"] == 85.0

    async def test_sport_mapping(self, client):
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(aid="i10", dt=_FIXED_TODAY, sport="Swim", tss=40.0),
                _make_activity(aid="i11", dt=_FIXED_TODAY, sport="Ride", tss=80.0),
                _make_activity(aid="i12", dt=_FIXED_TODAY, sport="VirtualRide", tss=70.0),
                _make_activity(aid="i13", dt=_FIXED_TODAY, sport="EBikeRide", tss=50.0),
                _make_activity(aid="i14", dt=_FIXED_TODAY, sport="MountainBikeRide", tss=60.0),
                _make_activity(aid="i15", dt=_FIXED_TODAY, sport="Run", tss=55.0),
                _make_activity(aid="i16", dt=_FIXED_TODAY, sport="TrailRun", tss=65.0),
            ],
        )

        async with client as c:
            resp = await c.get("/api/activities?days=28")

        sports = [a["sport"] for a in resp.json()["activities"]]
        assert sports.count("swimming") == 1
        assert sports.count("cycling") == 4  # Ride + VirtualRide + EBikeRide + MountainBikeRide
        assert sports.count("running") == 2  # Run + TrailRun

    async def test_drops_unmappable_sports(self, client):
        """Yoga / hike / weights aren't on the chart; they must be dropped."""
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(aid="i20", dt=_FIXED_TODAY, sport="Yoga", tss=10.0),
                _make_activity(aid="i21", dt=_FIXED_TODAY, sport="Hike", tss=30.0),
                _make_activity(aid="i22", dt=_FIXED_TODAY, sport="WeightTraining", tss=20.0),
                _make_activity(aid="i23", dt=_FIXED_TODAY, sport="Run", tss=55.0),
            ],
        )

        async with client as c:
            resp = await c.get("/api/activities?days=28")

        activities = resp.json()["activities"]
        assert len(activities) == 1
        assert activities[0]["sport"] == "running"

    async def test_drops_null_tss(self, client):
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(aid="i30", dt=_FIXED_TODAY, sport="Run", tss=None),
                _make_activity(aid="i31", dt=_FIXED_TODAY, sport="Run", tss=55.0),
            ],
        )

        async with client as c:
            resp = await c.get("/api/activities?days=28")

        activities = resp.json()["activities"]
        assert len(activities) == 1
        assert activities[0]["tss"] == 55.0

    async def test_dates_within_window(self, client):
        """28-day window — older activities must not appear."""
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(aid="i40", dt=_FIXED_TODAY, sport="Run"),
                _make_activity(aid="i41", dt=_FIXED_TODAY - timedelta(days=27), sport="Run"),
                _make_activity(aid="i42", dt=_FIXED_TODAY - timedelta(days=28), sport="Run"),
                _make_activity(aid="i43", dt=_FIXED_TODAY - timedelta(days=60), sport="Run"),
            ],
        )

        async with client as c:
            resp = await c.get("/api/activities?days=28")

        oldest_allowed = _FIXED_TODAY - timedelta(days=27)
        for a in resp.json()["activities"]:
            d = date.fromisoformat(a["date"])
            assert oldest_allowed <= d <= _FIXED_TODAY

    async def test_per_user_scoping(self, client):
        """Activities for another user must not leak into the response."""
        async with get_session() as session:
            session.add(User(id=2, chat_id="23456", role="athlete"))
            await session.commit()
        await Activity.save_bulk(
            2,
            activities=[_make_activity(aid="i_other", dt=_FIXED_TODAY, sport="Run", tss=99.0)],
        )
        await Activity.save_bulk(
            1,
            activities=[_make_activity(aid="i_self", dt=_FIXED_TODAY, sport="Run", tss=42.0)],
        )

        async with client as c:
            resp = await c.get("/api/activities?days=28")

        activities = resp.json()["activities"]
        assert len(activities) == 1
        assert activities[0]["tss"] == 42.0


# ---------------------------------------------------------------------------
# /api/recovery-trend
# ---------------------------------------------------------------------------


class TestRecoveryTrend:
    async def test_returns_correct_shape(self, client):
        await _seed_wellness(1, _FIXED_TODAY, recovery_score=80.0, hrv=55.5)

        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"dates", "recovery", "hrv"}
        assert data["dates"] == [_FIXED_TODAY.isoformat()]
        assert data["recovery"] == [80.0]
        assert data["hrv"] == [55.5]

    async def test_dates_within_window(self, client):
        for offset in range(5):
            await _seed_wellness(1, _FIXED_TODAY - timedelta(days=offset))
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=30))  # outside 21-day window

        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        data = resp.json()
        oldest_allowed = _FIXED_TODAY - timedelta(days=20)
        assert len(data["dates"]) == 5
        for d_str in data["dates"]:
            d = date.fromisoformat(d_str)
            assert oldest_allowed <= d <= _FIXED_TODAY

    async def test_skips_rows_with_neither_recovery_nor_hrv(self, client):
        """A wellness row with both fields NULL is the same as no row for the chart."""
        await _seed_wellness(1, _FIXED_TODAY, recovery_score=70.0, hrv=50.0)
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=1), recovery_score=None, hrv=None)

        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        data = resp.json()
        assert len(data["dates"]) == 1
        assert data["dates"][0] == _FIXED_TODAY.isoformat()

    async def test_keeps_rows_with_only_one_metric(self, client):
        """Recovery alone (or HRV alone) is still a meaningful point on the dual-axis chart."""
        await _seed_wellness(1, _FIXED_TODAY, recovery_score=70.0, hrv=None)

        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        data = resp.json()
        assert data["recovery"] == [70.0]
        assert data["hrv"] == [None]

    async def test_empty_user_returns_empty_arrays(self, client):
        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        assert resp.json() == {"dates": [], "recovery": [], "hrv": []}


# ---------------------------------------------------------------------------
# /api/goal
# ---------------------------------------------------------------------------


async def _seed_goal(
    user_id: int,
    *,
    event_date: date,
    ctl_target: float | None = 75.0,
    per_sport_targets: dict | None = None,
    event_name: str = "Ironman 70.3",
    category: str = "RACE_A",
) -> None:
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


async def _seed_wellness_with_sport_ctl(
    user_id: int,
    dt: date,
    *,
    ctl: float,
    sport_info: list[dict],
) -> None:
    async with get_session() as session:
        session.add(
            Wellness(
                user_id=user_id,
                date=dt.isoformat(),
                ctl=ctl,
                atl=ctl,  # value irrelevant, just satisfy NOT NULL constraints
                sport_info=sport_info,
                updated=datetime.now(timezone.utc),
            )
        )
        await session.commit()


class TestGoal:
    """Shape changed in #323 Strand C: ``/api/goal`` now returns ``{"has_goals": bool, "goals": [...]}``
    — a list of progress blocks, one per active future goal, sorted by ``event_date ASC``.
    Past goals are filtered out (the helper ``get_goals_for_settings`` enforces it).
    """

    async def test_no_goal_returns_empty_list(self, client):
        async with client as c:
            resp = await c.get("/api/goal")

        assert resp.status_code == 200
        assert resp.json() == {"has_goals": False, "goals": []}

    async def test_goal_without_per_sport_targets(self, client):
        """Athlete with overall target only — single overall bar, no per_sport block."""
        await _seed_goal(1, event_date=_FIXED_TODAY + timedelta(days=70), ctl_target=80.0)
        await _seed_wellness(1, _FIXED_TODAY, ctl=60.0, atl=55.0)

        async with client as c:
            resp = await c.get("/api/goal")

        data = resp.json()
        assert data["has_goals"] is True
        assert len(data["goals"]) == 1
        g = data["goals"][0]
        assert g["event_name"] == "Ironman 70.3"
        assert g["category"] == "RACE_A"
        assert g["sport_type"] == "triathlon"
        assert g["weeks_remaining"] == 10  # 70 // 7
        assert g["days_remaining"] == 70
        assert g["ctl_current"] == 60.0
        assert g["ctl_target"] == 80.0
        assert g["overall_pct"] == 75  # 60/80 * 100
        # Per-sport block must be omitted when targets are absent (END-12 scoping
        # decision — don't fake bars from canonical 70.3 ratios).
        assert "per_sport" not in g

    async def test_goal_with_per_sport_targets(self, client):
        await _seed_goal(
            1,
            event_date=_FIXED_TODAY + timedelta(days=84),
            ctl_target=80.0,
            per_sport_targets={"swim": 15.0, "ride": 35.0, "run": 25.0},
        )
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY,
            ctl=60.0,
            sport_info=[
                {"type": "Swim", "ctl": 12.0},
                {"type": "Ride", "ctl": 28.0},
                {"type": "Run", "ctl": 20.0},
            ],
        )

        async with client as c:
            resp = await c.get("/api/goal")

        data = resp.json()
        assert data["has_goals"] is True
        g = data["goals"][0]
        assert g["weeks_remaining"] == 12
        # Single-day wellness can't compute a 14-day ramp — projection is
        # insufficient_data per sport AND for the overall block. Bar still
        # renders the % correctly.
        insufficient = {
            "ramp_per_week": None,
            "projected_date": None,
            "reason": "insufficient_data",
            "on_track": None,
        }
        assert g["projection"] == insufficient
        assert g["per_sport"] == {
            "swim": {"ctl_current": 12.0, "ctl_target": 15.0, "pct": 80, "projection": insufficient},
            "ride": {"ctl_current": 28.0, "ctl_target": 35.0, "pct": 80, "projection": insufficient},
            "run": {"ctl_current": 20.0, "ctl_target": 25.0, "pct": 80, "projection": insufficient},
        }

    async def test_multiple_goals_returned_sorted_by_date(self, client):
        """#323 Strand C: Dashboard Goal tab shows ALL active goals, nearest first.
        Each goal carries its own progress block computed from the same wellness row."""
        await _seed_goal(
            1,
            event_date=_FIXED_TODAY + timedelta(days=120),
            ctl_target=80.0,
            event_name="Far A-race",
            category="RACE_A",
        )
        await _seed_goal(
            1,
            event_date=_FIXED_TODAY + timedelta(days=30),
            ctl_target=60.0,
            event_name="Near tune-up",
            category="RACE_B",
        )
        await _seed_wellness(1, _FIXED_TODAY, ctl=50.0, atl=45.0)

        async with client as c:
            resp = await c.get("/api/goal")

        data = resp.json()
        assert data["has_goals"] is True
        assert len(data["goals"]) == 2
        # Sort: nearest first
        assert data["goals"][0]["event_name"] == "Near tune-up"
        assert data["goals"][0]["weeks_remaining"] == 4  # 30 // 7
        assert data["goals"][1]["event_name"] == "Far A-race"
        assert data["goals"][1]["weeks_remaining"] == 17  # 120 // 7

    async def test_per_sport_drops_sports_without_target(self, client):
        """A target=0 or missing entry means "not part of this race plan" — drop, don't render 0%."""
        await _seed_goal(
            1,
            event_date=_FIXED_TODAY + timedelta(days=42),
            ctl_target=50.0,
            per_sport_targets={"run": 25.0, "swim": 0.0},  # ride missing, swim zeroed
        )
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY,
            ctl=40.0,
            sport_info=[
                {"type": "Swim", "ctl": 5.0},
                {"type": "Ride", "ctl": 22.0},
                {"type": "Run", "ctl": 18.0},
            ],
        )

        async with client as c:
            resp = await c.get("/api/goal")

        g = resp.json()["goals"][0]
        assert "per_sport" in g
        assert set(g["per_sport"].keys()) == {"run"}
        assert g["per_sport"]["run"]["pct"] == 72  # 18 / 25

    async def test_overall_pct_handles_missing_target(self, client):
        """Athlete set the race but never set a CTL target — overall_pct must be null, not /0."""
        await _seed_goal(1, event_date=_FIXED_TODAY + timedelta(days=21), ctl_target=None)
        await _seed_wellness(1, _FIXED_TODAY, ctl=60.0, atl=55.0)

        async with client as c:
            resp = await c.get("/api/goal")

        g = resp.json()["goals"][0]
        assert g["ctl_target"] is None
        assert g["overall_pct"] is None
        assert g["ctl_current"] == 60.0

    async def test_zero_weeks_remaining_on_race_day(self, client):
        """Day-of-event reads "0 weeks", not rounded up to 1."""
        await _seed_goal(1, event_date=_FIXED_TODAY, ctl_target=80.0)
        await _seed_wellness(1, _FIXED_TODAY, ctl=70.0, atl=55.0)

        async with client as c:
            resp = await c.get("/api/goal")

        g = resp.json()["goals"][0]
        assert g["weeks_remaining"] == 0
        assert g["days_remaining"] == 0

    async def test_past_race_filtered_out(self, client):
        """``get_goals_for_settings`` filters past goals server-side — past races
        no longer surface in the Dashboard list (was «clamps to 0» pre-Strand-C)."""
        await _seed_goal(1, event_date=_FIXED_TODAY - timedelta(days=10), ctl_target=80.0)
        await _seed_wellness(1, _FIXED_TODAY, ctl=70.0, atl=55.0)

        async with client as c:
            resp = await c.get("/api/goal")

        # Past goal silently dropped — list is empty
        assert resp.json() == {"has_goals": False, "goals": []}

    async def test_no_wellness_yet(self, client):
        """Brand-new athlete with a goal but no wellness rows yet renders an
        empty bar instead of crashing the endpoint."""
        await _seed_goal(1, event_date=_FIXED_TODAY + timedelta(days=30), ctl_target=80.0)

        async with client as c:
            resp = await c.get("/api/goal")

        data = resp.json()
        assert data["has_goals"] is True
        g = data["goals"][0]
        assert g["ctl_current"] is None
        assert g["overall_pct"] is None

    async def test_per_user_scoping(self, client):
        """Goal for another user must not leak into the response."""
        async with get_session() as session:
            session.add(User(id=2, chat_id="23456", role="athlete"))
            await session.commit()
        await _seed_goal(2, event_date=_FIXED_TODAY + timedelta(days=14), ctl_target=90.0)

        async with client as c:
            resp = await c.get("/api/goal")

        # User 1 has no goal of their own; user 2's must not leak through
        assert resp.json() == {"has_goals": False, "goals": []}


# ---------------------------------------------------------------------------
# /api/weekly-recap
# ---------------------------------------------------------------------------


# _FIXED_TODAY is a Thursday (weekday 3), so for offset=0:
#   newest week (idx=3): Mon 2026-04-27 → Sun 2026-05-03  (current)
#   week idx=2:          Mon 2026-04-20 → Sun 2026-04-26
#   week idx=1:          Mon 2026-04-13 → Sun 2026-04-19
#   oldest (idx=0):      Mon 2026-04-06 → Sun 2026-04-12
_W_NEWEST_START = date(2026, 4, 27)
_W_NEWEST_END = date(2026, 5, 3)
_W_OLDEST_START = date(2026, 4, 6)


async def _save_detail(activity_id: str, distance_m: float) -> None:
    """Write an ActivityDetail row directly. ``ActivityDetail.save`` expects
    an Intervals.icu detail blob; for tests we only need ``distance``, so we
    persist the model directly."""
    async with get_session() as session:
        session.add(ActivityDetail(activity_id=activity_id, distance=distance_m))
        await session.commit()


class TestWeeklyRecap:
    async def test_bucket_boundary_sunday_vs_monday(self, client):
        """Activity exactly on Sunday lands in week N; the next Monday lands in N+1."""
        # Sunday of week idx=2 → must end up in that week, not the newer one
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(aid="i_sun", dt=date(2026, 4, 26), sport="Run", tss=50.0),
                _make_activity(aid="i_mon", dt=date(2026, 4, 27), sport="Run", tss=70.0),
            ],
        )

        async with client as c:
            resp = await c.get("/api/weekly-recap?weeks=4&offset=0")

        assert resp.status_code == 200
        data = resp.json()
        # Response is freshest-first, so weeks[0] is the current week
        assert data["weeks"][0]["week_start"] == _W_NEWEST_START.isoformat()
        assert data["weeks"][0]["week_end"] == _W_NEWEST_END.isoformat()
        # Monday activity in newest week
        assert data["weeks"][0]["by_sport"]["running"]["tss"] == 70.0
        # Sunday activity in week idx=2 (one week back)
        assert data["weeks"][1]["by_sport"]["running"]["tss"] == 50.0

    async def test_drops_unmappable_sports_from_by_sport(self, client):
        """Yoga / hike never make it into by_sport — they aren't on _SPORT_MAP."""
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(aid="i_yoga", dt=_FIXED_TODAY, sport="Yoga", tss=10.0),
                _make_activity(aid="i_hike", dt=_FIXED_TODAY, sport="Hike", tss=30.0),
                _make_activity(aid="i_run", dt=_FIXED_TODAY, sport="Run", tss=55.0),
            ],
        )

        async with client as c:
            resp = await c.get("/api/weekly-recap?weeks=4&offset=0")

        newest = resp.json()["weeks"][0]
        assert set(newest["by_sport"].keys()) == {"running"}
        assert newest["by_sport"]["running"]["tss"] == 55.0

    async def test_empty_week_still_carries_ctl_bookends(self, client):
        """A week with zero activities still emits a bucket and resolves CTL bookends."""
        # Seed wellness on the bookend days only — every week's start/end has CTL/ATL.
        # Note: _nearest_wellness anchors on (week_start - 1) for ctl_start, so
        # we seed Sunday-of-prior-week as well.
        for d in [
            _W_OLDEST_START - timedelta(days=1),  # 2026-04-05 (anchor for oldest week's ctl_start)
            date(2026, 4, 12),  # oldest week's last day
            date(2026, 4, 19),
            date(2026, 4, 26),
            _W_NEWEST_END,  # 2026-05-03
        ]:
            await _seed_wellness(1, d, ctl=60.0, atl=50.0)

        async with client as c:
            resp = await c.get("/api/weekly-recap?weeks=4&offset=0")

        weeks = resp.json()["weeks"]
        assert len(weeks) == 4
        # Every bucket has bookends and an empty by_sport map
        for w in weeks:
            assert w["by_sport"] == {}
            assert w["ctl_start"] == 60.0
            assert w["ctl_end"] == 60.0
            assert w["ctl_delta"] == 0.0
            assert w["tsb_end"] == 10.0  # 60 - 50

    async def test_ctl_back_walk_covers_bootstrap_gaps(self, client):
        """When the exact bookend day is missing, _nearest_wellness walks back up
        to 6 days. The pre-fetch must cover that whole window — regression test
        for the CodeReviewer-flagged 1-day vs 7-day mismatch."""
        # Anchor for oldest week's ctl_start is (window_start - 1) = 2026-04-05.
        # Drop wellness rows 5 days BEFORE that anchor (2026-03-31). With the old
        # (wellness_start = window_start - 1) bug those rows never landed in the
        # cache and ctl_start would silently come back null.
        await _seed_wellness(1, date(2026, 3, 31), ctl=42.0, atl=35.0)
        # Plus an end-bookend anchor so ctl_end is not null and we can isolate
        # the regression to ctl_start.
        await _seed_wellness(1, _W_NEWEST_END, ctl=70.0, atl=55.0)

        async with client as c:
            resp = await c.get("/api/weekly-recap?weeks=4&offset=0")

        weeks = resp.json()["weeks"]
        # Oldest week (last in the freshest-first list)
        oldest = weeks[-1]
        assert oldest["week_start"] == _W_OLDEST_START.isoformat()
        # Without the back-walk widening this would be null.
        assert oldest["ctl_start"] == 42.0

    async def test_has_prev_true_when_older_activity_exists(self, client):
        """has_prev must flip true if any activity sits strictly before window_start."""
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(aid="i_old", dt=_W_OLDEST_START - timedelta(days=1), sport="Run"),
            ],
        )

        async with client as c:
            resp = await c.get("/api/weekly-recap?weeks=4&offset=0")

        assert resp.json()["has_prev"] is True

    async def test_has_prev_false_when_no_older_activity(self, client):
        """No activities anywhere → has_prev is false."""
        async with client as c:
            resp = await c.get("/api/weekly-recap?weeks=4&offset=0")

        assert resp.json()["has_prev"] is False

    async def test_offset_minus_one_shifts_window_back(self, client):
        """offset=-1 moves the freshest visible week to the previous Mon–Sun."""
        async with client as c:
            resp = await c.get("/api/weekly-recap?weeks=4&offset=-1")

        weeks = resp.json()["weeks"]
        # Freshest week is the prior Mon (2026-04-20) → Sun (2026-04-26)
        assert weeks[0]["week_start"] == "2026-04-20"
        assert weeks[0]["week_end"] == "2026-04-26"

    async def test_distance_aggregates_from_activity_detail(self, client):
        """Distance comes from the outer-joined ActivityDetail row; multiple
        activities in the same week roll up to one bucket per sport."""
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(aid="i_d1", dt=_FIXED_TODAY, sport="Run", tss=40.0),
                _make_activity(aid="i_d2", dt=_FIXED_TODAY - timedelta(days=1), sport="Run", tss=60.0),
            ],
        )
        await _save_detail("i_d1", 5_000.0)
        await _save_detail("i_d2", 7_500.0)

        async with client as c:
            resp = await c.get("/api/weekly-recap?weeks=4&offset=0")

        run = resp.json()["weeks"][0]["by_sport"]["running"]
        assert run["distance_m"] == 12500.0
        assert run["tss"] == 100.0
        assert run["duration_sec"] == 7200  # 2 × 3600

    async def test_per_user_scoping(self, client):
        """Activities from another user must not leak into the recap."""
        async with get_session() as session:
            session.add(User(id=2, chat_id="23456", role="athlete"))
            await session.commit()
        await Activity.save_bulk(
            2,
            activities=[_make_activity(aid="i_other", dt=_FIXED_TODAY, sport="Run", tss=99.0)],
        )

        async with client as c:
            resp = await c.get("/api/weekly-recap?weeks=4&offset=0")

        # User 1 has no activities; the response should be empty buckets
        for w in resp.json()["weeks"]:
            assert w["by_sport"] == {}


# ---------------------------------------------------------------------------
# /api/marathon-shape
# ---------------------------------------------------------------------------


# _FIXED_TODAY = 2026-04-30 (Thursday, weekday=3)
# anchor_sunday = 2026-04-26 (most recent Sunday on/before today)
# 12 weeks Mon-Sun: 2026-02-02 (Mon) → 2026-04-26 (Sun)
_MS_WINDOW_END = date(2026, 4, 26)
_MS_NEWEST_WEEK_START = date(2026, 4, 20)


async def _seed_vo2max(user_id: int, dt: date, vo2max: float) -> None:
    """Insert a wellness row with vo2max set. Bypasses _seed_wellness because the
    DTO ingestion path doesn't carry vo2max consistently — for these tests we
    only need the single field, not full wellness state."""
    async with get_session() as session:
        session.add(
            Wellness(
                user_id=user_id,
                date=dt.isoformat(),
                vo2max=vo2max,
                updated=datetime.now(timezone.utc),
            )
        )
        await session.commit()


class TestMarathonShape:
    async def test_returns_12_weeks_newest_first(self, client):
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["weeks"]) == 12
        assert data["weeks"][0]["week_start"] == _MS_NEWEST_WEEK_START.isoformat()
        assert data["weeks"][0]["week_end"] == _MS_WINDOW_END.isoformat()

    async def test_no_vo2max_returns_null_shape(self, client):
        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        # Без wellness vo2max строки — все weeks: shape_pct=null
        for w in resp.json()["weeks"]:
            assert w["shape_pct"] is None
            assert w["vo2max_used"] is None
            assert w["components"] is None

    async def test_vo2max_30d_backfill(self, client):
        """vo2max snapshot 25 дней назад должен подтянуться через walk-back."""
        await _seed_vo2max(1, _MS_WINDOW_END - timedelta(days=25), vo2max=48.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        newest = resp.json()["weeks"][0]
        assert newest["vo2max_used"] == 48.0  # picked up via back-walk

    async def test_run_distance_meters_to_km_conversion(self, client):
        """ActivityDetail.distance is METERS — endpoint must divide by 1000."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)
        # One 21.1 km long run (= 21100 m) 5 days before week_end
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(
                    aid="i_long",
                    dt=_MS_WINDOW_END - timedelta(days=5),
                    sport="Run",
                    tss=110.0,
                )
            ],
        )
        await _save_detail("i_long", 21100.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        newest = resp.json()["weeks"][0]
        # 21.1 km registered as longjog (>13 km) → actual_longjog_km == 21.1
        assert newest["components"]["actual_longjog_km"] == 21.1

    async def test_race_runs_excluded(self, client):
        """Run with is_race=True must NOT contribute to shape."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)
        race_dto = _make_activity(
            aid="i_race",
            dt=_MS_WINDOW_END - timedelta(days=10),
            sport="Run",
            tss=200.0,
        )
        race_dto.is_race = True
        await Activity.save_bulk(1, activities=[race_dto])
        await _save_detail("i_race", 42195.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        newest = resp.json()["weeks"][0]
        # No non-race runs → weekly volume 0, longjog 0, shape 0
        assert newest["shape_pct"] == 0.0
        assert newest["components"]["actual_weekly_km"] == 0.0

    async def test_race_filter_keeps_non_race_run_in_mixed_set(self, client):
        """Mixed-bag regression: race excluded, sibling non-race in same window stays."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)
        race = _make_activity(
            aid="i_race_mb",
            dt=_MS_WINDOW_END - timedelta(days=2),
            sport="Run",
            tss=200.0,
        )
        race.is_race = True
        regular = _make_activity(
            aid="i_normal_mb",
            dt=_MS_WINDOW_END - timedelta(days=4),
            sport="Run",
            tss=60.0,
        )
        await Activity.save_bulk(1, activities=[race, regular])
        await _save_detail("i_race_mb", 42195.0)
        await _save_detail("i_normal_mb", 18000.0)  # 18 km — longjog

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        newest = resp.json()["weeks"][0]
        # The 18 km non-race run survives the filter; 42.195 km race does not.
        assert newest["components"]["actual_longjog_km"] == 18.0
        assert newest["components"]["actual_weekly_km"] > 0

    async def test_vo2max_below_minimum_surfaces_clamped_value(self, client):
        """Real VO2max < 25 → response surfaces clamped 25.0 (documented in spec §7)."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=20.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        newest = resp.json()["weeks"][0]
        assert newest["vo2max_used"] == 25.0  # not 20.0 — clamp surfaces honestly
        assert newest["components"]["target_weekly_km"] == 38.6  # 25^1.135 rounded

    async def test_per_user_scoping(self, client):
        """User 2's runs and vo2max don't leak into user 1's response."""
        async with get_session() as session:
            session.add(User(id=2, chat_id="23456", role="athlete"))
            await session.commit()
        await _seed_vo2max(2, _MS_WINDOW_END, vo2max=55.0)
        run_dto = _make_activity(
            aid="i_other_run",
            dt=_MS_WINDOW_END - timedelta(days=3),
            sport="Run",
            tss=80.0,
        )
        await Activity.save_bulk(2, activities=[run_dto])
        await _save_detail("i_other_run", 15000.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        # User 1 has no wellness vo2max → all weeks null
        for w in resp.json()["weeks"]:
            assert w["shape_pct"] is None

    async def test_current_components_from_newest_week(self, client):
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(
                    aid="i_r",
                    dt=_MS_WINDOW_END - timedelta(days=2),
                    sport="Run",
                    tss=60.0,
                )
            ],
        )
        await _save_detail("i_r", 14000.0)  # 14 km — counts as longjog

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        data = resp.json()
        assert data["current_components"] is not None
        assert data["current_components"]["vo2max"] == 50.0
        assert data["current_components"]["actual_longjog_km"] == 14.0


# ---------------------------------------------------------------------------
# /api/marathon-shape — predicted_times (Phase 1.5)
# ---------------------------------------------------------------------------


def _ml_envelope(*, pred: float, total_sec: int, ci_spread_sec_per_km: float = 10.0) -> dict:
    """Build a minimal `predict_splits_with_ci` envelope with `run` populated.

    Tests mock the ML call rather than train a real model — we only care that
    the endpoint plumbs the envelope into `predicted_times` correctly.
    """
    ci_low = pred - ci_spread_sec_per_km
    ci_high = pred + ci_spread_sec_per_km
    # total_sec_ci scales proportionally to pace CI (same multiplier on distance)
    total_ci_low = int(total_sec * (ci_low / pred))
    total_ci_high = int(total_sec * (ci_high / pred))
    return {
        "mode": "today",
        "splits": {
            "run": {
                "pred": pred,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "units": "sec_per_km",
                "total_sec": total_sec,
                "total_sec_ci_low": total_ci_low,
                "total_sec_ci_high": total_ci_high,
            }
        },
        "not_available": [],
        "below_acceptance": [],
        "warnings": [],
    }


def _ml_envelope_cold_start() -> dict:
    """Envelope shape when ModelNotTrained is caught internally."""
    return {
        "mode": "today",
        "splits": {},
        "not_available": ["run"],
        "below_acceptance": [],
        "warnings": ["race_run model not trained — call `train-race-models` first"],
    }


def _ml_envelope_below_acceptance() -> dict:
    """Envelope shape when ModelBelowAcceptance gate trips (CV R²/MAE under floor)."""
    return {
        "mode": "today",
        "splits": {},
        "not_available": [],
        "below_acceptance": ["run"],
        "warnings": ["race_run model below acceptance floor — needs more / cleaner data"],
    }


class TestMarathonShapePredictedTimes:
    async def test_all_three_distances_filled(self, client):
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)

        def mock_predict(*, race_distance_run_m, **_kwargs):
            # 10K: 5:34/km, 55:40; HM: 4:51/km, 1:42:15; Marathon: 5:05/km, 3:34:50
            mapping = {
                10000: _ml_envelope(pred=334.0, total_sec=3340),
                21097: _ml_envelope(pred=290.7, total_sec=6135),
                42195: _ml_envelope(pred=305.4, total_sec=12890),
            }
            return mapping[race_distance_run_m]

        with patch(
            "api.routers.dashboard.predict_splits_with_ci",
            new=AsyncMock(side_effect=mock_predict),
        ):
            async with client as c:
                resp = await c.get("/api/marathon-shape?weeks=12")

        pt = resp.json()["predicted_times"]
        assert set(pt.keys()) == {"10K", "HM", "Marathon"}
        assert pt["10K"]["total_sec"] == 3340
        assert pt["10K"]["pace_sec_per_km"] == 334.0
        assert pt["HM"]["total_sec"] == 6135
        assert pt["Marathon"]["total_sec"] == 12890
        # CI bounds — assert values, not just key-existence (regression guard:
        # a future swap of pace_ci_low ↔ total_sec_ci_low would otherwise pass).
        # _ml_envelope uses ±10 sec/km spread, total_sec scaled proportionally.
        assert pt["10K"]["pace_ci_low"] == 324.0  # 334 − 10
        assert pt["10K"]["pace_ci_high"] == 344.0  # 334 + 10
        assert pt["HM"]["pace_ci_low"] == 280.7  # 290.7 − 10
        assert pt["HM"]["pace_ci_high"] == 300.7  # 290.7 + 10
        # total_sec_ci scales as `total × (ci_pace / pred_pace)` per _ml_envelope
        assert pt["10K"]["total_sec_ci_low"] == int(3340 * (324.0 / 334.0))
        assert pt["10K"]["total_sec_ci_high"] == int(3340 * (344.0 / 334.0))

    async def test_below_acceptance_distance_null(self, client):
        """`below_acceptance` envelope path → predicted_times[label] = null.

        Structurally identical to cold-start at the endpoint layer (no `run`
        key → null), but exercised as a separate path so a future refactor
        that splits the two doesn't silently regress.
        """
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)

        with patch(
            "api.routers.dashboard.predict_splits_with_ci",
            new=AsyncMock(return_value=_ml_envelope_below_acceptance()),
        ):
            async with client as c:
                resp = await c.get("/api/marathon-shape?weeks=12")

        pt = resp.json()["predicted_times"]
        assert pt == {"10K": None, "HM": None, "Marathon": None}

    async def test_partial_cold_start_some_distances_null(self, client):
        """Marathon = cold start, 10K + HM valid — picker switches must still work."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)

        def mock_predict(*, race_distance_run_m, **_kwargs):
            if race_distance_run_m == 42195:
                return _ml_envelope_cold_start()
            return _ml_envelope(pred=300.0, total_sec=6000)

        with patch(
            "api.routers.dashboard.predict_splits_with_ci",
            new=AsyncMock(side_effect=mock_predict),
        ):
            async with client as c:
                resp = await c.get("/api/marathon-shape?weeks=12")

        pt = resp.json()["predicted_times"]
        assert pt["10K"] is not None
        assert pt["HM"] is not None
        assert pt["Marathon"] is None  # cold start → null

    async def test_total_predict_failure_all_null(self, client):
        """ML call raises (e.g. joblib I/O fail) → graceful null, no 500."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)

        with patch(
            "api.routers.dashboard.predict_splits_with_ci",
            new=AsyncMock(side_effect=RuntimeError("joblib load failed")),
        ):
            async with client as c:
                resp = await c.get("/api/marathon-shape?weeks=12")

        assert resp.status_code == 200
        pt = resp.json()["predicted_times"]
        assert pt == {"10K": None, "HM": None, "Marathon": None}

    async def test_today_iso_passed_as_race_date(self, client):
        """Verify spec §13 contract: race_date == today.isoformat() (days_to_race=0)."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)

        mock = AsyncMock(return_value=_ml_envelope(pred=300.0, total_sec=6000))
        with patch("api.routers.dashboard.predict_splits_with_ci", new=mock):
            async with client as c:
                await c.get("/api/marathon-shape?weeks=12")

        # 3 calls (10K, HM, Marathon), each with mode='today' + race_date=today
        assert mock.call_count == 3
        for call in mock.call_args_list:
            assert call.kwargs["mode"] == "today"
            assert call.kwargs["race_date"] == "2026-04-30"  # _FIXED_TODAY

    async def test_cache_hit_skips_ml_call(self, client):
        """When Redis returns cached predicted_times, ML inference is skipped."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)
        cached = {
            "10K": {
                "total_sec": 3340,
                "total_sec_ci_low": 3210,
                "total_sec_ci_high": 3490,
                "pace_sec_per_km": 334.0,
                "pace_ci_low": 321.0,
                "pace_ci_high": 349.0,
            },
            "HM": None,
            "Marathon": None,
        }
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(cached))
        mock_redis.set = AsyncMock()
        ml_mock = AsyncMock(return_value=_ml_envelope(pred=999.9, total_sec=99999))

        with (
            patch("api.routers.dashboard.get_redis", return_value=mock_redis),
            patch("api.routers.dashboard.predict_splits_with_ci", new=ml_mock),
        ):
            async with client as c:
                resp = await c.get("/api/marathon-shape?weeks=12")

        assert resp.json()["predicted_times"] == cached
        ml_mock.assert_not_called()  # cache hit skipped all 3 inference calls

    async def test_cache_miss_writes_through(self, client):
        """Cache miss → ML called → result written to Redis with TTL."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)  # miss
        mock_redis.set = AsyncMock()
        ml_mock = AsyncMock(return_value=_ml_envelope(pred=300.0, total_sec=6000))

        with (
            patch("api.routers.dashboard.get_redis", return_value=mock_redis),
            patch("api.routers.dashboard.predict_splits_with_ci", new=ml_mock),
        ):
            async with client as c:
                await c.get("/api/marathon-shape?weeks=12")

        assert ml_mock.call_count == 3
        mock_redis.set.assert_called_once()
        # set(key, json_value, ex=ttl) — verify TTL is positive
        _kwargs = mock_redis.set.call_args.kwargs
        assert _kwargs["ex"] > 0  # TTL until midnight

    async def test_cache_disabled_falls_through(self, client):
        """`get_redis() is None` (Redis disabled) → endpoint computes fresh, no error."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)
        ml_mock = AsyncMock(return_value=_ml_envelope(pred=300.0, total_sec=6000))

        with (
            patch("api.routers.dashboard.get_redis", return_value=None),
            patch("api.routers.dashboard.predict_splits_with_ci", new=ml_mock),
        ):
            async with client as c:
                resp = await c.get("/api/marathon-shape?weeks=12")

        assert resp.status_code == 200
        assert ml_mock.call_count == 3
        assert resp.json()["predicted_times"]["10K"] is not None

    async def test_cache_write_failure_does_not_break_response(self, client):
        """Redis.set raising must not 500 — response still carries fresh ML output."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock(side_effect=RuntimeError("Redis unreachable"))
        ml_mock = AsyncMock(return_value=_ml_envelope(pred=300.0, total_sec=6000))

        with (
            patch("api.routers.dashboard.get_redis", return_value=mock_redis),
            patch("api.routers.dashboard.predict_splits_with_ci", new=ml_mock),
        ):
            async with client as c:
                resp = await c.get("/api/marathon-shape?weeks=12")

        assert resp.status_code == 200
        assert resp.json()["predicted_times"]["10K"] is not None

    async def test_total_sec_unavailable_treated_as_null(self, client):
        """Run envelope without total_sec (e.g. Ride power_only flag) → predicted_times[label] = null."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)

        # Pretend run leg came back without total_sec (edge: should never happen
        # for Run, but the endpoint must be robust)
        broken_env = {
            "mode": "today",
            "splits": {"run": {"pred": 290.0, "ci_low": 280.0, "ci_high": 300.0, "units": "sec_per_km"}},
            "not_available": [],
            "below_acceptance": [],
            "warnings": [],
        }
        with patch(
            "api.routers.dashboard.predict_splits_with_ci",
            new=AsyncMock(return_value=broken_env),
        ):
            async with client as c:
                resp = await c.get("/api/marathon-shape?weeks=12")

        pt = resp.json()["predicted_times"]
        assert pt == {"10K": None, "HM": None, "Marathon": None}
