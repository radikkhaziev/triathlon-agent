"""Tests for `data/endurance_score.py` — pure formulas, no DB."""

from datetime import date, timedelta

import pytest

from data.endurance_score import (
    DEFAULT_VO2MAX,
    DETRAIN_FLOOR,
    ENDURANCE_MAX,
    AthleteProfile,
    EnduranceActivity,
    WellnessSnapshot,
    classify_zone,
    compute_badge,
    compute_endurance_score,
    consistency_bonus,
    detrain_factor,
    duration_bonus,
    long_term_bonus,
    per_sport_breakdown,
    recent_bonus,
    recovery_bonus,
    vo2max_bike_storer,
    vo2max_composite,
    vo2max_run_daniels,
)

REF_DATE = date(2026, 5, 25)


# ─── VO2max formulas ──────────────────────────────────────────────────────


class TestVO2maxBikeStorer:
    """Storer 1990 — bike VO2max from FTP/weight/age."""

    def test_radik_today(self):
        # FTP 207.8, weight 78.5, age 43 → ~35 ml/kg/min
        result = vo2max_bike_storer(207.8, 78.5, 43)
        assert round(result, 1) == 35.0

    def test_strong_amateur(self):
        # FTP 280, weight 70, age 35 → ~50 (strong AG cyclist)
        result = vo2max_bike_storer(280, 70, 35)
        assert 48 < result < 53


class TestVO2maxRunDaniels:
    """Daniels VDOT — run VO2max from threshold pace."""

    def test_radik_threshold(self):
        # 4:47/km = 287 sec/km → ~52 (matches Garmin's recorded VO2max=53 from Nov 2025)
        result = vo2max_run_daniels(287)
        assert round(result, 1) == 52.1

    def test_elite_threshold(self):
        # 3:30/km = 210 sec/km → ~70 (sub-elite marathoner)
        result = vo2max_run_daniels(210)
        assert 67 < result < 72


# ─── Components ───────────────────────────────────────────────────────────


class TestLongTermBonus:
    def test_zero_ctl(self):
        assert long_term_bonus(0) == 0

    def test_at_cap(self):
        assert long_term_bonus(80) == 1000

    def test_above_cap(self):
        assert long_term_bonus(100) == 1000  # clamped

    def test_radik_today_ctl_41(self):
        # 41/80 * 1000 = 512.5
        assert round(long_term_bonus(41.6)) == 520


class TestRecentBonus:
    def test_none_ramp(self):
        assert recent_bonus(None) == 0

    def test_negative_ramp_clamps_to_zero(self):
        # Detrain phase (Feb 2026): ramp = -3.56 → 0 (not negative)
        assert recent_bonus(-3.56) == 0

    def test_at_cap(self):
        # ramp +8 TSS/wk = full bonus
        assert recent_bonus(8.0) == 200

    def test_above_cap(self):
        assert recent_bonus(15.0) == 200

    def test_radik_today_ramp_62(self):
        # 6.2/8 * 200 = 155
        assert round(recent_bonus(6.2)) == 155


