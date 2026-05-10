"""Tests for tasks/actors/changelog.py — see docs/WEEKLY_CHANGELOG_SPEC.md §14.

We exercise ``publish_weekly_changelog`` (the entry point that the actor and
the CLI both call). Each test patches the three external integrations
(GitHub REST, Claude, GitHub GraphQL) at the module-attribute level via
``monkeypatch`` — no httpx/anthropic mocks needed below the API surface.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from tasks.actors import changelog as cl

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_NOW = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)


def _make_pr(
    number: int,
    title: str = "Add new feature",
    body: str = "",
    author: str = "radik",
    author_type: str = "User",
    labels: tuple[str, ...] = (),
    merged_at: datetime | None = None,
    base_ref: str = "main",
) -> cl.MergedPR:
    return cl.MergedPR(
        number=number,
        title=title,
        body=body,
        url=f"https://github.com/x/y/pull/{number}",
        author=author,
        author_type=author_type,
        labels=labels,
        merged_at=merged_at or (_NOW - timedelta(days=1)),
        base_ref=base_ref,
    )


@pytest.fixture
def enabled_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend env vars are set so ``publish_weekly_changelog`` runs through."""
    from pydantic import SecretStr

    monkeypatch.setattr(cl.settings, "CHANGELOG_REPO_ID", "R_test")
    monkeypatch.setattr(cl.settings, "CHANGELOG_DISCUSSION_CATEGORY_ID", "DIC_test")
    monkeypatch.setattr(cl.settings, "GITHUB_TOKEN", SecretStr("ghp_test"))
    monkeypatch.setattr(cl.settings, "ANTHROPIC_API_KEY", SecretStr("sk-ant-test"))
    monkeypatch.setattr(cl.settings, "GITHUB_REPO", "x/y")


@pytest.fixture
def sentry_capture_calls(monkeypatch: pytest.MonkeyPatch) -> list[Exception]:
    """Capture every ``sentry_sdk.capture_exception`` call made from the actor.

    H1 from review — without this, all three error-path tests would silently
    pass on a refactor that quietly drops Sentry reporting.
    """
    calls: list[Exception] = []
    monkeypatch.setattr(cl.sentry_sdk, "capture_exception", calls.append)
    return calls


