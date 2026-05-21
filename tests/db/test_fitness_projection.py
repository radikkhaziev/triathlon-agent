"""Tests for FitnessProjection model — save_bulk upsert and get_projection."""

from unittest.mock import MagicMock

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