class TestDurationBonus:
    def test_no_activities(self):
        assert duration_bonus([]) == 0

    def test_only_short_sessions(self):
        # Run 45min × 10 sessions — none meet 90min threshold
        sessions = [
            EnduranceActivity(
                dt=REF_DATE - timedelta(days=i),
                type="Run",
                moving_time_sec=45 * 60,
                training_load=50.0,
                z2plus_time_pct=0.8,
                dfa_a1_mean=None,
            )
            for i in range(10)
        ]
        assert duration_bonus(sessions) == 0

    def test_long_ride_quality(self):
        # One 3-hour ride (180min, ≥120min threshold), Z2+ share 0.85
        # plus filler short sessions to dilute share
        long = EnduranceActivity(
            dt=REF_DATE - timedelta(days=2),
            type="Ride",
            moving_time_sec=180 * 60,
            training_load=200.0,
            z2plus_time_pct=0.85,
            dfa_a1_mean=1.0,
        )
        filler = [
            EnduranceActivity(
                dt=REF_DATE - timedelta(days=i),
                type="Run",
                moving_time_sec=30 * 60,
                training_load=40.0,
                z2plus_time_pct=0.7,
                dfa_a1_mean=None,
            )
            for i in range(10)
        ]
        # share = 200 / (200+400) = 0.333 → 0.333 × 800 = 267
        result = duration_bonus([long, *filler])
        assert 260 < result < 275

    def test_quality_filter_drops_trash_ride(self):
        # 4-hour ride with only 50% Z2+ — filtered out
        trash = EnduranceActivity(
            dt=REF_DATE - timedelta(days=1),
            type="Ride",
            moving_time_sec=240 * 60,
            training_load=300.0,
            z2plus_time_pct=0.50,  # below 0.70 filter
            dfa_a1_mean=None,
        )
        filler = EnduranceActivity(
            dt=REF_DATE - timedelta(days=2),
            type="Ride",
            moving_time_sec=60 * 60,
            training_load=60.0,
            z2plus_time_pct=0.8,
            dfa_a1_mean=None,
        )
        # trash filtered → share = 0/360 = 0
        assert duration_bonus([trash, filler]) == 0

    def test_z2plus_unknown_counts_as_valid(self):
        # If z2plus_time_pct is None (no activity_details), session still counts
        long = EnduranceActivity(
            dt=REF_DATE - timedelta(days=1),
            type="Ride",
            moving_time_sec=180 * 60,
            training_load=200.0,
            z2plus_time_pct=None,
            dfa_a1_mean=None,
        )
        # share = 200/200 = 1.0 → cap at 0.5 → 400
        assert duration_bonus([long]) == 400


class TestConsistencyBonus:
    def test_empty(self):
        assert consistency_bonus([]) == 0

    def test_few_weeks(self):
        # Only 3 non-empty weeks — below minimum 4
        assert consistency_bonus([0, 100, 200, 0, 300, 0, 0, 0]) == 0

    def test_perfectly_consistent(self):
        # All weeks identical → CV = 0 → bonus = 200
        assert consistency_bonus([400, 400, 400, 400, 400, 400, 400, 400]) == 200

    def test_high_variability(self):
        # CV ~1.0 (chaotic) → bonus ~0
        result = consistency_bonus([100, 1000, 50, 800, 200, 1500, 90, 1200])
        assert result < 30

    def test_radik_4week_moderate(self):
        # 4 non-empty weeks 328/396/378/535 — CV ≈ 0.19 → bonus ≈ 162
        result = consistency_bonus([328, 396, 378, 535])
        assert 155 < result < 170

    def test_empty_weeks_skipped(self):
        # Two empty weeks shouldn't artificially boost variance
        # 4 non-empty consistent + 4 empty → still good consistency
        result = consistency_bonus([400, 400, 0, 400, 0, 400, 0, 0])
        # 4 non-empty all 400 → CV = 0 → 200
        assert result == 200


class TestRecoveryBonus:
    def test_no_activities(self):
        assert recovery_bonus([]) == 0

    def test_no_dfa_data(self):
        # 5 long rides but none have DFA recorded — bonus 0
        rides = [
            EnduranceActivity(
                dt=REF_DATE - timedelta(days=i),
                type="Ride",
                moving_time_sec=120 * 60,
                training_load=100.0,
                z2plus_time_pct=0.8,
                dfa_a1_mean=None,
            )
            for i in range(5)
        ]
        assert recovery_bonus(rides) == 0

    def test_below_min_valid_sessions(self):
        # Only 2 valid sessions — minimum 3 required
        sessions = [
            EnduranceActivity(
                dt=REF_DATE - timedelta(days=i),
                type="Ride",
                moving_time_sec=70 * 60,
                training_load=80.0,
                z2plus_time_pct=0.8,
                dfa_a1_mean=1.0,
            )
            for i in range(2)
        ]
        assert recovery_bonus(sessions) == 0

    def test_all_green(self):
        # 5 valid sessions, all DFA ≥0.75 → 100% green → 200
        sessions = [
            EnduranceActivity(
                dt=REF_DATE - timedelta(days=i),
                type="Ride",
                moving_time_sec=70 * 60,
                training_load=80.0,
                z2plus_time_pct=0.8,
                dfa_a1_mean=1.1,
            )
            for i in range(5)
        ]
        assert recovery_bonus(sessions) == 200

    def test_radik_today_mix(self):
        # 11 green of 12 valid → 0.917 × 200 ≈ 183
        sessions = [
            EnduranceActivity(
                dt=REF_DATE - timedelta(days=i),
                type="Ride",
                moving_time_sec=70 * 60,
                training_load=80.0,
                z2plus_time_pct=0.8,
                dfa_a1_mean=1.1,
            )
            for i in range(11)
        ]
        sessions.append(
            EnduranceActivity(
                dt=REF_DATE - timedelta(days=12),
                type="Run",
                moving_time_sec=60 * 60,
                training_load=70.0,
                z2plus_time_pct=0.75,
                dfa_a1_mean=0.6,  # yellow
            )
        )
        result = recovery_bonus(sessions)
        assert 180 < result < 187


