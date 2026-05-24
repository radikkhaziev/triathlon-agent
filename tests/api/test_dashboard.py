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
from data.db import (
    Activity,
    ActivityDetail,
    AthleteGoal,
    AthleteSettings,
    Race,
    ScheduledWorkout,
    User,
    Wellness,
    get_session,
)
from data.intervals.dto import ActivityDTO

_FIXED_TODAY = date(2026, 4, 30)


@pytest.fixture(autouse=True)
def _freeze_today():
    """Pin the endpoint's notion of "today" so date-window assertions are stable.
    Two names to patch: legacy `_today_local` (still used by most endpoints) and
    `local_today` (training-load endpoint switched to the canonical version)."""
    with (
        patch("api.routers.dashboard._today_local", return_value=_FIXED_TODAY),
        patch("api.routers.dashboard.local_today", return_value=_FIXED_TODAY),
    ):
        yield


# Mirror of `api.routers.dashboard._FORECAST_FALLBACK_DAYS`. When there are no
# future scheduled workouts, the endpoint still extends arrays forward by this
# many days for zero-load decay (the "what if I stop training" view).
_FORECAST_FALLBACK_DAYS = 28


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
    resting_hr: int | None = None,
    sleep_secs: int | None = None,
    sleep_score: float | None = None,
    weight: float | None = None,
    body_fat: float | None = None,
    vo2max: float | None = None,
    steps: int | None = None,
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
                resting_hr=resting_hr,
                sleep_secs=sleep_secs,
                sleep_score=sleep_score,
                weight=weight,
                body_fat=body_fat,
                vo2max=vo2max,
                steps=steps,
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
        assert set(data.keys()) == {
            "dates",
            "today_date",
            "ctl",
            "atl",
            "tsb",
            "ctl_swim",
            "ctl_ride",
            "ctl_run",
            "atl_swim",
            "atl_ride",
            "atl_run",
        }
        # 3 in-window rows + 1 NULL-CTL row dropped + 1 outside-window row dropped.
        # Past length is verified via the today_date split; forward extension
        # (decay-only fallback or plan-aware) appends beyond it.
        past_len = data["dates"].index(data["today_date"]) + 1
        assert past_len == 3
        assert len([v for v in data["ctl"][:past_len] if v is not None]) == 3
        assert len([v for v in data["atl"][:past_len] if v is not None]) == 3

    async def test_dates_within_window(self, client):
        for offset in range(5):
            await _seed_wellness(1, _FIXED_TODAY - timedelta(days=offset))

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        # Past dates clamped by ?days=; future dates extend up to the forecast
        # horizon (fallback 28d when no scheduled workouts exist).
        oldest_allowed = _FIXED_TODAY - timedelta(days=83)
        newest_allowed = _FIXED_TODAY + timedelta(days=_FORECAST_FALLBACK_DAYS)
        for d_str in data["dates"]:
            d = date.fromisoformat(d_str)
            assert oldest_allowed <= d <= newest_allowed

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

    async def test_per_sport_ctl_parsed_from_sport_info(self, client):
        """Per-sport CTL series feed the Training-load detail by-sport breakdown.
        Check the past slice; the forecast tail is verified in TestTrainingLoadForecast."""
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
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        past = data["dates"].index(data["today_date"]) + 1
        assert data["ctl_swim"][:past] == [12.0]
        assert data["ctl_ride"][:past] == [28.0]
        assert data["ctl_run"][:past] == [20.0]

    async def test_per_sport_ctl_null_when_sport_info_absent(self, client):
        """A wellness row with no sport_info yields null per-sport CTL, not 0.
        Forecast tail also stays null when there's no past value to extrapolate from."""
        await _seed_wellness(1, _FIXED_TODAY)

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        assert all(v is None for v in data["ctl_swim"])
        assert all(v is None for v in data["ctl_ride"])
        assert all(v is None for v in data["ctl_run"])

    async def test_per_sport_ctl_interleaved_nulls_stay_index_aligned(self, client):
        """When a sport has CTL on some days but not others, its array keeps
        null holes index-aligned with `dates` — the detail screen's chart
        spans those gaps."""
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY - timedelta(days=1),
            ctl=58.0,
            sport_info=[{"type": "Ride", "ctl": 28.0}],
        )
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY,
            ctl=60.0,
            sport_info=[{"type": "Run", "ctl": 20.0}],
        )

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        past_len = data["dates"].index(data["today_date"]) + 1
        assert data["dates"][:past_len] == [(_FIXED_TODAY - timedelta(days=1)).isoformat(), _FIXED_TODAY.isoformat()]
        assert data["ctl_swim"][:past_len] == [None, None]
        assert data["ctl_ride"][:past_len] == [28.0, None]
        assert data["ctl_run"][:past_len] == [None, 20.0]

    async def test_per_sport_atl_parsed_from_sport_info(self, client):
        """Per-sport ATL series — parallel to CTL, same JSON source. Past slice only."""
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY,
            ctl=60.0,
            sport_info=[
                {"type": "Swim", "ctl": 12.0, "atl": 8.0},
                {"type": "Ride", "ctl": 28.0, "atl": 35.0},
                {"type": "Run", "ctl": 20.0, "atl": 18.0},
            ],
        )

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        past = data["dates"].index(data["today_date"]) + 1
        assert data["atl_swim"][:past] == [8.0]
        assert data["atl_ride"][:past] == [35.0]
        assert data["atl_run"][:past] == [18.0]

    async def test_per_sport_atl_null_when_only_ctl_present(self, client):
        """Legacy rows pre-Step-1 carry only ctl key → atl arrays return null.
        Forecast can't extrapolate ATL without a past anchor — stays null in tail too."""
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY,
            ctl=60.0,
            sport_info=[{"type": "Run", "ctl": 20.0}],
        )

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        past = data["dates"].index(data["today_date"]) + 1
        assert data["ctl_run"][:past] == [20.0]
        assert all(v is None for v in data["atl_run"])

    async def test_empty_user_returns_decay_forecast_only(self, client):
        """No past rows → today_date falls back to today, forecast tail extends
        with the 28-day decay window. Per-sport arrays stay all-null because
        there's no past per-sport CTL to extrapolate from. Overall arrays are
        null-padded — past slice empty, future slice null."""
        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        assert resp.status_code == 200
        data = resp.json()
        assert data["today_date"] == _FIXED_TODAY.isoformat()
        # Past slice empty (no wellness rows), future slice = 28 days of nulls.
        assert len(data["dates"]) == _FORECAST_FALLBACK_DAYS
        assert all(v is None for v in data["ctl"])
        assert all(v is None for v in data["atl"])
        assert all(v is None for v in data["tsb"])
        assert all(v is None for v in data["ctl_swim"])
        assert all(v is None for v in data["atl_run"])

    async def test_today_date_reflects_last_past_row(self, client):
        """today_date is the anchor for the actual/forecast split — it tracks
        the most recent past wellness row, not necessarily _FIXED_TODAY."""
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY - timedelta(days=3),
            ctl=50.0,
            sport_info=[{"type": "Run", "ctl": 20.0, "atl": 15.0}],
        )

        async with client as c:
            resp = await c.get("/api/training-load?days=84")

        data = resp.json()
        assert data["today_date"] == (_FIXED_TODAY - timedelta(days=3)).isoformat()

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
        # Only user 1's row (yesterday) appears in the past slice; the rest of
        # `dates` is forecast tail extending past today_date.
        past_len = data["dates"].index(data["today_date"]) + 1
        assert past_len == 1
        assert data["dates"][0] == (_FIXED_TODAY - timedelta(days=1)).isoformat()


