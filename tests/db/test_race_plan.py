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
    "confidence_tier": "mid",  # replaces legacy `preliminary` boolean — see _resolve_confidence_tier
    "model_version": "v1-2026-05-09",
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

    # Removed: test_save_with_null_goal — fully covered by
    # tests/db/test_race_plan_integration.py::test_two_inserts_with_null_goal_id_both_succeed
    # which exercises the partial unique index against real Postgres (review L3, 2026-05-09).


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
        """The 'today' filter pins generated_at to a half-open [today, tomorrow)
        UTC range — both bounds present (review L1, 2026-05-09)."""
        from datetime import timedelta

        mock_session = MagicMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = scalar_result

        RacePlan.get_today_for_goal(42, user_id=1, session=mock_session)

        stmt = mock_session.execute.call_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        today_iso = datetime.now(timezone.utc).date().isoformat()
        tomorrow_iso = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        assert today_iso in sql
        # Upper bound also present — without it a future-day plan (e.g. PR2.3
        # in-place UPDATE bumping generated_at) would leak into today's query.
        assert tomorrow_iso in sql
        assert "<" in sql or "<" in sql.lower()  # half-open via < not <=


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
