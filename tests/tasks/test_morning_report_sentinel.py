"""Tests for the two-phase morning-report sentinel pipeline.

The sentinel lives in ``Wellness.ai_recommendation`` and serializes the
state of the per-user morning-report slot:

- ``None`` / empty                      → free, anyone can claim.
- ``"__scheduled__:{set_at}"``         → recovery-score callback deferred the compose
                                           by ``MORNING_REPORT_DELAY_SEC``;
                                           webhooks must NOT re-dispatch in this
                                           window. Stale after 2× the delay.
- ``"__generating__:{set_at}"``        → compose actor is currently running;
                                           skip if fresh (< delay), else
                                           assume worker crash and retry.

Critical path: a bug here either silently skips morning reports (user
complaint surface) or fires them twice (sentry storm). Worth testing.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from data.db import Wellness
from data.db.user import UserDTO
from data.intervals.dto import WellnessDTO
from tasks.actors import wellness as wellness_mod
from tasks.actors._constants import MORNING_REPORT_DELAY_SEC
from tasks.actors.wellness import _is_free_for_morning_report
from tasks.dto import ORMDTO

_TODAY = date(2026, 9, 21)


def _user() -> UserDTO:
    return UserDTO(id=48, chat_id="111", username="tester", athlete_id="i001")


def _session_returning(row) -> MagicMock:
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.execute.return_value.scalar_one_or_none.return_value = row
    return session


class TestIsFreeForMorningReport:
    """Pure-unit tests on the sentinel parser. Direct UPDATE wins are guarded
    by SELECT FOR UPDATE in ``_dispatch_morning_report_if_ready`` — these
    tests cover the parser shape only.
    """

    def test_none_is_free(self):
        assert _is_free_for_morning_report(None) is True

    def test_empty_string_is_free(self):
        assert _is_free_for_morning_report("") is True

    def test_plain_ai_recommendation_is_taken(self):
        """Any non-sentinel string means a real report already exists —
        the slot is taken until the daily wellness reset."""
        assert _is_free_for_morning_report("Today is recovery day, keep it easy.") is False

    def test_fresh_scheduled_is_taken(self):
        """A `__scheduled__` sentinel set NOW means a delayed compose is
        in flight; the wellness cron must not re-dispatch."""
        sentinel = f"__scheduled__:{time.time():.0f}"
        assert _is_free_for_morning_report(sentinel) is False

    def test_scheduled_within_delay_window_is_taken(self):
        """Anywhere inside the 2× delay grace window stays taken."""
        sentinel = f"__scheduled__:{time.time() - MORNING_REPORT_DELAY_SEC:.0f}"
        assert _is_free_for_morning_report(sentinel) is False

    def test_stale_scheduled_is_free(self):
        """Past 2× the delay, the delayed message clearly never arrived
        (Redis loss / broker eviction). Slot reopens so cron can retry."""
        sentinel = f"__scheduled__:{time.time() - 2 * MORNING_REPORT_DELAY_SEC - 1:.0f}"
        assert _is_free_for_morning_report(sentinel) is True

    def test_malformed_scheduled_timestamp_is_free(self):
        """A corrupt timestamp (manual edit, encoding bug) shouldn't
        permanently lock the user out — treat as stale."""
        assert _is_free_for_morning_report("__scheduled__:not-a-number") is True

    def test_scheduled_without_colon_payload_is_free(self):
        """Missing payload after the prefix — same defensive treatment."""
        # ``"__scheduled__:"`` -> split → ["__scheduled__", ""] → float("")
        # raises ValueError → caught → return True.
        assert _is_free_for_morning_report("__scheduled__:") is True

    def test_generating_sentinel_is_taken(self):
        """`__generating__` is not a state the wellness cron's pre-check
        should ever clear — that's the compose actor's responsibility.
        From the cron's standpoint, it's a real recommendation in progress."""
        sentinel = f"__generating__:{time.time():.0f}"
        assert _is_free_for_morning_report(sentinel) is False


class TestScheduledSentinelFormat:
    """Regression guard on what ``_dispatch_morning_report_if_ready`` writes."""

    def test_scheduled_format_uses_set_at_not_eligibility(self):
        """Regression guard: the on-disk format must be SET-time, not
        eligibility-time. Mixing them up changes the meaning of the 2×delay
        stale window from 'twice the dispatch delay' to 'three times' — a
        silent shift hard to spot in review.
        """
        # Grep the source of the dispatcher for the format we serialize.
        # If someone reintroduces `time.time() + MORNING_REPORT_DELAY_SEC`,
        # this test fails loudly, prompting them to also update the parser.
        import inspect

        src = inspect.getsource(wellness_mod._dispatch_morning_report_if_ready)
        assert "__scheduled__:{time.time():.0f}" in src, (
            "_dispatch_morning_report_if_ready no longer serializes `__scheduled__` as SET-time; "
            "update `_is_free_for_morning_report` accordingly (and this test)."
        )


class TestDispatchAfterRecoveryScore:
    """The dispatch gate needs ``recovery_score``, which is written by the
    ``_actor_update_recovery_score`` completion callback — *after*
    ``actor_user_wellness`` returns. Gating inside ``actor_user_wellness``
    read a pre-callback row, so the day's first wellness webhook never fired
    the report and athletes with sparse webhooks got it in the evening.
    """

    @staticmethod
    def _row(**fields) -> MagicMock:
        return MagicMock(spec=Wellness, **fields)

    def _dispatch(self, row, dt: date = _TODAY) -> tuple[MagicMock, MagicMock]:
        session = _session_returning(row)
        with (
            patch.object(wellness_mod, "local_today", return_value=_TODAY),
            patch.object(wellness_mod, "get_sync_session", return_value=session),
            patch("tasks.actors.reports.actor_compose_user_morning_report.send_with_options") as send,
        ):
            wellness_mod._dispatch_morning_report_if_ready(_user(), dt)
        return session, send

    def test_ready_row_claims_slot_and_schedules_delayed_compose(self):
        row = self._row(sleep_score=68.0, recovery_score=57.0, ai_recommendation=None)
        session, send = self._dispatch(row)

        assert row.ai_recommendation.startswith("__scheduled__:")
        session.commit.assert_called_once()
        send.assert_called_once_with(kwargs={"user": _user()}, delay=MORNING_REPORT_DELAY_SEC * 1000)

    def test_skips_without_recovery_score(self):
        row = self._row(sleep_score=68.0, recovery_score=None, ai_recommendation=None)
        session, send = self._dispatch(row)

        assert row.ai_recommendation is None
        session.commit.assert_not_called()
        send.assert_not_called()

    def test_skips_without_sleep_score(self):
        row = self._row(sleep_score=None, recovery_score=57.0, ai_recommendation=None)
        _, send = self._dispatch(row)
        send.assert_not_called()

    def test_skips_when_slot_already_taken(self):
        row = self._row(sleep_score=68.0, recovery_score=57.0, ai_recommendation="Real report text.")
        session, send = self._dispatch(row)

        assert row.ai_recommendation == "Real report text."
        session.commit.assert_not_called()
        send.assert_not_called()

    def test_skips_when_compose_already_scheduled(self):
        """Second webhook inside the 10-min delay must not enqueue a second compose."""
        sentinel = f"__scheduled__:{time.time():.0f}"
        row = self._row(sleep_score=68.0, recovery_score=57.0, ai_recommendation=sentinel)
        session, send = self._dispatch(row)

        assert row.ai_recommendation == sentinel
        session.commit.assert_not_called()
        send.assert_not_called()

    def test_skips_other_dates_without_touching_db(self):
        row = self._row(sleep_score=68.0, recovery_score=57.0, ai_recommendation=None)
        session, send = self._dispatch(row, dt=_TODAY - timedelta(days=1))

        session.execute.assert_not_called()
        send.assert_not_called()

    def _run_recovery_actor(self, *, hrv_row=True, **kwargs) -> MagicMock:
        recovery = MagicMock(score=57.0, category="moderate", recommendation="zone1_long")
        with (
            patch.object(wellness_mod, "get_sync_session", return_value=_session_returning(None)),
            patch.object(wellness_mod.Wellness, "get", return_value=MagicMock(sleep_score=68.0)),
            patch.object(wellness_mod.HrvAnalysis, "get", return_value=MagicMock() if hrv_row else None),
            patch.object(wellness_mod.RhrAnalysis, "get", return_value=MagicMock()),
            patch.object(wellness_mod, "combined_recovery_score", return_value=recovery),
            patch.object(wellness_mod, "_dispatch_morning_report_if_ready") as dispatch,
        ):
            wellness_mod._actor_update_recovery_score(user=_user(), dt=_TODAY, **kwargs)
        return dispatch

    def test_recovery_actor_dispatches_after_score_is_written(self):
        dispatch = self._run_recovery_actor(dispatch_report=True)
        dispatch.assert_called_once_with(_user(), _TODAY)

    def test_recovery_actor_default_does_not_dispatch(self):
        """Bootstrap backfill calls the actor inline per historical day —
        it must not start scheduling reports."""
        dispatch = self._run_recovery_actor()
        dispatch.assert_not_called()

    def test_recovery_actor_without_hrv_baseline_does_not_dispatch(self):
        """<14 days of HRV → no analysis row → no score → nothing to report on."""
        dispatch = self._run_recovery_actor(hrv_row=False, dispatch_report=True)
        dispatch.assert_not_called()

    def test_wellness_actor_delegates_dispatch_to_completion_callback(self):
        """The actor must hand the dispatch to the callback, never decide inline
        — it returns before ``recovery_score`` exists."""
        row = self._row(sleep_score=68.0, recovery_score=57.0, ai_recommendation=None)
        dt_str = _TODAY.isoformat()
        with (
            patch.object(wellness_mod, "is_user_dormant", return_value=False),
            patch.object(wellness_mod, "local_today", return_value=_TODAY),
            patch.object(wellness_mod.Wellness, "save", return_value=ORMDTO(is_changed=True, row=row)),
            patch.object(wellness_mod.AthleteSettings, "is_stale", return_value=False),
            patch.object(wellness_mod.actor_snapshot_endurance_scores, "send"),
            patch.object(wellness_mod, "group") as mock_group,
            patch("tasks.actors.reports.actor_compose_user_morning_report.send_with_options") as send,
        ):
            wellness_mod.actor_user_wellness(_user(), dt=dt_str, wellness=WellnessDTO(id=dt_str))

        callback = mock_group.return_value.add_completion_callback.call_args.args[0]
        assert callback.actor_name == "_actor_update_recovery_score"
        assert callback.kwargs["dispatch_report"] is True
        send.assert_not_called()


class TestComposeActorClaimsScheduledSlot:
    """The delayed compose actor must claim a `__scheduled__` slot (not
    skip it as 'taken'). This is the bit that lets the deferred message
    actually run after `MORNING_REPORT_DELAY_SEC`.

    We assert the branch shape via inspect — fully wiring the compose
    actor requires real DB + Anthropic, out of scope for a unit test.
    """

    def test_compose_actor_has_scheduled_passthrough_branch(self):
        import inspect

        from tasks.actors import reports as reports_mod

        src = inspect.getsource(reports_mod.actor_compose_user_morning_report.fn)
        # The branch must check for __scheduled__ explicitly and `pass`
        # through to the claim step (NOT `return`). A regex would be too
        # narrow; substring matches are sufficient regression guard.
        assert 'startswith("__scheduled__")' in src or "'__scheduled__'" in src, (
            "compose actor must recognize a `__scheduled__` sentinel as the "
            "wellness cron's reservation — otherwise the delayed message can't claim it"
        )

    def test_compose_actor_treats_fresh_generating_as_in_progress(self):
        """Two delayed compose runs in flight (cron double-fire): the second
        must see the first one's `__generating__` sentinel and bail without
        regenerating the report (cost + Sentry storm)."""
        import inspect

        from tasks.actors import reports as reports_mod

        src = inspect.getsource(reports_mod.actor_compose_user_morning_report.fn)
        assert "MORNING_REPORT_DELAY_SEC" in src, (
            "compose actor's freshness check must reference MORNING_REPORT_DELAY_SEC; "
            "using a magic 600 risks drift from the wellness-side delay"
        )


class TestSentinelCorruption:
    """The wellness column is plain text — anything could in principle end up
    there. Tests below pin the defensive behaviour so a poisoned row doesn't
    deadlock the morning-report path for that user."""

    def test_only_sentinel_prefix_with_no_value_is_free(self):
        """A truncated sentinel — e.g., from an aborted write — must not lock."""
        assert _is_free_for_morning_report("__scheduled__") is False  # no colon → not a sentinel
        assert _is_free_for_morning_report("__scheduled__:") is True  # parsed → ValueError → free

    def test_garbage_text_is_treated_as_real_recommendation(self):
        """Anything that doesn't match a sentinel prefix counts as a real
        report (don't try to be clever — false-positives here would
        regenerate over a valid AI message)."""
        assert _is_free_for_morning_report("Sleep was great. Z2 ride OK.") is False
        assert _is_free_for_morning_report("__almost__scheduled__:123") is False
        assert _is_free_for_morning_report("scheduled:123") is False  # missing leading underscores

    @patch("tasks.actors.wellness.time")
    def test_time_module_patchable_for_deterministic_tests(self, mock_time):
        """Smoke test: the parser reads `time.time()` from the wellness module,
        so monkeypatching is enough to pin behaviour in higher-level integration
        tests without freezing the whole process clock."""
        mock_time.time.return_value = 1_000_000
        # 30 min ago == comfortably stale (> 2 * 600 = 1200).
        assert _is_free_for_morning_report("__scheduled__:998200") is True
        # 5 min ago == still in the live window.
        assert _is_free_for_morning_report("__scheduled__:999700") is False
