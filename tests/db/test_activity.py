from datetime import date, timedelta

import pytest  # noqa

from data.db import Activity
from data.intervals.dto import ActivityDTO


def _make_activity(
    *,
    id: str = "i100",
    dt: date = date(2026, 3, 15),
    type: str = "Run",
    load: float | None = 80.0,
    moving_time: int = 3600,
    average_hr: float | None = 145.0,
) -> ActivityDTO:
    return ActivityDTO(
        id=id,
        start_date_local=dt,
        type=type,
        icu_training_load=load,
        moving_time=moving_time,
        average_hr=average_hr,
    )


# ---------------------------------------------------------------------------
# Activities CRUD
# ---------------------------------------------------------------------------


class TestSaveActivities:
    async def test_insert_with_average_hr(self):
        act = _make_activity(average_hr=145.0)
        new_ids = await Activity.save_bulk(1, activities=[act])
        assert "i100" in new_ids

        rows = Activity.get_for_banister(user_id=1, days=90, as_of=date(2026, 3, 15))
        assert len(rows) == 1
        assert rows[0].average_hr == 145.0

    async def test_upsert_updates_average_hr(self):
        dt = date(2026, 3, 15)
        await Activity.save_bulk(1, activities=[_make_activity(id="i200", dt=dt, average_hr=130.0)])
        await Activity.save_bulk(1, activities=[_make_activity(id="i200", dt=dt, average_hr=142.0)])

        rows = Activity.get_for_banister(user_id=1, days=90, as_of=dt)
        assert len(rows) == 1
        assert rows[0].average_hr == 142.0

    @pytest.mark.real_db
    @pytest.mark.skip(reason="Core setup commented out; i300 never created — needs rewrite")
    async def test_none_average_hr_stored(self):
        """Activities without HR (e.g. pool swim) should still be saved."""
        # act = _make_activity(id="i300", average_hr=None)
        # new_ids = await Activity.save_bulk(1, activities=[act])
        # assert "i300" in new_ids

        # # Should NOT appear in banister query (filters average_hr IS NOT NULL)
        # rows = Activity.get_for_banister(user_id=1, days=90, as_of=date(2026, 3, 15))
        # assert all(r.id != "i300" for r in rows)

        # But should appear in CTL query (filters icu_training_load IS NOT NULL)
        ctl_rows = Activity.get_for_ctl(user_id=1, days=90)
        assert any(r.id == "i300" for r in ctl_rows)


class TestGetActivitiesForBanister:
    async def test_filters_by_date_range(self):
        ref = date(2026, 3, 15)
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(id="i401", dt=ref, average_hr=140.0),
                _make_activity(id="i402", dt=ref - timedelta(days=100), average_hr=140.0),
            ],
        )

        rows = Activity.get_for_banister(user_id=1, days=90, as_of=ref)
        ids = {r.id for r in rows}
        assert "i401" in ids
        assert "i402" not in ids  # outside 90-day window

    async def test_excludes_zero_hr(self):
        dt = date(2026, 3, 15)
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(id="i501", dt=dt, average_hr=0.0),
                _make_activity(id="i502", dt=dt, average_hr=140.0),
            ],
        )

        rows = Activity.get_for_banister(user_id=1, days=90, as_of=dt)
        ids = {r.id for r in rows}
        assert "i501" not in ids
        assert "i502" in ids

    async def test_ordered_oldest_first(self):
        ref = date(2026, 3, 15)
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(id="i601", dt=ref, average_hr=140.0),
                _make_activity(id="i602", dt=ref - timedelta(days=5), average_hr=140.0),
                _make_activity(id="i603", dt=ref - timedelta(days=10), average_hr=140.0),
            ],
        )

        rows = Activity.get_for_banister(user_id=1, days=90, as_of=ref)
        dates = [r.start_date_local for r in rows]
        assert dates == sorted(dates)
