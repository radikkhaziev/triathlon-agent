from datetime import date, datetime, timezone

from data.db import User, Wellness, get_session
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


async def _add_row(user_id: int, dt: str, **fields) -> None:
    """Insert a Wellness row directly. The helpers below are read-only ORM
    accessors, so we bypass the WellnessDTO upsert path and write the model
    fields the test cares about straight in."""
    async with get_session() as session:
        session.add(Wellness(user_id=user_id, date=dt, **fields))
        await session.commit()


async def _seed_user(user_id: int) -> None:
    """conftest seeds user 1; a second tenant is needed for scoping tests
    (Wellness has an FK on users.id)."""
    async with get_session() as session:
        session.add(User(id=user_id, chat_id=f"u{user_id}", role="athlete"))
        await session.commit()


# ---------------------------------------------------------------------------
# get_latest_weight / get_latest_vo2max — «last known value» accessors
# (used by api/routers/wellness.py Body card — weight + VO₂max sync
# sporadically, so the current row is often null).
# ---------------------------------------------------------------------------


class TestGetLatestWeight:
    async def test_returns_none_when_no_rows(self):
        assert await Wellness.get_latest_weight(1) is None

    async def test_returns_none_when_all_weights_null(self):
        await _add_row(1, "2026-03-10", weight=None)
        await _add_row(1, "2026-03-11", weight=None)
        assert await Wellness.get_latest_weight(1) is None

    async def test_returns_most_recent_value(self):
        await _add_row(1, "2026-03-10", weight=71.2)
        await _add_row(1, "2026-03-12", weight=70.5)
        assert await Wellness.get_latest_weight(1) == 70.5

    async def test_skips_null_on_latest_date(self):
        """The newest row often has weight=None (no weigh-in that day) — the
        helper must fall back to the most recent NON-null value."""
        await _add_row(1, "2026-03-10", weight=72.0)
        await _add_row(1, "2026-03-15", weight=None)
        assert await Wellness.get_latest_weight(1) == 72.0

    async def test_scoped_per_user(self):
        await _seed_user(2)
        await _add_row(1, "2026-03-12", weight=70.0)
        await _add_row(2, "2026-03-14", weight=88.0)
        assert await Wellness.get_latest_weight(1) == 70.0


class TestGetLatestVo2max:
    async def test_returns_none_when_no_rows(self):
        assert await Wellness.get_latest_vo2max(1) is None

    async def test_returns_most_recent_value(self):
        await _add_row(1, "2026-03-10", vo2max=52.0)
        await _add_row(1, "2026-03-14", vo2max=53.0)
        assert await Wellness.get_latest_vo2max(1) == 53.0

    async def test_skips_null_on_latest_date(self):
        await _add_row(1, "2026-03-10", vo2max=51.0)
        await _add_row(1, "2026-03-15", vo2max=None)
        assert await Wellness.get_latest_vo2max(1) == 51.0

    async def test_scoped_per_user(self):
        await _seed_user(2)
        await _add_row(1, "2026-03-12", vo2max=50.0)
        await _add_row(2, "2026-03-14", vo2max=60.0)
        assert await Wellness.get_latest_vo2max(1) == 50.0


# ---------------------------------------------------------------------------
# get_sleep_series — fixed-length last-N-nights window (Sleep card bar-strip)
# ---------------------------------------------------------------------------


class TestGetSleepSeries:
    async def test_length_always_equals_days(self):
        """Window length is exactly `days` even with no rows — missing days
        are None so the frontend bar index stays aligned to the calendar."""
        series = await Wellness.get_sleep_series(1, "2026-03-15", 7)
        assert series == [None] * 7

    async def test_chronological_oldest_first_target_last(self):
        await _add_row(1, "2026-03-13", sleep_score=70.0)
        await _add_row(1, "2026-03-14", sleep_score=80.0)
        await _add_row(1, "2026-03-15", sleep_score=90.0)
        series = await Wellness.get_sleep_series(1, "2026-03-15", 3)
        assert series == [70.0, 80.0, 90.0]

    async def test_missing_day_is_none_at_correct_index(self):
        await _add_row(1, "2026-03-13", sleep_score=70.0)
        # 2026-03-14 intentionally absent — a sync gap.
        await _add_row(1, "2026-03-15", sleep_score=90.0)
        series = await Wellness.get_sleep_series(1, "2026-03-15", 3)
        assert series == [70.0, None, 90.0]

    async def test_window_excludes_rows_outside_range(self):
        await _add_row(1, "2026-03-12", sleep_score=60.0)  # before the 2-day window
        await _add_row(1, "2026-03-14", sleep_score=80.0)
        await _add_row(1, "2026-03-15", sleep_score=90.0)
        series = await Wellness.get_sleep_series(1, "2026-03-15", 2)
        assert series == [80.0, 90.0]

    async def test_scoped_per_user(self):
        await _seed_user(2)
        await _add_row(1, "2026-03-15", sleep_score=88.0)
        await _add_row(2, "2026-03-15", sleep_score=40.0)
        series = await Wellness.get_sleep_series(1, "2026-03-15", 1)
        assert series == [88.0]