# ─── Composite VO2max ─────────────────────────────────────────────────────


class TestVO2maxComposite:
    def test_no_thresholds_returns_default(self):
        athlete = AthleteProfile(age=43, weight_kg=78.5, ftp_w=None, threshold_pace_sec_per_km=None)
        assert vo2max_composite(athlete, {}) == DEFAULT_VO2MAX

    def test_only_bike_threshold(self):
        # No run pace → run falls back to bike
        athlete = AthleteProfile(age=43, weight_kg=78.5, ftp_w=207.8, threshold_pace_sec_per_km=None)
        result = vo2max_composite(athlete, {"Ride": 20.0})
        assert round(result, 1) == 35.0  # bike Storer

    def test_radik_today(self):
        # FTP 207.8 / threshold 287 / sport-CTL bike 17.6 / run 17.7 / swim 5.7
        athlete = AthleteProfile(age=43, weight_kg=78.5, ftp_w=207.8, threshold_pace_sec_per_km=287)
        result = vo2max_composite(athlete, {"Ride": 17.6, "Run": 17.7, "Swim": 5.7})
        # bike 35.0*0.422 + run 52.1*0.425 + swim 52.1*0.137 = 14.8 + 22.1 + 7.1 ≈ 44.0
        assert 43.5 < result < 45.5

    def test_ride_eftp_overrides_settings_ftp(self):
        # Stale manual FTP 225 in settings, date-specific eFTP 207.8 — eFTP wins.
        athlete = AthleteProfile(age=43, weight_kg=78.5, ftp_w=225.0, threshold_pace_sec_per_km=None)
        with_eftp = vo2max_composite(athlete, {"Ride": 20.0}, ride_eftp=207.8)
        assert round(with_eftp, 1) == 35.0  # Storer at 207.8, not 225
        without_eftp = vo2max_composite(athlete, {"Ride": 20.0})
        assert without_eftp > with_eftp  # 225 would inflate

    def test_ride_eftp_fallback_to_settings_ftp(self):
        # No eFTP (user without power meter in sport_info) → settings FTP used.
        athlete = AthleteProfile(age=43, weight_kg=78.5, ftp_w=207.8, threshold_pace_sec_per_km=None)
        result = vo2max_composite(athlete, {"Ride": 20.0}, ride_eftp=None)
        assert round(result, 1) == 35.0

    def test_ride_eftp_alone_enables_bike_vo2(self):
        # No settings FTP at all — eFTP still unlocks the Storer branch.
        athlete = AthleteProfile(age=43, weight_kg=78.5, ftp_w=None, threshold_pace_sec_per_km=None)
        result = vo2max_composite(athlete, {"Ride": 20.0}, ride_eftp=207.8)
        assert round(result, 1) == 35.0


# ─── Zones ────────────────────────────────────────────────────────────────


