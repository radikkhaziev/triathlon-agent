from datetime import date, datetime, timezone

from data.db import Wellness
from data.intervals.dto import WellnessDTO


def _make_wellness(
    *,
    dt: date = date(2026, 3, 15),
    sleep_score: float = 85,
    sleep_secs: int = 28800,
    avg_sleeping_hr: float = 52,
    hrv: float = 55,
    resting_hr: int = 42,
    updated: datetime | None = None,
) -> WellnessDTO:
    return WellnessDTO(
        id=str(dt),
        sleep_score=sleep_score,
        sleep_secs=sleep_secs,
        avg_sleeping_hr=avg_sleeping_hr,
        hrv=hrv,
        resting_hr=resting_hr,
        updated=updated or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Wellness CRUD
# ---------------------------------------------------------------------------


class TestSaveWellness:
    async def test_insert_new_row(self):
        w = _make_wellness()
        result = Wellness.save(1, wellness=w)

        assert result.is_new is True
        assert result.is_changed is True
        assert result.row.date == "2026-03-15"
        assert result.row.sleep_score == 85
        assert result.row.sleep_secs == 28800

    async def test_upsert_updates_existing(self):
        dt = date(2026, 3, 15)
        ts1 = datetime(2026, 3, 15, 6, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 3, 15, 7, 0, tzinfo=timezone.utc)
        Wellness.save(1, wellness=_make_wellness(sleep_score=70, updated=ts1))
        result = Wellness.save(1, wellness=_make_wellness(sleep_score=90, updated=ts2))

        assert result.is_changed is True
        assert result.row.sleep_score == 90

        fetched = await Wellness.get(1, dt)
        assert fetched is not None
        assert fetched.sleep_score == 90


class TestGetWellness:
    async def test_returns_row(self):
        Wellness.save(1, wellness=_make_wellness())
        result = await Wellness.get(1, date(2026, 3, 15))

        assert result is not None
        assert result.sleep_score == 85

    async def test_returns_none_when_not_found(self):
        result = await Wellness.get(1, date(2099, 1, 1))
        assert result is None