@pytest.fixture
def patched_publish(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture create_discussion calls without hitting GraphQL."""
    captured: dict[str, Any] = {}

    def fake(*, repo_id: str, category_id: str, title: str, body: str, token: str) -> dict:
        captured.update(
            repo_id=repo_id,
            category_id=category_id,
            title=title,
            body=body,
            token=token,
        )
        return {"number": 999, "url": "https://github.com/x/y/discussions/999", "title": title}

    monkeypatch.setattr(cl, "create_discussion", fake)
    return captured


@pytest.fixture
def no_existing_discussion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default for happy-path tests — repo has no fresh Discussion to skip."""
    monkeypatch.setattr(cl, "fetch_latest_discussion", lambda **kw: None)


# --------------------------------------------------------------------------- #
# Pre-filter rules — table-driven so deviations stay visible.
# --------------------------------------------------------------------------- #


class TestPrefilter:
    def test_drops_dependabot_authors(self) -> None:
        prs = [
            _make_pr(1, author="dependabot[bot]", author_type="Bot"),
            _make_pr(2, author="radik"),
        ]
        out = cl.prefilter_prs(prs)
        assert {p.number for p in out} == {2}

    def test_drops_unknown_bot_authors_via_user_type(self) -> None:
        """H2 — login allowlist alone misses new bots (mergify, imgbot, …).

        ``user.type == "Bot"`` is the GitHub-canonical signal; we keep the
        login allowlist as belt-and-suspenders for clarity in skip-by reason.
        """
        prs = [
            _make_pr(1, author="mergify[bot]", author_type="Bot"),
            _make_pr(2, author="imgbot[bot]", author_type="Bot"),
            _make_pr(3, author="radik", author_type="User"),
        ]
        out = cl.prefilter_prs(prs)
        assert {p.number for p in out} == {3}

    def test_drops_internal_conventional_commit_prefixes(self) -> None:
        prs = [
            _make_pr(1, title="chore: bump deps"),
            _make_pr(2, title="ci: tweak workflow"),
            _make_pr(3, title="build: docker fix"),
            _make_pr(4, title="test: add coverage"),
            _make_pr(5, title="docs: update README"),
            _make_pr(6, title="Add new dashboard widget"),
        ]
        out = cl.prefilter_prs(prs)
        assert {p.number for p in out} == {6}

    def test_keeps_perf_style_refactor_for_claude_to_judge(self) -> None:
        """Spec §4 deviation — perf/style/refactor go to Claude, not hard-drop.

        ``perf:`` улучшения user-facing («дашборд в 3× быстрее»). Claude
        отсеет действительно internal по правилу промпта.
        """
        prs = [
            _make_pr(1, title="perf: speed up dashboard"),
            _make_pr(2, title="style: tighten button spacing"),
            _make_pr(3, title="refactor: rewrite onboarding flow"),
        ]
        out = cl.prefilter_prs(prs)
        assert {p.number for p in out} == {1, 2, 3}

    def test_drops_skip_changelog_label(self) -> None:
        prs = [
            _make_pr(1, labels=("skip-changelog",)),
            _make_pr(2, labels=("internal",)),
            _make_pr(3, labels=("dependencies",)),
            _make_pr(4, labels=("bug",)),
        ]
        out = cl.prefilter_prs(prs)
        assert {p.number for p in out} == {4}

    def test_drops_non_main_base_ref(self) -> None:
        prs = [
            _make_pr(1, base_ref="dev"),
            _make_pr(2, base_ref="main"),
        ]
        out = cl.prefilter_prs(prs)
        assert {p.number for p in out} == {2}

    def test_dedup_keeps_newest_when_title_and_body_identical(self) -> None:
        """Re-merge artifact (POC #318/#320): title+body byte-identical."""
        old = _make_pr(318, title="Refines ramp tests", body="Same body", merged_at=_NOW - timedelta(days=2))
        new = _make_pr(320, title="Refines ramp tests", body="Same body", merged_at=_NOW - timedelta(days=1))
        out = cl.prefilter_prs([old, new])
        assert {p.number for p in out} == {320}

    def test_dedup_keeps_both_when_same_title_different_body(self) -> None:
        """Spec §4 deviation — stacked PRs with same title but different bodies survive.

        Body[:200] hash differs → both rows go to Claude, который сам решит
        склеивать ли в один буллет.
        """
        a = _make_pr(
            10,
            title="Multi-tenant fixes",
            body="A" * 250,  # first 200 chars: "AAA..."
            merged_at=_NOW - timedelta(days=2),
        )
        b = _make_pr(
            11,
            title="Multi-tenant fixes",
            body="B" * 250,  # first 200 chars: "BBB..."
            merged_at=_NOW - timedelta(days=1),
        )
        out = cl.prefilter_prs([a, b])
        assert {p.number for p in out} == {10, 11}


# --------------------------------------------------------------------------- #
# build_prompt — body truncation deviation (§5: 500 → 1500).
# --------------------------------------------------------------------------- #


class TestBuildPrompt:
    def test_truncates_body_at_1500_chars_with_suffix(self) -> None:
        long_body = "x" * 2000
        pr = _make_pr(1, body=long_body)
        prompt = cl.build_prompt([pr])
        # 1500 x's plus the suffix should be present once; the 1501st char is gone.
        assert ("x" * cl.PR_BODY_MAX_CHARS) in prompt
        assert cl.PR_BODY_TRUNC_SUFFIX in prompt
        # No body of length ≥1501 should leak through.
        assert ("x" * (cl.PR_BODY_MAX_CHARS + 1)) not in prompt

    def test_short_body_passes_through_without_suffix(self) -> None:
        pr = _make_pr(1, body="What was done: small fix.")
        prompt = cl.build_prompt([pr])
        assert "What was done: small fix." in prompt
        assert cl.PR_BODY_TRUNC_SUFFIX not in prompt

    def test_caps_at_top_50_when_more_than_max(self) -> None:
        prs = [_make_pr(i, title=f"PR {i}", body=f"body {i}") for i in range(60)]
        prompt = cl.build_prompt(prs)
        # PR with index 49 (50th) should be in; index 50 onward dropped.
        assert "body 49" in prompt
        assert "body 50" not in prompt


# --------------------------------------------------------------------------- #
# build_discussion_title — same/cross-month spec §6.
# --------------------------------------------------------------------------- #


class TestDiscussionTitle:
    def test_same_month_format(self) -> None:
        start = datetime(2026, 5, 4, tzinfo=timezone.utc)
        end = datetime(2026, 5, 10, tzinfo=timezone.utc)
        # 7-day Mon-Sun window ending on publish-day Sunday.
        assert cl.build_discussion_title(start, end) == "✨ Что нового — неделя 04–10 мая 2026"

    def test_cross_month_format(self) -> None:
        start = datetime(2026, 4, 28, tzinfo=timezone.utc)
        end = datetime(2026, 5, 4, tzinfo=timezone.utc)
        out = cl.build_discussion_title(start, end)
        assert "28 апреля" in out
        assert "04 мая" in out
        assert "2026" in out


# --------------------------------------------------------------------------- #
# publish_weekly_changelog — end-to-end skip and happy-path branches.
# --------------------------------------------------------------------------- #


class TestPublishWeeklyChangelog:
    def test_skipped_when_env_vars_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cl.settings, "CHANGELOG_REPO_ID", "")
        result = cl.publish_weekly_changelog()
        assert result == {"status": "skipped_disabled"}

    def test_skipped_when_anthropic_key_empty(self, enabled_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
        """M2 — empty ANTHROPIC_API_KEY must short-circuit BEFORE GitHub fetch."""
        from pydantic import SecretStr

        monkeypatch.setattr(cl.settings, "ANTHROPIC_API_KEY", SecretStr(""))
        called = {"fetched": False}

        def boom_fetch(*a: Any, **kw: Any) -> list:
            called["fetched"] = True
            raise AssertionError("fetch_merged_prs must not be called when ANTHROPIC_API_KEY is empty")

        monkeypatch.setattr(cl, "fetch_merged_prs", boom_fetch)
        result = cl.publish_weekly_changelog()
        assert result == {"status": "skipped_disabled"}
        assert called["fetched"] is False

    def test_skipped_when_no_merged_prs(
        self,
        enabled_settings: None,
        no_existing_discussion: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(cl, "fetch_merged_prs", lambda *a, **kw: [])
        result = cl.publish_weekly_changelog()
        assert result == {"status": "skipped_no_prs"}

    def test_skipped_when_all_prs_filtered_by_prefilter(
        self,
        enabled_settings: None,
        no_existing_discussion: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            cl,
            "fetch_merged_prs",
            lambda *a, **kw: [
                _make_pr(1, author="dependabot[bot]"),
                _make_pr(2, title="chore: bump deps"),
            ],
        )
        result = cl.publish_weekly_changelog()
        assert result["status"] == "skipped_all_filtered"
        assert result["fetched"] == 2

    def test_skipped_when_claude_returns_no_user_facing_changes(
        self,
        enabled_settings: None,
        no_existing_discussion: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(cl, "fetch_merged_prs", lambda *a, **kw: [_make_pr(1)])
        monkeypatch.setattr(cl, "call_claude", lambda prompt: cl.NO_USER_FACING_CHANGES)
        called = {"created": False}

        def fail_publish(**kw: Any) -> dict:
            called["created"] = True
            return {}

        monkeypatch.setattr(cl, "create_discussion", fail_publish)
        result = cl.publish_weekly_changelog()
        assert result["status"] == "skipped_internal"
        assert called["created"] is False

    def test_publishes_with_correct_title_and_wrapped_body(
        self,
        enabled_settings: None,
        no_existing_discussion: None,
        monkeypatch: pytest.MonkeyPatch,
        patched_publish: dict[str, Any],
    ) -> None:
        # Freeze time so title is deterministic — C1 regression (8-day Sun-Sun
        # range) would change the day numbers and fail the exact-match assert.
        class _FrozenDT(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                return datetime(2026, 5, 10, 13, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(cl, "datetime", _FrozenDT)
        monkeypatch.setattr(cl, "fetch_merged_prs", lambda *a, **kw: [_make_pr(1, title="Add race plan")])
        monkeypatch.setattr(cl, "call_claude", lambda prompt: "## 🎯 Цели\n- Теперь можно X")

        result = cl.publish_weekly_changelog()
        assert result["status"] == "published"
        assert result["discussion"]["number"] == 999

        # C1 — assert exact 7-day Mon-Sun window (May 4-10), NOT 8-day Sun-Sun (May 3-10).
        assert patched_publish["title"] == "✨ Что нового — неделя 04–10 мая 2026"
        assert "## 🎯 Цели" in patched_publish["body"]
        assert "Сводка изменений за неделю" in patched_publish["body"]
        assert "Полный список merged PR'ов за неделю" in patched_publish["body"]

    def test_skipped_when_github_fetch_raises(
        self,
        enabled_settings: None,
        no_existing_discussion: None,
        monkeypatch: pytest.MonkeyPatch,
        sentry_capture_calls: list[Exception],
    ) -> None:
        def boom(*a: Any, **kw: Any) -> list:
            raise RuntimeError("GitHub 503")

        monkeypatch.setattr(cl, "fetch_merged_prs", boom)
        result = cl.publish_weekly_changelog()
        assert result["status"] == "skipped_error"
        assert result["stage"] == "fetch"
        # H1 — Sentry must record the failure even though the actor swallows it.
        assert len(sentry_capture_calls) == 1
        assert isinstance(sentry_capture_calls[0], RuntimeError)

    def test_skipped_when_claude_raises(
        self,
        enabled_settings: None,
        no_existing_discussion: None,
        monkeypatch: pytest.MonkeyPatch,
        sentry_capture_calls: list[Exception],
    ) -> None:
        monkeypatch.setattr(cl, "fetch_merged_prs", lambda *a, **kw: [_make_pr(1)])

        def boom(prompt: str) -> str:
            raise RuntimeError("Claude 529")

        monkeypatch.setattr(cl, "call_claude", boom)
        result = cl.publish_weekly_changelog()
        assert result["status"] == "skipped_error"
        assert result["stage"] == "claude"
        assert len(sentry_capture_calls) == 1
        assert isinstance(sentry_capture_calls[0], RuntimeError)

    def test_skipped_when_create_discussion_raises(
        self,
        enabled_settings: None,
        no_existing_discussion: None,
        monkeypatch: pytest.MonkeyPatch,
        sentry_capture_calls: list[Exception],
    ) -> None:
        monkeypatch.setattr(cl, "fetch_merged_prs", lambda *a, **kw: [_make_pr(1)])
        monkeypatch.setattr(cl, "call_claude", lambda prompt: "## 🐛 Багфиксы\n- Поправили Y")

        def boom(**kw: Any) -> dict:
            raise RuntimeError("GraphQL 500")

        monkeypatch.setattr(cl, "create_discussion", boom)
        result = cl.publish_weekly_changelog()
        assert result["status"] == "skipped_error"
        assert result["stage"] == "publish"
        assert len(sentry_capture_calls) == 1
        assert isinstance(sentry_capture_calls[0], RuntimeError)


# --------------------------------------------------------------------------- #
# Weekly idempotency — manual run + Sun cron must not double-publish.
# --------------------------------------------------------------------------- #


class TestWeeklyIdempotency:
    """A Wed manual ``publish-changelog`` should make Sun 15:00 cron a no-op."""

    def test_skipped_when_fresh_discussion_already_exists(
        self, enabled_settings: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Discussion created 2 days ago (within 7-day window) → cron should skip.
        recent = (_NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z")
        monkeypatch.setattr(
            cl,
            "fetch_latest_discussion",
            lambda **kw: {
                "url": "https://github.com/x/y/discussions/100",
                "title": "✨ Что нового — неделя 04–10 мая 2026",
                "created_at": recent,
            },
        )
        called = {"prs_fetched": False, "claude_called": False, "published": False}

        def fail_fetch(*a: Any, **kw: Any) -> list:
            called["prs_fetched"] = True
            return []

        monkeypatch.setattr(cl, "fetch_merged_prs", fail_fetch)
        monkeypatch.setattr(cl, "call_claude", lambda prompt: called.update(claude_called=True) or "")
        monkeypatch.setattr(cl, "create_discussion", lambda **kw: called.update(published=True) or {})

        result = cl.publish_weekly_changelog()
        assert result["status"] == "skipped_already_published"
        assert result["existing"]["url"] == "https://github.com/x/y/discussions/100"
        # Critical: short-circuit BEFORE the expensive Claude call.
        assert called["prs_fetched"] is False
        assert called["claude_called"] is False
        assert called["published"] is False

    def test_publishes_when_latest_discussion_older_than_window(
        self,
        enabled_settings: None,
        monkeypatch: pytest.MonkeyPatch,
        patched_publish: dict[str, Any],
    ) -> None:
        # Last Discussion is 10 days old → well outside the idempotency window → publish.
        old = (_NOW - timedelta(days=10)).isoformat().replace("+00:00", "Z")
        monkeypatch.setattr(
            cl,
            "fetch_latest_discussion",
            lambda **kw: {"url": "https://x", "title": "old", "created_at": old},
        )
        monkeypatch.setattr(cl, "fetch_merged_prs", lambda *a, **kw: [_make_pr(1)])
        monkeypatch.setattr(cl, "call_claude", lambda prompt: "## 🎯\n- bullet")

        result = cl.publish_weekly_changelog()
        assert result["status"] == "published"

    def test_idempotency_window_padded_against_late_cron_jitter(
        self, enabled_settings: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """M2 — Discussion exactly 7 days old must still skip.

        Cron firing N seconds LATE makes ``now`` slide forward, so a flat
        ``-7d`` cutoff would push last week's Discussion (``created_at = scheduled - 7d``)
        to ``< now - 7d`` by N seconds → duplicate publish. With cutoff
        widened to ``-7d 12h`` (further into the past), any Discussion up
        to 7d 12h old is still caught.

        Test ``ts = actor_now - 7d - 1m`` — would slip past flat ``-7d``,
        skipped by the padded window.
        """
        actor_now = datetime.now(timezone.utc)
        ts = (actor_now - timedelta(days=7, minutes=1)).isoformat().replace("+00:00", "Z")
        monkeypatch.setattr(
            cl,
            "fetch_latest_discussion",
            lambda **kw: {"url": "u", "title": "t", "created_at": ts},
        )
        monkeypatch.setattr(cl, "fetch_merged_prs", lambda *a, **kw: [_make_pr(1)])
        result = cl.publish_weekly_changelog()
        assert result["status"] == "skipped_already_published"

    def test_publishes_just_outside_padded_window(
        self,
        enabled_settings: None,
        monkeypatch: pytest.MonkeyPatch,
        patched_publish: dict[str, Any],
    ) -> None:
        """Boundary — Discussion 7d 13h old is outside the padded
        idempotency window (cutoff 7d 12h) → publish proceeds. Locks the
        ``-12h`` padding constant; if someone bumps it to 24h this test
        flips and forces the discussion."""
        actor_now = datetime.now(timezone.utc)
        ts = (actor_now - timedelta(days=7, hours=13)).isoformat().replace("+00:00", "Z")
        monkeypatch.setattr(
            cl,
            "fetch_latest_discussion",
            lambda **kw: {"url": "u", "title": "t", "created_at": ts},
        )
        monkeypatch.setattr(cl, "fetch_merged_prs", lambda *a, **kw: [_make_pr(1)])
        monkeypatch.setattr(cl, "call_claude", lambda prompt: "## 🎯\n- bullet")
        result = cl.publish_weekly_changelog()
        assert result["status"] == "published"

    def test_force_overrides_idempotency(
        self,
        enabled_settings: None,
        monkeypatch: pytest.MonkeyPatch,
        patched_publish: dict[str, Any],
    ) -> None:
        recent = (_NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z")
        called = {"lookup": False}

        def lookup(**kw: Any) -> dict:
            called["lookup"] = True
            return {"url": "x", "title": "y", "created_at": recent}

        monkeypatch.setattr(cl, "fetch_latest_discussion", lookup)
        monkeypatch.setattr(cl, "fetch_merged_prs", lambda *a, **kw: [_make_pr(1)])
        monkeypatch.setattr(cl, "call_claude", lambda prompt: "## 🎯\n- bullet")

        result = cl.publish_weekly_changelog(force=True)
        assert result["status"] == "published"
        # ``force=True`` skips the lookup entirely — saves one GraphQL call.
        assert called["lookup"] is False

    def test_falls_through_to_publish_when_lookup_raises(
        self,
        enabled_settings: None,
        monkeypatch: pytest.MonkeyPatch,
        patched_publish: dict[str, Any],
    ) -> None:
        """Idempotency check is best-effort — its failure must not block publishing."""

        def boom(**kw: Any) -> dict:
            raise RuntimeError("GraphQL 502")

        monkeypatch.setattr(cl, "fetch_latest_discussion", boom)
        monkeypatch.setattr(cl, "fetch_merged_prs", lambda *a, **kw: [_make_pr(1)])
        monkeypatch.setattr(cl, "call_claude", lambda prompt: "## 🎯\n- bullet")

        result = cl.publish_weekly_changelog()
        assert result["status"] == "published"

    def test_window_includes_pr_merged_eight_days_ago(
        self,
        enabled_settings: None,
        no_existing_discussion: None,
        monkeypatch: pytest.MonkeyPatch,
        patched_publish: dict[str, Any],
    ) -> None:
        """Pin the days=8 buffer behaviour: a PR merged ~7d 23h ago (Sat
        afternoon of the previous week, after that week's Sun-15:00 cron
        had already published) MUST appear in the current Sunday's digest.

        With the legacy days=7 window such a PR would slide into next
        week's window only — by which point the «what shipped» framing
        has decayed. This test locks the +1d buffer documented inline in
        ``publish_weekly_changelog`` so a future tightening to days=7
        flips the result and surfaces the regression.
        """
        captured: dict[str, datetime] = {}

        def capture_since(repo: str, since: datetime, *, token: str) -> list[cl.MergedPR]:
            captured["since"] = since
            return [_make_pr(1, merged_at=datetime.now(timezone.utc) - timedelta(days=7, hours=23))]

        monkeypatch.setattr(cl, "fetch_merged_prs", capture_since)
        monkeypatch.setattr(cl, "call_claude", lambda prompt: "## 🎯\n- bullet")

        result = cl.publish_weekly_changelog()

        assert result["status"] == "published"
        # The Saturday-afternoon PR (7d 23h ago) is strictly later than `since`,
        # so the GitHub query MUST return it. We verify by asserting `since` is
        # ≥ 8 days back (the buffer) — a regression to days=7 leaves `since`
        # only 7d back, the PR's `merged_at` slips past it, and the assertion
        # below fails.
        now = datetime.now(timezone.utc)
        assert (now - captured["since"]) >= timedelta(days=7, hours=23, minutes=30), (
            f"`since` is {now - captured['since']!r} back — too narrow. " f"days=8 buffer should be ≥ ~8 days."
        )
