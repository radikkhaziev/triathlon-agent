"""Tests for mcp_server/tools/races.py — suggest_race + generate_race_plan.

Covers:
- dry_run preview text (create vs update path)
- validation (past date, bad category, bad ISO)
- idempotency: (user_id, category) — new date on same category → update, not create
- recovery fallback: local goal missing but Intervals has event → picks update
- ctl_target pass-through + separate write path
- no Intervals HTTP in dry-run
- generate_race_plan: validator refusals (>120d, <6 activities) and dry-run
  happy path with anthropic.AsyncAnthropic patched, plus the no-tool_use-block
  fallback.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
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
            patch(f"{_MODULE}.AthleteGoal.deactivate_by_id", deactivate),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await delete_race_goal(category="RACE_A")

        client.delete_event.assert_awaited_once_with(555)
        deactivate.assert_awaited_once_with(existing.id, 1)
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
            patch(f"{_MODULE}.AthleteGoal.deactivate_by_id", deactivate),
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
            patch(f"{_MODULE}.AthleteGoal.deactivate_by_id", deactivate),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await delete_race_goal(category="RACE_A")

        deactivate.assert_not_called()
        assert "500" in out or "HTTP" in out

    @pytest.mark.asyncio
    async def test_local_deactivate_targets_previewed_goal_id(self):
        """Regression: with multiple active rows per category, deactivation
        must target the same row shown in preview and sent to Intervals —
        not "some active row picked by id DESC".
        """
        from mcp_server.tools.races import delete_race_goal

        previewed = _goal(id=250, intervals_event_id=555)
        client = MagicMock()
        client.delete_event = AsyncMock()
        for_user = MagicMock(return_value=_async_ctx(client))

        deactivate = AsyncMock(return_value=previewed)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=previewed)),
            patch(f"{_MODULE}.AthleteGoal.deactivate_by_id", deactivate),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await delete_race_goal(category="RACE_A")

        client.delete_event.assert_awaited_once_with(555)
        deactivate.assert_awaited_once_with(250, 1)
        assert out.startswith("🗑️")

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
            patch(f"{_MODULE}.AthleteGoal.deactivate_by_id", deactivate),
            patch(f"{_MODULE}.IntervalsAsyncClient.for_user", for_user),
        ):
            out = await delete_race_goal(category="RACE_A")

        deactivate.assert_not_called()
        assert "Error" in out


# ---------------------------------------------------------------------------
# generate_race_plan
# ---------------------------------------------------------------------------


def _race_goal(*, id: int = 7, days_to_race: int = 30, sport_type: str = "Run"):
    """Athlete-goals row stand-in for generate_race_plan's resolver."""
    return SimpleNamespace(
        id=id,
        user_id=1,
        category="RACE_A",
        event_name="Drina Trail",
        event_date=date.today() + timedelta(days=days_to_race),
        sport_type=sport_type,
        disciplines=None,
        ctl_target=55,
    )


def _activity(idx: int, *, minutes: int = 75, hr: float = 145.0):
    """Minimal Activity duck-type for _summarize_activities."""
    return SimpleNamespace(
        type="Run",
        moving_time=minutes * 60,
        icu_training_load=70.0,
        average_hr=hr,
        start_date_local=(date.today() - timedelta(days=idx)).isoformat(),
        is_race=False,
    )


def _patch_session_for_plan():
    """get_session() patch that yields a session with no wellness row."""
    session = MagicMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=scalar_result)
    session.get = AsyncMock(return_value=None)
    session.commit = AsyncMock()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=ctx), session


def _valid_plan_input() -> dict:
    """Realistic submit_race_plan tool_input that should pass validator."""
    return {
        "warmup": "10 min easy + 4×30s strides.",
        "legs": [
            {
                "leg": "run",
                "distance": "21.1 km",
                "pacing": {"low": "5:30/km", "target": "5:10/km", "cap": "4:50/km"},
                "hr_ceiling_bpm": 175,
                "notes": "Hold target through km 16; cap only after.",
            }
        ],
        "fueling": {"carbs_g_per_hour": 70, "notes": "Gel every 25 min."},
        "transitions": [],
        "contingencies": [
            {"scenario": "heat", "plan": "Slow target by 5%."},
            {"scenario": "cramp", "plan": "Walk to feed, take salt."},
            {"scenario": "off-pace", "plan": "Drop to low, hold to km 18."},
        ],
        "headline": "Steady to km 16, race the last 5k.",
    }


def _anthropic_response(blocks: list[Any], stop_reason: str = "tool_use"):
    """Build a fake Anthropic Messages response with .content / .stop_reason."""
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def _tool_use_block(plan_input: dict, *, name: str = "submit_race_plan"):
    return SimpleNamespace(type="tool_use", name=name, input=plan_input)


