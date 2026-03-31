"""Tests for Activity.save_bulk new API (user: int | UserDTO, xmax-based new-ID detection).

These tests cover the refactored save_bulk signature and the PostgreSQL xmax trick
for detecting newly inserted vs. updated rows.
"""

from datetime import date

from data.db import Activity
from data.db.user import UserDTO
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


def _user_dto(*, id: int = 1) -> UserDTO:
    return UserDTO(id=id, chat_id="111", username="tester", athlete_id="i001", api_key="key1")


# ---------------------------------------------------------------------------
# save_bulk — new signature: user: int | UserDTO
# ---------------------------------------------------------------------------


class TestSaveBulkNewSignature:
    """save_bulk accepts user as int or UserDTO (new API)."""

    async def test_insert_with_int_user_id(self):
        """save_bulk accepts plain int as user argument."""
        act = _make_activity(id="j100")
        result = await Activity.save_bulk(1, activities=[act])
        assert "j100" in result

    async def test_insert_with_user_dto(self):
        """save_bulk accepts UserDTO as user argument."""
        user = _user_dto(id=1)
        act = _make_activity(id="j101")
        result = await Activity.save_bulk(user, activities=[act])
        assert "j101" in result

    async def test_returns_empty_list_for_no_activities(self):
        """Empty activities list → returns []."""
        result = await Activity.save_bulk(1, activities=[])
        assert result == []

    async def test_user_id_stored_from_dto(self):
        """user_id column is taken from UserDTO.id, not hardcoded."""
        user = _user_dto(id=1)
        act = _make_activity(id="j102")
        await Activity.save_bulk(user, activities=[act])

        rows = Activity.get_for_banister(user_id=1, days=90, as_of=date(2026, 3, 15))
        assert any(r.id == "j102" for r in rows)


# ---------------------------------------------------------------------------
# save_bulk — xmax trick: returns only newly inserted IDs
# ---------------------------------------------------------------------------


class TestSaveBulkXmaxNewIds:
    """save_bulk returns IDs of newly inserted rows only (xmax == 0).

    PostgreSQL sets xmax=0 for new inserts; for updated rows xmax holds the
    transaction ID of the last UPDATE. We exploit this to identify net-new activities.
    """

    async def test_new_insert_returns_id(self):
        """First insert of an activity ID appears in the returned list."""
        act = _make_activity(id="j200")
        new_ids = await Activity.save_bulk(1, activities=[act])
        assert "j200" in new_ids

    async def test_upsert_does_not_return_updated_id(self):
        """Second upsert of the same ID does NOT appear in the returned list."""
        act = _make_activity(id="j201")
        # First insert — should be in result
        first = await Activity.save_bulk(1, activities=[act])
        assert "j201" in first

        # Second call (upsert) — same ID, different data
        updated_act = _make_activity(id="j201", average_hr=155.0)
        second = await Activity.save_bulk(1, activities=[updated_act])
        assert "j201" not in second

    async def test_mixed_new_and_existing_ids(self):
        """Returns only the truly new IDs, not the updated ones."""
        existing = _make_activity(id="j210")
        await Activity.save_bulk(1, activities=[existing])

        new_act = _make_activity(id="j211")
        result = await Activity.save_bulk(1, activities=[existing, new_act])

        assert "j211" in result
        assert "j210" not in result

    async def test_returns_list_not_count(self):
        """Return type is list[str] (activity IDs), not a count integer."""
        act = _make_activity(id="j220")
        result = await Activity.save_bulk(1, activities=[act])
        assert isinstance(result, list)
        assert all(isinstance(i, str) for i in result)

    async def test_multiple_new_activities_all_returned(self):
        """All IDs from a batch of new inserts are returned."""
        activities = [_make_activity(id=f"j23{i}") for i in range(3)]
        result = await Activity.save_bulk(1, activities=activities)
        assert set(result) == {"j230", "j231", "j232"}


# ---------------------------------------------------------------------------
# save_bulk — sync context (used by actor_fetch_user_activities via @dual DualMethod)
# ---------------------------------------------------------------------------


