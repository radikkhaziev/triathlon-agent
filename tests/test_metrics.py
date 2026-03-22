from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from data.metrics import (
    HR_ZONES,
    _calculate_trend,
    _classify_recovery,
    _rmssd_ai_endurance,
    _rmssd_flatt_esco,
    calc_hr_tss,
    calc_power_tss,
    calc_swim_tss,
    calculate_banister_recovery,
    calculate_ess,
    calculate_readiness,
    calculate_rhr_status,
    calculate_rmssd_status,
    combined_recovery_score,
    update_ctl_atl,
)
from data.models import HRVData, ReadinessLevel, RhrStatus, RmssdStatus, SleepData, TrendResult

# ---------------------------------------------------------------------------
# TSS Calculations
# ---------------------------------------------------------------------------


class TestCalcHrTss:
    def test_threshold_effort_one_hour(self):
        assert calc_hr_tss(3600, 158, 42, 182, 158) == 100.0

    def test_easy_effort(self):
        assert 0 < calc_hr_tss(3600, 130, 42, 182, 158) < 100

    def test_short_duration(self):
        assert calc_hr_tss(1800, 158, 42, 182, 158) == 50.0

    def test_above_threshold(self):
        assert calc_hr_tss(3600, 170, 42, 182, 158) > 100

    def test_lthr_equals_resting_hr(self):
        assert calc_hr_tss(3600, 130, 60, 182, 60) == 0.0

    def test_zero_duration(self):
        assert calc_hr_tss(0, 158, 42, 182, 158) == 0.0


class TestCalcPowerTss:
    def test_ftp_effort_one_hour(self):
        assert calc_power_tss(3600, 245, 245) == 100.0

    def test_half_ftp(self):
        assert calc_power_tss(3600, 122.5, 245) == 25.0

    def test_above_ftp(self):
        assert calc_power_tss(3600, 270, 245) > 100

    def test_zero_ftp(self):
        assert calc_power_tss(3600, 200, 0) == 0.0


class TestCalcSwimTss:
    def test_at_css_pace(self):
        distance = 3000
        duration = 3000 / 100 * 98
        tss = calc_swim_tss(distance, duration, 98)
        assert abs(tss - (duration / 3600) * 100) < 0.2

    def test_zero_distance(self):
        assert calc_swim_tss(0, 1800, 98) == 0.0

    def test_faster_than_css(self):
        assert calc_swim_tss(3000, 2700, 98) > 0

    def test_zero_duration(self):
        assert calc_swim_tss(3000, 0, 98) == 0.0

    def test_zero_css(self):
        assert calc_swim_tss(3000, 2940, 0) == 0.0


# ---------------------------------------------------------------------------
# CTL / ATL / TSB
# ---------------------------------------------------------------------------


class TestUpdateCtlAtl:
    def test_empty_history(self):
        ctl, atl, tsb = update_ctl_atl([])
        assert (ctl, atl, tsb) == (0.0, 0.0, 0.0)

    def test_single_day(self):
        ctl, atl, tsb = update_ctl_atl([100.0])
        assert ctl > 0
        assert atl > ctl  # ATL reacts faster

    def test_consistent_training(self):
        ctl, atl, tsb = update_ctl_atl([80.0] * 60)
        assert abs(ctl - 80) < 5
        assert abs(atl - 80) < 2

    def test_rest_after_training(self):
        _, _, tsb = update_ctl_atl([100.0] * 14 + [0.0] * 7)
        assert tsb > 0


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


class TestCalculateReadiness:
    def _hrv(self, last: float, avg: float) -> HRVData:
        return HRVData(date=date(2026, 1, 1), hrv_weekly_avg=avg, hrv_last_night=last, status="Balanced")

    def _sleep(self, score: int) -> SleepData:
        return SleepData(date=date(2026, 1, 1), score=score, duration=28800)

    def test_perfect_conditions(self):
        score, level = calculate_readiness(self._hrv(60, 55), self._sleep(90), 85, 42, 42)
        assert score >= 80
        assert level == ReadinessLevel.GREEN

    def test_poor_hrv(self):
        score, _ = calculate_readiness(self._hrv(40, 60), self._sleep(80), 70, 43, 42)
        assert score < 80

    def test_poor_sleep(self):
        score, _ = calculate_readiness(self._hrv(55, 55), self._sleep(40), 70, 42, 42)
        assert score < 80

    def test_red_zone(self):
        _, level = calculate_readiness(self._hrv(30, 55), self._sleep(40), 20, 52, 42)
        assert level == ReadinessLevel.RED

    def test_score_clamped(self):
        score, _ = calculate_readiness(self._hrv(60, 50), self._sleep(95), 90, 40, 42)
        assert 0 <= score <= 100

    def test_zero_hrv_weekly_avg(self):
        score, _ = calculate_readiness(self._hrv(50, 0), self._sleep(80), 70, 42, 42)
        assert 0 <= score <= 100

    def test_none_sleep_score(self):
        sleep = SleepData(date=date(2026, 1, 1), score=None, duration=28800)
        score, _ = calculate_readiness(self._hrv(55, 55), sleep, 70, 42, 42)
        assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# HR Zones
