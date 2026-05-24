"""Tests for `data/marathon_shape.py` — pure formulas, no DB."""

from datetime import date, timedelta

from data.marathon_shape import (
    DAYS_FOR_LONGJOGS,
    MIN_KM_FOR_LONGJOG,
    MINIMAL_EFFECTIVE_VO2MAX,
    RunActivity,
    calculate_marathon_shape,
    target_longjog_km,
    target_weekly_km,
)

REF_DATE = date(2026, 5, 14)


class TestTargetWeeklyKm:
    """`target_weekly_km(vo2) = max(vo2, 25) ** 1.135`."""

    def test_vo2max_45(self):
        assert round(target_weekly_km(45), 2) == 75.23

    def test_vo2max_50(self):
        assert round(target_weekly_km(50), 2) == 84.79

    def test_vo2max_55(self):
        assert round(target_weekly_km(55), 2) == 94.47

    def test_clamps_below_minimum(self):
        # vo2max < 25 should clamp to 25 — target stays at 25^1.135
        assert target_weekly_km(20) == target_weekly_km(MINIMAL_EFFECTIVE_VO2MAX)
        assert round(target_weekly_km(20), 2) == 38.61


class TestTargetLongjogKm:
    """`target_longjog_km(vo2) = ln(max(vo2, 25) / 4) * 12 - 13`."""

    def test_vo2max_45(self):
        assert round(target_longjog_km(45), 2) == 16.04

    def test_vo2max_50(self):
        assert round(target_longjog_km(50), 2) == 17.31

    def test_vo2max_55(self):
        assert round(target_longjog_km(55), 2) == 18.45

    def test_clamps_below_minimum(self):
        assert target_longjog_km(20) == target_longjog_km(MINIMAL_EFFECTIVE_VO2MAX)
        assert round(target_longjog_km(20), 2) == 8.99