class TestSaveBulkSyncContext:
    """Activity.save_bulk works in sync context (no event loop) via DualMethod dispatch."""

    def test_sync_insert_saves_data(self):
        """Sync call inserts a new activity successfully."""
        act = _make_activity(id="j300")
        result = Activity.save_bulk(1, activities=[act])
        assert isinstance(result, list)
        rows = Activity.get_for_banister(user_id=1, days=90, as_of=date(2026, 3, 15))
        assert any(r.id == "j300" for r in rows)

    def test_sync_upsert_does_not_return_existing_id(self):
        """Second sync call with same ID → ID not in result."""
        act = _make_activity(id="j301")
        Activity.save_bulk(1, activities=[act])
        second = Activity.save_bulk(1, activities=[act])
        assert "j301" not in second

    def test_sync_returns_empty_for_no_activities(self):
        """Empty list → returns []."""
        result = Activity.save_bulk(1, activities=[])
        assert result == []

    def test_sync_accepts_user_dto(self):
        """save_bulk accepts UserDTO in sync context."""
        user = _user_dto(id=1)
        act = _make_activity(id="j302")
        result = Activity.save_bulk(user, activities=[act])
        assert isinstance(result, list)
        rows = Activity.get_for_banister(user_id=1, days=90, as_of=date(2026, 3, 15))
        assert any(r.id == "j302" for r in rows)


# ---------------------------------------------------------------------------
# fit_file_path — new nullable field on Activity
# ---------------------------------------------------------------------------


class TestActivityFitFilePath:
    """Activity.fit_file_path field is nullable and can be updated."""

    async def test_fit_file_path_is_none_by_default(self):
        """New activity has fit_file_path == None."""
        act = _make_activity(id="j400")
        await Activity.save_bulk(1, activities=[act])

        from data.db.common import get_session

        async with get_session() as session:
            row = await session.get(Activity, "j400")

        assert row is not None
        assert row.fit_file_path is None

    async def test_fit_file_path_can_be_set(self):
        """fit_file_path can be updated to a non-null value."""
        act = _make_activity(id="j401")
        await Activity.save_bulk(1, activities=[act])

        from data.db.common import get_session

        async with get_session() as session:
            row = await session.get(Activity, "j401")
            row.fit_file_path = "static/fit-files/j401.fit"
            await session.commit()

        async with get_session() as session:
            row = await session.get(Activity, "j401")

        assert row.fit_file_path == "static/fit-files/j401.fit"

    async def test_save_bulk_does_not_overwrite_fit_file_path(self):
        """Re-upserting an activity does not clear fit_file_path (it's not in SET clause)."""
        act = _make_activity(id="j402")
        await Activity.save_bulk(1, activities=[act])

        from data.db.common import get_session

        # Manually set fit_file_path
        async with get_session() as session:
            row = await session.get(Activity, "j402")
            row.fit_file_path = "static/fit-files/j402.fit"
            await session.commit()

        # Re-upsert the same activity (e.g. new sync)
        updated_act = _make_activity(id="j402", average_hr=160.0)
        await Activity.save_bulk(1, activities=[updated_act])

        async with get_session() as session:
            row = await session.get(Activity, "j402")

        # fit_file_path should still be set (not cleared by upsert SET clause)
        assert row.fit_file_path == "static/fit-files/j402.fit"


# ---------------------------------------------------------------------------
# get_for_date — @dual decorated method
# ---------------------------------------------------------------------------


class TestGetForDate:
    """Activity.get_for_date returns activities matching a specific date."""

    async def test_returns_activities_for_date(self):
        """get_for_date returns all activities on the given date."""
        dt = date(2026, 3, 20)
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(id="j500", dt=dt),
                _make_activity(id="j501", dt=dt),
            ],
        )
        rows = await Activity.get_for_date(1, dt)
        ids = {r.id for r in rows}
        assert "j500" in ids
        assert "j501" in ids

    async def test_excludes_other_dates(self):
        """get_for_date does not return activities from other dates."""
        dt = date(2026, 3, 21)
        other_dt = date(2026, 3, 22)
        await Activity.save_bulk(
            1,
            activities=[
                _make_activity(id="j510", dt=dt),
                _make_activity(id="j511", dt=other_dt),
            ],
        )
        rows = await Activity.get_for_date(1, dt)
        ids = {r.id for r in rows}
        assert "j510" in ids
        assert "j511" not in ids

    async def test_accepts_date_string(self):
        """get_for_date accepts a date string (ISO format)."""
        dt = date(2026, 3, 23)
        await Activity.save_bulk(1, activities=[_make_activity(id="j520", dt=dt)])
        rows = await Activity.get_for_date(1, "2026-03-23")
        assert any(r.id == "j520" for r in rows)

    async def test_returns_empty_when_no_activities(self):
        """No activities on date → returns empty list."""
        rows = await Activity.get_for_date(1, date(2026, 1, 1))
        assert rows == []

    def test_dual_method_works_in_sync(self):
        """get_for_date is callable in sync context via DualMethod."""
        rows = Activity.get_for_date(1, date(2026, 1, 1))
        assert rows == []
