from datetime import date, datetime

from data.database import get_daily_metrics, save_daily_metrics
from data.models import SleepData


def _make_sleep(
    *,
    score: int = 85,
    duration: int = 28800,
    start: int | None = 1710972000000,  # milliseconds (Garmin format)
    end: int | None = 1710999600000,
    stress_avg: int | None = 25,
    hrv_avg: int | None = 55,
    heart_rate_avg: int | None = 52,
) -> SleepData:
    return SleepData(
        date=date(2026, 3, 15),
        score=score,
        duration=duration,
        start=start,
        end=end,
        stress_avg=stress_avg,
        hrv_avg=hrv_avg,
        heart_rate_avg=heart_rate_avg,
    )


class TestSaveDailyMetrics:
    async def test_insert_new_row(self):
        sleep = _make_sleep()
        row = await save_daily_metrics(date(2026, 3, 15), sleep_data=sleep)

        assert row.date == "2026-03-15"
        assert row.sleep_score == 85
        assert row.sleep_duration == 28800
        assert row.sleep_stress_avg == 25
        assert isinstance(row.sleep_start, datetime)
        assert isinstance(row.sleep_end, datetime)

    async def test_upsert_updates_existing(self):
        dt = date(2026, 3, 15)
        await save_daily_metrics(dt, sleep_data=_make_sleep(score=70))
        row = await save_daily_metrics(dt, sleep_data=_make_sleep(score=90))

        assert row.sleep_score == 90

        fetched = await get_daily_metrics(dt)
        assert fetched is not None
        assert fetched.sleep_score == 90

    async def test_none_timestamps(self):
        sleep = _make_sleep(start=None, end=None)
        row = await save_daily_metrics(date(2026, 3, 15), sleep_data=sleep)

        assert row.sleep_start is None
        assert row.sleep_end is None

    async def test_none_stress(self):
        sleep = _make_sleep(stress_avg=None)
        row = await save_daily_metrics(date(2026, 3, 15), sleep_data=sleep)

        assert row.sleep_stress_avg is None

    async def test_timestamp_conversion(self):
        """sleep_start/end (millisecond timestamps) are converted to datetime."""
        from datetime import timezone

        ts_ms = 1710972000000
        row = await save_daily_metrics(
            date(2026, 3, 15),
            sleep_data=_make_sleep(start=ts_ms, end=ts_ms + 28800000),
        )

        expected_start = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        expected_end = datetime.fromtimestamp((ts_ms + 28800000) / 1000, tz=timezone.utc)
        assert row.sleep_start == expected_start
        assert row.sleep_end == expected_end


class TestGetDailyMetrics:
    async def test_returns_row(self):
        await save_daily_metrics(date(2026, 3, 15), sleep_data=_make_sleep())
        result = await get_daily_metrics(date(2026, 3, 15))

        assert result is not None
        assert result.sleep_score == 85

    async def test_returns_none_when_not_found(self):
        result = await get_daily_metrics(date(2099, 1, 1))
        assert result is None
