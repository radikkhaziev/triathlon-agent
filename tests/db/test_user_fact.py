"""Tests for UserFact ORM — cap enforcement, race protection, tenant guard.

Covers USER_CONTEXT_SPEC §3 (append-with-cap semantics) and §8 (tenant
isolation). The invariants under test:

- Per-topic cap evicts the oldest fact with ``deactivated_reason='topic_cap'``.
- ``TOPIC_CAPS['injury'] = 5`` (and ``'health' = 5``); everything else = 3.
- Global hard cap of 200 evicts with ``deactivated_reason='hard_cap'`` after
  per-topic eviction has run — the two reasons must not shadow each other.
- ``FOR UPDATE`` lock serializes concurrent saves on the same ``(user, topic)``.
- ``reactivate`` refuses to flip a fact owned by another user (tenant guard).
- The 300-char validator fires at the ORM layer.
"""

import asyncio

import pytest

from data.db import User, UserFact
from data.db.common import get_session
from data.db.user_fact import DEFAULT_TOPIC_CAP, TOPIC_CAPS


async def _ensure_user(user_id: int) -> None:
    """Idempotently create a secondary test user for tenant-isolation tests."""
    async with get_session() as session:
        existing = await session.get(User, user_id)
        if existing is None:
            session.add(User(id=user_id, chat_id=f"test_user_{user_id}", role="athlete"))
            await session.commit()


class TestAppendWithCapPerTopic:
    """Per-topic cap evicts oldest with reason='topic_cap'."""

    async def test_injury_cap_is_5(self, _test_db):
        """TOPIC_CAPS['injury']=5: the 6th save evicts the oldest, cap holds."""
        assert TOPIC_CAPS["injury"] == 5

        for i in range(5):
            await UserFact.save_with_cap(user_id=1, topic="injury", fact=f"injury #{i}")

        # Sanity: all 5 active, none evicted yet.
        active = await UserFact.list_active(user_id=1)
        assert len([f for f in active if f.topic == "injury"]) == 5

        result = await UserFact.save_with_cap(user_id=1, topic="injury", fact="injury #5")
        assert len(result["evicted_ids"]) == 1

        # Verify: active count stays at cap, evicted row is the oldest (fact #0),
        # and its reason is specifically ``topic_cap`` (not hard_cap or anything else).
        active_after = await UserFact.list_active(user_id=1)
        injuries = sorted([f for f in active_after if f.topic == "injury"], key=lambda f: f.created_at)
        assert len(injuries) == 5
        assert injuries[0].fact == "injury #1"  # #0 evicted, #1 is now the oldest active

        all_rows = await UserFact.list_all(user_id=1, include_inactive=True)
        evicted = [f for f in all_rows if f.id in result["evicted_ids"]]
        assert len(evicted) == 1
        assert evicted[0].fact == "injury #0"
        assert evicted[0].deactivated_reason == "topic_cap"

    async def test_default_topic_cap_is_3(self, _test_db):
        """TOPIC_CAPS defaults to 3 — verify with an unlisted topic (``preference``)."""
        assert DEFAULT_TOPIC_CAP == 3
        assert "preference" not in TOPIC_CAPS

        for i in range(3):
            await UserFact.save_with_cap(user_id=1, topic="preference", fact=f"pref #{i}")

        result = await UserFact.save_with_cap(user_id=1, topic="preference", fact="pref #3")
        assert len(result["evicted_ids"]) == 1

        all_rows = await UserFact.list_all(user_id=1, include_inactive=True)
        evicted = [f for f in all_rows if f.id in result["evicted_ids"]]
        assert len(evicted) == 1
        assert evicted[0].fact == "pref #0"
        assert evicted[0].deactivated_reason == "topic_cap"