def _patch_anthropic(plan_input: dict | None, *, stop_reason: str = "tool_use"):
    """Patch anthropic.AsyncAnthropic so .messages.create returns one tool_use block.

    ``plan_input=None`` simulates the model bailing without calling the tool —
    only an end_turn text block is returned.
    """
    blocks: list = []
    if plan_input is not None:
        blocks.append(_tool_use_block(plan_input))
    else:
        blocks.append(SimpleNamespace(type="text", text="…", name=None, input=None))

    fake_client = MagicMock()
    fake_client.messages = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_anthropic_response(blocks, stop_reason))
    return MagicMock(return_value=fake_client)


class TestGenerateRacePlanRefusals:
    @pytest.mark.asyncio
    async def test_refuses_when_no_active_race_a(self):
        from mcp_server.tools.races import generate_race_plan

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=None)),
        ):
            out = await generate_race_plan()

        assert "error" in out
        assert "RACE_A" in out["error"]

    @pytest.mark.asyncio
    async def test_refuses_when_race_more_than_120d_out(self):
        """Validator must refuse even before the Claude call when goal is too far out."""
        from mcp_server.tools.races import generate_race_plan

        goal = _race_goal(days_to_race=200)
        anthropic_patch = _patch_anthropic(_valid_plan_input())
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=goal)),
            patch(f"{_MODULE}.RacePlan.get_today_for_goal", AsyncMock(return_value=None)),
            patch("anthropic.AsyncAnthropic", anthropic_patch),
        ):
            out = await generate_race_plan()

        assert "error" in out
        assert ">120" in out["error"]
        assert out["days_to_race"] == 200
        # No Claude spend on a refused-by-window plan.
        anthropic_patch.assert_not_called()

    @pytest.mark.asyncio
    async def test_refuses_when_fewer_than_6_activities(self):
        from mcp_server.tools.races import generate_race_plan

        goal = _race_goal(days_to_race=30)
        anthropic_patch = _patch_anthropic(_valid_plan_input())
        thin_log = ([_activity(i) for i in range(3)], None)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=goal)),
            patch(f"{_MODULE}.RacePlan.get_today_for_goal", AsyncMock(return_value=None)),
            patch(f"{_MODULE}.Activity.get_range", AsyncMock(return_value=thin_log)),
            patch("anthropic.AsyncAnthropic", anthropic_patch),
        ):
            out = await generate_race_plan()

        assert "error" in out
        assert out["activity_count"] == 3
        anthropic_patch.assert_not_called()


class TestGenerateRacePlanDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_happy_path_returns_payload_without_persisting(self):
        """dry_run=True calls Claude, validates, returns payload, never saves."""
        from mcp_server.tools.races import generate_race_plan

        goal = _race_goal(days_to_race=30)
        anthropic_patch = _patch_anthropic(_valid_plan_input())
        save_mock = AsyncMock()

        get_session_patch, _ = _patch_session_for_plan()
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=goal)),
            patch(f"{_MODULE}.Activity.get_range", AsyncMock(return_value=([_activity(i) for i in range(8)], None))),
            patch(f"{_MODULE}.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch(f"{_MODULE}.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch(f"{_MODULE}.get_session", get_session_patch),
            patch(f"{_MODULE}.RacePlan.save", save_mock),
            patch(f"{_MODULE}.RacePlan.get_today_for_goal", AsyncMock(return_value=None)),
            patch(f"{_MODULE}.settings") as fake_settings,
            patch("anthropic.AsyncAnthropic", anthropic_patch),
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            out = await generate_race_plan(dry_run=True)

        assert out["dry_run"] is True
        assert out["id"] is None
        assert out["preliminary"] is True  # 30 days > 14 → preliminary
        assert out["payload"]["plan"]["headline"].startswith("Steady")
        save_mock.assert_not_called()
        # The Claude client was actually constructed once.
        anthropic_patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_tool_use_block_returned_returns_error(self):
        """If Claude returns only text and skips submit_race_plan, surface a clean error."""
        from mcp_server.tools.races import generate_race_plan

        goal = _race_goal(days_to_race=30)
        anthropic_patch = _patch_anthropic(plan_input=None, stop_reason="end_turn")
        save_mock = AsyncMock()

        get_session_patch, _ = _patch_session_for_plan()
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            patch(f"{_MODULE}.AthleteGoal.get_by_category", AsyncMock(return_value=goal)),
            patch(f"{_MODULE}.Activity.get_range", AsyncMock(return_value=([_activity(i) for i in range(8)], None))),
            patch(f"{_MODULE}.AthleteSettings.get_all", AsyncMock(return_value=[])),
            patch(f"{_MODULE}.FitnessProjection.get_projection", AsyncMock(return_value=[])),
            patch(f"{_MODULE}.get_session", get_session_patch),
            patch(f"{_MODULE}.RacePlan.save", save_mock),
            patch(f"{_MODULE}.RacePlan.get_today_for_goal", AsyncMock(return_value=None)),
            patch(f"{_MODULE}.settings") as fake_settings,
            patch("anthropic.AsyncAnthropic", anthropic_patch),
        ):
            fake_settings.ANTHROPIC_API_KEY = SimpleNamespace(get_secret_value=lambda: "test-key")
            out = await generate_race_plan(dry_run=True)

        assert "error" in out
        assert "structured plan" in out["error"]
        save_mock.assert_not_called()