class TestClassifyZone:
    def test_below_detrained(self):
        assert classify_zone(0).id == "detrained"
        assert classify_zone(2999).id == "detrained"

    def test_recovering_band(self):
        assert classify_zone(3000).id == "recovering"
        assert classify_zone(4499).id == "recovering"

    def test_maintaining_band(self):
        assert classify_zone(4500).id == "maintaining"
        assert classify_zone(5499).id == "maintaining"

    def test_productive_band(self):
        assert classify_zone(5500).id == "productive"
        assert classify_zone(6499).id == "productive"

    def test_peaking_band(self):
        assert classify_zone(6500).id == "peaking"
        assert classify_zone(ENDURANCE_MAX).id == "peaking"


# ─── Per-sport ───────────────────────────────────────────────────────────


class TestPerSportBreakdown:
    def test_radik_today(self):
        # bike 17.6 / run 17.7 / swim 5.7 → 42.4 / 42.7 / 13.7
        parts = per_sport_breakdown({"Ride": 17.6, "Run": 17.7, "Swim": 5.7})
        names = [p.name for p in parts]
        # No Other — sums to ~98.8 (rounding), gap 1.2 — emits Other
        assert "Bike" in names
        assert "Run" in names
        assert "Swim" in names

    def test_empty_ctl(self):
        assert per_sport_breakdown({}) == []
        assert per_sport_breakdown({"Ride": 0, "Run": 0}) == []

    def test_no_other_when_zero_gap(self):
        # Exactly 100% accounted across three sports → no Other row
        parts = per_sport_breakdown({"Ride": 50, "Run": 30, "Swim": 20})
        assert [p.name for p in parts] == ["Bike", "Run", "Swim"]


# ─── Badges ──────────────────────────────────────────────────────────────