class TestHardCap:
    """Global 200-fact hard cap fires AFTER per-topic eviction and uses a
    distinct reason so the audit trail stays unambiguous."""

    async def test_hard_cap_evicts_with_hard_cap_reason(self, _test_db, monkeypatch):
        """200 active + 1 new → oldest globally becomes ``hard_cap``, not ``topic_cap``.

        Shrinking ``HARD_CAP_ACTIVE`` for the test keeps it fast while still
        exercising the production code path (the constant is read at call time).
        """
        monkeypatch.setattr("data.db.user_fact.HARD_CAP_ACTIVE", 10)

        # Fill 10 active facts across 4 topics so no per-topic cap triggers
        # (2 or 3 per topic is below default cap 3, and injury cap is 5).
        # Layout: preference=2, equipment=2, travel=3, health=3 → 10 total.
        for topic, n in (("preference", 2), ("equipment", 2), ("travel", 3), ("health", 3)):
            for i in range(n):
                await UserFact.save_with_cap(user_id=1, topic=topic, fact=f"{topic} #{i}")

        assert await UserFact.count_active(user_id=1) == 10

        # 11th fact triggers hard_cap, not topic_cap (its own topic has room).
        result = await UserFact.save_with_cap(user_id=1, topic="job", fact="new job")
        assert len(result["evicted_ids"]) == 1

        # Active stays at cap.
        assert await UserFact.count_active(user_id=1) == 10

        # The evicted row's reason is specifically ``hard_cap``. This is the
        # whole point — if the loop ran topic_cap logic instead, this would fail.
        all_rows = await UserFact.list_all(user_id=1, include_inactive=True)
        evicted = [f for f in all_rows if f.id in result["evicted_ids"]]
        assert len(evicted) == 1
        assert evicted[0].deactivated_reason == "hard_cap"
        # Oldest overall was the first preference — confirm we really picked
        # globally, not within a topic.
        assert evicted[0].fact == "preference #0"

    async def test_soft_warn_emitted(self, _test_db, monkeypatch):
        """Above SOFT_WARN_ACTIVE (still under hard cap) → response carries a warning string."""
        monkeypatch.setattr("data.db.user_fact.SOFT_WARN_ACTIVE", 4)
        monkeypatch.setattr("data.db.user_fact.HARD_CAP_ACTIVE", 100)

        # 5 active total, >4 soft threshold, well under hard cap.
        topics = ["a", "b", "c", "d", "e"]
        last = None
        for t in topics:
            last = await UserFact.save_with_cap(user_id=1, topic=t, fact=f"fact {t}")

        assert last is not None
        assert last["warning"] is not None
        assert "active facts" in last["warning"]


class TestRaceProtection:
    """``SELECT ... FOR UPDATE`` must serialize concurrent saves on the same
    (user, topic). Without it both callers could see ``active_count == cap``
    and each evict their own victim → 2 evictions instead of 1.

    Spec §3 documents the **two-writer** scenario as the contract (bot +
    extractor, or two bot instances). Asserting that case is enough to prove
    the lock is in place; at higher fan-out the asyncio+greenlet scheduling
    introduces variance we deliberately don't encode as an invariant.
    """

    async def test_two_concurrent_saves_honor_cap(self, _test_db):
        """Two parallel saves on a topic at cap → cap honored, two evictions,
        both with ``topic_cap`` reason."""
        for i in range(DEFAULT_TOPIC_CAP):
            await UserFact.save_with_cap(user_id=1, topic="preference", fact=f"seed #{i}")

        results = await asyncio.gather(
            UserFact.save_with_cap(user_id=1, topic="preference", fact="parallel A"),
            UserFact.save_with_cap(user_id=1, topic="preference", fact="parallel B"),
        )

        # Cap invariant: active must equal cap. Failure here = FOR UPDATE
        # did not serialize the two writers.
        active = await UserFact.list_active(user_id=1)
        prefs_active = [f for f in active if f.topic == "preference"]
        assert (
            len(prefs_active) == DEFAULT_TOPIC_CAP
        ), f"cap invariant violated: expected {DEFAULT_TOPIC_CAP} active, got {len(prefs_active)}"

        total_evicted = sum(len(r["evicted_ids"]) for r in results)
        assert total_evicted == 2, f"expected 2 evictions across 2 saves, got {total_evicted}"

        all_rows = await UserFact.list_all(user_id=1, include_inactive=True)
        evicted_rows = [f for f in all_rows if f.deactivated_reason == "topic_cap"]
        assert len(evicted_rows) == 2


