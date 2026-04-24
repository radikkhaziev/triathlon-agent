"""Unit tests for the chunk-recursive OAuth bootstrap actor.

The actor depends on:
- ``UserBackfillState`` (ORM helpers — first-call init, status guard, cursor advance, finalize).
- ``User.intervals_auth_method`` (deauth guard).
- ``IntervalsSyncClient`` (range fetches).
- ``Activity.save_bulk`` / ``Wellness`` / downstream ``.send`` dispatches.

We mock the external surface and assert behaviour: what .send was called with,
whether the cursor advanced, whether the recursion fires on a non-final chunk,
whether finalize fires on the last chunk, and the EMPTY_INTERVALS path.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from data.db.user import UserDTO

# All tests in this module mock every DB collaborator — they never touch the
# test database. The marker just opts out of ``tests/conftest.py``'s per-test
# engine-patching so we don't pay the DB-truncate round-trip for nothing.
pytestmark = pytest.mark.real_db


def _user() -> UserDTO:
    return UserDTO(id=1, chat_id="111", username="tester", athlete_id="i001")


def _state(
    *,
    status: str = "running",
    cursor_dt: date | None = None,
    oldest_dt: date | None = None,
    newest_dt: date | None = None,
    period_days: int = 365,
    chunks_done: int = 0,
    last_error: str | None = None,
) -> SimpleNamespace:
    today = date.today()
    return SimpleNamespace(
        status=status,
        cursor_dt=cursor_dt or (today - timedelta(days=365)),
        oldest_dt=oldest_dt or (today - timedelta(days=365)),
        newest_dt=newest_dt or (today - timedelta(days=1)),
        period_days=period_days,
        chunks_done=chunks_done,
        last_error=last_error,
    )


def _mock_client_ctx(wellness: list | None = None, activities: list | None = None) -> MagicMock:
    """Context-manager-shaped mock for ``IntervalsSyncClient.for_user(user)``."""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    mock.get_wellness_range.return_value = wellness or []
    mock.get_activities.return_value = activities or []
    return mock


def _mock_db_user(auth_method: str = "oauth"):
    """SQLAlchemy-like User row with just the attribute we guard on."""
    return SimpleNamespace(id=1, intervals_auth_method=auth_method)


@pytest.fixture
def bootstrap_mocks():
    """Patch all external collaborators in ``tasks.actors.bootstrap`` at once.

    Returns a SimpleNamespace exposing the individual mocks so tests can assert
    on specific call arguments without re-opening the ``with`` stack each time.
    """
    # NOTE: we patch ``Activity.save_bulk`` (the method) but NOT the ``Activity`` /
    # ``Wellness`` classes themselves — the finalize step builds SQLAlchemy column
    # comparisons (``Wellness.date >= ...``) and those need the real column
    # descriptors. ``session.execute`` is mocked, so no real query runs.
    with (
        patch("tasks.actors.bootstrap.get_sync_session") as mock_session_cm,
        patch("tasks.actors.bootstrap.UserBackfillState") as mock_state_cls,
        patch("tasks.actors.bootstrap.IntervalsSyncClient") as mock_client_cls,
        patch("tasks.actors.bootstrap.Activity.save_bulk") as mock_save_bulk,
        patch("tasks.actors.bootstrap.process_wellness_analysis_sync") as mock_actor_wellness,
        patch("tasks.actors.bootstrap.actor_update_activity_details") as mock_actor_details,
        patch("tasks.actors.bootstrap.actor_bootstrap_step") as mock_self,
        patch("tasks.actors.bootstrap._actor_send_bootstrap_completion_notification") as mock_notify,
    ):
        session = MagicMock()
        mock_session_cm.return_value.__enter__.return_value = session
        mock_session_cm.return_value.__exit__.return_value = False

        # Default: running state, no row at first call (test overrides as needed)
        mock_state_cls.get.return_value = None
        mock_state_cls.start.return_value = None
        mock_state_cls.advance_cursor.return_value = None
        mock_state_cls.mark_finished.return_value = None
        mock_state_cls.mark_failed.return_value = None

        # session.get(User, ...) → oauth user by default
        session.get.return_value = _mock_db_user("oauth")

        # Default: no rows returned from Intervals
        client = _mock_client_ctx()
        mock_client_cls.for_user.return_value = client

        # Default: no new activities
        mock_save_bulk.return_value = []

        # Count queries for finalize
        session.execute.return_value.scalar_one.return_value = 0

        yield SimpleNamespace(
            session=session,
            state_cls=mock_state_cls,
            client_cls=mock_client_cls,
            client=client,
            save_bulk=mock_save_bulk,
            actor_wellness=mock_actor_wellness,
            actor_details=mock_actor_details,
            actor_self=mock_self,
            actor_notify=mock_notify,
        )


# ---------------------------------------------------------------------------
# First-call initialization
# ---------------------------------------------------------------------------


class TestFirstCallInit:
    def test_creates_state_on_first_call(self, bootstrap_mocks):
        """No existing state → UserBackfillState.start is called and state is used for newest_dt."""
        from tasks.actors.bootstrap import actor_bootstrap_step

        user = _user()
        today = date.today()
        oldest = today - timedelta(days=365)

        # First call: no row, start creates one with newest=today-1
        bootstrap_mocks.state_cls.get.return_value = None
        created = _state(oldest_dt=oldest, newest_dt=today - timedelta(days=1), cursor_dt=oldest)
        bootstrap_mocks.state_cls.start.return_value = created

        actor_bootstrap_step(user.model_dump(), cursor_dt=oldest.isoformat(), period_days=365)

        bootstrap_mocks.state_cls.start.assert_called_once()
        kwargs = bootstrap_mocks.state_cls.start.call_args.kwargs
        assert kwargs["user_id"] == 1
        assert kwargs["period_days"] == 365
        assert kwargs["oldest_dt"] == oldest
        # newest is today-1, set by the actor (not by the caller)
        assert kwargs["newest_dt"] == today - timedelta(days=1)


# ---------------------------------------------------------------------------
# Idempotency — status guard
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_completed_state_early_returns(self, bootstrap_mocks):
        """Calling the actor when state is already completed → no fetch, no advance."""
        from tasks.actors.bootstrap import actor_bootstrap_step

        user = _user()
        oldest = date.today() - timedelta(days=365)
        bootstrap_mocks.state_cls.get.return_value = _state(status="completed", oldest_dt=oldest)

        actor_bootstrap_step(user.model_dump(), cursor_dt=oldest.isoformat())

        bootstrap_mocks.client_cls.for_user.assert_not_called()
        bootstrap_mocks.state_cls.advance_cursor.assert_not_called()
        bootstrap_mocks.actor_self.send.assert_not_called()

    def test_failed_state_early_returns(self, bootstrap_mocks):
        """Failed state — same guard."""
        from tasks.actors.bootstrap import actor_bootstrap_step

        user = _user()
        oldest = date.today() - timedelta(days=365)
        bootstrap_mocks.state_cls.get.return_value = _state(status="failed", oldest_dt=oldest)

        actor_bootstrap_step(user.model_dump(), cursor_dt=oldest.isoformat())

        bootstrap_mocks.client_cls.for_user.assert_not_called()
        bootstrap_mocks.actor_self.send.assert_not_called()


# ---------------------------------------------------------------------------
# Deauth guard
# ---------------------------------------------------------------------------


class TestDeauthGuard:
    def test_revoked_oauth_marks_failed_without_fetch(self, bootstrap_mocks):
        """intervals_auth_method='none' → mark_failed, no HTTP call, no recursion."""
        from tasks.actors.bootstrap import actor_bootstrap_step

        user = _user()
        oldest = date.today() - timedelta(days=365)
        bootstrap_mocks.state_cls.get.return_value = _state(oldest_dt=oldest, cursor_dt=oldest)
        bootstrap_mocks.session.get.return_value = _mock_db_user(auth_method="none")

        actor_bootstrap_step(user.model_dump(), cursor_dt=oldest.isoformat())

        bootstrap_mocks.state_cls.mark_failed.assert_called_once()
        fail_kwargs = bootstrap_mocks.state_cls.mark_failed.call_args.kwargs
        assert fail_kwargs.get("error") or bootstrap_mocks.state_cls.mark_failed.call_args.args
        bootstrap_mocks.client_cls.for_user.assert_not_called()
        bootstrap_mocks.actor_self.send.assert_not_called()


# ---------------------------------------------------------------------------
# Cursor CAS — message-arg vs DB-cursor mismatch (retry after partial commit)
# ---------------------------------------------------------------------------


class TestCursorCAS:
    def test_stale_cursor_arg_reenqueues_from_state(self, bootstrap_mocks):
        """When Dramatiq retries a step whose ``advance_cursor`` already committed,
        the actor must NOT reprocess the stale chunk — it re-enqueues with the
        current ``state.cursor_dt`` so the chain continues exactly once."""
        from tasks.actors.bootstrap import CHUNK_DAYS, actor_bootstrap_step

        user = _user()
        today = date.today()
        oldest = today - timedelta(days=365)
        newest = today - timedelta(days=1)
        stale_arg = oldest  # what Dramatiq re-delivers on retry
        advanced = oldest + timedelta(days=CHUNK_DAYS)  # DB cursor already advanced

        bootstrap_mocks.state_cls.get.return_value = _state(oldest_dt=oldest, newest_dt=newest, cursor_dt=advanced)

        actor_bootstrap_step(user.model_dump(), cursor_dt=stale_arg.isoformat(), period_days=365)

        # No HTTP fetch, no second advance, no duplicate downstream dispatch.
        bootstrap_mocks.client_cls.for_user.assert_not_called()
        bootstrap_mocks.state_cls.advance_cursor.assert_not_called()
        # Re-enqueue exactly once, pointing at the DB cursor.
        bootstrap_mocks.actor_self.send.assert_called_once()
        kwargs = bootstrap_mocks.actor_self.send.call_args.kwargs
        assert kwargs["cursor_dt"] == advanced


# ---------------------------------------------------------------------------
# Middle chunk — advance + recursive send
# ---------------------------------------------------------------------------


class TestMiddleChunk:
    def test_advances_cursor_and_reschedules(self, bootstrap_mocks):
        """Chunk ends before newest_dt → advance_cursor + self.send with next cursor."""
        from tasks.actors.bootstrap import CHUNK_DAYS, actor_bootstrap_step

        user = _user()
        today = date.today()
        oldest = today - timedelta(days=365)
        newest = today - timedelta(days=1)
        cursor = oldest  # first chunk, not the last

        bootstrap_mocks.state_cls.get.return_value = _state(oldest_dt=oldest, newest_dt=newest, cursor_dt=cursor)

        actor_bootstrap_step(user.model_dump(), cursor_dt=cursor.isoformat(), period_days=365)

        expected_chunk_end = cursor + timedelta(days=CHUNK_DAYS - 1)
        expected_next = expected_chunk_end + timedelta(days=1)

        bootstrap_mocks.state_cls.advance_cursor.assert_called_once_with(user_id=1, cursor_dt=expected_next)

        bootstrap_mocks.actor_self.send.assert_called_once()
        send_kwargs = bootstrap_mocks.actor_self.send.call_args.kwargs
        assert send_kwargs["cursor_dt"] == expected_next
        assert send_kwargs["period_days"] == 365
        bootstrap_mocks.actor_notify.send.assert_not_called()

    def test_dispatches_wellness_and_activity_pipelines_per_row(self, bootstrap_mocks):
        """For each fetched wellness/activity row we dispatch the correct downstream actor."""
        from tasks.actors.bootstrap import actor_bootstrap_step

        user = _user()
        today = date.today()
        oldest = today - timedelta(days=365)
        newest = today - timedelta(days=1)

        wellness_rows = [
            SimpleNamespace(id="2025-05-02"),
            SimpleNamespace(id="2025-05-01"),  # out of order — actor must sort chronologically
        ]
        activity_rows = [
            SimpleNamespace(id="a1", source="GARMIN_CONNECT"),
            SimpleNamespace(id="a2", source="STRAVA"),  # filtered out
        ]
        client = _mock_client_ctx(wellness=wellness_rows, activities=activity_rows)
        bootstrap_mocks.client_cls.for_user.return_value = client

        bootstrap_mocks.state_cls.get.return_value = _state(oldest_dt=oldest, newest_dt=newest, cursor_dt=oldest)
        # Activity.save_bulk returns only NEW ids — we simulate one new id "a1"
        bootstrap_mocks.save_bulk.return_value = ["a1"]

        actor_bootstrap_step(user.model_dump(), cursor_dt=oldest.isoformat(), period_days=365)

        # save_bulk received the filtered list (no Strava)
        save_args, save_kwargs = bootstrap_mocks.save_bulk.call_args
        passed_activities = save_kwargs.get("activities") if "activities" in save_kwargs else save_args[1]
        assert [a.id for a in passed_activities] == ["a1"]

        # Wellness processed inline + in chronological order (previously
        # `actor_user_wellness.send` — now direct `process_wellness_analysis_sync`
        # call to fix HRV baseline ordering race, see spec §17).
        calls = bootstrap_mocks.actor_wellness.call_args_list
        assert len(calls) == 2
        # Positional args: (user_dto, wellness_dto)
        assert calls[0].args[1].id == "2025-05-01"
        assert calls[1].args[1].id == "2025-05-02"

        # activity details dispatched only for NEW ids
        bootstrap_mocks.actor_details.send.assert_called_once()
        assert bootstrap_mocks.actor_details.send.call_args.kwargs["activity_id"] == "a1"


# ---------------------------------------------------------------------------
# Last chunk — finalize inline
# ---------------------------------------------------------------------------


class TestLastChunk:
    def test_last_chunk_triggers_finalize_not_self_send(self, bootstrap_mocks):
        """When chunk_end >= newest_dt the actor must NOT self-reschedule,
        instead mark_finished + send the completion notification."""
        from tasks.actors.bootstrap import CHUNK_DAYS, actor_bootstrap_step

        user = _user()
        today = date.today()
        newest = today - timedelta(days=1)
        # Cursor positioned so chunk_end == newest (last chunk exactly)
        cursor = newest - timedelta(days=CHUNK_DAYS - 1)
        oldest = cursor - timedelta(days=CHUNK_DAYS * 3)  # arbitrary past

        bootstrap_mocks.state_cls.get.return_value = _state(
            oldest_dt=oldest, newest_dt=newest, cursor_dt=cursor, period_days=365
        )
        # Non-empty counts so we take the normal (not EMPTY_INTERVALS) branch
        bootstrap_mocks.session.execute.return_value.scalar_one.side_effect = [42, 17]

        actor_bootstrap_step(user.model_dump(), cursor_dt=cursor.isoformat(), period_days=365)

        bootstrap_mocks.actor_self.send.assert_not_called()
        bootstrap_mocks.state_cls.mark_finished.assert_called_once()
        finish_kwargs = bootstrap_mocks.state_cls.mark_finished.call_args.kwargs
        assert finish_kwargs["status"] == "completed"
        assert finish_kwargs["last_error"] is None

        # Completion notification is delayed 60s so the wellness dispatch tail
        # from the last chunk can drain; the actor re-queries counts at dispatch.
        bootstrap_mocks.actor_notify.send_with_options.assert_called_once()
        call = bootstrap_mocks.actor_notify.send_with_options.call_args
        assert call.kwargs["delay"] == 60_000
        n_kwargs = call.kwargs["kwargs"]
        assert n_kwargs["empty_import"] is False
        assert n_kwargs["period_days"] == 365

    def test_empty_import_sentinel(self, bootstrap_mocks):
        """Zero wellness + zero activities → last_error='EMPTY_INTERVALS' + empty_import=True."""
        from tasks.actors.bootstrap import CHUNK_DAYS, actor_bootstrap_step

        user = _user()
        today = date.today()
        newest = today - timedelta(days=1)
        cursor = newest - timedelta(days=CHUNK_DAYS - 1)
        oldest = cursor - timedelta(days=CHUNK_DAYS * 3)

        bootstrap_mocks.state_cls.get.return_value = _state(
            oldest_dt=oldest, newest_dt=newest, cursor_dt=cursor, period_days=365
        )
        bootstrap_mocks.session.execute.return_value.scalar_one.side_effect = [0, 0]

        actor_bootstrap_step(user.model_dump(), cursor_dt=cursor.isoformat(), period_days=365)

        finish_kwargs = bootstrap_mocks.state_cls.mark_finished.call_args.kwargs
        assert finish_kwargs["status"] == "completed"
        assert finish_kwargs["last_error"] == "EMPTY_INTERVALS"

        call = bootstrap_mocks.actor_notify.send_with_options.call_args
        assert call.kwargs["delay"] == 60_000
        n_kwargs = call.kwargs["kwargs"]
        assert n_kwargs["empty_import"] is True


# ---------------------------------------------------------------------------
# Wellness ordering + error resilience (code review fixes)
# ---------------------------------------------------------------------------


class TestWellnessOrderingResilience:
    def test_sort_uses_real_date_not_lexicographic(self, bootstrap_mocks):
        """Sort key is ``date.fromisoformat`` so a future Intervals ID format
        (non-ISO) doesn't silently break chronological order. Today's IDs are
        ISO dates — test still passes via that path; if an ID ever fails to
        parse, ``date.max`` sink pushes it to the end."""
        from tasks.actors.bootstrap import actor_bootstrap_step

        user = _user()
        today = date.today()
        oldest = today - timedelta(days=365)
        newest = today - timedelta(days=1)

        wellness_rows = [
            SimpleNamespace(id="2025-05-03"),
            SimpleNamespace(id="not-a-date"),  # unparseable → sinks to end
            SimpleNamespace(id="2025-05-01"),
            SimpleNamespace(id="2025-05-02"),
        ]
        client = _mock_client_ctx(wellness=wellness_rows)
        bootstrap_mocks.client_cls.for_user.return_value = client
        bootstrap_mocks.state_cls.get.return_value = _state(
            oldest_dt=oldest,
            newest_dt=newest,
            cursor_dt=oldest,
        )

        actor_bootstrap_step(user.model_dump(), cursor_dt=oldest.isoformat(), period_days=365)

        processed_ids = [c.args[1].id for c in bootstrap_mocks.actor_wellness.call_args_list]
        # Parsed dates first in chronological order, unparseable last.
        assert processed_ids == ["2025-05-01", "2025-05-02", "2025-05-03", "not-a-date"]

    def test_per_day_failure_swallowed_and_captured(self, bootstrap_mocks):
        """A single day raising should NOT abort the chunk — cursor still
        advances, other days still process. Sentry captures the exception so
        the silent quality degradation (HRV baseline gap) is observable."""
        from tasks.actors.bootstrap import actor_bootstrap_step

        user = _user()
        today = date.today()
        oldest = today - timedelta(days=365)
        newest = today - timedelta(days=1)

        wellness_rows = [
            SimpleNamespace(id="2025-05-01"),
            SimpleNamespace(id="2025-05-02"),  # this one explodes
            SimpleNamespace(id="2025-05-03"),
        ]
        client = _mock_client_ctx(wellness=wellness_rows)
        bootstrap_mocks.client_cls.for_user.return_value = client
        bootstrap_mocks.state_cls.get.return_value = _state(
            oldest_dt=oldest,
            newest_dt=newest,
            cursor_dt=oldest,
        )

        # Second day raises
        def _side_effect(_user, w):
            if w.id == "2025-05-02":
                raise RuntimeError("synthetic failure")

        bootstrap_mocks.actor_wellness.side_effect = _side_effect

        with patch("tasks.actors.bootstrap.sentry_sdk.capture_exception") as capture:
            actor_bootstrap_step(user.model_dump(), cursor_dt=oldest.isoformat(), period_days=365)

        # All three days attempted (none short-circuits the loop)
        assert bootstrap_mocks.actor_wellness.call_count == 3
        # Cursor still advanced
        bootstrap_mocks.state_cls.advance_cursor.assert_called_once()
        # Sentry captured the one failure
        capture.assert_called_once()