class TestBadge:
    def test_no_history_no_badge(self):
        assert (
            compute_badge(
                score_today=5000,
                zone_today_id="maintaining",
                zone_yesterday_id=None,
                scores_last_90d=[],
                scores_last_365d=[],
                zones_last_84d=[],
            )
            is None
        )

    def test_zone_breakthrough(self):
        b = compute_badge(
            score_today=5600,
            zone_today_id="productive",
            zone_yesterday_id="maintaining",
            scores_last_90d=[],
            scores_last_365d=[],
            zones_last_84d=[],
        )
        assert b is not None
        assert b.id == "new_zone"
        assert "Развиваюсь" in b.label

    def test_zone_demotion_no_badge(self):
        b = compute_badge(
            score_today=4200,
            zone_today_id="recovering",
            zone_yesterday_id="maintaining",
            scores_last_90d=[5000] * 60,
            scores_last_365d=[],
            zones_last_84d=[],
        )
        # Demotion shouldn't trigger #1, and score isn't a new max
        assert b is None or b.id != "new_zone"

    def test_best_90d_requires_min_history(self):
        # 20 days of data, less than 30 — no badge
        b = compute_badge(
            score_today=5500,
            zone_today_id="productive",
            zone_yesterday_id="productive",
            scores_last_90d=[5000] * 20,
            scores_last_365d=[],
            zones_last_84d=[],
        )
        assert b is None

    def test_best_90d_triggers(self):
        b = compute_badge(
            score_today=5500,
            zone_today_id="productive",
            zone_yesterday_id="productive",
            scores_last_90d=[4500] * 60,  # ≥30 history, max=4500 < today
            scores_last_365d=[],
            zones_last_84d=[],
        )
        assert b is not None
        assert b.id == "best_90d"

    def test_in_form_3m(self):
        b = compute_badge(
            score_today=5800,
            zone_today_id="productive",
            zone_yesterday_id="productive",
            # Ensure #2 (best_90d) doesn't fire — recent max higher than today
            scores_last_90d=[6500] * 60,
            scores_last_365d=[],
            zones_last_84d=["productive"] * 80 + ["peaking"] * 4,
        )
        assert b is not None
        assert b.id == "in_form_3m"

    def test_priority_zone_over_best90d(self):
        # Both #1 and #2 would trigger — #1 wins (higher priority)
        b = compute_badge(
            score_today=5500,
            zone_today_id="productive",
            zone_yesterday_id="maintaining",
            scores_last_90d=[5000] * 60,
            scores_last_365d=[],
            zones_last_84d=[],
        )
        assert b is not None
        assert b.id == "new_zone"

    def test_best_90d_suppressed_by_cooldown(self):
        """Spec §3.9 — 7d cooldown on #2. If `best_90d` fired recently, skip."""
        b = compute_badge(
            score_today=5500,
            zone_today_id="productive",
            zone_yesterday_id="productive",
            scores_last_90d=[4500] * 60,
            scores_last_365d=[],
            zones_last_84d=[],
            recent_badge_ids=["best_90d"],  # fired in last 7 days
        )
        assert b is None

    def test_zone_breakthrough_suppressed_by_cooldown(self):
        """Spec §3.9 — even the priority `new_zone` respects cooldown."""
        b = compute_badge(
            score_today=5600,
            zone_today_id="productive",
            zone_yesterday_id="maintaining",
            scores_last_90d=[5400] * 60,  # would trigger best_90d if not for cooldown below
            scores_last_365d=[],
            zones_last_84d=[],
            recent_badge_ids=["new_zone", "best_90d"],
        )
        # Both #1 and #2 cooled down → fallthrough to None
        assert b is None

    def test_new_zone_cooldown_can_be_independent_from_others(self):
        """Spec §3.9: `new_zone` 1d cooldown is independent from #2/#3/#4 7d.

        Regression for review L2 — the 1d window collapses into the 7d only
        when caller passes the same id list for both. Caller is expected to
        supply BOTH windows; we exercise the engine's id-membership directly:
        if `new_zone` is in the recent list, #1 should be skipped regardless
        of what other badges fired (or didn't) recently.
        """
        # Scenario A: only `new_zone` cooled — #2 free → falls through to #2.
        b = compute_badge(
            score_today=5600,
            zone_today_id="productive",
            zone_yesterday_id="maintaining",
            scores_last_90d=[5400] * 60,
            scores_last_365d=[],
            zones_last_84d=[],
            recent_badge_ids=["new_zone"],
        )
        assert b is not None and b.id == "best_90d"

        # Scenario B: only `best_90d` cooled — #1 free → falls through to #1.
        b2 = compute_badge(
            score_today=5600,
            zone_today_id="productive",
            zone_yesterday_id="maintaining",
            scores_last_90d=[5400] * 60,
            scores_last_365d=[],
            zones_last_84d=[],
            recent_badge_ids=["best_90d"],
        )
        assert b2 is not None and b2.id == "new_zone"

    def test_cooldown_allows_other_rule_to_fire(self):
        """If #1 is cooled down but #2 isn't, #2 should still fire."""
        b = compute_badge(
            score_today=5500,
            zone_today_id="productive",
            zone_yesterday_id="maintaining",
            scores_last_90d=[4500] * 60,
            scores_last_365d=[],
            zones_last_84d=[],
            recent_badge_ids=["new_zone"],  # only #1 cooled
        )
        assert b is not None
        assert b.id == "best_90d"


# ─── End-to-end: Radik's 5 anchor dates ──────────────────────────────────