class TestTrainingLoadForecast:
    """Plan-aware forward extension of per-sport CTL/ATL — Step 3.5 of
    docs/PER_SPORT_LOAD_SPEC.md."""

    async def test_no_future_workouts_uses_decay_fallback(self, client):
        """No scheduled future workouts → forecast still drawn as zero-load
        decay over the fallback window (28 days). ATL collapses fast (τ=7),
        CTL decays slowly (τ=42)."""
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY,
            ctl=60.0,
            sport_info=[{"type": "Run", "ctl": 20.0, "atl": 15.0}],
        )

        async with client as c:
            resp = await c.get("/api/training-load?days=30")

        data = resp.json()
        assert len(data["dates"]) == 1 + _FORECAST_FALLBACK_DAYS
        # Run forecast: ATL should collapse below CTL within ~14 days.
        last_ctl = data["ctl_run"][-1]
        last_atl = data["atl_run"][-1]
        assert last_ctl is not None and last_atl is not None
        assert last_atl < last_ctl  # ATL decays much faster than CTL
        assert last_ctl < 20.0  # CTL trended down from start

    async def test_future_workouts_extend_arrays_to_horizon(self, client):
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY,
            ctl=60.0,
            sport_info=[{"type": "Run", "ctl": 20.0, "atl": 18.0}],
        )
        # Plan one Run 5 days out — horizon is that day.
        await _seed_scheduled_workout(
            user_id=1,
            workout_id=1001,
            dt=_FIXED_TODAY + timedelta(days=5),
            sport="Run",
            tss=50,
        )

        async with client as c:
            resp = await c.get("/api/training-load?days=30")

        data = resp.json()
        # 1 past + 5 future dates.
        assert len(data["dates"]) == 6
        assert data["dates"][-1] == (_FIXED_TODAY + timedelta(days=5)).isoformat()
        # Run array extends with forecasted values; Swim/Ride have no past CTL
        # → future stays None for them.
        assert len(data["ctl_run"]) == 6
        assert data["ctl_run"][0] == 20.0
        assert all(v is not None for v in data["ctl_run"])
        assert data["atl_run"][-1] is not None
        # Ride/Swim never had per-sport CTL recorded → forecast cannot extrapolate.
        assert data["ctl_ride"] == [None] * 6
        assert data["ctl_swim"] == [None] * 6

    async def test_horizon_is_globally_latest_workout(self, client):
        """One sport plans further than another → all sports' forecast extends
        to the global horizon (the further sport's last workout)."""
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY,
            ctl=60.0,
            sport_info=[
                {"type": "Run", "ctl": 20.0, "atl": 18.0},
                {"type": "Ride", "ctl": 25.0, "atl": 22.0},
            ],
        )
        # Ride workout at T+2, Run workout at T+8 → global horizon = T+8.
        await _seed_scheduled_workout(1, 2001, _FIXED_TODAY + timedelta(days=2), "Ride", 60)
        await _seed_scheduled_workout(1, 2002, _FIXED_TODAY + timedelta(days=8), "Run", 40)

        async with client as c:
            resp = await c.get("/api/training-load?days=30")

        data = resp.json()
        assert data["dates"][-1] == (_FIXED_TODAY + timedelta(days=8)).isoformat()
        # Both Ride and Run extend through to the horizon.
        assert len(data["ctl_ride"]) == 9  # 1 past + 8 future
        assert len(data["ctl_run"]) == 9
        # Ride decays after its single workout — no plan from T+3 onward.
        ride_future = data["ctl_ride"][1:]
        assert all(v is not None for v in ride_future)

    async def test_past_workouts_do_not_trigger_plan_aware_horizon(self, client):
        """Workouts strictly in the past are ignored — they don't pull the
        plan-aware horizon out. Falls back to the 28-day decay window."""
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY,
            ctl=60.0,
            sport_info=[{"type": "Run", "ctl": 20.0, "atl": 18.0}],
        )
        await _seed_scheduled_workout(1, 3001, _FIXED_TODAY - timedelta(days=2), "Run", 50)

        async with client as c:
            resp = await c.get("/api/training-load?days=30")

        data = resp.json()
        # Past workout doesn't extend the horizon → fallback fires.
        assert len(data["dates"]) == 1 + _FORECAST_FALLBACK_DAYS

    async def test_overall_ctl_atl_tsb_project_forward(self, client):
        """Overall ctl/atl/tsb values past today_date carry projected EMA
        values, not nulls (spec decision #12 reversal). Code-review W3."""
        await _seed_wellness_with_sport_ctl(
            1,
            _FIXED_TODAY,
            ctl=60.0,
            sport_info=[{"type": "Run", "ctl": 20.0, "atl": 18.0}],
        )

        async with client as c:
            resp = await c.get("/api/training-load?days=30")

        data = resp.json()
        today_idx = data["dates"].index(data["today_date"])
        future_overall = data["ctl"][today_idx + 1 :]
        assert len(future_overall) > 0
        # No nulls in the forecast tail — overall IS extended.
        assert all(v is not None for v in future_overall), data["ctl"]
        # Past anchor = 60. Zero-load decay over 28 days at τ=42 → e^(-28/42) ≈ 0.51,
        # so the last point should drop materially below today's value.
        assert future_overall[-1] < 60.0
        # Sanity: tsb = ctl - atl on every forecast index too.
        for i, idx in enumerate(range(today_idx + 1, len(data["dates"]))):
            assert data["tsb"][idx] == pytest.approx(
                data["ctl"][idx] - data["atl"][idx], abs=0.2
            ), f"tsb mismatch at offset {i}"

    async def test_overall_forecast_atl_decays_faster_than_ctl(self, client):
        """ATL τ=7 vs CTL τ=42 — over 28 days of zero load ATL should collapse
        to near zero while CTL still holds about half its value. Regression
        guard against accidentally swapping the EMA decay constants."""
        await _seed_wellness(1, _FIXED_TODAY, ctl=80.0, atl=80.0)

        async with client as c:
            resp = await c.get("/api/training-load?days=7")

        data = resp.json()
        # 28-day fallback fires (no planned workouts).
        last_ctl = data["ctl"][-1]
        last_atl = data["atl"][-1]
        # CTL after 28d zero load: 80 * e^(-28/42) ≈ 41.
        assert 35 < last_ctl < 50, f"CTL={last_ctl}"
        # ATL after 28d zero load: 80 * e^(-28/7) ≈ 1.5.
        assert last_atl < 5, f"ATL={last_atl}"
        # TSB becomes strongly positive when fatigue collapses faster than fitness.
        assert data["tsb"][-1] > 30, f"TSB={data['tsb'][-1]}"

    async def test_overall_forecast_includes_non_tri_sports(self, client):
        """W2: overall TSB forecast must include WeightTraining/Yoga/Hike etc.
        Per-sport arrays stay Swim/Ride/Run-only, but the overall should match
        what Intervals.icu computes (which sums every sport)."""
        await _seed_wellness(1, _FIXED_TODAY, ctl=50.0, atl=50.0)
        # Plan one WeightTraining session tomorrow — should affect overall TSB
        # but stay invisible in per-sport arrays. Plus one Run workout further
        # out so the plan-aware horizon extends past T+1 (otherwise the forecast
        # would be a single day, leaving no room to verify decay).
        await _seed_scheduled_workout(1, 9500, _FIXED_TODAY + timedelta(days=1), "WeightTraining", 60)
        await _seed_scheduled_workout(1, 9501, _FIXED_TODAY + timedelta(days=5), "Run", 10)

        async with client as c:
            resp = await c.get("/api/training-load?days=7")

        data = resp.json()
        today_idx = data["dates"].index(data["today_date"])
        # Per-sport arrays for WeightTraining day: Ride/Swim get nothing
        # (no past per-sport CTL → null array). Run gets nothing on T+1 either
        # because its past CTL is also missing.
        assert data["ctl_ride"][today_idx + 1] is None
        assert data["ctl_swim"][today_idx + 1] is None
        # Overall ATL on the WeightTraining day MUST move up vs the no-plan
        # baseline (50.0). τ=7 with 60 TSS adds ~13% × (60-50) ≈ +1.3, so
        # atl[T+1] > 51 confirms the strength session reached overall.
        atl_workout_day = data["atl"][today_idx + 1]
        atl_two_days_in = data["atl"][today_idx + 2]
        assert atl_workout_day > 50.5, f"WeightTraining must pump overall ATL above 50: {atl_workout_day}"
        # T+2 is zero-load → ATL decays from the T+1 peak.
        assert atl_workout_day > atl_two_days_in, "ATL must decay on the no-load day after"


