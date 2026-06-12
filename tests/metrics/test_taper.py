from datetime import date, timedelta

import pytest

from data.metrics import _TAPER_CLASS_PARAMS, _project_loads_one_day, build_taper_plan, project_sport_load_forward

TODAY = date(2026, 6, 12)


def _plan(days_to_race: int = 14, race_class: str = "standard", **kw) -> dict:
    defaults = dict(
        race_date=TODAY + timedelta(days=days_to_race),
        today=TODAY,
        ctl_now=60.0,
        atl_now=65.0,
        peak_daily_load=85.0,
        race_distance_class=race_class,
    )
    defaults.update(kw)
    return build_taper_plan(**defaults)


class TestBuildTaperPlan:
    def test_targets_monotonic_decay(self):
        plan = _plan()
        targets = [d["target_tss"] for d in plan["daily_targets"]]
        assert targets == sorted(targets, reverse=True)
        assert targets[-1] == 0  # race day

    def test_targets_span_window(self):
        plan = _plan()
        days = plan["daily_targets"]
        assert days[0]["date"] == plan["taper_start_date"]
        assert days[-1]["date"] == TODAY + timedelta(days=14)
        assert days[-1]["note"] == "race day"
        assert len(days) == plan["taper_days"]
        # Consecutive dates, no gaps
        for prev, cur in zip(days, days[1:]):
            assert cur["date"] - prev["date"] == timedelta(days=1)

    def test_ewma_matches_forward_projection(self):
        """Race-day projection must be reproducible by re-running the project's
        own EMA forward-sim over the returned daily targets (rounded TSS →
        small tolerance)."""
        plan = _plan()
        loads = {d["date"]: float(d["target_tss"]) for d in plan["daily_targets"] if d["date"] > TODAY}
        race_date = TODAY + timedelta(days=14)
        # Pre-taper days hold steady at ctl_now — the simulation's documented
        # "keep training as before" assumption.
        cur = TODAY + timedelta(days=1)
        while cur < plan["taper_start_date"]:
            loads[cur] = 60.0
            cur += timedelta(days=1)
        ctl_series, atl_series = project_sport_load_forward(60.0, 65.0, loads, race_date, TODAY)
        assert abs(ctl_series[-1][1] - plan["projected_race_day"]["ctl"]) <= 0.5
        assert abs(atl_series[-1][1] - plan["projected_race_day"]["atl"]) <= 0.5

    def test_class_corridor_orders_taper_length(self):
        # Longer race class → longer taper, but via the per-class length
        # corridors (long min 14 vs short min 7), NOT via the optimizer — the
        # (length, τ) choice is a class property, blind to the athlete (see
        # test_choice_is_class_property_not_athlete_property).
        long_plan = _plan(days_to_race=25, race_class="long", ctl_now=70.0)
        short_plan = _plan(days_to_race=25, race_class="short")
        assert long_plan["taper_days"] >= short_plan["taper_days"]
        assert long_plan["taper_days"] >= 14

    def test_choice_is_class_property_not_athlete_property(self):
        """Intentional property: reduction is a near-injective function of
        (length, τ), so the corridor filter collapses the pool to ~one
        candidate and the chosen (taper_days, τ) does not depend on CTL/ATL.
        The athlete's numbers drive the projection, not the choice. If this
        ever fails, the selection tiers changed — update the docstring and
        spec §4 wording, not just this test."""
        baseline = _plan()
        for ctl, atl in ((50.0, 55.0), (75.0, 80.0), (90.0, 70.0)):
            plan = _plan(ctl_now=ctl, atl_now=atl)
            assert plan["taper_days"] == baseline["taper_days"]
            assert plan["tau_taper"] == baseline["tau_taper"]
            assert plan["volume_reduction_pct"] == baseline["volume_reduction_pct"]

    def test_reduction_near_class_corridor(self):
        # Standard corridor 41-60% has reachable grid candidates — must land inside.
        plan = _plan()
        lo, hi = _TAPER_CLASS_PARAMS["standard"]["reduction"]
        assert lo <= plan["volume_reduction_pct"] <= hi

    def test_tsb_lands_in_target_zone(self):
        plan = _plan()
        assert plan["projected_race_day"]["tsb_zone"] in ("fresh", "transition")
        assert "tsb_lands_outside_target" not in plan["warnings"]

    def test_p_banister_consistent(self):
        race = _plan()["projected_race_day"]
        assert race["p_banister"] == pytest.approx(race["ctl"] - 2 * race["atl"], abs=0.05)
        assert race["tsb"] == pytest.approx(race["ctl"] - race["atl"], abs=0.05)

    def test_intensity_hold_rule_present(self):
        rules = " ".join(_plan()["rules"])
        assert "интенсивность" in rules.lower()
        assert "частоту" in rules.lower()

    def test_low_ctl_warning_and_shallow_taper(self):
        # Realistic low-fitness athlete: peak daily load near CTL (a peak ≫ CTL
        # would mean the "taper" adds load and nothing lands in fresh/transition)
        plan = _plan(ctl_now=30.0, atl_now=32.0, peak_daily_load=40.0)
        assert "low_ctl" in plan["warnings"]
        # Grid clamps to the shortest standard-class length
        assert plan["taper_days"] == _TAPER_CLASS_PARAMS["standard"]["days"][0]
        # Corridor clamp still applies on the τ-only grid
        lo, hi = _TAPER_CLASS_PARAMS["standard"]["reduction"]
        assert lo <= plan["volume_reduction_pct"] <= hi

    def test_race_in_2_days_grid_boundary(self):
        """dtr=2 is the degenerate/grid split boundary AND forces
        taper_start == today — the pre-roll branch where today's opener must
        roll the morning state forward without double-counting in the sim."""
        plan = _plan(days_to_race=2)
        assert "degenerate_window" not in plan["warnings"]
        assert plan["taper_start_date"] == TODAY
        assert plan["taper_days"] == 3  # today, tomorrow, race day
        assert len(plan["daily_targets"]) == 3
        # Independent re-derivation: roll today's opener, then sim the rest
        ctl, atl, _ = _project_loads_one_day(60.0, 65.0, float(plan["daily_targets"][0]["target_tss"]))
        race_date = TODAY + timedelta(days=2)
        loads = {d["date"]: float(d["target_tss"]) for d in plan["daily_targets"] if d["date"] > TODAY}
        ctl_series, atl_series = project_sport_load_forward(ctl, atl, loads, race_date, TODAY)
        assert abs(ctl_series[-1][1] - plan["projected_race_day"]["ctl"]) <= 0.5
        assert abs(atl_series[-1][1] - plan["projected_race_day"]["atl"]) <= 0.5

    def test_first_day_already_reduced(self):
        # Day 0 of the taper must NOT prescribe a full peak-load session
        plan = _plan()
        assert plan["daily_targets"][0]["target_tss"] < 85
        assert plan["daily_targets"][0]["pct_of_peak"] < 100

    def test_race_in_3_days_is_late(self):
        plan = _plan(days_to_race=3)
        assert plan["confidence"] == "late"
        assert "short_window" in plan["warnings"]
        assert plan["taper_start_date"] == TODAY
        assert plan["taper_days"] == 4  # today..race inclusive

    def test_race_tomorrow_degenerate(self):
        plan = _plan(days_to_race=1)
        assert "degenerate_window" in plan["warnings"]
        assert plan["confidence"] == "late"
        assert len(plan["daily_targets"]) == 2
        opener = plan["daily_targets"][0]["target_tss"]
        assert 0 < opener < 85 * 0.25  # small opener, nowhere near peak
        assert plan["daily_targets"][1]["target_tss"] == 0

    def test_early_mode_withholds_daily_targets(self):
        plan = _plan(days_to_race=45)
        assert plan["confidence"] == "early"
        assert "early_estimate" in plan["warnings"]
        assert plan["daily_targets"] == []
        # The race-day projection is withheld too — it would be simulated from
        # today's CTL/ATL held flat for 20+ days, exactly the false precision
        # the early gate exists to suppress (contract for the Phase 2 caller).
        assert plan["projected_race_day"] is None
        # Start date estimate still present and inside the horizon
        expected_start = TODAY + timedelta(days=45) - timedelta(days=plan["taper_days"] - 1)
        assert plan["taper_start_date"] == expected_start

    def test_early_mode_suppresses_sim_derived_warnings(self):
        # peak ≫ CTL keeps TSB from landing in fresh/transition — in non-early
        # mode that surfaces as a warning, but in early mode it is a verdict of
        # the withheld simulation and must not leak. low_ctl (from today's
        # actual CTL) stays.
        kw = dict(ctl_now=30.0, atl_now=80.0, peak_daily_load=85.0)
        assert "tsb_lands_outside_target" in _plan(days_to_race=14, **kw)["warnings"]
        early = _plan(days_to_race=45, **kw)
        assert "tsb_lands_outside_target" not in early["warnings"]
        assert "low_ctl" in early["warnings"]

    def test_non_early_modes_keep_projection(self):
        for days in (2, 14, 21):
            assert _plan(days_to_race=days)["projected_race_day"] is not None
        assert _plan(days_to_race=1)["projected_race_day"] is not None  # degenerate path too

    def test_invalid_inputs(self):
        with pytest.raises(ValueError):
            _plan(days_to_race=0)
        with pytest.raises(ValueError):
            _plan(days_to_race=-5)
        with pytest.raises(ValueError):
            _plan(race_class="ultra")
        with pytest.raises(ValueError):
            _plan(peak_daily_load=0)

    def test_deterministic(self):
        assert _plan() == _plan()
