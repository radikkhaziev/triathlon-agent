"""Tests for mcp_server/tools/races.py — suggest_race.

Covers:
- dry_run preview text (create vs update path)
- validation (past date, bad category, bad ISO)
- idempotency: (user_id, category) — new date on same category → update, not create
- recovery fallback: local goal missing but Intervals has event → picks update
- ctl_target pass-through + separate write path
- no Intervals HTTP in dry-run
"""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_MODULE = "mcp_server.tools.races"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future_iso(days: int = 30) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _goal(
    *, id: int = 1, category: str = "RACE_A", event_date: date | None = None, intervals_event_id: int | None = 999
):
    return SimpleNamespace(
        id=id,
        category=category,
        event_name="Existing Race",
        event_date=event_date or (date.today() + timedelta(days=60)),
        intervals_event_id=intervals_event_id,
        disciplines=None,
    )


def _mock_intervals_client(*, create_id: int = 12345, update_id: int = 999, get_events_result: list | None = None):
    """Async-context-manager mock for IntervalsAsyncClient.for_user(...)."""
    client = MagicMock()
    client.create_event = AsyncMock(return_value=SimpleNamespace(id=create_id))
    client.update_event = AsyncMock(return_value=SimpleNamespace(id=update_id))
    client.get_events = AsyncMock(return_value=get_events_result or [])

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=None)

    for_user = MagicMock(return_value=ctx)
    return for_user, client


def _patch_session_with_ctl(current_ctl: float | None = 30.0):
    """Patch get_session to return an async session whose wellness.ctl query resolves to current_ctl."""
    session = MagicMock()
    scalar_result = MagicMock()
    scalar_result.scalar = MagicMock(return_value=current_ctl)
    session.execute = AsyncMock(return_value=scalar_result)
    session.commit = AsyncMock()
    session.get = AsyncMock(return_value=None)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=ctx)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.asyncio
    async def test_invalid_category(self):
        from mcp_server.tools.races import suggest_race

        with patch("mcp_server.tools.races.get_current_user_id", return_value=1):
            out = await suggest_race(name="X", category="RACE_Z", dt=_future_iso(10))
        assert out.startswith("Error:") and "RACE_Z" in out

    @pytest.mark.asyncio
    async def test_invalid_date_format(self):
        from mcp_server.tools.races import suggest_race

        with patch("mcp_server.tools.races.get_current_user_id", return_value=1):
            out = await suggest_race(name="X", category="RACE_A", dt="not-a-date")
        assert out.startswith("Error:") and "ISO" in out

    @pytest.mark.asyncio
    async def test_past_date(self):
        from mcp_server.tools.races import suggest_race

        past = (date.today() - timedelta(days=1)).isoformat()
        with patch("mcp_server.tools.races.get_current_user_id", return_value=1):
            out = await suggest_race(name="X", category="RACE_A", dt=past)
        assert out.startswith("Error:") and "past" in out


# ---------------------------------------------------------------------------
# dry_run — no side effects
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_preview_create(self):
        from mcp_server.tools.races import suggest_race

        for_user, client = _mock_intervals_client()
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=None)),
            patch(f"{_MODULE}.get_session", _patch_session_with_ctl(25.0)),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await suggest_race(
                name="Drina Trail",
                category="RACE_A",
                dt=_future_iso(14),
                sport="TrailRun",
                distance_m=17000,
                ctl_target=55,
                dry_run=True,
            )

        assert "Preview" in out
        assert "Drina Trail" in out
        assert "RACE_A" in out
        assert "TrailRun" in out
        # No HTTP calls in dry-run
        client.create_event.assert_not_called()
        client.update_event.assert_not_called()
        # Still no Intervals fallback check in dry-run
        for_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_preview_update_shows_old_date(self):
        from mcp_server.tools.races import suggest_race

        existing = _goal(event_date=date.today() + timedelta(days=60))
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=existing)),
            patch(f"{_MODULE}.get_session", _patch_session_with_ctl(25.0)),
        ):
            out = await suggest_race(
                name="Drina Trail",
                category="RACE_A",
                dt=_future_iso(14),
                dry_run=True,
            )

        assert "Update" in out
        # Preview is now all-English (MCP tools are language-agnostic, Claude
        # paraphrases). Was: Russian "Было:" before review fix.
        assert "Was:" in out
        assert "Now:" in out