class TestComputeEnduranceScoreRadik:
    """Validates the formula against Radik's real data across 5 phases.

    These tests are the canonical drift-vs-Garmin check from the spec §8.
    If they shift, either the spec calibration is stale or the implementation
    drifted — investigate before adjusting expectations.
    """

    def _athlete_may_2026(self):
        return AthleteProfile(age=43, weight_kg=78.5, ftp_w=207.8, threshold_pace_sec_per_km=287)

    def test_today_2026_05_25(self):
        """Garmin anchor = 5773. With Daniels VDOT corrected (~52, matches Garmin's
        recorded VO2max=53 from Nov 2025), our predicted ≈ 5660. Drift ≈ −2%."""
        athlete = self._athlete_may_2026()
        latest = WellnessSnapshot(
            dt=REF_DATE,
            ctl=41.6,
            ramp_rate=6.2,
            sport_ctl={"Ride": 17.6, "Run": 17.7, "Swim": 5.7},
        )
        # CTL_avg_8w ≈ 30 (climbing from injury) — simulating the average rather
        # than the exact wellness rows. This is the calibration we expect for
        # the production endpoint when wellness_56d returns ~56 daily values.
        wellness_56d = [
            WellnessSnapshot(dt=REF_DATE - timedelta(days=i), ctl=30 + i * 0.2, ramp_rate=None) for i in range(56)
        ]
        # Mock activities matching the 28-day calibration from spec §8:
        # 4 long sessions (3 rides ≥120min, 1 run ≥90min), total TL ≈ 1637
        activities_28d = [
            # Long sessions
            EnduranceActivity(REF_DATE - timedelta(days=1), "Ride", 130 * 60, 84.0, 0.85, 1.252),
            EnduranceActivity(REF_DATE - timedelta(days=9), "Ride", 141 * 60, 129.0, 0.85, 0.963),
            EnduranceActivity(REF_DATE - timedelta(days=23), "Ride", 120 * 60, 76.0, 0.85, 1.273),
            EnduranceActivity(REF_DATE - timedelta(days=2), "Run", 110 * 60, 124.0, 0.80, 0.754),
        ]
        # Filler — 39 more sessions to reach total_tss=1637, ensuring share_long ≈ 0.25
        total_long_tl = 84 + 129 + 76 + 124  # 413
        remaining = 1637 - total_long_tl  # 1224
        filler_count = 39
        filler_tl = remaining / filler_count
        for i in range(filler_count):
            day_offset = (i % 20) + 1
            # Mix rides/runs of medium length with valid DFA so RecoveryBonus
            # also picks them up (≥45min Run / ≥60min Ride).
            sport = "Ride" if i % 2 == 0 else "Run"
            duration = 60 * 60 if sport == "Ride" else 50 * 60
            activities_28d.append(
                EnduranceActivity(
                    REF_DATE - timedelta(days=day_offset),
                    sport,
                    duration,
                    filler_tl,
                    0.80,
                    1.0,
                )
            )
        # Same activities for the 8w window — formula sums by week, the same
        # daily distribution covers both 28d (last 4 weeks) and 8w consistency.
        result = compute_endurance_score(
            ref_date=REF_DATE,
            athlete=athlete,
            latest_wellness=latest,
            wellness_56d=wellness_56d,
            activities_28d=activities_28d,
            activities_8w=activities_28d,
        )
        # Spec §8: expected ~5422 (drift -6% from Garmin 5773).
        # Allow ±300 envelope for filler-construction noise.
        assert 5100 < result.score < 5700, f"Got {result.score}, components={result.components}"
        assert result.zone_id in ("maintaining", "productive")  # 5422 at the boundary

    def test_detrain_2026_02_01(self):
        """Reactive arthritis — 1 activity in 28 days. ES should drop to ~4400."""
        athlete = self._athlete_may_2026()  # thresholds still on file from Nov 2025
        latest = WellnessSnapshot(
            dt=date(2026, 2, 1),
            ctl=19.6,
            ramp_rate=-3.56,
            sport_ctl={"Ride": 10.1, "Run": 7.0, "Swim": 2.0},
        )
        wellness_56d = [
            WellnessSnapshot(dt=date(2026, 2, 1) - timedelta(days=i), ctl=20 - i * 0.1, ramp_rate=None)
            for i in range(56)
        ]
        # One Other-type session, 5min — doesn't count for anything
        activities = [
            EnduranceActivity(date(2026, 1, 26), "Other", 5 * 60, 1.0, None, None),
        ]
        result = compute_endurance_score(
            ref_date=date(2026, 2, 1),
            athlete=athlete,
            latest_wellness=latest,
            wellness_56d=wellness_56d,
            activities_28d=activities,
            activities_8w=activities,
        )
        # With corrected Daniels VDOT (Base ≈ 4440), Feb 2026 Score ≈ 4685 →
        # falls into "maintaining" zone (4500-5499) rather than "recovering".
        # This is correct behaviour: 2 weeks of detrain ≠ VO2max decay.
        # Apr 2026 (2 months in) drops further to ~4400 = recovering.
        assert 4500 < result.score < 4900, f"Got {result.score}"
        assert result.zone_id == "maintaining"
        # All training bonuses should be 0 (no real training)
        assert result.components.duration == 0
        assert result.components.consistency == 0
        assert result.components.recovery == 0
        assert result.components.recent == 0  # ramp negative → clamped to 0