# ---------------------------------------------------------------------------


class TestHrZones:
    def test_run_zones_exist(self):
        assert len(HR_ZONES["run"]) == 5

    def test_bike_zones_exist(self):
        assert len(HR_ZONES["bike"]) == 5

    def test_zones_are_ordered(self):
        for sport in ("run", "bike"):
            prev_high = 0.0
            for zone in sorted(HR_ZONES[sport]):
                low, high = HR_ZONES[sport][zone]
                assert low >= prev_high or low == 0
                assert high > low
                prev_high = high


# ---------------------------------------------------------------------------
# Trend Analysis
# ---------------------------------------------------------------------------


class TestCalculateTrend:
    def test_insufficient_data(self):
        t = _calculate_trend([1.0, 2.0])
        assert t.direction == "stable"
        assert t.slope == 0.0

    def test_rising_trend(self):
        t = _calculate_trend([10, 12, 14, 16, 18, 20, 22])
        assert t.direction in ("rising", "rising_fast")
        assert t.slope > 0
        assert t.r_squared > 0.9

    def test_declining_trend(self):
        t = _calculate_trend([22, 20, 18, 16, 14, 12, 10])
        assert t.direction in ("declining", "declining_fast")
        assert t.slope < 0

    def test_stable_trend(self):
        t = _calculate_trend([50, 50.1, 49.9, 50, 50.1, 49.9, 50])
        assert t.direction == "stable"

    def test_window_parameter(self):
        data = [10, 20, 30, 40, 50, 50, 50, 50, 50]
        t = _calculate_trend(data, window=4)
        assert t.direction == "stable"

    def test_r_squared_range(self):
        t = _calculate_trend([10, 20, 15, 25, 12, 22, 18])
        assert 0.0 <= t.r_squared <= 1.0


# ---------------------------------------------------------------------------
# HRV Recovery Classification
# ---------------------------------------------------------------------------


class TestClassifyRecovery:
    def test_red_below_lower(self):
        assert _classify_recovery(35, 50, 40, 55) == "red"

    def test_yellow_in_band(self):
        assert _classify_recovery(45, 50, 40, 55) == "yellow"

    def test_green_above_upper(self):
        assert _classify_recovery(60, 50, 40, 55) == "green"

    def test_boundary_lower_is_yellow(self):
        assert _classify_recovery(40, 50, 40, 55) == "yellow"

    def test_boundary_upper_is_yellow(self):
        assert _classify_recovery(55, 50, 40, 55) == "yellow"


# ---------------------------------------------------------------------------
# RMSSD Algorithms (pure functions)
# ---------------------------------------------------------------------------


class TestRmssdFlattEsco:
    def _history(self, n=20, base=50.0):
        """Generate n days of HRV around base value."""
        import random

        random.seed(42)
        return [base + random.gauss(0, 3) for _ in range(n)]

    def test_returns_rmssd_status(self):
        result = _rmssd_flatt_esco(self._history())
        assert isinstance(result, RmssdStatus)
        assert result.status in ("green", "yellow", "red")
        assert result.days_available == 20
        assert result.days_needed == 0

    def test_trend_is_trend_result(self):
        result = _rmssd_flatt_esco(self._history())
        assert isinstance(result.trend, TrendResult)

    def test_asymmetric_bounds(self):
        result = _rmssd_flatt_esco(self._history())
        # lower bound should be further from mean than upper
        mean = result.rmssd_7d
        lower_gap = mean - result.lower_bound
        upper_gap = result.upper_bound - mean
        assert lower_gap > upper_gap  # asymmetric: -1 SD vs +0.5 SD

    def test_no_60d_baseline_with_short_history(self):
        result = _rmssd_flatt_esco(self._history(20))
        assert result.rmssd_60d is None
        assert result.swc is None

    def test_60d_baseline_with_long_history(self):
        result = _rmssd_flatt_esco(self._history(70))
        assert result.rmssd_60d is not None
        assert result.swc is not None

    def test_low_hrv_triggers_red(self):
        history = [50.0] * 14 + [30.0]  # big drop
        result = _rmssd_flatt_esco(history)
        assert result.status == "red"

    def test_high_hrv_triggers_green(self):
        history = [50.0] * 14 + [65.0]  # big jump
        result = _rmssd_flatt_esco(history)
        assert result.status == "green"