# ---------------------------------------------------------------------------
# Real push — idempotency
# ---------------------------------------------------------------------------


class TestPushIdempotency:
    @pytest.mark.asyncio
    async def test_create_when_no_existing(self):
        from mcp_server.tools.races import suggest_race

        for_user, client = _mock_intervals_client(create_id=777)
        upsert = AsyncMock(return_value=_goal(id=10, intervals_event_id=777))
        set_ctl = AsyncMock()
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=None)),
            patch(f"{_MODULE}.AthleteGoal.upsert_from_intervals", upsert),
            patch(f"{_MODULE}.AthleteGoal.set_ctl_target", set_ctl),
            patch(f"{_MODULE}.get_session", _patch_session_with_ctl(20.0)),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await suggest_race(
                name="Half Marathon",
                category="RACE_B",
                dt=_future_iso(30),
                ctl_target=40,
                dry_run=False,
            )

        client.create_event.assert_awaited_once()
        client.update_event.assert_not_called()
        upsert.assert_awaited_once()
        set_ctl.assert_awaited_once_with(10, 40, user_id=1)
        assert "created" in out
        assert "event/777" in out

    @pytest.mark.asyncio
    async def test_update_when_same_category_new_date(self):
        """(user_id, category) idempotency: same RACE_A, new date → update_event, not create."""
        from mcp_server.tools.races import suggest_race

        existing = _goal(id=5, intervals_event_id=111, event_date=date.today() + timedelta(days=60))
        for_user, client = _mock_intervals_client(update_id=111)
        upsert = AsyncMock(return_value=_goal(id=5, intervals_event_id=111))
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=existing)),
            patch(f"{_MODULE}.AthleteGoal.upsert_from_intervals", upsert),
            patch(f"{_MODULE}.AthleteGoal.set_ctl_target", AsyncMock()),
            patch(f"{_MODULE}.get_session", _patch_session_with_ctl(30.0)),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await suggest_race(
                name="Renamed Race",
                category="RACE_A",
                dt=_future_iso(90),
                dry_run=False,
            )

        client.update_event.assert_awaited_once()
        client.create_event.assert_not_called()
        # Passes the *existing* intervals_event_id for update
        args = client.update_event.await_args
        assert args.args[0] == 111
        assert "updated" in out

    @pytest.mark.asyncio
    async def test_recovery_path_local_missing_intervals_has_event(self):
        """Spec §4.4: local upsert failed on prior attempt → retry finds remote event,
        picks update path instead of creating a duplicate."""
        from mcp_server.tools.races import suggest_race

        remote_event = SimpleNamespace(id=222)
        for_user, client = _mock_intervals_client(update_id=222, get_events_result=[remote_event])
        upsert = AsyncMock(return_value=_goal(id=7, intervals_event_id=222))
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=None)),
            patch(f"{_MODULE}.AthleteGoal.upsert_from_intervals", upsert),
            patch(f"{_MODULE}.AthleteGoal.set_ctl_target", AsyncMock()),
            patch(f"{_MODULE}.get_session", _patch_session_with_ctl(20.0)),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await suggest_race(
                name="Recovered",
                category="RACE_A",
                dt=_future_iso(30),
                dry_run=False,
            )

        # No duplicate create — recovery picked update path
        client.create_event.assert_not_called()
        client.update_event.assert_awaited_once()
        assert client.update_event.await_args.args[0] == 222
        assert "updated" in out

    @pytest.mark.asyncio
    async def test_ctl_target_not_set_when_none(self):
        from mcp_server.tools.races import suggest_race

        for_user, _ = _mock_intervals_client(create_id=1)
        upsert = AsyncMock(return_value=_goal(id=1, intervals_event_id=1))
        set_ctl = AsyncMock()
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=None)),
            patch(f"{_MODULE}.AthleteGoal.upsert_from_intervals", upsert),
            patch(f"{_MODULE}.AthleteGoal.set_ctl_target", set_ctl),
            patch(f"{_MODULE}.get_session", _patch_session_with_ctl(20.0)),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            await suggest_race(
                name="X",
                category="RACE_C",
                dt=_future_iso(10),
                ctl_target=None,
                dry_run=False,
            )

        set_ctl.assert_not_called()


