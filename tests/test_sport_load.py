"""Per-sport CTL/ATL EMA tests — data/metrics.py."""

import math
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from data.metrics import _project_loads_one_day, calculate_sport_atl, calculate_sport_ctl, project_sport_load_forward


def _act(sport: str, dt: date, load: float):
    a = MagicMock()
    a.type = sport
    a.icu_training_load = load
    a.start_date_local = dt.isoformat()
    return a


class TestCalculateSportCtl:
    def test_empty_returns_zeros(self):
        assert calculate_sport_ctl([]) == {"swim": 0.0, "ride": 0.0, "run": 0.0}

    def test_unknown_sport_ignored(self):
        result = calculate_sport_ctl([_act("Yoga", date(2026, 1, 1), 50.0)])
        assert result == {"swim": 0.0, "ride": 0.0, "run": 0.0}

    def test_none_load_skipped(self):
        result = calculate_sport_ctl([_act("Run", date(2026, 1, 1), None)])
        assert result == {"swim": 0.0, "ride": 0.0, "run": 0.0}

    def test_constant_daily_load_approaches_load(self):
        """EMA with constant input X converges to X. 5τ ≈ 99.3% of steady state."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 50.0) for i in range(210)]
        result = calculate_sport_ctl(acts)
        # 5τ → steady-state error <1%. CTL should be very close to daily load.
        assert 49.0 <= result["run"] <= 50.0
        assert result["ride"] == 0.0
        assert result["swim"] == 0.0

    def test_lowercase_sport_normalized(self):
        """Activity.type stored as canonical Title case; matcher lowers it."""
        acts = [_act("Run", date(2026, 1, 1), 50.0)]
        result = calculate_sport_ctl(acts)
        assert result["run"] > 0


class TestCalculateSportAtl:
    def test_empty_returns_zeros(self):
        assert calculate_sport_atl([]) == {"swim": 0.0, "ride": 0.0, "run": 0.0}

    def test_atl_reacts_faster_than_ctl(self):
        """τ_ATL=7 vs τ_CTL=42: after a short load burst ATL should be ≫ CTL."""
        # 7 consecutive days of TSS=100, evaluated on day 7.
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 100.0) for i in range(7)]

        ctl = calculate_sport_ctl(acts)["run"]
        atl = calculate_sport_atl(acts)["run"]

        # After 7 days at constant 100 TSS: ATL ≈ 1τ → ~63 of steady-state (100).
        # CTL ≈ 7/42 τ → far from steady-state.
        assert atl > ctl * 3, f"expected atl ≫ ctl (atl={atl}, ctl={ctl})"

    def test_constant_load_approaches_value(self):
        """5τ_ATL = 35 days. After 35 days at constant load, ATL ≈ steady-state."""
        start = date(2026, 1, 1)
        acts = [_act("Ride", start + timedelta(days=i), 80.0) for i in range(35)]
        atl = calculate_sport_atl(acts)["ride"]
        assert 78.0 <= atl <= 80.0

    def test_per_sport_independent(self):
        """ATL for one sport doesn't bleed into another's value."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 60.0) for i in range(30)]
        result = calculate_sport_atl(acts)
        assert result["run"] > 0
        assert result["ride"] == 0.0
        assert result["swim"] == 0.0