async def _seed_scheduled_workout(
    user_id: int,
    workout_id: int,
    dt: date,
    sport: str,
    tss: int,
) -> None:
    async with get_session() as session:
        session.add(
            ScheduledWorkout(
                id=workout_id,
                user_id=user_id,
                start_date_local=dt.isoformat(),
                category="WORKOUT",
                type=sport,
                icu_training_load=tss,
            )
        )
        await session.commit()


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

    async def test_planned_returns_future_workouts(self, client):
        """Forecast bars in the daily-TSS chart need planned workouts past
        today — same {date, sport, tss} shape as past activities."""
        await _seed_scheduled_workout(1, 9001, _FIXED_TODAY + timedelta(days=2), "Run", 50)
        await _seed_scheduled_workout(1, 9002, _FIXED_TODAY + timedelta(days=4), "Swim", 30)

        async with client as c:
            resp = await c.get("/api/activities?days=28")

        data = resp.json()
        assert "planned" in data
        assert len(data["planned"]) == 2
        # Same shape as past activities — frontend treats both uniformly.
        assert set(data["planned"][0].keys()) == {"date", "sport", "tss"}
        assert data["planned"][0]["sport"] == "running"
        assert data["planned"][0]["tss"] == 50.0
        assert data["planned"][1]["sport"] == "swimming"

    async def test_planned_excludes_today_and_past(self, client):
        """Today's workout shows up in `activities` (it's already done or in
        progress); planned is strictly future to avoid double-counting."""
        await _seed_scheduled_workout(1, 9100, _FIXED_TODAY, "Run", 30)
        await _seed_scheduled_workout(1, 9101, _FIXED_TODAY - timedelta(days=1), "Run", 40)
        await _seed_scheduled_workout(1, 9102, _FIXED_TODAY + timedelta(days=1), "Run", 50)

        async with client as c:
            resp = await c.get("/api/activities?days=28")

        planned = resp.json()["planned"]
        assert len(planned) == 1
        assert planned[0]["date"] == (_FIXED_TODAY + timedelta(days=1)).isoformat()

    async def test_planned_empty_when_no_future_workouts(self, client):
        async with client as c:
            resp = await c.get("/api/activities?days=28")
        assert resp.json()["planned"] == []