class TestComputeEnduranceScoreEdgeCases:
    def test_score_clamped_to_max(self):
        # Athlete with elite VO2max + all caps → would push above ENDURANCE_MAX
        athlete = AthleteProfile(age=30, weight_kg=65, ftp_w=400, threshold_pace_sec_per_km=180)
        latest = WellnessSnapshot(
            dt=REF_DATE,
            ctl=90,  # caps LongTerm
            ramp_rate=10,  # caps Recent
            sport_ctl={"Ride": 50, "Run": 30, "Swim": 20},
        )
        wellness_56d = [WellnessSnapshot(dt=REF_DATE - timedelta(days=i), ctl=85, ramp_rate=None) for i in range(56)]
        # Many long quality sessions with green DFA
        activities = [
            EnduranceActivity(
                REF_DATE - timedelta(days=i),
                "Ride",
                150 * 60,
                250.0,
                0.85,
                1.1,
            )
            for i in range(20)
        ]
        result = compute_endurance_score(
            ref_date=REF_DATE,
            athlete=athlete,
            latest_wellness=latest,
            wellness_56d=wellness_56d,
            activities_28d=activities,
            activities_8w=activities,
        )
        assert result.score <= ENDURANCE_MAX
        assert result.zone_id == "peaking"

    def test_no_thresholds_flags_insufficient(self):
        athlete = AthleteProfile(age=None, weight_kg=None, ftp_w=None, threshold_pace_sec_per_km=None)
        latest = WellnessSnapshot(dt=REF_DATE, ctl=None, ramp_rate=None)
        result = compute_endurance_score(
            ref_date=REF_DATE,
            athlete=athlete,
            latest_wellness=latest,
            wellness_56d=[],
            activities_28d=[],
            activities_8w=[],
        )
        assert result.insufficient_data is True
        assert result.insufficient_reason == "no_thresholds"
        # Score still returned (Base from DEFAULT_VO2MAX)
        assert result.score == round(100 * DEFAULT_VO2MAX)  # 4000

    def test_ride_eftp_alone_is_sufficient(self):
        # No settings thresholds, but the wellness snapshot carries eFTP —
        # Base computes from Storer, not flagged insufficient.
        athlete = AthleteProfile(age=43, weight_kg=78.5, ftp_w=None, threshold_pace_sec_per_km=None)
        latest = WellnessSnapshot(
            dt=REF_DATE,
            ctl=None,
            ramp_rate=None,
            sport_ctl={"Ride": 20.0},
            ride_eftp=207.8,
        )
        result = compute_endurance_score(
            ref_date=REF_DATE,
            athlete=athlete,
            latest_wellness=latest,
            wellness_56d=[],
            activities_28d=[],
            activities_8w=[],
        )
        assert result.insufficient_data is False
        # Storer at 207.8/78.5/43 ≈ 35.04 — base uses the unrounded composite.
        assert result.components.base == round(100 * vo2max_bike_storer(207.8, 78.5, 43))

    def test_ride_eftp_drives_base_over_stale_ftp(self):
        # Radik's real case: settings FTP stuck at 225 (Dec peak), eFTP 207.8.
        athlete = AthleteProfile(age=43, weight_kg=78.5, ftp_w=225.0, threshold_pace_sec_per_km=287)
        latest = WellnessSnapshot(
            dt=REF_DATE,
            ctl=41.6,
            ramp_rate=None,
            sport_ctl={"Ride": 17.6, "Run": 17.7, "Swim": 5.7},
            ride_eftp=207.8,
        )
        result = compute_endurance_score(
            ref_date=REF_DATE,
            athlete=athlete,
            latest_wellness=latest,
            wellness_56d=[],
            activities_28d=[],
            activities_8w=[],
        )
        # eFTP 207.8 → bike 35.0 → composite ≈ 44.8; stale FTP 225 would give
        # bike 37.3 → composite ≈ 45.8. Assert the eFTP value won.
        assert 44.5 < result.vo2max_composite < 45.1