class TestAsOfDecay:
    """Regression: without `as_of`, EMA stops at last activity date — rest gaps
    leave the value frozen instead of decaying. See code-review M1 (2026-05-24)."""

    def test_rest_gap_after_training_decays_ctl(self):
        """100 days at TSS=50, then 30 days rest → CTL should halve under τ=42."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 50.0) for i in range(100)]
        last_train_day = start + timedelta(days=99)

        ctl_at_last_train = calculate_sport_ctl(acts, as_of=last_train_day)["run"]
        ctl_30d_later = calculate_sport_ctl(acts, as_of=last_train_day + timedelta(days=30))["run"]

        # e^(-30/42) ≈ 0.490 → CTL should drop ~51%
        expected = ctl_at_last_train * math.exp(-30 / 42)
        assert ctl_30d_later == pytest.approx(expected, abs=0.5)

    def test_as_of_before_first_activity_clamps_to_zero(self):
        """Defensive: as_of before any activity → loop should still run min..min."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start, 50.0)]
        # as_of way before first activity — clamps to min_date (one EMA step).
        result = calculate_sport_ctl(acts, as_of=date(2025, 1, 1))["run"]
        assert result > 0

    def test_as_of_none_preserves_legacy_behavior(self):
        """No as_of → iterate to last activity date (legacy)."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 50.0) for i in range(50)]
        with_explicit = calculate_sport_ctl(acts, as_of=start + timedelta(days=49))["run"]
        legacy = calculate_sport_ctl(acts)["run"]
        assert with_explicit == legacy

    def test_atl_decays_faster_than_ctl_through_gap(self):
        """ATL (τ=7) collapses much faster than CTL (τ=42) over a rest gap."""
        start = date(2026, 1, 1)
        acts = [_act("Run", start + timedelta(days=i), 80.0) for i in range(60)]
        target = start + timedelta(days=89)  # 30-day rest after day 59

        ctl = calculate_sport_ctl(acts, as_of=target)["run"]
        atl = calculate_sport_atl(acts, as_of=target)["run"]
        assert atl < ctl, f"ATL ({atl}) should decay below CTL ({ctl}) after long rest"
        assert atl < 5.0, f"ATL after 30d rest should be near zero, got {atl}"


class TestSharedCore:
    """Both wrappers share `_calculate_sport_load_ema` — these tests verify the
    contract holds identically for both."""

    @pytest.mark.parametrize("fn", [calculate_sport_ctl, calculate_sport_atl])
    def test_multi_sport_independent_accumulation(self, fn):
        start = date(2026, 1, 1)
        acts = (
            [_act("Run", start + timedelta(days=i), 40.0) for i in range(60)]
            + [_act("Ride", start + timedelta(days=i), 80.0) for i in range(60)]
            + [_act("Swim", start + timedelta(days=i), 20.0) for i in range(60)]
        )
        result = fn(acts)
        # Ride should be highest, Swim lowest — order preserved.
        assert result["ride"] > result["run"] > result["swim"] > 0

    @pytest.mark.parametrize("fn", [calculate_sport_ctl, calculate_sport_atl])
    def test_multiple_activities_same_day_summed(self, fn):
        """Two activities on the same day → loads summed before EMA step."""
        dt = date(2026, 1, 1)
        single = fn([_act("Run", dt, 100.0)])
        double = fn([_act("Run", dt, 50.0), _act("Run", dt, 50.0)])
        assert single["run"] == double["run"]


class TestProjectSportLoadForward:
    def test_zero_planned_load_pure_decay(self):
        """No future workouts → CTL/ATL decay from today's value at τ=42/7."""
        today = date(2026, 1, 1)
        horizon = today + timedelta(days=30)
        ctl_series, atl_series = project_sport_load_forward(
            today_ctl=50.0,
            today_atl=50.0,
            daily_planned_load={},
            horizon=horizon,
            today=today,
        )
        assert len(ctl_series) == 30
        assert len(atl_series) == 30
        # After 30 days of zero load:
        # CTL ≈ 50 * e^(-30/42) ≈ 24.5
        # ATL ≈ 50 * e^(-30/7) ≈ 0.7
        last_ctl = ctl_series[-1][1]
        last_atl = atl_series[-1][1]
        assert last_ctl == pytest.approx(50 * math.exp(-30 / 42), abs=0.5)
        assert last_atl == pytest.approx(50 * math.exp(-30 / 7), abs=0.5)

    def test_constant_load_approaches_steady_state(self):
        """Constant daily load X → both CTL and ATL converge to X."""
        today = date(2026, 1, 1)
        horizon = today + timedelta(days=200)
        planned = {today + timedelta(days=i): 60.0 for i in range(1, 201)}
        ctl_series, atl_series = project_sport_load_forward(
            today_ctl=0.0,
            today_atl=0.0,
            daily_planned_load=planned,
            horizon=horizon,
            today=today,
        )
        # ~5τ_CTL warm-up → very close to 60
        assert 58.0 <= ctl_series[-1][1] <= 60.0
        # ~28τ_ATL warm-up → essentially 60
        assert 59.9 <= atl_series[-1][1] <= 60.0

    def test_horizon_at_today_returns_empty(self):
        today = date(2026, 1, 1)
        ctl_series, atl_series = project_sport_load_forward(
            today_ctl=30.0,
            today_atl=30.0,
            daily_planned_load={},
            horizon=today,
            today=today,
        )
        assert ctl_series == []
        assert atl_series == []

    def test_horizon_before_today_returns_empty(self):
        today = date(2026, 1, 1)
        ctl_series, atl_series = project_sport_load_forward(
            today_ctl=30.0,
            today_atl=30.0,
            daily_planned_load={},
            horizon=today - timedelta(days=5),
            today=today,
        )
        assert ctl_series == []
        assert atl_series == []

    def test_atl_decays_faster_than_ctl_under_zero_load(self):
        today = date(2026, 1, 1)
        horizon = today + timedelta(days=14)
        ctl_series, atl_series = project_sport_load_forward(
            today_ctl=30.0,
            today_atl=30.0,
            daily_planned_load={},
            horizon=horizon,
            today=today,
        )
        last_ctl = ctl_series[-1][1]
        last_atl = atl_series[-1][1]
        # 2τ_ATL gone → ATL ~13.5% of start. CTL barely moved (1/3 τ_CTL).
        assert last_atl < last_ctl / 4

    def test_starts_at_today_plus_one(self):
        """First series point is `today + 1`, not `today` itself."""
        today = date(2026, 1, 1)
        ctl_series, _ = project_sport_load_forward(
            today_ctl=30.0,
            today_atl=30.0,
            daily_planned_load={},
            horizon=today + timedelta(days=3),
            today=today,
        )
        assert ctl_series[0][0] == today + timedelta(days=1)
        assert ctl_series[-1][0] == today + timedelta(days=3)

    def test_workout_burst_in_middle_of_plan(self):
        """A single 200-TSS day in middle of plan should spike ATL then decay."""
        today = date(2026, 1, 1)
        horizon = today + timedelta(days=20)
        burst_day = today + timedelta(days=10)
        planned = {burst_day: 200.0}
        _, atl_series = project_sport_load_forward(
            today_ctl=0.0,
            today_atl=0.0,
            daily_planned_load=planned,
            horizon=horizon,
            today=today,
        )
        atl_on_burst = next(v for d, v in atl_series if d == burst_day)
        atl_at_horizon = atl_series[-1][1]
        assert atl_on_burst > 20.0
        assert atl_at_horizon < atl_on_burst / 3  # ~10 days decay


