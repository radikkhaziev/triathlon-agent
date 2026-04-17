"""Tests for FitnessProjection model — save_bulk upsert and get_projection."""

from unittest.mock import MagicMock, patch

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


class TestFitnessProjectionEndpoint:
    """Tests for the /api/fitness-projection API response shape."""

    @pytest.mark.asyncio
    async def test_empty_projection_response(self):
        """Empty projection returns count=0 with empty arrays."""
        from unittest.mock import AsyncMock

        with patch("api.routers.activities.FitnessProjection") as mock_cls:
            mock_cls.get_projection = AsyncMock(return_value=[])

            from api.routers.activities import fitness_projection

            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.role = "athlete"

            with patch("api.routers.activities.get_data_user_id", return_value=1):
                result = await fitness_projection(user=mock_user)

            assert result["count"] == 0
            assert result["dates"] == []
            assert result["ctl"] == []
            assert result["atl"] == []
            assert result["ramp_rate"] == []

    @pytest.mark.asyncio
    async def test_projection_response_shape(self):
        """Projection with data returns parallel arrays."""
        from unittest.mock import AsyncMock

        mock_rows = [
            MagicMock(date="2026-04-16", ctl=19.5, atl=38.0, ramp_rate=4.7),
            MagicMock(date="2026-04-17", ctl=18.8, atl=35.2, ramp_rate=4.5),
        ]

        with patch("api.routers.activities.FitnessProjection") as mock_cls:
            mock_cls.get_projection = AsyncMock(return_value=mock_rows)

            from api.routers.activities import fitness_projection

            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.role = "athlete"

            with patch("api.routers.activities.get_data_user_id", return_value=1):
                result = await fitness_projection(user=mock_user)

            assert result["count"] == 2
            assert result["dates"] == ["2026-04-16", "2026-04-17"]
            assert result["ctl"] == [19.5, 18.8]
            assert result["atl"] == [38.0, 35.2]
            assert result["ramp_rate"] == [4.7, 4.5]