class TestReactivateTenantGuard:
    """User B must never be able to reactivate a fact owned by User A."""

    async def test_reactivate_refuses_cross_tenant(self, _test_db):
        await _ensure_user(2)

        # User 1 saves a fact, then deactivates it so it's eligible for reactivate.
        result = await UserFact.save_with_cap(user_id=1, topic="injury", fact="owner A fact")
        fact_id = result["fact_id"]
        ok_deactivate = await UserFact.deactivate(user_id=1, fact_id=fact_id)
        assert ok_deactivate is True

        # User 2 (attacker) tries to reactivate user 1's fact by id.
        ok_cross = await UserFact.reactivate(user_id=2, fact_id=fact_id)
        assert ok_cross is False, "reactivate must refuse cross-tenant writes"

        # Fact is still deactivated — the attacker's call was a no-op.
        all_rows = await UserFact.list_all(user_id=1, include_inactive=True)
        target = next(f for f in all_rows if f.id == fact_id)
        assert target.deactivated_at is not None

        # Legitimate owner can still reactivate.
        ok_owner = await UserFact.reactivate(user_id=1, fact_id=fact_id)
        assert ok_owner is True
        active = await UserFact.list_active(user_id=1)
        assert any(f.id == fact_id for f in active)


class TestFactValidator:
    """ORM-level validation lives in ``save_with_cap`` — MCP tools surface the
    ValueError as a model-visible ``{"error": ...}``."""

    async def test_fact_over_300_chars_rejected(self, _test_db):
        with pytest.raises(ValueError, match="fact too long"):
            await UserFact.save_with_cap(user_id=1, topic="preference", fact="x" * 301)

    async def test_fact_exactly_300_chars_accepted(self, _test_db):
        result = await UserFact.save_with_cap(user_id=1, topic="preference", fact="x" * 300)
        assert isinstance(result["fact_id"], int)

    async def test_empty_fact_rejected(self, _test_db):
        with pytest.raises(ValueError, match="fact must be non-empty"):
            await UserFact.save_with_cap(user_id=1, topic="preference", fact="   ")

    async def test_empty_topic_rejected(self, _test_db):
        with pytest.raises(ValueError, match="topic must be non-empty"):
            await UserFact.save_with_cap(user_id=1, topic=" ", fact="some fact")


class TestPromptInjectionTenantIsolation:
    """``render_athlete_block`` must show only the requesting user's facts.

    This is the regression test for USER_CONTEXT_SPEC threat T1 on the prompt
    render path. The renderer goes through ``UserFact.list_active`` which is
    already user-scoped, but a single-point-of-render leak is high-impact
    (cross-tenant data leak into system prompt) so we guard the invariant end
    to end.
    """

    async def test_renderer_returns_only_own_facts(self, _test_db):
        from bot.prompts import render_athlete_block

        await _ensure_user(2)

        # User 1's facts — need a marker string distinctive enough we can grep it.
        await UserFact.save_with_cap(user_id=1, topic="injury", fact="USER_A_UNIQUE_INJURY_TOKEN")
        # User 2's facts — different marker; must never appear in user 1's prompt.
        await UserFact.save_with_cap(user_id=2, topic="family", fact="USER_B_UNIQUE_FAMILY_TOKEN")

        rendered_a = await render_athlete_block(user_id=1, language="ru")
        assert "USER_A_UNIQUE_INJURY_TOKEN" in rendered_a
        assert "USER_B_UNIQUE_FAMILY_TOKEN" not in rendered_a, "cross-tenant leak into user A's prompt"

        rendered_b = await render_athlete_block(user_id=2, language="ru")
        assert "USER_B_UNIQUE_FAMILY_TOKEN" in rendered_b
        assert "USER_A_UNIQUE_INJURY_TOKEN" not in rendered_b, "cross-tenant leak into user B's prompt"

    async def test_no_facts_block_when_empty(self, _test_db):
        """Zero active facts → no ``## Что я помню о тебе`` heading emitted.

        Empty memory should not render a negative-prompt block — it wastes
        tokens and can invite the model to fabricate recalled facts.
        """
        from bot.prompts import render_athlete_block

        # User 1 starts clean in this test (the fixture truncates between tests).
        assert await UserFact.count_active(user_id=1) == 0

        rendered = await render_athlete_block(user_id=1, language="ru")
        assert "Что я помню о тебе" not in rendered
        assert "What I remember about you" not in rendered

    async def test_facts_heading_localized_by_language(self, _test_db):
        """en vs ru heading picked from ``language`` arg — spec §11.1."""
        from bot.prompts import render_athlete_block

        await UserFact.save_with_cap(user_id=1, topic="injury", fact="локализация работает")

        rendered_ru = await render_athlete_block(user_id=1, language="ru")
        rendered_en = await render_athlete_block(user_id=1, language="en")
        assert "## Что я помню о тебе" in rendered_ru
        assert "## What I remember about you" in rendered_en
