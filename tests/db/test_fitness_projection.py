"""Tests for FitnessProjection model — save_bulk upsert and get_projection."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from data.db.fitness_projection import FitnessProjection

SAMPLE_RECORDS = [
    {"id": "2026-04-16", "ctl": 19.5, "atl": 38.0, "rampRate": 4.7},
    {"id": "2026-04-17", "ctl": 18.8, "atl": 35.2, "rampRate": 4.5},
    {"id": "2026-09-15", "ctl": 0.48, "atl": 0.0, "rampRate": 0.0},
]


class TestSaveBulk:
    def test_returns_zero_for_empty_records(self):
        """Empty records list → no DB interaction, returns 0."""
        mock_session = MagicMock()
        result = FitnessProjection.save_bulk(user_id=1, records=[], session=mock_session)
        assert result == 0
        mock_session.execute.assert_not_called()
        mock_session.commit.assert_not_called()

    def test_returns_record_count(self):
        """Returns the number of records upserted."""
        mock_session = MagicMock()
        result = FitnessProjection.save_bulk(user_id=1, records=SAMPLE_RECORDS, session=mock_session)
        assert result == 3
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    def test_builds_correct_values(self):
        """Verify the INSERT statement contains correct user_id and mapped fields."""
        mock_session = MagicMock()
        FitnessProjection.save_bulk(user_id=42, records=SAMPLE_RECORDS, session=mock_session)

        call_args = mock_session.execute.call_args
        stmt = call_args[0][0]
        # The statement should be an Insert with on_conflict_do_update
        compiled = stmt.compile()
        sql = str(compiled)
        assert "fitness_projection" in sql
        assert "ON CONFLICT" in sql

    def test_maps_rampRate_to_ramp_rate(self):
        """camelCase 'rampRate' from webhook maps to snake_case 'ramp_rate' column."""
        mock_session = MagicMock()
        records = [{"id": "2026-04-16", "ctl": 10.0, "atl": 20.0, "rampRate": 3.14}]
        FitnessProjection.save_bulk(user_id=1, records=records, session=mock_session)
        # If it didn't crash, the mapping worked
        assert mock_session.execute.called

    def test_handles_missing_optional_fields(self):
        """Records without ctl/atl/rampRate should use None."""
        mock_session = MagicMock()
        records = [{"id": "2026-04-16"}]
        result = FitnessProjection.save_bulk(user_id=1, records=records, session=mock_session)
        assert result == 1


class TestGetProjection:
    def test_returns_ordered_by_date(self):
        """Results should be ordered by date ascending."""
        mock_session = MagicMock()
        mock_row1 = MagicMock(date="2026-04-16")
        mock_row2 = MagicMock(date="2026-09-15")
        mock_session.execute.return_value.scalars.return_value.all.return_value = [mock_row1, mock_row2]

        result = FitnessProjection.get_projection(user_id=1, session=mock_session)
        assert len(result) == 2
        assert result[0].date == "2026-04-16"
        assert result[1].date == "2026-09-15"

    def test_returns_empty_list_for_no_data(self):
        """User without projection data gets empty list."""
        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = []

        result = FitnessProjection.get_projection(user_id=999, session=mock_session)
        assert result == []

    @staticmethod
    def _executed_sql(mock_session) -> str:
        return str(mock_session.execute.call_args[0][0].compile(compile_kwargs={"literal_binds": True}))

    def test_no_bounds_emits_no_date_predicate(self):
        """Default call (oldest=newest=None) → only the user_id filter, full series.

        Backward-compat guard: data/race_plan_service.py relies on the unbounded
        behaviour to read future race-day rows.
        """
        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = []

        FitnessProjection.get_projection(user_id=7, session=mock_session)

        sql = self._executed_sql(mock_session)
        assert "fitness_projection.date >=" not in sql
        assert "fitness_projection.date <=" not in sql

    def test_oldest_and_newest_window_the_query(self):
        """oldest/newest add inclusive >= / <= predicates on date."""
        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = []

        FitnessProjection.get_projection(user_id=7, oldest="2026-02-15", newest="2026-05-29", session=mock_session)

        sql = self._executed_sql(mock_session)
        assert "2026-02-15" in sql
        assert "2026-05-29" in sql
        assert "fitness_projection.date >=" in sql
        assert "fitness_projection.date <=" in sql

    def test_only_oldest_applies_lower_bound_only(self):
        """A lone oldest bound emits >= but not <=."""
        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = []

        FitnessProjection.get_projection(user_id=7, oldest="2026-02-15", session=mock_session)

        sql = self._executed_sql(mock_session)
        assert "fitness_projection.date >=" in sql
        assert "fitness_projection.date <=" not in sql


class TestFitnessProjectionEndpoint:
    """Tests for the /api/fitness-projection API response shape."""

    # Endpoint's "today" is pinned so window assertions check literal dates —
    # this makes the tz/offset/max() logic genuinely testable (a UTC-vs-Belgrade
    # regression would change FIXED_TODAY's effect and fail, instead of the test
    # recomputing the same wrong value). 2026-05-16 − 90d == 2026-02-15.
    FIXED_TODAY = date(2026, 5, 16)

    async def _call(self, *, projection_rows, last_planned, days=90):
        """Invoke the endpoint with both DB calls + local_today mocked.

        ``days`` is passed explicitly because calling the endpoint function
        directly (not via the app) would otherwise leave the ``Query`` default
        as a FieldInfo object. Returns ``(result, get_projection mock)``.
        """
        with (
            patch("api.routers.activities.FitnessProjection") as mock_fp,
            patch("api.routers.activities.ScheduledWorkout") as mock_sw,
            patch("api.routers.activities.local_today", return_value=self.FIXED_TODAY),
            patch("api.routers.activities.get_data_user_id", return_value=1),
        ):
            mock_fp.get_projection = AsyncMock(return_value=projection_rows)
            mock_sw.get_last_scheduled_date = AsyncMock(return_value=last_planned)

            from api.routers.activities import fitness_projection

            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.role = "athlete"
            result = await fitness_projection(days=days, user=mock_user)

        return result, mock_fp.get_projection

    @pytest.mark.asyncio
    async def test_empty_projection_response(self):
        """Empty projection returns count=0 with empty arrays."""
        result, _ = await self._call(projection_rows=[], last_planned=None)

        assert result["count"] == 0
        assert result["dates"] == []
        assert result["ctl"] == []
        assert result["atl"] == []
        assert result["ramp_rate"] == []

    @pytest.mark.asyncio
    async def test_projection_response_shape(self):
        """Projection with data returns parallel arrays."""
        mock_rows = [
            MagicMock(date="2026-04-16", ctl=19.5, atl=38.0, ramp_rate=4.7),
            MagicMock(date="2026-04-17", ctl=18.8, atl=35.2, ramp_rate=4.5),
        ]

        result, _ = await self._call(projection_rows=mock_rows, last_planned="2099-01-01")

        assert result["count"] == 2
        assert result["dates"] == ["2026-04-16", "2026-04-17"]
        assert result["ctl"] == [19.5, 18.8]
        assert result["atl"] == [38.0, 35.2]
        assert result["ramp_rate"] == [4.7, 4.5]

    @pytest.mark.asyncio
    async def test_window_lower_bound_defaults_to_today_minus_90d(self):
        """Default days=90 → oldest = today − 90 days (== 2026-02-15)."""
        _, get_proj = await self._call(projection_rows=[], last_planned=None)

        assert get_proj.await_args.kwargs["oldest"] == "2026-02-15"

    @pytest.mark.asyncio
    # Literal expecteds (FIXED_TODAY=2026-05-16 minus N days) so the test can't
    # pass by recomputing the production formula. 1m/3m/6m == the UI toggle.
    @pytest.mark.parametrize(
        ("days", "expected_oldest"),
        [(30, "2026-04-16"), (90, "2026-02-15"), (180, "2025-11-17")],
    )
    async def test_days_param_sets_lower_bound(self, days, expected_oldest):
        """The UI's 1m/3m/6m toggle (30/90/180) drives the oldest bound."""
        _, get_proj = await self._call(projection_rows=[], last_planned=None, days=days)

        assert get_proj.await_args.kwargs["oldest"] == expected_oldest

    @pytest.mark.asyncio
    async def test_upper_bound_is_future_planned_workout(self):
        """A planned workout in the future becomes the upper bound."""
        _, get_proj = await self._call(projection_rows=[], last_planned="2026-06-05")

        assert get_proj.await_args.kwargs["newest"] == "2026-06-05"

    @pytest.mark.asyncio
    async def test_upper_bound_falls_back_to_today_when_no_plan(self):
        """No planned workouts → upper bound clamps to today."""
        _, get_proj = await self._call(projection_rows=[], last_planned=None)

        assert get_proj.await_args.kwargs["newest"] == "2026-05-16"

    @pytest.mark.asyncio
    async def test_upper_bound_clamps_to_today_when_plan_in_past(self):
        """Last planned workout already in the past → still reach at least today."""
        _, get_proj = await self._call(projection_rows=[], last_planned="2026-05-01")

        assert get_proj.await_args.kwargs["newest"] == "2026-05-16"
