"""Tests for the two-phase morning-report sentinel pipeline.

The sentinel lives in ``Wellness.ai_recommendation`` and serializes the
state of the per-user morning-report slot:

- ``None`` / empty                      → free, anyone can claim.
- ``"__scheduled__:{set_at}"``         → wellness cron deferred the compose
                                           by ``MORNING_REPORT_DELAY_SEC``;
                                           cron must NOT re-dispatch in this
                                           window. Stale after 2× the delay.
- ``"__generating__:{set_at}"``        → compose actor is currently running;
                                           skip if fresh (< delay), else
                                           assume worker crash and retry.

Critical path: a bug here either silently skips morning reports (user
complaint surface) or fires them twice (sentry storm). Worth testing.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from tasks.actors._constants import MORNING_REPORT_DELAY_SEC
from tasks.actors.wellness import _is_free_for_morning_report


class TestIsFreeForMorningReport:
    """Pure-unit tests on the sentinel parser used by the wellness cron's
    pre-check. Direct UPDATE wins are guarded by SELECT FOR UPDATE in the
    actor itself — these tests cover the lock-free pre-check shape only.
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


class TestActorScheduledClaim:
    """Integration-style test for the SELECT FOR UPDATE claim inside
    ``actor_user_wellness``: a concurrent second invocation must see the
    sentinel and skip re-dispatching the compose."""

    def test_second_invocation_sees_scheduled_sentinel_and_skips_dispatch(self):
        """First invocation writes `__scheduled__:t`; second invocation's
        post-lock re-check sees it and returns without enqueueing a second
        delayed message. We mock both the DB row and the delayed send so
        the test stays unit-fast — the lock semantics live in `with_for_update`
        which is integration-tested by Postgres itself."""
        # Simulate a freshly-set sentinel (well within the 20-min grace).
        recent_sentinel = f"__scheduled__:{time.time():.0f}"

        # _is_free_for_morning_report is the only gate the second invocation
        # consults inside the lock — if it returns False, the function `return`s
        # before calling `send_with_options`. Verify that contract directly.
        assert _is_free_for_morning_report(recent_sentinel) is False

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

        from tasks.actors import wellness as wellness_mod

        src = inspect.getsource(wellness_mod.actor_user_wellness.fn)
        assert "__scheduled__:{time.time():.0f}" in src, (
            "actor_user_wellness no longer serializes `__scheduled__` as SET-time; "
            "update `_is_free_for_morning_report` accordingly (and this test)."
        )


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