class TestProjectLoadsOneDay:
    """One-day forward projection used by `recompute_today_loads` — the
    morning-report fix that strips Intervals.icu's planned-workout bake-in
    from today's CTL/ATL."""

    def test_zero_tss_pure_decay(self):
        ctl, atl, tsb = _project_loads_one_day(prev_ctl=50.0, prev_atl=60.0, tss_today=0.0)
        # CTL ≈ 50 * e^(-1/42) ≈ 48.8 ; ATL ≈ 60 * e^(-1/7) ≈ 52.0
        assert ctl == pytest.approx(50.0 * math.exp(-1.0 / 42), abs=0.05)
        assert atl == pytest.approx(60.0 * math.exp(-1.0 / 7), abs=0.05)
        assert tsb == round(ctl - atl, 1)
        # ATL drops faster than CTL → TSB more positive than prev (50-60=-10).
        assert tsb > -10

    def test_completed_tss_bumps_atl_more_than_ctl(self):
        """ATL is more responsive (τ=7 vs τ=42) — a hard session should
        push ATL up sharply while CTL nudges only slightly."""
        no_load = _project_loads_one_day(50.0, 50.0, tss_today=0.0)
        with_load = _project_loads_one_day(50.0, 50.0, tss_today=100.0)
        # CTL gain ≈ 100 * (1 - e^(-1/42)) ≈ 2.35  → rounded delta ~2.4
        # ATL gain ≈ 100 * (1 - e^(-1/7))  ≈ 13.35 → rounded delta ~13.3
        assert with_load[0] - no_load[0] == pytest.approx(2.35, abs=0.15)
        assert with_load[1] - no_load[1] == pytest.approx(13.35, abs=0.15)
        # ATL must jump 5x harder than CTL — that's the whole point of τ split.
        assert (with_load[1] - no_load[1]) > 5 * (with_load[0] - no_load[0])
        # TSB drops because ATL jumps more than CTL.
        assert with_load[2] < no_load[2]

    def test_equilibrium_tss_holds_loads_constant(self):
        """If today's TSS equals yesterday's CTL exactly, CTL stays flat —
        you're training right at chronic load."""
        ctl, _, _ = _project_loads_one_day(prev_ctl=50.0, prev_atl=50.0, tss_today=50.0)
        assert ctl == pytest.approx(50.0, abs=0.05)

    def test_rounded_to_one_decimal(self):
        ctl, atl, tsb = _project_loads_one_day(50.123, 60.987, 42.5)
        assert ctl == round(ctl, 1)
        assert atl == round(atl, 1)
        assert tsb == round(tsb, 1)


