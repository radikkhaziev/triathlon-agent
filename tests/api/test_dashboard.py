"""Smoke tests for the real Load-tab endpoints (END-14).

We only validate the contract the React Load tab depends on:
- shape of the response,
- date window respected,
- filtering rules (NULL TSS dropped, non-bucketable sports dropped),
- per-user scoping.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.deps import require_viewer
from api.routers.dashboard import router as dashboard_router
from data.db import Activity, User, Wellness, get_session
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
            session.add(User(id=2, chat_id="other_user", role="athlete"))
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
        await _seed_wellness(
            1, _FIXED_TODAY - timedelta(days=1), recovery_score=None, hrv=None
        )

        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        data = resp.json()
        assert len(data["dates"]) == 1
        assert data["dates"][0] == _FIXED_TODAY.isoformat()

    async def test_keeps_rows_with_only_one_metric(self, client):
        """Recovery alone (or HRV alone) is still a meaningful point on the dual-axis chart."""
        await _seed_wellness(
            1, _FIXED_TODAY, recovery_score=70.0, hrv=None
        )

        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        data = resp.json()
        assert data["recovery"] == [70.0]
        assert data["hrv"] == [None]

    async def test_empty_user_returns_empty_arrays(self, client):
        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        assert resp.json() == {"dates": [], "recovery": [], "hrv": []}