class TestCalculateMarathonShape:
    """End-to-end shape computation."""

    def test_no_runs_returns_zero_shape(self):
        result = calculate_marathon_shape([], vo2max=50.0, reference_date=REF_DATE)
        assert result.shape_pct == 0.0
        assert result.actual_weekly_km == 0.0
        assert result.longjog_score == 0.0
        # Targets всё равно вычисляются — нужны для отображения «куда стремиться»
        assert result.target_weekly_km == 84.8
        assert result.target_longjog_km == 17.3

    def test_steady_weekly_volume_no_longjogs(self):
        """80 km/week for 26 weeks, all runs <13km (no longjogs).

        target_weekly(50) = 84.8 → weekly_ratio ≈ 80/84.8 ≈ 0.944
        longjog_ratio = 0
        shape = 100 × (0.67 × 0.944 + 0.33 × 0) ≈ 63.2
        """
        # 26 weeks × 7 runs/week × ~11.43 km = 80 km/week, each below longjog threshold
        runs = []
        for week in range(26):
            for day in range(7):
                days_ago = week * 7 + day
                runs.append(
                    RunActivity(
                        dt=REF_DATE - timedelta(days=days_ago),
                        distance_km=80 / 7,  # ~11.43 km — under 13km threshold
                    )
                )

        result = calculate_marathon_shape(runs, vo2max=50.0, reference_date=REF_DATE)
        assert result.longjog_score == 0.0  # no longjogs
        assert 78 <= result.actual_weekly_km <= 82
        assert 60 <= result.shape_pct <= 66  # weekly-only contribution

    def test_recent_longjog_outweighs_old(self):
        """Same 20km run today vs 35 days ago. Today weight=2, midpoint weight=1."""
        today_run = [RunActivity(dt=REF_DATE, distance_km=20.0)]
        mid_run = [RunActivity(dt=REF_DATE - timedelta(days=35), distance_km=20.0)]

        today_result = calculate_marathon_shape(today_run, vo2max=50.0, reference_date=REF_DATE)
        mid_result = calculate_marathon_shape(mid_run, vo2max=50.0, reference_date=REF_DATE)

        assert today_result.longjog_score > mid_result.longjog_score
        # 2× ratio within rounding
        assert abs(today_result.longjog_score - 2 * mid_result.longjog_score) < 0.01

    def test_longjog_at_window_edge_excluded(self):
        """Run exactly 70 days old → excluded from longjog window."""
        edge_run = [RunActivity(dt=REF_DATE - timedelta(days=DAYS_FOR_LONGJOGS), distance_km=20.0)]
        result = calculate_marathon_shape(edge_run, vo2max=50.0, reference_date=REF_DATE)
        assert result.longjog_score == 0.0
        assert result.actual_longjog_km == 0.0

    def test_sub_threshold_run_not_counted_as_longjog(self):
        """Run <13 km counts toward weekly_km but not longjog score."""
        runs = [RunActivity(dt=REF_DATE - timedelta(days=1), distance_km=MIN_KM_FOR_LONGJOG - 0.1)]
        result = calculate_marathon_shape(runs, vo2max=50.0, reference_date=REF_DATE)
        assert result.longjog_score == 0.0
        assert result.actual_weekly_km > 0  # the run still contributes to weekly

    def test_actual_longjog_km_is_max_in_window(self):
        runs = [
            RunActivity(dt=REF_DATE - timedelta(days=10), distance_km=15.0),
            RunActivity(dt=REF_DATE - timedelta(days=20), distance_km=22.0),  # max
            RunActivity(dt=REF_DATE - timedelta(days=30), distance_km=18.0),
        ]
        result = calculate_marathon_shape(runs, vo2max=50.0, reference_date=REF_DATE)
        assert result.actual_longjog_km == 22.0

    def test_short_history_clamps_to_70_day_denominator(self):
        """Athlete with 30 days of history isn't penalised by 182d denominator."""
        runs = [RunActivity(dt=REF_DATE - timedelta(days=i), distance_km=10.0) for i in range(30)]
        result = calculate_marathon_shape(runs, vo2max=50.0, reference_date=REF_DATE)
        # 300 km total / 70 days × 7 = 30 km/week (not 300×7/182 = 11.5)
        assert 29 <= result.actual_weekly_km <= 31

    def test_mid_history_gap_keeps_full_window_denominator(self):
        """Gap between two run-blocks (both inside 182d) does NOT trigger clamp.

        Fixture: 30 days of 10km/day (recent) + 91 days of 10km/day (90-180d
        ago) with a 60-day dead zone in the middle. Earliest run = 180 days
        ago → `actual_training_days = 181` → no clamp, full 182 denominator.

        This is the «mid-history gap» variant — the old block keeps earliest
        deep in the window, so the gap doesn't fast-track shape recovery.
        Documents what happens when an athlete returns but their old history
        is still being counted (typical for someone who took a break <6 mo
        ago — their pre-break volume is still in the 26-week window).

        For the «long break, only recent block» case where the clamp DOES
        trigger, see `test_returning_athlete_clamps_to_70_day_denominator`.
        """
        runs = []
        # Recent block: days 0-29
        for i in range(30):
            runs.append(RunActivity(dt=REF_DATE - timedelta(days=i), distance_km=10.0))
        # Old block: days 90-180 ago — still inside 182d window
        for i in range(90, 181):
            runs.append(RunActivity(dt=REF_DATE - timedelta(days=i), distance_km=10.0))

        result = calculate_marathon_shape(runs, vo2max=50.0, reference_date=REF_DATE)
        # earliest run = 180 days ago → actual_training_days = 181 → clamp 182
        # Total km = (30 + 91) × 10 = 1210 km
        # actual_weekly = 1210 × 7 / 182 ≈ 46.5 km/wk
        assert 45 <= result.actual_weekly_km <= 48
        assert result.shape_pct > 0  # no NaN, no crash

    def test_returning_athlete_clamps_to_70_day_denominator(self):
        """Long break, only recent block in window — clamp DOES trigger.

        Pure «returning after long break» fixture: only 30 days of recent
        runs, no older history in the 182d window. earliest_run = 29 days
        ago → `actual_training_days = 30` → clamped to 70 → weekly_ratio
        uses 70-day denominator instead of 182.

        This is the §3-documented side-effect: small denominator inflates
        weekly_ratio relative to total volume — «shape after break grows
        back fast» because we don't penalise the dead period.
        """
        runs = [RunActivity(dt=REF_DATE - timedelta(days=i), distance_km=10.0) for i in range(30)]
        result = calculate_marathon_shape(runs, vo2max=50.0, reference_date=REF_DATE)
        # 300 km / 70 (clamped) × 7 = 30 km/wk — NOT 300×7/182 = 11.5
        assert 29 <= result.actual_weekly_km <= 31
        assert result.shape_pct > 0  # no NaN, no division-by-zero

    def test_run_at_exactly_13km_not_counted_as_longjog(self):
        """Strict `> 13` per PHP reference (zero numeric effect — score at exactly 13 is 0)."""
        runs = [RunActivity(dt=REF_DATE - timedelta(days=1), distance_km=MIN_KM_FOR_LONGJOG)]
        result = calculate_marathon_shape(runs, vo2max=50.0, reference_date=REF_DATE)
        assert result.longjog_score == 0.0
        assert result.actual_longjog_km == 0.0

    def test_vo2max_below_minimum_uses_clamp(self):
        result = calculate_marathon_shape([], vo2max=20.0, reference_date=REF_DATE)
        assert result.vo2max_used == MINIMAL_EFFECTIVE_VO2MAX
        assert result.target_weekly_km == 38.6  # 25^1.135 rounded

    def test_runs_outside_182d_window_ignored(self):
        """Old runs don't bleed into the weekly_km calculation."""
        runs = [RunActivity(dt=REF_DATE - timedelta(days=200), distance_km=20.0)]
        result = calculate_marathon_shape(runs, vo2max=50.0, reference_date=REF_DATE)
        assert result.actual_weekly_km == 0.0
        assert result.longjog_score == 0.0  # also outside 70d longjog window

    def test_ready_for_marathon_scenario(self):
        """80 km/week steady + weekly 20km longjog → shape должен быть около 100%.

        target_weekly(50) = 84.8 → weekly_ratio ≈ 0.94
        target_longjog(50) = 17.3 → normalised (20-13)/17.3 = 0.405
        За 70 дней — 10 longjog'ов на даты 0, 7, 14, ..., 63 → linear weights
        sum of weights ≈ 2 + 1.8 + 1.6 + ... + 0.2 = 11 (arithmetic series)
        longjog_score ≈ 11 × 0.405² ≈ 1.8
        longjog_ratio = 1.8 × 7 / 70 ≈ 0.18
        shape ≈ 100 × (0.67 × 0.94 + 0.33 × 0.18) ≈ 69

        (Marathon-ready demands BOTH high volume AND consistent longjogs; this
        scenario shows что только volume + еженедельный 20км long не дотягивают.)
        """
        runs = []
        for week in range(26):
            # 7 weekly runs: 6 × 10km + 1 × 20km = 80km/week
            for day in range(6):
                runs.append(
                    RunActivity(
                        dt=REF_DATE - timedelta(days=week * 7 + day),
                        distance_km=10.0,
                    )
                )
            runs.append(
                RunActivity(
                    dt=REF_DATE - timedelta(days=week * 7 + 6),
                    distance_km=20.0,
                )
            )

        result = calculate_marathon_shape(runs, vo2max=50.0, reference_date=REF_DATE)
        assert 65 <= result.shape_pct <= 80
        assert result.actual_longjog_km == 20.0
        assert result.longjog_score > 0