# ---------------------------------------------------------------------------
# DB-integration: recompute_today_loads
# ---------------------------------------------------------------------------


_TODAY = date(2026, 4, 30)
_YESTERDAY = _TODAY - timedelta(days=1)


async def _seed_yesterday_wellness(user_id: int, *, ctl: float | None, atl: float | None) -> None:
    """Plant yesterday's wellness row directly via SQLAlchemy — bypassing
    `Wellness.save`'s `_apply_intervals_fields` which expects a full WellnessDTO.
    We only care about ctl/atl as the baseline."""
    from datetime import datetime, timezone

    from data.db import Wellness, get_session

    async with get_session() as s:
        s.add(
            Wellness(
                user_id=user_id,
                date=_YESTERDAY.isoformat(),
                ctl=ctl,
                atl=atl,
                updated=datetime.now(timezone.utc),
            )
        )
        await s.commit()


async def _seed_today_activity(user_id: int, *, activity_id: str, tss: float) -> None:
    from data.db import Activity
    from data.intervals.dto import ActivityDTO

    dto = ActivityDTO(
        id=activity_id,
        start_date_local=_TODAY,
        type="Ride",
        icu_training_load=tss,
        moving_time=3600,
    )
    await Activity.save_bulk(user_id, [dto])


class TestRecomputeTodayLoads:
    """DB-integration: `recompute_today_loads` reads yesterday's wellness +
    today's activities and rolls forward by one day. Multi-tenant scoping
    and edge-case handling matter for the morning-report fix that consumes it."""

    async def test_returns_none_when_no_yesterday_wellness(self, monkeypatch):
        from data import metrics

        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)
        assert await metrics.recompute_today_loads(1) is None

    async def test_returns_none_when_yesterday_has_no_ctl_atl(self, monkeypatch):
        """Sleep-only row (CTL/ATL not yet computed by Intervals) is the same
        as 'no baseline'. Caller falls back to whatever Intervals reported."""
        from data import metrics

        await _seed_yesterday_wellness(1, ctl=None, atl=None)
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)
        assert await metrics.recompute_today_loads(1) is None

    async def test_pure_decay_when_no_activities(self, monkeypatch):
        """Yesterday baked, no work today → pure exponential decay."""
        from data import metrics

        await _seed_yesterday_wellness(1, ctl=50.0, atl=60.0)
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)
        result = await metrics.recompute_today_loads(1)

        assert result is not None
        ctl, atl, tsb = result
        # CTL ≈ 50 * e^(-1/42), ATL ≈ 60 * e^(-1/7).
        assert ctl == pytest.approx(50.0 * math.exp(-1.0 / 42), abs=0.05)
        assert atl == pytest.approx(60.0 * math.exp(-1.0 / 7), abs=0.05)
        assert tsb == round(ctl - atl, 1)

    async def test_single_activity_today_contributes_to_loads(self, monkeypatch):
        from data import metrics

        await _seed_yesterday_wellness(1, ctl=50.0, atl=50.0)
        await _seed_today_activity(1, activity_id="a1", tss=100.0)
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)

        result = await metrics.recompute_today_loads(1)
        assert result is not None
        expected = metrics._project_loads_one_day(50.0, 50.0, 100.0)
        assert result == expected

    async def test_multiple_activities_today_sum_tss(self, monkeypatch):
        """Two completed sessions on the same day → TSS adds. The fix exists
        precisely because Intervals bakes in *planned* TSS — our recompute
        must include all *completed* sessions, not just one."""
        from data import metrics

        await _seed_yesterday_wellness(1, ctl=50.0, atl=50.0)
        await _seed_today_activity(1, activity_id="m1", tss=40.0)
        await _seed_today_activity(1, activity_id="m2", tss=60.0)
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)

        result = await metrics.recompute_today_loads(1)
        assert result == metrics._project_loads_one_day(50.0, 50.0, 100.0)

    async def test_activity_with_null_tss_treated_as_zero(self, monkeypatch):
        """Some imported activities (e.g. WeightTraining without HR) carry
        ``icu_training_load=None``. Mustn't crash — treat as 0 TSS."""
        from data import metrics

        await _seed_yesterday_wellness(1, ctl=50.0, atl=50.0)
        await _seed_today_activity(1, activity_id="zero", tss=None)  # type: ignore[arg-type]
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)

        result = await metrics.recompute_today_loads(1)
        assert result == metrics._project_loads_one_day(50.0, 50.0, 0.0)