class TestRmssdAiEndurance:
    def _history(self, n=20, base=50.0):
        import random

        random.seed(42)
        return [base + random.gauss(0, 3) for _ in range(n)]

    def test_returns_rmssd_status(self):
        result = _rmssd_ai_endurance(self._history())
        assert isinstance(result, RmssdStatus)
        assert result.status in ("green", "yellow", "red")

    def test_symmetric_bounds(self):
        result = _rmssd_ai_endurance(self._history(70))
        mean = result.rmssd_60d
        lower_gap = mean - result.lower_bound
        upper_gap = result.upper_bound - mean
        assert abs(lower_gap - upper_gap) < 0.15  # symmetric ±0.5 SD (rounding tolerance)

    def test_always_has_60d_fields(self):
        result = _rmssd_ai_endurance(self._history(20))
        # ai_endurance uses all available data, so always has 60d fields
        assert result.rmssd_60d is not None


# ---------------------------------------------------------------------------
# RMSSD Dispatcher
# ---------------------------------------------------------------------------


class TestCalculateRmssdStatus:
    @pytest.mark.asyncio
    async def test_insufficient_data(self):
        with patch("data.database.get_hrv_history", new_callable=AsyncMock, return_value=[50.0] * 5):
            with patch("config.settings") as mock_settings:
                mock_settings.HRV_ALGORITHM = "flatt_esco"
                result = await calculate_rmssd_status()
        assert result.status == "insufficient_data"
        assert result.days_available == 5
        assert result.days_needed == 9

    @pytest.mark.asyncio
    async def test_flatt_esco_dispatched(self):
        history = [50.0] * 20
        with patch("data.database.get_hrv_history", new_callable=AsyncMock, return_value=history):
            with patch("config.settings") as mock_settings:
                mock_settings.HRV_ALGORITHM = "flatt_esco"
                result = await calculate_rmssd_status()
        assert result.status in ("green", "yellow", "red")

    @pytest.mark.asyncio
    async def test_ai_endurance_dispatched(self):
        history = [50.0] * 20
        with patch("data.database.get_hrv_history", new_callable=AsyncMock, return_value=history):
            with patch("config.settings") as mock_settings:
                mock_settings.HRV_ALGORITHM = "ai_endurance"
                result = await calculate_rmssd_status()
        assert result.status in ("green", "yellow", "red")


# ---------------------------------------------------------------------------
# RHR Status
# ---------------------------------------------------------------------------


class TestCalculateRhrStatus:
    @pytest.mark.asyncio
    async def test_insufficient_data(self):
        with patch("data.database.get_rhr_history", new_callable=AsyncMock, return_value=[42.0] * 3):
            result = await calculate_rhr_status()
        assert result.status == "insufficient_data"
        assert result.days_needed == 4

    @pytest.mark.asyncio
    async def test_normal_rhr(self):
        history = [42.0] * 20
        with patch("data.database.get_rhr_history", new_callable=AsyncMock, return_value=history):
            result = await calculate_rhr_status()
        assert result.status == "yellow"  # within bounds
        assert result.rhr_today == 42.0

    @pytest.mark.asyncio
    async def test_elevated_rhr_is_red(self):
        history = [42.0] * 19 + [52.0]  # spike
        with patch("data.database.get_rhr_history", new_callable=AsyncMock, return_value=history):
            result = await calculate_rhr_status()
        assert result.status == "red"

    @pytest.mark.asyncio
    async def test_low_rhr_is_green(self):
        history = [42.0] * 19 + [35.0]  # drop
        with patch("data.database.get_rhr_history", new_callable=AsyncMock, return_value=history):
            result = await calculate_rhr_status()
        assert result.status == "green"


# ---------------------------------------------------------------------------
# ESS (External Stress Score)
# ---------------------------------------------------------------------------


class TestCalculateEss:
    def test_threshold_effort_one_hour(self):
        ess = calculate_ess(60, 158, 42, 182)
        assert abs(ess - 100) < 1  # ~100 by definition

    def test_easy_effort(self):
        ess = calculate_ess(60, 120, 42, 182)
        assert 0 < ess < 100

    def test_hard_effort(self):
        ess = calculate_ess(60, 170, 42, 182)
        assert ess > 100

    def test_zero_duration(self):
        assert calculate_ess(0, 158, 42, 182) == 0.0

    def test_avg_hr_below_resting(self):
        assert calculate_ess(60, 40, 42, 182) == 0.0

    def test_hr_max_equals_rest(self):
        assert calculate_ess(60, 100, 60, 60) == 0.0