# ---------------------------------------------------------------------------
# /api/recovery-trend
# ---------------------------------------------------------------------------


class TestRecoveryTrend:
    async def test_returns_correct_shape(self, client):
        await _seed_wellness(1, _FIXED_TODAY, recovery_score=80.0, hrv=55.5, resting_hr=48)

        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"dates", "recovery", "hrv", "rhr"}
        assert data["dates"] == [_FIXED_TODAY.isoformat()]
        assert data["recovery"] == [80.0]
        assert data["hrv"] == [55.5]
        assert data["rhr"] == [48]

    async def test_accepts_365_day_window(self, client):
        """The Wellness "Recovery trend" 1y pill requests days=365; 366 is over the cap."""
        async with client as c:
            ok = await c.get("/api/recovery-trend?days=365")
            over = await c.get("/api/recovery-trend?days=366")
        assert ok.status_code == 200
        assert over.status_code == 422

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

    async def test_skips_rows_with_no_metric_at_all(self, client):
        """A wellness row with recovery + HRV + RHR all NULL is the same as no row."""
        await _seed_wellness(1, _FIXED_TODAY, recovery_score=70.0, hrv=50.0)
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=1), recovery_score=None, hrv=None, resting_hr=None)

        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        data = resp.json()
        assert len(data["dates"]) == 1
        assert data["dates"][0] == _FIXED_TODAY.isoformat()

    async def test_keeps_rows_with_only_one_metric(self, client):
        """Recovery alone (or HRV/RHR alone) is still a meaningful point on the chart."""
        await _seed_wellness(1, _FIXED_TODAY, recovery_score=70.0, hrv=None)

        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        data = resp.json()
        assert data["recovery"] == [70.0]
        assert data["hrv"] == [None]
        assert data["rhr"] == [None]

    async def test_keeps_rows_with_only_rhr(self, client):
        """A row carrying just RHR feeds the RHR line — it must not be dropped."""
        await _seed_wellness(1, _FIXED_TODAY, recovery_score=None, hrv=None, resting_hr=44)

        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        data = resp.json()
        assert data["dates"] == [_FIXED_TODAY.isoformat()]
        assert data["rhr"] == [44]
        assert data["recovery"] == [None]
        assert data["hrv"] == [None]

    async def test_zero_rhr_treated_as_missing(self, client):
        """Intervals.icu uses restingHR=0 as a no-data sentinel — never plot it."""
        # A row whose only field is the 0 sentinel is functionally empty → dropped.
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=1), recovery_score=None, hrv=None, resting_hr=0)
        # A real day with a 0-sentinel RHR keeps the row but nulls the rhr point.
        await _seed_wellness(1, _FIXED_TODAY, recovery_score=70.0, hrv=50.0, resting_hr=0)

        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        data = resp.json()
        assert data["dates"] == [_FIXED_TODAY.isoformat()]
        assert data["rhr"] == [None]

    async def test_empty_user_returns_empty_arrays(self, client):
        async with client as c:
            resp = await c.get("/api/recovery-trend?days=21")

        assert resp.json() == {"dates": [], "recovery": [], "hrv": [], "rhr": []}


# ---------------------------------------------------------------------------
# /api/sleep-trend
# ---------------------------------------------------------------------------


class TestSleepTrend:
    async def test_returns_correct_shape(self, client):
        await _seed_wellness(1, _FIXED_TODAY, sleep_secs=26640, sleep_score=68.0)

        async with client as c:
            resp = await c.get("/api/sleep-trend?days=21")

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"dates", "duration_min", "score"}
        assert data["dates"] == [_FIXED_TODAY.isoformat()]
        # 26640s = 444 min exactly.
        assert data["duration_min"] == [444]
        assert data["score"] == [68.0]

    async def test_accepts_365_day_window(self, client):
        """The 1y range pill requests days=365; 366 is over the cap."""
        async with client as c:
            ok = await c.get("/api/sleep-trend?days=365")
            over = await c.get("/api/sleep-trend?days=366")
        assert ok.status_code == 200
        assert over.status_code == 422

    async def test_skips_rows_with_no_sleep_data(self, client):
        """A wellness row with neither duration nor score is the same as no row."""
        await _seed_wellness(1, _FIXED_TODAY, sleep_secs=25200, sleep_score=72.0)
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=1), sleep_secs=None, sleep_score=None)

        async with client as c:
            resp = await c.get("/api/sleep-trend?days=21")

        data = resp.json()
        assert data["dates"] == [_FIXED_TODAY.isoformat()]

    async def test_zero_sleep_secs_treated_as_missing(self, client):
        """Intervals.icu writes sleep_secs=0 for an un-captured night — a sentinel."""
        # Only the 0 sentinel → functionally empty row → dropped.
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=1), sleep_secs=0, sleep_score=None)
        # Real score but a 0-sentinel duration → row kept, duration nulled.
        await _seed_wellness(1, _FIXED_TODAY, sleep_secs=0, sleep_score=64.0)

        async with client as c:
            resp = await c.get("/api/sleep-trend?days=21")

        data = resp.json()
        assert data["dates"] == [_FIXED_TODAY.isoformat()]
        assert data["duration_min"] == [None]
        assert data["score"] == [64.0]

    async def test_empty_user_returns_empty_arrays(self, client):
        async with client as c:
            resp = await c.get("/api/sleep-trend?days=21")

        assert resp.json() == {"dates": [], "duration_min": [], "score": []}

    async def test_per_user_scoping(self, client):
        """Sleep rows for another user must not leak into the response."""
        async with get_session() as session:
            session.add(User(id=2, chat_id="23456", role="athlete"))
            await session.commit()
        await _seed_wellness(2, _FIXED_TODAY, sleep_secs=28800, sleep_score=90.0)
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=1), sleep_secs=21600, sleep_score=55.0)

        async with client as c:
            resp = await c.get("/api/sleep-trend?days=21")

        # Authenticated user is 1 — user 2's row must not leak.
        data = resp.json()
        assert data["dates"] == [(_FIXED_TODAY - timedelta(days=1)).isoformat()]
        assert data["score"] == [55.0]


# ---------------------------------------------------------------------------
# /api/body-trend
# ---------------------------------------------------------------------------