def _seed_yesterday_wellness_sync(user_id: int, *, ctl: float | None, atl: float | None) -> None:
    """Sync twin of `_seed_yesterday_wellness` — must run with no event loop so
    `@dual` ORM helpers downstream dispatch to their sync paths (mirrors the
    Dramatiq worker that consumes `recompute_today_loads_sync`)."""
    from datetime import datetime, timezone

    from data.db import Wellness, get_sync_session

    with get_sync_session() as s:
        s.add(
            Wellness(
                user_id=user_id,
                date=_YESTERDAY.isoformat(),
                ctl=ctl,
                atl=atl,
                updated=datetime.now(timezone.utc),
            )
        )
        s.commit()


def _seed_today_activity_sync(user_id: int, *, activity_id: str, tss: float) -> None:
    from data.db import Activity
    from data.intervals.dto import ActivityDTO

    dto = ActivityDTO(
        id=activity_id,
        start_date_local=_TODAY,
        type="Ride",
        icu_training_load=tss,
        moving_time=3600,
    )
    Activity.save_bulk(user_id, [dto])


class TestRecomputeTodayLoadsSync:
    """Sync twin of `recompute_today_loads`, consumed by the Strava-signature
    actor (`actor_rename_activity`) to de-plan today's ctl/atl. These are plain
    sync tests on purpose: `@dual` dispatches on event-loop presence, so the sync
    twin only resolves to its sync DB path when no loop is running — exactly the
    Dramatiq-worker condition. Run inside an async test it would yield coroutines."""

    def test_returns_none_when_no_yesterday_wellness(self, monkeypatch):
        from data import metrics

        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)
        assert metrics.recompute_today_loads_sync(1) is None

    def test_returns_none_when_yesterday_has_no_ctl_atl(self, monkeypatch):
        """Sleep-only row (CTL/ATL not yet computed by Intervals) is the same
        as 'no baseline' — caller falls back to whatever Intervals reported."""
        from data import metrics

        _seed_yesterday_wellness_sync(1, ctl=None, atl=None)
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)
        assert metrics.recompute_today_loads_sync(1) is None

    def test_activity_with_null_tss_treated_as_zero(self, monkeypatch):
        """Imported activities (e.g. WeightTraining without HR) carry
        ``icu_training_load=None`` — must treat as 0 TSS, not crash."""
        from data import metrics

        _seed_yesterday_wellness_sync(1, ctl=50.0, atl=50.0)
        _seed_today_activity_sync(1, activity_id="snull", tss=None)  # type: ignore[arg-type]
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)

        result = metrics.recompute_today_loads_sync(1)
        assert result == metrics._project_loads_one_day(50.0, 50.0, 0.0)

    def test_pure_decay_when_no_activities(self, monkeypatch):
        from data import metrics

        _seed_yesterday_wellness_sync(1, ctl=50.0, atl=60.0)
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)
        result = metrics.recompute_today_loads_sync(1)

        assert result is not None
        ctl, atl, tsb = result
        assert ctl == pytest.approx(50.0 * math.exp(-1.0 / 42), abs=0.05)
        assert atl == pytest.approx(60.0 * math.exp(-1.0 / 7), abs=0.05)
        assert tsb == round(ctl - atl, 1)

    def test_completed_activities_today_sum_tss(self, monkeypatch):
        """Matches the async twin: all completed sessions count, planned TSS
        baked by Intervals does not — that is the whole point of the de-plan."""
        from data import metrics

        _seed_yesterday_wellness_sync(1, ctl=50.0, atl=50.0)
        _seed_today_activity_sync(1, activity_id="s1", tss=40.0)
        _seed_today_activity_sync(1, activity_id="s2", tss=60.0)
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)

        result = metrics.recompute_today_loads_sync(1)
        assert result == metrics._project_loads_one_day(50.0, 50.0, 100.0)

    def test_explicit_today_pins_reference_day(self, monkeypatch):
        """The `today` arg wins over `local_today()` — the actor snapshots the
        day once and passes it so a midnight rollover can't shift the baseline."""
        from data import metrics

        _seed_yesterday_wellness_sync(1, ctl=50.0, atl=60.0)
        # local_today() deliberately returns the *wrong* day; if the param were
        # ignored, the yesterday lookup would miss and return None.
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY + timedelta(days=1))
        result = metrics.recompute_today_loads_sync(1, _TODAY)

        assert result is not None
        ctl, atl, _ = result
        assert ctl == pytest.approx(50.0 * math.exp(-1.0 / 42), abs=0.05)
        assert atl == pytest.approx(60.0 * math.exp(-1.0 / 7), abs=0.05)


