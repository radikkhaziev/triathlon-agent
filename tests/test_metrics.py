from datetime import date

from data.metrics import (
    HR_ZONES,
    calc_hr_tss,
    calc_power_tss,
    calc_swim_tss,
    calculate_readiness,
    update_ctl_atl,
)
from data.models import HRVData, ReadinessLevel, SleepData


class TestCalcHrTss:
    def test_threshold_effort_one_hour(self):
        tss = calc_hr_tss(3600, 158, 42, 182, 158)
        assert tss == 100.0

    def test_easy_effort(self):
        tss = calc_hr_tss(3600, 130, 42, 182, 158)
        assert 0 < tss < 100

    def test_short_duration(self):
        tss = calc_hr_tss(1800, 158, 42, 182, 158)
        assert tss == 50.0

    def test_above_threshold(self):
        tss = calc_hr_tss(3600, 170, 42, 182, 158)
        assert tss > 100


    def test_lthr_equals_resting_hr(self):
        tss = calc_hr_tss(3600, 130, 60, 182, 60)
        assert tss == 0.0

    def test_zero_duration(self):
        tss = calc_hr_tss(0, 158, 42, 182, 158)
        assert tss == 0.0


class TestCalcPowerTss:
    def test_ftp_effort_one_hour(self):
        tss = calc_power_tss(3600, 245, 245)
        assert tss == 100.0

    def test_half_ftp(self):
        tss = calc_power_tss(3600, 122.5, 245)
        assert tss == 25.0

    def test_above_ftp(self):
        tss = calc_power_tss(3600, 270, 245)
        assert tss > 100


    def test_zero_ftp(self):
        tss = calc_power_tss(3600, 200, 0)
        assert tss == 0.0


class TestCalcSwimTss:
    def test_at_css_pace(self):
        distance = 3000
        duration = 3000 / 100 * 98  # exactly CSS pace
        tss = calc_swim_tss(distance, duration, 98)
        assert abs(tss - (duration / 3600) * 100) < 0.2

    def test_zero_distance(self):
        assert calc_swim_tss(0, 1800, 98) == 0.0

    def test_faster_than_css(self):
        tss = calc_swim_tss(3000, 2700, 98)  # faster than CSS
        assert tss > 0


    def test_zero_duration(self):
        tss = calc_swim_tss(3000, 0, 98)
        assert tss == 0.0

    def test_zero_css(self):
        tss = calc_swim_tss(3000, 2940, 0)
        assert tss == 0.0


class TestUpdateCtlAtl:
    def test_empty_history(self):
        ctl, atl, tsb = update_ctl_atl([])
        assert ctl == 0.0
        assert atl == 0.0
        assert tsb == 0.0

    def test_single_day(self):
        ctl, atl, tsb = update_ctl_atl([100.0])
        assert ctl > 0
        assert atl > 0
        assert atl > ctl  # ATL reacts faster

    def test_consistent_training(self):
        history = [80.0] * 60
        ctl, atl, tsb = update_ctl_atl(history)
        assert abs(ctl - 80) < 5
        assert abs(atl - 80) < 2

    def test_rest_after_training(self):
        history = [100.0] * 14 + [0.0] * 7
        ctl, atl, tsb = update_ctl_atl(history)
        assert tsb > 0  # form should be positive after rest


class TestCalculateReadiness:
    def _make_hrv(self, last: float, avg: float) -> HRVData:
        return HRVData(date=date(2026, 1, 1), hrv_weekly_avg=avg, hrv_last_night=last, status="Balanced")

    def _make_sleep(self, score: int) -> SleepData:
        return SleepData(
            date=date(2026, 1, 1), sleep_score=score,
            duration_seconds=28800, deep_sleep_seconds=7200,
            rem_sleep_seconds=5400, awake_seconds=1800,
        )

    def test_perfect_conditions(self):
        score, level = calculate_readiness(
            self._make_hrv(60, 55), self._make_sleep(90), 85, 42, 42,
        )
        assert score >= 80
        assert level == ReadinessLevel.GREEN

    def test_poor_hrv(self):
        score, level = calculate_readiness(
            self._make_hrv(40, 60), self._make_sleep(80), 70, 43, 42,
        )
        assert score < 80

    def test_poor_sleep(self):
        score, level = calculate_readiness(
            self._make_hrv(55, 55), self._make_sleep(40), 70, 42, 42,
        )
        assert score < 80

    def test_red_zone(self):
        score, level = calculate_readiness(
            self._make_hrv(30, 55), self._make_sleep(40), 20, 52, 42,
        )
        assert level == ReadinessLevel.RED

    def test_score_clamped(self):
        score, _ = calculate_readiness(
            self._make_hrv(60, 50), self._make_sleep(95), 90, 40, 42,
        )
        assert 0 <= score <= 100


    def test_zero_hrv_weekly_avg(self):
        hrv = HRVData(date=date(2026, 1, 1), hrv_weekly_avg=0, hrv_last_night=50, status="Unknown")
        sleep = SleepData(
            date=date(2026, 1, 1), sleep_score=80,
            duration_seconds=28800, deep_sleep_seconds=7200,
            rem_sleep_seconds=5400, awake_seconds=1800,
        )
        score, level = calculate_readiness(hrv, sleep, 70, 42, 42)
        assert 0 <= score <= 100


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