class TestGenerateRacePlanValidator:
    """Direct unit tests for _validate_race_plan covering schema-uncatchable cases."""

    def test_accepts_valid_plan(self):
        from mcp_server.tools.races import _validate_race_plan

        errors = _validate_race_plan(_valid_plan_input(), athlete_max_hr=190)
        assert errors == []

    def test_rejects_inverted_pace_corridor(self):
        """For pace, low (slow) > target > cap (fast). Inverted → error."""
        from mcp_server.tools.races import _validate_race_plan

        plan = _valid_plan_input()
        plan["legs"][0]["pacing"] = {"low": "4:50/km", "target": "5:10/km", "cap": "5:30/km"}
        errors = _validate_race_plan(plan, athlete_max_hr=190)
        assert any("corridor" in e for e in errors)

    def test_rejects_inverted_power_corridor(self):
        """For power, low (W) < target < cap. Inverted → error."""
        from mcp_server.tools.races import _validate_race_plan

        plan = _valid_plan_input()
        plan["legs"][0]["pacing"] = {"low": "260W", "target": "240W", "cap": "220W"}
        errors = _validate_race_plan(plan, athlete_max_hr=190)
        assert any("corridor" in e for e in errors)

    def test_rejects_hr_ceiling_above_athlete_max_plus_5(self):
        from mcp_server.tools.races import _validate_race_plan

        plan = _valid_plan_input()
        plan["legs"][0]["hr_ceiling_bpm"] = 210
        errors = _validate_race_plan(plan, athlete_max_hr=190)
        assert any("hr_ceiling_bpm" in e for e in errors)

    def test_skips_unparseable_pacing(self):
        """Free-form leg notes (e.g. 'easy', 'tempo') shouldn't trigger a false reject."""
        from mcp_server.tools.races import _validate_race_plan

        plan = _valid_plan_input()
        plan["legs"][0]["pacing"] = {"low": "easy", "target": "tempo", "cap": "threshold"}
        errors = _validate_race_plan(plan, athlete_max_hr=190)
        assert errors == []

    def test_logs_unparseable_corridor_fields(self, caplog):
        """END-70: every unparseable leg/field combo emits a structured log
        tagged with goal_id, leg, field, value so we can eyeball the rate of
        qualitative pacing labels after dog-food races land.
        """
        import logging

        from mcp_server.tools.races import _validate_race_plan

        plan = _valid_plan_input()
        plan["legs"][0]["leg"] = "run"
        plan["legs"][0]["pacing"] = {"low": "easy", "target": "tempo", "cap": "5:00/km"}

        with caplog.at_level(logging.INFO, logger="mcp_server.tools.races"):
            errors = _validate_race_plan(plan, athlete_max_hr=190, goal_id=42)

        assert errors == []
        skip_records = [
            r for r in caplog.records if getattr(r, "race_plan_corridor_unparseable", False)
        ]
        # Two unparseable fields (low="easy", target="tempo"); cap="5:00/km" parses.
        assert len(skip_records) == 2
        fields_logged = {(r.leg, r.field, r.value) for r in skip_records}
        assert fields_logged == {("run", "low", "easy"), ("run", "target", "tempo")}
        for r in skip_records:
            assert r.goal_id == 42

    def test_unparseable_log_tolerates_missing_goal_id(self, caplog):
        """goal_id is optional — older callers (or tests) shouldn't crash."""
        import logging

        from mcp_server.tools.races import _validate_race_plan

        plan = _valid_plan_input()
        plan["legs"][0]["pacing"] = {"low": "easy", "target": "tempo", "cap": "threshold"}

        with caplog.at_level(logging.INFO, logger="mcp_server.tools.races"):
            _validate_race_plan(plan, athlete_max_hr=190)

        skip_records = [
            r for r in caplog.records if getattr(r, "race_plan_corridor_unparseable", False)
        ]
        assert len(skip_records) == 3
        assert all(r.goal_id is None for r in skip_records)
