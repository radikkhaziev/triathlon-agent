"""Tests for RacePlan ORM — save / get_today_for_goal / get_latest_for_race.

Mirrors the mock-based pattern used in ``test_fitness_projection.py`` — these
methods are thin wrappers around ``session.execute`` so we verify the SQL shape
and the user_id scoping that prevents cross-tenant reads.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from data.db.race_plan import RacePlan


SAMPLE_PAYLOAD = {
    "plan": {"warmup": "10 min easy", "legs": []},
    "race": {"id": 7, "name": "Drina Trail"},
    "preliminary": False,
    "model_version": "v0-2026-04-30",
}


class TestSave:
    def test_round_trip_returns_row(self):
        """save adds, commits, returns the row populated with the input fields."""
        mock_session = MagicMock()
        captured: list = []
        mock_session.add = MagicMock(side_effect=lambda r: captured.append(r))

        row = RacePlan.save(
            user_id=1,
            goal_id=42,
            model_version="v0-2026-04-30",
            payload=SAMPLE_PAYLOAD,
            session=mock_session,
        )

        assert mock_session.add.called
        assert mock_session.commit.called
        assert row is captured[0]
        assert row.user_id == 1
        assert row.goal_id == 42
        assert row.model_version == "v0-2026-04-30"
        assert row.payload == SAMPLE_PAYLOAD
        assert row.generated_at is not None

    def test_save_with_null_goal(self):
        """Ad-hoc plans (goal_id=None) must persist — partial unique index allows it."""
        mock_session = MagicMock()
        row = RacePlan.save(
            user_id=1,
            goal_id=None,
            model_version="v0-2026-04-30",
            payload=SAMPLE_PAYLOAD,
            session=mock_session,
        )
        assert row.goal_id is None
        assert mock_session.commit.called


class TestGetTodayForGoal:
    def test_returns_row_when_present(self):
        """get_today_for_goal returns the row that the executed query found."""
        existing = MagicMock(id=99, goal_id=42, user_id=1)
        mock_session = MagicMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = existing
        mock_session.execute.return_value = scalar_result

        out = RacePlan.get_today_for_goal(42, user_id=1, session=mock_session)
        assert out is existing
        mock_session.execute.assert_called_once()

    def test_returns_none_when_absent(self):
        mock_session = MagicMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = scalar_result

        out = RacePlan.get_today_for_goal(42, user_id=1, session=mock_session)
        assert out is None

    def test_query_includes_user_id_scope(self):
        """Defense-in-depth: the WHERE clause must filter by user_id, not just goal_id.

        Mirrors data/db/athlete.py:362-407 — a leaked goal_id would otherwise
        allow cross-tenant reads.
        """
        mock_session = MagicMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = scalar_result

        RacePlan.get_today_for_goal(42, user_id=7, session=mock_session)

        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "race_plans.user_id" in sql
        assert "race_plans.goal_id" in sql
        # Generated SQL embeds the literal bind values
        assert "= 7" in sql
        assert "= 42" in sql

    def test_query_filters_to_utc_day(self):
        """The 'today' filter pins generated_at to the current UTC midnight."""
        mock_session = MagicMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = scalar_result

        RacePlan.get_today_for_goal(42, user_id=1, session=mock_session)

        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        today_iso = datetime.now(timezone.utc).date().isoformat()
        assert today_iso in sql


class TestGetLatestForRace:
    def test_returns_row_when_present(self):
        existing = MagicMock(id=12)
        mock_session = MagicMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = existing
        mock_session.execute.return_value = scalar_result

        out = RacePlan.get_latest_for_race(42, user_id=1, session=mock_session)
        assert out is existing

    def test_query_includes_user_id_scope(self):
        mock_session = MagicMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = scalar_result

        RacePlan.get_latest_for_race(42, user_id=7, session=mock_session)

        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "race_plans.user_id" in sql
        assert "race_plans.goal_id" in sql
        assert "= 7" in sql
        assert "= 42" in sql

    def test_orders_by_generated_at_desc(self):
        mock_session = MagicMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = scalar_result

        RacePlan.get_latest_for_race(42, user_id=1, session=mock_session)

        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "ORDER BY race_plans.generated_at DESC" in sql


class TestGetForUserRecent:
    def test_default_limit_is_10(self):
        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = []

        RacePlan.get_for_user_recent(1, session=mock_session)

        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "LIMIT 10" in sql

    def test_custom_limit_passes_through(self):
        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = []

        RacePlan.get_for_user_recent(1, limit=3, session=mock_session)

        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "LIMIT 3" in sql
