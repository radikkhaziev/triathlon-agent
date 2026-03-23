from datetime import date

from data.database import get_wellness, save_wellness
from data.models import Wellness


def _make_wellness(
    *,
    sleep_score: float = 85,
    sleep_secs: int = 28800,
    avg_sleeping_hr: float = 52,
    hrv: float = 55,
    resting_hr: int = 42,
) -> Wellness:
    return Wellness(
        id=str(date(2026, 3, 15)),
        sleep_score=sleep_score,
        sleep_secs=sleep_secs,
        avg_sleeping_hr=avg_sleeping_hr,
        hrv=hrv,
        resting_hr=resting_hr,
    )


class TestSaveWellness:
    async def test_insert_new_row(self):
        w = _make_wellness()
        row = await save_wellness(date(2026, 3, 15), wellness=w)

        assert row.id == "2026-03-15"
        assert row.sleep_score == 85
        assert row.sleep_secs == 28800

    async def test_upsert_updates_existing(self):
        dt = date(2026, 3, 15)
        await save_wellness(dt, wellness=_make_wellness(sleep_score=70))
        row = await save_wellness(dt, wellness=_make_wellness(sleep_score=90))

        assert row.sleep_score == 90

        fetched = await get_wellness(dt)
        assert fetched is not None
        assert fetched.sleep_score == 90


class TestGetWellness:
    async def test_returns_row(self):
        await save_wellness(date(2026, 3, 15), wellness=_make_wellness())
        result = await get_wellness(date(2026, 3, 15))

        assert result is not None
        assert result.sleep_score == 85

    async def test_returns_none_when_not_found(self):
        result = await get_wellness(date(2099, 1, 1))
        assert result is None