# ---------------------------------------------------------------------------
# DB-integration: recompute_today_ramp
# ---------------------------------------------------------------------------


_WEEK_AGO = _TODAY - timedelta(days=7)


async def _seed_wellness_at(user_id: int, dt: date, *, ctl: float | None) -> None:
    """Plant a wellness row at an arbitrary date with just CTL — the 7-day-ago
    baseline `recompute_today_ramp` reads."""
    from datetime import datetime, timezone

    from data.db import Wellness, get_session

    async with get_session() as s:
        s.add(Wellness(user_id=user_id, date=dt.isoformat(), ctl=ctl, updated=datetime.now(timezone.utc)))
        await s.commit()


class TestRecomputeTodayRamp:
    """DB-integration: `recompute_today_ramp` returns projected weekly ramp as
    de-planned-today-CTL minus the (settled) CTL from 7 days ago, matching how
    Intervals.icu defines rampRate. Keeps ramp consistent with the de-planned
    CTL/ATL/TSB shown beside it on the today screen."""

    async def test_returns_none_when_no_week_ago_row(self, monkeypatch):
        from data import metrics

        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)
        assert await metrics.recompute_today_ramp(1, ctl_today=55.0) is None

    async def test_returns_none_when_week_ago_has_no_ctl(self, monkeypatch):
        from data import metrics

        await _seed_wellness_at(1, _WEEK_AGO, ctl=None)
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)
        assert await metrics.recompute_today_ramp(1, ctl_today=55.0) is None

    async def test_positive_ramp(self, monkeypatch):
        """Fitness climbing across the week → positive ramp."""
        from data import metrics

        await _seed_wellness_at(1, _WEEK_AGO, ctl=50.0)
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)
        assert await metrics.recompute_today_ramp(1, ctl_today=55.5) == pytest.approx(5.5)

    async def test_negative_ramp_on_detraining(self, monkeypatch):
        """Detraining week → negative ramp, must not clamp to zero."""
        from data import metrics

        await _seed_wellness_at(1, _WEEK_AGO, ctl=60.0)
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)
        assert await metrics.recompute_today_ramp(1, ctl_today=56.2) == pytest.approx(-3.8)

    async def test_week_ago_scoped_per_user(self, monkeypatch):
        """Another user's week-ago row must not leak into this user's ramp."""
        from data import metrics
        from data.db import User, get_session

        async with get_session() as s:
            s.add(User(id=2, chat_id="other_user", role="user"))
            await s.commit()
        await _seed_wellness_at(2, _WEEK_AGO, ctl=10.0)
        monkeypatch.setattr(metrics, "local_today", lambda: _TODAY)
        assert await metrics.recompute_today_ramp(1, ctl_today=55.0) is None