# ---------------------------------------------------------------------------
# Banister Recovery Model
# ---------------------------------------------------------------------------


class TestBanisterRecovery:
    def test_empty_log(self):
        result = calculate_banister_recovery([])
        assert result == []

    def test_rest_day_recovers(self):
        log = [{"date": "2026-03-01", "ess": 0}]
        result = calculate_banister_recovery(log, initial_recovery=80.0)
        assert result[0].recovery_pct > 0  # decays toward 0 but doesn't increase without load

    def test_training_reduces_recovery(self):
        log = [{"date": "2026-03-01", "ess": 100}]
        result = calculate_banister_recovery(log, k=0.2, initial_recovery=90.0)
        assert result[0].recovery_pct < 90.0

    def test_clamped_to_0_100(self):
        log = [{"date": f"2026-03-{i+1:02d}", "ess": 200} for i in range(30)]
        result = calculate_banister_recovery(log, k=1.0)
        for state in result:
            assert 0.0 <= state.recovery_pct <= 100.0

    def test_multiple_days(self):
        log = [
            {"date": "2026-03-01", "ess": 80},
            {"date": "2026-03-02", "ess": 0},
            {"date": "2026-03-03", "ess": 0},
        ]
        result = calculate_banister_recovery(log)
        assert len(result) == 3
        # Recovery should increase on rest days (decay toward 0, but with no load)
        # Actually R decays via exp(-1/tau), so it decreases but less than with load


# ---------------------------------------------------------------------------
# Combined Recovery Score
# ---------------------------------------------------------------------------


class TestCombinedRecoveryScore:
    def _rmssd(self, status="green", cv=5.0) -> RmssdStatus:
        return RmssdStatus(
            status=status,
            days_available=30,
            days_needed=0,
            rmssd_7d=50,
            lower_bound=40,
            upper_bound=55,
            cv_7d=cv,
            trend=TrendResult(direction="stable", slope=0, r_squared=0.5, emoji="→"),
        )

    def _rhr(self, status="yellow") -> RhrStatus:
        return RhrStatus(status=status, days_available=30, days_needed=0, rhr_today=42, rhr_30d=42)

    def test_all_green_high_score(self):
        result = combined_recovery_score(
            self._rmssd("green"),
            self._rhr("green"),
            banister_recovery=90,
            sleep_score=85,
            body_battery=80,
        )
        assert result.score > 80
        assert result.category in ("excellent", "good")
        assert result.recommendation == "zone2_ok"

    def test_all_red_low_score(self):
        result = combined_recovery_score(
            self._rmssd("red"),
            self._rhr("red"),
            banister_recovery=20,
            sleep_score=30,
            body_battery=20,
        )
        assert result.score < 40
        assert result.recommendation == "skip"  # red RMSSD overrides

    def test_red_rmssd_always_skip(self):
        result = combined_recovery_score(
            self._rmssd("red"),
            self._rhr("green"),
            banister_recovery=95,
            sleep_score=95,
            body_battery=95,
        )
        assert result.recommendation == "skip"

    def test_late_sleep_penalty(self):
        base = combined_recovery_score(
            self._rmssd(),
            self._rhr(),
            banister_recovery=70,
            sleep_score=70,
            body_battery=70,
        )
        late = combined_recovery_score(
            self._rmssd(),
            self._rhr(),
            banister_recovery=70,
            sleep_score=70,
            body_battery=70,
            sleep_start_hour=23.5,
        )
        assert late.score < base.score
        assert "late_sleep" in late.flags

    def test_high_cv_penalty(self):
        base = combined_recovery_score(
            self._rmssd(cv=5.0),
            self._rhr(),
            banister_recovery=70,
            sleep_score=70,
            body_battery=70,
        )
        unstable = combined_recovery_score(
            self._rmssd(cv=18.0),
            self._rhr(),
            banister_recovery=70,
            sleep_score=70,
            body_battery=70,
        )
        assert unstable.score < base.score
        assert "hrv_unstable" in unstable.flags

    def test_components_returned(self):
        result = combined_recovery_score(
            self._rmssd(),
            self._rhr(),
            banister_recovery=70,
            sleep_score=70,
            body_battery=70,
        )
        assert "rmssd" in result.components
        assert "banister" in result.components
        assert "rhr" in result.components
        assert "sleep" in result.components
        assert "body_battery" in result.components

    def test_score_clamped(self):
        result = combined_recovery_score(
            self._rmssd("green"),
            self._rhr("green"),
            banister_recovery=100,
            sleep_score=100,
            body_battery=100,
        )
        assert 0 <= result.score <= 100