class TestBodyTrend:
    async def test_returns_correct_shape(self, client):
        await _seed_wellness(1, _FIXED_TODAY, weight=78.2, body_fat=25.2, vo2max=48.5, steps=8432)

        async with client as c:
            resp = await c.get("/api/body-trend?days=21")

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"dates", "weight", "body_fat", "vo2max", "steps"}
        assert data["dates"] == [_FIXED_TODAY.isoformat()]
        assert data["weight"] == [78.2]
        assert data["body_fat"] == [25.2]
        assert data["vo2max"] == [48.5]
        assert data["steps"] == [8432]

    async def test_accepts_365_day_window(self, client):
        """The 1y range pill requests days=365; 366 is over the cap."""
        async with client as c:
            ok = await c.get("/api/body-trend?days=365")
            over = await c.get("/api/body-trend?days=366")
        assert ok.status_code == 200
        assert over.status_code == 422

    async def test_skips_rows_with_no_body_data(self, client):
        """A wellness row with no body metric at all is the same as no row."""
        await _seed_wellness(1, _FIXED_TODAY, weight=80.0)
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=1), recovery_score=70.0, hrv=50.0)

        async with client as c:
            resp = await c.get("/api/body-trend?days=21")

        data = resp.json()
        assert data["dates"] == [_FIXED_TODAY.isoformat()]

    async def test_keeps_rows_with_only_one_metric(self, client):
        """A row carrying just one body metric is still a point on its chart."""
        await _seed_wellness(1, _FIXED_TODAY, steps=9000)

        async with client as c:
            resp = await c.get("/api/body-trend?days=21")

        data = resp.json()
        assert data["dates"] == [_FIXED_TODAY.isoformat()]
        assert data["steps"] == [9000]
        assert data["weight"] == [None]
        assert data["body_fat"] == [None]
        assert data["vo2max"] == [None]

    async def test_zero_steps_kept_as_real_value(self, client):
        """steps=0 is a genuine rest day — NOT a no-data sentinel (unlike
        resting_hr=0 / sleep_secs=0). It must survive as 0, not become null."""
        await _seed_wellness(1, _FIXED_TODAY, steps=0)

        async with client as c:
            resp = await c.get("/api/body-trend?days=21")

        data = resp.json()
        assert data["dates"] == [_FIXED_TODAY.isoformat()]
        assert data["steps"] == [0]

    async def test_empty_user_returns_empty_arrays(self, client):
        async with client as c:
            resp = await c.get("/api/body-trend?days=21")

        assert resp.json() == {"dates": [], "weight": [], "body_fat": [], "vo2max": [], "steps": []}

    async def test_per_user_scoping(self, client):
        """Body rows for another user must not leak into the response."""
        async with get_session() as session:
            session.add(User(id=2, chat_id="23456", role="athlete"))
            await session.commit()
        await _seed_wellness(2, _FIXED_TODAY, weight=99.9)
        await _seed_wellness(1, _FIXED_TODAY - timedelta(days=1), weight=70.0)

        async with client as c:
            resp = await c.get("/api/body-trend?days=21")

        data = resp.json()
        assert data["dates"] == [(_FIXED_TODAY - timedelta(days=1)).isoformat()]
        assert data["weight"] == [70.0]


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