class TestDetrainFactor:
    """Detrain decay (spec §13.1) — scales the VO2max anchor by CTL relative to
    the athlete's own 26-week peak. Downward-only; 1.0 = no decay."""

    def test_no_peak_history_no_decay(self):
        # New user / Phase-1 fallback — nothing to decay from.
        assert detrain_factor(20.0, None) == 1.0

    def test_zero_peak_no_decay(self):
        assert detrain_factor(20.0, 0.0) == 1.0

    def test_missing_ctl_now_no_decay(self):
        # Can't measure the drop without today's CTL.
        assert detrain_factor(None, 60.0) == 1.0

    def test_full_fitness_no_decay(self):
        # ctl_now == peak → ratio 1.0 → factor 1.0 (calibration anchors unchanged).
        assert detrain_factor(60.0, 60.0) == 1.0

    def test_above_peak_clamps_to_one(self):
        # A new peak forming mid-window must not inflate above 1.0.
        assert detrain_factor(70.0, 60.0) == 1.0

    def test_deep_detrain_hits_floor(self):
        # CTL collapsed → floor (VO2max loses ~15-25%, not more — Coyle 1984).
        assert detrain_factor(0.0, 60.0) == pytest.approx(DETRAIN_FLOOR)

    def test_half_ratio_interpolates_linearly(self):
        expected = DETRAIN_FLOOR + (1.0 - DETRAIN_FLOOR) * 0.5
        assert detrain_factor(30.0, 60.0) == pytest.approx(expected)


class TestComputeEnduranceScoreDetrainDecay:
    """Spec §13.1 — base decay gives the score real downward range on a layoff.

    Mirrors `test_detrain_2026_02_01` (the legacy no-decay anchor) but feeds a
    realistic 26-week CTL peak so the decay actually engages.
    """

    def _athlete(self):
        return AthleteProfile(age=43, weight_kg=78.5, ftp_w=207.8, threshold_pace_sec_per_km=287)

    def _detrain_feb_2026(self, ctl_peak_26w):
        latest = WellnessSnapshot(
            dt=date(2026, 2, 1),
            ctl=19.6,
            ramp_rate=-3.56,
            sport_ctl={"Ride": 10.1, "Run": 7.0, "Swim": 2.0},
        )
        wellness_56d = [
            WellnessSnapshot(dt=date(2026, 2, 1) - timedelta(days=i), ctl=20 - i * 0.1, ramp_rate=None)
            for i in range(56)
        ]
        # One Other-type 5min session — no training bonuses, isolates the base.
        activities = [EnduranceActivity(date(2026, 1, 26), "Other", 5 * 60, 1.0, None, None)]
        return compute_endurance_score(
            ref_date=date(2026, 2, 1),
            athlete=self._athlete(),
            latest_wellness=latest,
            wellness_56d=wellness_56d,
            activities_28d=activities,
            activities_8w=activities,
            ctl_peak_26w=ctl_peak_26w,
        )

    def test_default_no_peak_is_backward_compatible(self):
        # No ctl_peak → factor 1.0 / peak None → identical to the legacy anchor.
        r = self._detrain_feb_2026(ctl_peak_26w=None)
        assert r.detrain_factor == 1.0
        assert r.ctl_peak_26w is None
        assert r.zone_id == "maintaining"

    def test_decay_drops_zone_below_no_decay(self):
        no_decay = self._detrain_feb_2026(ctl_peak_26w=None)
        # Nov-2025 peak fitness ≈ CTL 60 sits inside the trailing 26w window.
        with_decay = self._detrain_feb_2026(ctl_peak_26w=60.0)
        # ctl 19.6 vs peak 60 → ratio ≈ 0.33 → factor ≈ 0.85.
        assert with_decay.detrain_factor < 0.90
        assert with_decay.ctl_peak_26w == 60.0
        # Base decayed ~15% → score drops a full zone: maintaining → recovering.
        assert with_decay.score < no_decay.score - 400
        assert with_decay.zone_id == "recovering"

    def test_peak_equal_to_now_no_decay(self):
        # If the window's peak is today's value, there's been no decline.
        r = self._detrain_feb_2026(ctl_peak_26w=19.6)
        assert r.detrain_factor == 1.0
