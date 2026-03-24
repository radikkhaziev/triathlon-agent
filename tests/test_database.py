from datetime import date, timedelta

from data.database import (
    get_activities_for_banister,
    get_activities_for_ctl,
    get_wellness,
    save_activities,
    save_wellness,
)
from data.models import Activity, Wellness


def _make_wellness(
    *,
    dt: date = date(2026, 3, 15),
    sleep_score: float = 85,
    sleep_secs: int = 28800,
    avg_sleeping_hr: float = 52,
    hrv: float = 55,
    resting_hr: int = 42,
) -> Wellness:
    return Wellness(
        id=str(dt),
        sleep_score=sleep_score,
        sleep_secs=sleep_secs,
        avg_sleeping_hr=avg_sleeping_hr,
        hrv=hrv,
        resting_hr=resting_hr,
    )


def _make_activity(
    *,
    id: str = "i100",
    dt: date = date(2026, 3, 15),
    type: str = "Run",
    load: float | None = 80.0,
    moving_time: int = 3600,
    average_hr: float | None = 145.0,
) -> Activity:
    return Activity(
        id=id,
        start_date_local=dt,
        type=type,
        icu_training_load=load,
        moving_time=moving_time,
        average_hr=average_hr,
    )


# ---------------------------------------------------------------------------
# Wellness CRUD
# ---------------------------------------------------------------------------


class TestSaveWellness:
    async def test_insert_new_row(self):
        w = _make_wellness()
        row, _ = await save_wellness(date(2026, 3, 15), wellness=w)

        assert row.id == "2026-03-15"
        assert row.sleep_score == 85
        assert row.sleep_secs == 28800

    async def test_upsert_updates_existing(self):
        dt = date(2026, 3, 15)
        await save_wellness(dt, wellness=_make_wellness(sleep_score=70))
        row, _ = await save_wellness(dt, wellness=_make_wellness(sleep_score=90))

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


# ---------------------------------------------------------------------------
# Activities CRUD
# ---------------------------------------------------------------------------


class TestSaveActivities:
    async def test_insert_with_average_hr(self):
        act = _make_activity(average_hr=145.0)
        count = await save_activities([act])
        assert count == 1

        rows = await get_activities_for_banister(days=90, as_of=date(2026, 3, 15))
        assert len(rows) == 1
        assert rows[0].average_hr == 145.0

    async def test_upsert_updates_average_hr(self):
        dt = date(2026, 3, 15)
        await save_activities([_make_activity(id="i200", dt=dt, average_hr=130.0)])
        await save_activities([_make_activity(id="i200", dt=dt, average_hr=142.0)])

        rows = await get_activities_for_banister(days=90, as_of=dt)
        assert len(rows) == 1
        assert rows[0].average_hr == 142.0

    async def test_none_average_hr_stored(self):
        """Activities without HR (e.g. pool swim) should still be saved."""
        act = _make_activity(id="i300", average_hr=None)
        count = await save_activities([act])
        assert count == 1

        # Should NOT appear in banister query (filters average_hr IS NOT NULL)
        rows = await get_activities_for_banister(days=90, as_of=date(2026, 3, 15))
        assert all(r.id != "i300" for r in rows)

        # But should appear in CTL query (filters icu_training_load IS NOT NULL)
        ctl_rows = await get_activities_for_ctl(days=90, as_of=date(2026, 3, 15))
        assert any(r.id == "i300" for r in ctl_rows)


class TestGetActivitiesForBanister:
    async def test_filters_by_date_range(self):
        ref = date(2026, 3, 15)
        await save_activities(
            [
                _make_activity(id="i401", dt=ref, average_hr=140.0),
                _make_activity(id="i402", dt=ref - timedelta(days=100), average_hr=140.0),
            ]
        )

        rows = await get_activities_for_banister(days=90, as_of=ref)
        ids = {r.id for r in rows}
        assert "i401" in ids
        assert "i402" not in ids  # outside 90-day window

    async def test_excludes_zero_hr(self):
        dt = date(2026, 3, 15)
        await save_activities(
            [
                _make_activity(id="i501", dt=dt, average_hr=0.0),
                _make_activity(id="i502", dt=dt, average_hr=140.0),
            ]
        )

        rows = await get_activities_for_banister(days=90, as_of=dt)
        ids = {r.id for r in rows}
        assert "i501" not in ids
        assert "i502" in ids

    async def test_ordered_oldest_first(self):
        ref = date(2026, 3, 15)
        await save_activities(
            [
                _make_activity(id="i601", dt=ref, average_hr=140.0),
                _make_activity(id="i602", dt=ref - timedelta(days=5), average_hr=140.0),
                _make_activity(id="i603", dt=ref - timedelta(days=10), average_hr=140.0),
            ]
        )

        rows = await get_activities_for_banister(days=90, as_of=ref)
        dates = [r.start_date_local for r in rows]
        assert dates == sorted(dates)