# ---------------------------------------------------------------------------
# delete_race_goal
# ---------------------------------------------------------------------------


def _async_ctx(client: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


class TestDeleteRaceGoal:
    @pytest.mark.asyncio
    async def test_deletes_from_intervals_and_local(self):
        from mcp_server.tools.races import delete_race_goal

        existing = _goal(intervals_event_id=555)
        client = MagicMock()
        client.delete_event = AsyncMock()
        for_user = MagicMock(return_value=_async_ctx(client))

        deactivate = AsyncMock(return_value=existing)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=existing)),
            patch(f"{_MODULE}.AthleteGoal.deactivate_by_category", deactivate),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await delete_race_goal(category="RACE_A")

        client.delete_event.assert_awaited_once_with(555)
        deactivate.assert_awaited_once_with(1, "RACE_A")
        assert out.startswith("🗑️")
        assert "RACE_A" in out

    @pytest.mark.asyncio
    async def test_idempotent_when_nothing_to_delete(self):
        from mcp_server.tools.races import delete_race_goal

        for_user = MagicMock()
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=None)),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await delete_race_goal(category="RACE_B")

        assert "Nothing to delete" in out
        for_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_category(self):
        from mcp_server.tools.races import delete_race_goal

        with patch(f"{_MODULE}.get_current_user_id", return_value=1):
            out = await delete_race_goal(category="RACE_Z")
        assert out.startswith("Error:")

    @pytest.mark.asyncio
    async def test_intervals_404_treated_as_success(self):
        """Event already gone upstream — proceed with local cleanup.

        Uses a real httpx.HTTPStatusError with status 404 — substring matching
        on the exception message was the previous approach and was fragile.
        """
        import httpx

        from mcp_server.tools.races import delete_race_goal

        existing = _goal(intervals_event_id=123)
        fake_404 = httpx.HTTPStatusError(
            "not found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        )
        client = MagicMock()
        client.delete_event = AsyncMock(side_effect=fake_404)
        for_user = MagicMock(return_value=_async_ctx(client))

        deactivate = AsyncMock(return_value=existing)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=existing)),
            patch(f"{_MODULE}.AthleteGoal.deactivate_by_category", deactivate),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await delete_race_goal(category="RACE_A")

        deactivate.assert_awaited_once()
        assert out.startswith("🗑️")

    @pytest.mark.asyncio
    async def test_intervals_500_bails_before_local(self):
        """Non-404 HTTPStatusError → local deactivate MUST NOT run."""
        import httpx

        from mcp_server.tools.races import delete_race_goal

        existing = _goal(intervals_event_id=123)
        fake_500 = httpx.HTTPStatusError(
            "server error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
        client = MagicMock()
        client.delete_event = AsyncMock(side_effect=fake_500)
        for_user = MagicMock(return_value=_async_ctx(client))

        deactivate = AsyncMock()
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=existing)),
            patch(f"{_MODULE}.AthleteGoal.deactivate_by_category", deactivate),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await delete_race_goal(category="RACE_A")

        deactivate.assert_not_called()
        assert "500" in out or "HTTP" in out

    @pytest.mark.asyncio
    async def test_intervals_generic_exception_bails_before_local(self):
        """Any non-HTTPStatusError (network down, OAuth expired) also bails
        before touching the DB — don't cross streams.
        """
        from mcp_server.tools.races import delete_race_goal

        existing = _goal(intervals_event_id=123)
        client = MagicMock()
        client.delete_event = AsyncMock(side_effect=RuntimeError("connection reset"))
        for_user = MagicMock(return_value=_async_ctx(client))

        deactivate = AsyncMock()
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=existing)),
            patch(f"{_MODULE}.AthleteGoal.deactivate_by_category", deactivate),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await delete_race_goal(category="RACE_A")

        deactivate.assert_not_called()
        assert "Error" in out