async def _save_detail(activity_id: str, distance_m: float) -> None:
    """Write an ActivityDetail row directly. ``ActivityDetail.save`` expects
    an Intervals.icu detail blob; for tests we only need ``distance``, so we
    persist the model directly."""
    async with get_session() as session:
        session.add(ActivityDetail(activity_id=activity_id, distance=distance_m))
        await session.commit()


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

        # Без wellness vo2max строки — все weeks: shape_pct=null + current_components None
        data = resp.json()
        for w in data["weeks"]:
            assert w["shape_pct"] is None
        assert data["current_components"] is None

    async def test_vo2max_30d_backfill(self, client):
        """vo2max snapshot 25 дней назад должен подтянуться через walk-back."""
        await _seed_vo2max(1, _MS_WINDOW_END - timedelta(days=25), vo2max=48.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        assert resp.json()["current_components"]["vo2max"] == 48.0  # picked up via back-walk

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

        # 21.1 km registered as longjog (>13 km) → actual_longjog_km == 21.1
        assert resp.json()["current_components"]["actual_longjog_km"] == 21.1

    async def test_race_runs_included_matches_runalyze(self, client):
        """Run with is_race=True MUST contribute to shape — mirror Runalyze upstream.

        Spec §1 declarative stance + §7 + §14 D1.A: race-day km are real
        basic-endurance volume. Race kilometers count toward total_km_182d,
        and races ≥13km count toward longjog_score quadratic term.
        """
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)
        race_dto = _make_activity(
            aid="i_race",
            dt=_MS_WINDOW_END - timedelta(days=10),
            sport="Run",
            tss=200.0,
        )
        race_dto.is_race = True
        await Activity.save_bulk(1, activities=[race_dto])
        await _save_detail("i_race", 42195.0)  # marathon distance, > MIN_KM_FOR_LONGJOG

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        data = resp.json()
        # Race contributes to weekly_km (>0) and is a 42-km longjog
        assert data["current_components"]["actual_weekly_km"] > 0
        assert data["current_components"]["actual_longjog_km"] == 42.2  # rounded km
        assert data["weeks"][0]["shape_pct"] > 0  # non-zero shape from race volume

    async def test_race_and_non_race_both_counted(self, client):
        """Mixed-bag: race + sibling non-race in same window — both count in volume."""
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
        await _save_detail("i_normal_mb", 18000.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        # Both runs counted; race longest → actual_longjog_km = 42.2 (the race)
        assert resp.json()["current_components"]["actual_longjog_km"] == 42.2

    async def test_vo2max_below_minimum_surfaces_clamped_value(self, client):
        """Real VO2max < 25 → response surfaces clamped 25.0 (documented in spec §7)."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=20.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        cc = resp.json()["current_components"]
        assert cc["vo2max"] == 25.0  # not 20.0 — clamp surfaces honestly
        assert cc["target_weekly_km"] == 38.6  # 25^1.135 rounded

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

    async def test_displayed_target_long_run_km_in_components(self, client):
        """Spec §3 D2.A: displayed_target_long_run_km = target_longjog_km + 13.

        For V=50: target_longjog_km ≈ 17.3, displayed ≈ 30.3 — matches the
        Runalyze «Required Long Run» column on Marathon row (ln(50/4)*12).
        """
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        # displayed_target_long_run_km = ln(50/4)*12 = 30.31 → round 30.3
        assert resp.json()["current_components"]["displayed_target_long_run_km"] == 30.3

    async def test_max_run_race_distance_m_in_response(self, client):
        """Endpoint exposes longest Run-race distance so widget can flag extrapolated predictions.

        XGBoost can't extrapolate beyond training distance range — for a user
        with no marathons, Marathon prediction collapses to «pace for longest
        seen distance». Widget renders a footnote when
        `selected_distance > max_run_race_distance * 1.3`.
        """
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)
        # Two Run races at different distances. Race links via activity_id.
        for aid, distance_m in (("i_race_hm", 21097.0), ("i_race_10k", 10000.0)):
            await Activity.save_bulk(
                1,
                activities=[
                    _make_activity(
                        aid=aid,
                        dt=_MS_WINDOW_END - timedelta(days=60),
                        sport="Run",
                        tss=80.0,
                    )
                ],
            )
            await _save_detail(aid, distance_m)
        async with get_session() as session:
            session.add_all(
                [
                    Race(
                        user_id=1,
                        activity_id="i_race_hm",
                        name="HM Test",
                        race_type="B",
                        distance_m=21097.0,
                    ),
                    Race(
                        user_id=1,
                        activity_id="i_race_10k",
                        name="10K Test",
                        race_type="C",
                        distance_m=10000.0,
                    ),
                ]
            )
            await session.commit()

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        # Max of 21097 / 10000 → 21097.0
        assert resp.json()["max_run_race_distance_m"] == 21097.0

    async def test_max_run_race_distance_m_null_without_races(self, client):
        """No Run races → field is null. Widget shows no footnote in this case."""
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        assert resp.json()["max_run_race_distance_m"] is None

    async def test_endpoint_formula_outputs_for_v50(self, client):
        """Regression: endpoint emits raw marathon-baseline targets per spec §3.

        For V=50:
          target_weekly_km = 50^1.135 = 84.79 → round to 84.8
          displayed_target_long_run_km = ln(50/4)*12 = 30.31 → round to 30.3
          (target_longjog_km = displayed − 13 = 17.31 → round to 17.3)

        Distance-adjusted factors live CLIENT-side (§3 + MS-11) — server only
        emits raw marathon-baseline. Widget applies `_RUNALYZE_DISTANCE_FACTORS`
        per picker selection.

        Note: Runalyze screenshot Marathon row showed 58 km weekly which
        corresponds to V≈35.8 in the formula (not exactly 37) — display values
        in the upstream UI appear to use intermediate rounding. Our formula
        matches the canonical BasicEndurance.php source; precise screenshot
        parity requires exact V which is not derivable from the screenshot.
        """
        await _seed_vo2max(1, _MS_WINDOW_END, vo2max=50.0)

        async with client as c:
            resp = await c.get("/api/marathon-shape?weeks=12")

        c = resp.json()["current_components"]
        assert c["target_weekly_km"] == 84.8
        assert c["displayed_target_long_run_km"] == 30.3

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


# ---------------------------------------------------------------------------
# /api/bike-readiness   (BIKE_READINESS_SPEC §3, §5, §11)
# ---------------------------------------------------------------------------


# Sunday-anchored 12-week window, mirror /api/marathon-shape:
#   _FIXED_TODAY = 2026-04-30 (Thu) → newest Sunday = 2026-04-26 → newest week
#   Mon 2026-04-20 → Sun 2026-04-26; oldest Mon = 2026-02-02.
_BR_WINDOW_END = _MS_WINDOW_END  # = date(2026, 4, 26)
_BR_NEWEST_WEEK_START = _MS_NEWEST_WEEK_START  # = date(2026, 4, 20)


async def _seed_wellness_sport_info(
    user_id: int,
    dt: date,
    *,
    ctl_bike: float | None = None,
    ctl_run: float | None = None,
) -> None:
    """Insert a wellness row with `sport_info` populated. The endpoint reads
    CTL_bike via `extract_sport_ctl(sport_info)["ride"]`, which expects a list
    of `{"type": <sport>, "ctl": <float>}` dicts (the format Intervals.icu
    returns enriched with our pipeline's `ctl` field, see `data/utils.py:81`).
    """
    sport_info: list[dict] = []
    if ctl_bike is not None:
        sport_info.append({"type": "Ride", "ctl": ctl_bike})
    if ctl_run is not None:
        sport_info.append({"type": "Run", "ctl": ctl_run})
    async with get_session() as session:
        session.add(
            Wellness(
                user_id=user_id,
                date=dt.isoformat(),
                sport_info=sport_info or None,
                updated=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def _seed_valid_bike_ride(
    aid: str,
    *,
    dt: date,
    is_race: bool = False,
    decoupling: float | None = 4.0,
    moving_time: int = 4200,
    efficiency_factor: float = 2.10,
    user_id: int = 1,
) -> None:
    """Seed one bike Activity + an ActivityDetail row that clears every
    `is_valid_for_decoupling` gate (≥60min, VI ≤1.10, >70% Z1+Z2, decoupling
    not NULL) and `_is_z2` (avg_hr inside 68–83% LTHR, with LTHR=153 from
    `_seed_bike_thresholds`). Pass `decoupling=None` to seed an indoor-ride
    style row that the strict filter should drop. `efficiency_factor` is
    overridable so `test_ef_trend_pct_field_populated` can seed a positive
    trend (rides with different EF values across two weekly buckets)."""
    dto = ActivityDTO(
        id=aid,
        start_date_local=dt,
        type="Ride",
        moving_time=moving_time,
        average_hr=115.0,  # inside Z2 for LTHR=153
    )
    dto.is_race = is_race
    await Activity.save_bulk(user_id, activities=[dto])
    async with get_session() as session:
        session.add(
            ActivityDetail(
                activity_id=aid,
                variability_index=1.02,
                efficiency_factor=efficiency_factor,
                decoupling=decoupling,
                hr_zone_times=[1200, 2400, 400, 150, 50],  # 86% Z1+Z2
                pace=2.5,
            )
        )
        await session.commit()


async def _seed_bike_thresholds(user_id: int = 1) -> None:
    """LTHR=153 → Z2 (68–83% LTHR) = 104–127 bpm. `_seed_valid_bike_ride`
    sets avg_hr=115 so rides clear the Z2 gate under `strict_filter=True`."""
    await AthleteSettings.upsert(user_id=user_id, sport="Ride", lthr=153)


class TestBikeReadiness:
    """Spec BIKE_READINESS_SPEC §8 — 12 integration tests for /api/bike-readiness."""

    @pytest.fixture(autouse=True)
    def _freeze_progress_today(self):
        """`compute_efficiency_trend` (mcp_server/tools/progress.py:107) calls
        bare `date.today()` to compute its 84-day window. The module-level
        `_freeze_today` only pins the dashboard endpoint's notion of today —
        without this extra patch, the helper's window drifts with wall-clock
        time and the decoupling / EF-trend assertions become date-fragile."""

        class _FrozenDate(date):
            @classmethod
            def today(cls):
                return _FIXED_TODAY

        with patch("mcp_server.tools.progress.date", _FrozenDate):
            yield

    async def test_returns_12_weeks_newest_first(self, client):
        await _seed_wellness_sport_info(1, _BR_WINDOW_END, ctl_bike=70.0)

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["weeks"]) == 12
        assert data["weeks"][0]["week_start"] == _BR_NEWEST_WEEK_START.isoformat()
        assert data["weeks"][0]["week_end"] == _BR_WINDOW_END.isoformat()
        # Newest-first ordering — second bucket must be one week earlier
        assert data["weeks"][1]["week_end"] == (_BR_WINDOW_END - timedelta(days=7)).isoformat()

    async def test_ctl_bike_extracted_from_sport_info_json(self, client):
        await _seed_wellness_sport_info(1, _BR_WINDOW_END, ctl_bike=68.4, ctl_run=42.0)

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        data = resp.json()
        # Only the Ride entry's CTL is surfaced — not run.
        assert data["weeks"][0]["ctl_bike"] == 68.4
        assert data["current_components"]["ctl_bike"] == 68.4

    async def test_no_sport_info_returns_null_ctl(self, client):
        # Wellness row exists but without sport_info — typical for a fresh user
        # whose Intervals.icu pipeline hasn't enriched per-sport CTL yet.
        async with get_session() as session:
            session.add(
                Wellness(
                    user_id=1,
                    date=_BR_WINDOW_END.isoformat(),
                    sport_info=None,
                    updated=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        data = resp.json()
        for w in data["weeks"]:
            assert w["ctl_bike"] is None
        assert data["current_components"]["ctl_bike"] is None

    async def test_ctl_bike_7d_backwalk(self, client):
        """A Sunday with NULL sport_info should fall back to the most recent
        earlier day with a value (spec §7 — 7-day walk-back, safe because
        CTL decays with τ=42d)."""
        # Seed CTL 5 days BEFORE window_end (= Tuesday 2026-04-21). The
        # window_end Sunday itself has no row → endpoint walks back to find 65.0.
        await _seed_wellness_sport_info(1, _BR_WINDOW_END - timedelta(days=5), ctl_bike=65.0)

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        assert resp.json()["weeks"][0]["ctl_bike"] == 65.0

    async def test_longest_ride_max_in_28d(self, client):
        # Three rides: 70min (4200s), 120min (7200s), 90min (5400s). The
        # 120-min ride wins → 2.0h.
        await _seed_valid_bike_ride("short", dt=_BR_WINDOW_END - timedelta(days=2), moving_time=4200)
        await _seed_valid_bike_ride("long", dt=_BR_WINDOW_END - timedelta(days=10), moving_time=7200)
        await _seed_valid_bike_ride("mid", dt=_BR_WINDOW_END - timedelta(days=20), moving_time=5400)

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        cc = resp.json()["current_components"]
        assert cc["longest_ride_hours"] == 2.0
        assert cc["longest_ride_date"] == (_BR_WINDOW_END - timedelta(days=10)).isoformat()

    async def test_race_rides_excluded(self, client):
        """Spec §3.2, §7: is_race=True rides must not surface as longest_ride
        and must not contribute to the decoupling median."""
        await _seed_bike_thresholds()
        # 3-hour A-race + 1-hour training ride. Without the filter the race
        # would win the longest-ride slot and pollute decoupling with 12%.
        await _seed_valid_bike_ride(
            "race",
            dt=_BR_WINDOW_END - timedelta(days=3),
            is_race=True,
            moving_time=10800,  # 3h
            decoupling=12.0,
        )
        await _seed_valid_bike_ride(
            "train",
            dt=_BR_WINDOW_END - timedelta(days=2),
            moving_time=3600,
            decoupling=4.0,
        )

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        cc = resp.json()["current_components"]
        assert cc["longest_ride_hours"] == 1.0  # the 1h training ride, not the 3h race
        assert cc["longest_ride_date"] == (_BR_WINDOW_END - timedelta(days=2)).isoformat()
        # Race decoupling 12% would have made the median yellow/red; training-only
        # median is 4.0% → green.
        assert cc["decoupling_median_pct"] == 4.0
        assert cc["decoupling_status"] == "green"
        assert cc["decoupling_n"] == 1

    async def test_decoupling_median_from_compute_efficiency_trend(self, client):
        """`decoupling_median_pct` must match exactly the value the helper
        produces — spec §3.3 «не пишем свой SQL/Python pipeline»."""
        await _seed_bike_thresholds()
        # 5 rides with decoupling 3.0, 3.5, 4.0, 4.5, 5.0 → median = 4.0
        for i, drift in enumerate([3.0, 3.5, 4.0, 4.5, 5.0]):
            await _seed_valid_bike_ride(
                f"r{i}",
                dt=_BR_WINDOW_END - timedelta(days=10 + i),
                decoupling=drift,
            )

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        cc = resp.json()["current_components"]
        assert cc["decoupling_median_pct"] == 4.0
        assert cc["decoupling_n"] == 5

    async def test_decoupling_status_yellow_threshold(self, client):
        """Status binding follows `decoupling_status()` helper: green ≤5,
        yellow ≤10, red >10. This exercises the *yellow* band; the green
        path is covered by `test_race_rides_excluded`, the red band by
        `test_decoupling_status_red_threshold`."""
        await _seed_bike_thresholds()
        # 5 rides all at 7% → median = 7.0 → yellow
        for i in range(5):
            await _seed_valid_bike_ride(
                f"y{i}",
                dt=_BR_WINDOW_END - timedelta(days=2 + i),
                decoupling=7.0,
            )

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        cc = resp.json()["current_components"]
        assert cc["decoupling_median_pct"] == 7.0
        assert cc["decoupling_status"] == "yellow"

    async def test_decoupling_status_red_threshold(self, client):
        """Median strictly > 10 → red. Locks the upper threshold so a future
        widening of the yellow band (e.g. yellow ≤ 12) would fail loud."""
        await _seed_bike_thresholds()
        # 5 rides all at 12% → median = 12.0 → red
        for i in range(5):
            await _seed_valid_bike_ride(
                f"r{i}",
                dt=_BR_WINDOW_END - timedelta(days=2 + i),
                decoupling=12.0,
            )

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        cc = resp.json()["current_components"]
        assert cc["decoupling_median_pct"] == 12.0
        assert cc["decoupling_status"] == "red"

    async def test_indoor_ride_counts_if_decoupling_present(self, client):
        """Spec §7: indoor ride (VirtualRide normalises → "Ride" at DTO
        ingest, `data/intervals/dto.py:_normalize_type`) is counted iff
        ActivityDetail.decoupling is not NULL. A ride without decoupling —
        common for older indoor rides without HR drift computed — must be
        filtered out by `is_valid_for_decoupling`."""
        await _seed_bike_thresholds()
        await _seed_valid_bike_ride("indoor_ok", dt=_BR_WINDOW_END - timedelta(days=2), decoupling=4.0)
        await _seed_valid_bike_ride(
            "indoor_no_decoup",
            dt=_BR_WINDOW_END - timedelta(days=3),
            decoupling=None,  # is_valid_for_decoupling → False
        )

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        cc = resp.json()["current_components"]
        # Only the ride with non-NULL decoupling lands in last_5.
        assert cc["decoupling_n"] == 1
        assert cc["decoupling_median_pct"] == 4.0

    async def test_no_valid_rides_returns_null_decoupling(self, client):
        """Empty 84-day window → decoupling_median_pct is null, n=0. The
        widget reads decoupling_n=0 OR status=null as "insufficient" — both
        signals are emitted distinctly here so we don't lose the distinction
        through accidental coercion."""
        # No activities seeded → helper returns {"data_points": 0, ...}
        # without a decoupling_trend key.
        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        cc = resp.json()["current_components"]
        assert cc["decoupling_median_pct"] is None
        assert cc["decoupling_status"] is None
        assert cc["decoupling_n"] == 0

    async def test_per_user_scoping(self, client):
        """Tenant isolation: rows for user 2 must not leak into user 1's
        response — regression for `docs/MULTI_TENANT_SECURITY_SPEC.md` T1."""
        async with get_session() as session:
            session.add(User(id=2, chat_id="other", role="athlete"))
            await session.commit()
        # Seed under user 2 only
        await _seed_bike_thresholds(user_id=2)
        await _seed_wellness_sport_info(2, _BR_WINDOW_END, ctl_bike=99.0)
        await _seed_valid_bike_ride(
            "u2_ride",
            dt=_BR_WINDOW_END - timedelta(days=1),
            decoupling=4.0,
            user_id=2,
        )

        # Call as user 1 (the test fixture's mock_user)
        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        cc = resp.json()["current_components"]
        assert cc["ctl_bike"] is None
        assert cc["longest_ride_hours"] is None
        assert cc["decoupling_n"] == 0

    async def test_ef_trend_pct_field_populated(self, client):
        """ef_trend_pct is a signed percentage, not an enum or status string.
        Spec §6 — EF trend is a *supplementary* sub-line, not a traffic-light
        signal, so the widget needs the raw % to prefix '+' / '-' on the sign.

        Seed two weekly buckets with differentiated EF (older 2.00 → newer
        2.20, +10%) so the assertion catches a degenerate regression where
        the endpoint hardcodes zero or strips direction."""
        await _seed_bike_thresholds()
        # Older bucket (ISO W16): EF=2.00. Newer bucket (W17): EF=2.20.
        # `_trend_pct` is (last - first) / |first| * 100 = (2.20-2.00)/2.00*100 = +10.0.
        await _seed_valid_bike_ride(
            "w16_old",
            dt=_BR_WINDOW_END - timedelta(days=9),
            decoupling=4.0,
            efficiency_factor=2.00,
        )
        await _seed_valid_bike_ride(
            "w17_new",
            dt=_BR_WINDOW_END - timedelta(days=2),
            decoupling=4.0,
            efficiency_factor=2.20,
        )

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        cc = resp.json()["current_components"]
        assert isinstance(cc["ef_trend_pct"], (int, float))
        # +10% improvement — exact value pinned by `_trend_pct`'s rounding.
        assert cc["ef_trend_pct"] == 10.0

    async def test_ef_trend_pct_null_when_insufficient(self, client):
        """Single weekly EF sample → `_trend_pct` returns
        `direction=insufficient_data`, and the endpoint maps that to
        `ef_trend_pct: None`. The widget hides the sub-line on null
        (Progress.tsx EF-trend conditional render). Spec §3.3."""
        await _seed_bike_thresholds()
        # Single ride → only one ISO week populated → trend insufficient.
        await _seed_valid_bike_ride("lone", dt=_BR_WINDOW_END - timedelta(days=2), decoupling=4.0)

        async with client as c:
            resp = await c.get("/api/bike-readiness?weeks=12")

        cc = resp.json()["current_components"]
        assert cc["ef_trend_pct"] is None
        # Sanity: the ride DID land in the decoupling pipeline — null here
        # is about EF trend specifically, not a blanket "no data" miss.
        assert cc["decoupling_n"] == 1
