"""Long-term user memory — facts the bot remembers across conversations.

See ``docs/USER_CONTEXT_SPEC.md``. This module owns the ORM model and the
append-with-cap write path; MCP tools in ``mcp_server/tools/`` are thin
wrappers that forward to the helpers here. Prompt-time rendering lives in
``bot/prompts.py:render_athlete_block`` and uses ``list_active`` for reads.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TypedDict

from sqlalchemy import DateTime, ForeignKey, Integer, String, func, or_, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from .common import Base
from .decorator import dual

logger = logging.getLogger(__name__)

# Per-topic cap on active facts. Defaults to 3; injury/health get more because
# triathletes routinely carry multiple chronic issues simultaneously and we
# don't want valid medical facts evicted by a knee flare-up. Tune via this
# dict, not a new migration. See USER_CONTEXT_SPEC §3.
TOPIC_CAPS: dict[str, int] = {
    "injury": 5,
    "health": 5,
}
DEFAULT_TOPIC_CAP = 3

# Global safety nets. ``SOFT_WARN_ACTIVE`` triggers a warning string in the
# tool response so Claude can prune; ``HARD_CAP_ACTIVE`` is the enforcement
# fallback — after per-topic eviction, if total active still exceeds this,
# deactivate the oldest globally as ``hard_cap``. Don't trust the model to
# always heed the warning.
SOFT_WARN_ACTIVE = 50
HARD_CAP_ACTIVE = 200

FACT_MAX_LEN = 300


class SaveFactResult(TypedDict):
    """Return shape of ``UserFact.save_with_cap``. Consumed directly by the
    ``save_fact`` MCP tool — keep keys stable, webapp Phase 3 will read the
    same shape."""

    fact_id: int
    evicted_ids: list[int]  # deactivated by topic_cap OR hard_cap during this save
    warning: str | None  # soft-cap advisory string, None if under threshold


class UserFact(Base):
    """One fact the bot remembers about a user. Append-only: facts are never
    physically deleted, only marked ``deactivated_at`` with a reason so the
    audit trail stays intact. See spec §3 for semantics and §8 for
    tenant-isolation invariants (all reads/writes scope by ``user_id``).
    """

    __tablename__ = "user_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    fact: Mapped[str] = mapped_column(String(FACT_MAX_LEN), nullable=False)
    fact_language: Mapped[str | None] = mapped_column(String(5), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ------------------------------------------------------------------
    #  Writers
    # ------------------------------------------------------------------

    @classmethod
    @dual
    def save_with_cap(
        cls,
        user_id: int,
        topic: str,
        fact: str,
        *,
        fact_language: str | None = None,
        source: str = "tool",
        expires_at: datetime | None = None,
        session: Session,
    ) -> SaveFactResult:
        """Append a new fact and enforce both per-topic and global caps in one
        transaction. Returns the new id plus any evicted ids and a soft-cap
        warning (see ``SaveFactResult``).

        Race protection: we ``SELECT ... FOR UPDATE`` the topic's active rows
        before inserting, so two concurrent saves on the same ``(user_id, topic)``
        serialize at the PG level — without this, each caller could see the
        same count-at-cap and both evict, dropping two facts instead of one.

        Normalization: ``topic`` and ``fact`` are trimmed; an all-whitespace
        fact / topic raises ``ValueError`` (the MCP tool maps that into a
        model-visible tool-error message so Claude can retry).
        """
        topic = (topic or "").strip()
        fact = (fact or "").strip()
        if not topic:
            raise ValueError("topic must be non-empty")
        if not fact:
            raise ValueError("fact must be non-empty")
        if len(fact) > FACT_MAX_LEN:
            raise ValueError(f"fact too long ({len(fact)} > {FACT_MAX_LEN} chars)")

        cap = TOPIC_CAPS.get(topic, DEFAULT_TOPIC_CAP)

        # Lock the topic's active slice before the count so the post-insert
        # eviction sees a consistent view across concurrent writers.
        active_in_topic = (
            session.execute(
                select(cls)
                .where(
                    cls.user_id == user_id,
                    cls.topic == topic,
                    cls.deactivated_at.is_(None),
                )
                .order_by(cls.created_at.asc())
                .with_for_update()
            )
            .scalars()
            .all()
        )

        row = cls(
            user_id=user_id,
            topic=topic,
            fact=fact,
            fact_language=fact_language,
            source=source,
            expires_at=expires_at,
        )
        session.add(row)
        session.flush()  # populate row.id for return value

        evicted_ids: list[int] = []

        # Per-topic cap enforcement. With the lock above, len(active_in_topic)
        # is the pre-insert count, so "after our insert it becomes N+1" — if
        # that exceeds cap, evict (N + 1 - cap) oldest.
        overflow = len(active_in_topic) + 1 - cap
        if overflow > 0:
            to_evict = active_in_topic[:overflow]
            now = datetime.now(timezone.utc)
            for victim in to_evict:
                victim.deactivated_at = now
                victim.deactivated_reason = "topic_cap"
                evicted_ids.append(victim.id)
            logger.info(
                "UserFact.save_with_cap: user=%d topic=%s cap=%d evicted %d by topic_cap",
                user_id,
                topic,
                cap,
                overflow,
            )

        # Global hard-cap — runs AFTER per-topic so we don't double-count the
        # same row. Counts active rows for this user (including the fresh insert
        # and minus any topic_cap evictions) and trims the oldest globally.
        active_count = _count_active(cls, user_id, session)
        if active_count > HARD_CAP_ACTIVE:
            hard_overflow = active_count - HARD_CAP_ACTIVE
            # The "active" filter already excludes rows deactivated above
            # (topic_cap evictions committed ``deactivated_at`` in this same
            # transaction), so no explicit id-exclusion is needed.
            oldest_active = (
                session.execute(
                    select(cls)
                    .where(cls.user_id == user_id, cls.deactivated_at.is_(None))
                    .order_by(cls.created_at.asc())
                    .limit(hard_overflow)
                )
                .scalars()
                .all()
            )
            now = datetime.now(timezone.utc)
            for victim in oldest_active:
                victim.deactivated_at = now
                victim.deactivated_reason = "hard_cap"
                evicted_ids.append(victim.id)
            logger.warning(
                "UserFact.save_with_cap: user=%d hard_cap trimmed %d to stay under %d",
                user_id,
                hard_overflow,
                HARD_CAP_ACTIVE,
            )

        session.commit()

        # Soft-cap advisory: after commit, re-check total and tell Claude to
        # prune if we're approaching the hard cap.
        final_active = _count_active(cls, user_id, session)
        warning: str | None = None
        if final_active > SOFT_WARN_ACTIVE:
            warning = (
                f"You have {final_active} active facts for this user — "
                f"consider deactivating stale ones to keep memory focused "
                f"(hard cap is {HARD_CAP_ACTIVE})."
            )

        return SaveFactResult(fact_id=row.id, evicted_ids=evicted_ids, warning=warning)

    @classmethod
    @dual
    def deactivate(
        cls,
        user_id: int,
        fact_id: int,
        reason: str = "user_request",
        *,
        session: Session,
    ) -> bool:
        """Soft-delete a fact owned by ``user_id``. Returns True if a row was
        actually deactivated (i.e. it existed, was owned by this user, and was
        still active). False means the fact was already inactive, not found, or
        belongs to another user — callers SHOULD NOT distinguish these publicly
        (information disclosure), just report "couldn't deactivate".
        """
        result = session.execute(
            update(cls)
            .where(
                cls.id == fact_id,
                cls.user_id == user_id,
                cls.deactivated_at.is_(None),
            )
            .values(deactivated_at=func.now(), deactivated_reason=reason)
        )
        session.commit()
        return result.rowcount > 0

    @classmethod
    @dual
    def reactivate(cls, user_id: int, fact_id: int, *, session: Session) -> bool:
        """Reverse a ``deactivate`` call — used by the "↩️ Вернуть" undo button
        after a ``deactivate_fact`` tool call, so a mis-interpreted chat
        message doesn't silently lose context. Returns True if a row flipped
        back to active; False if it was already active, not found, or owned
        by another user. Tenant guard on ``user_id`` is load-bearing — this
        is the one method that turns a deactivated (= "deleted") row back on,
        easy target for accidental cross-tenant writes if the guard drifts.
        """
        result = session.execute(
            update(cls)
            .where(
                cls.id == fact_id,
                cls.user_id == user_id,
                cls.deactivated_at.is_not(None),
            )
            .values(deactivated_at=None, deactivated_reason=None)
        )
        session.commit()
        return result.rowcount > 0

    # ------------------------------------------------------------------
    #  Readers
    # ------------------------------------------------------------------

    @classmethod
    @dual
    def list_active(cls, user_id: int, *, session: Session) -> list[UserFact]:
        """All active facts for the user, stable-sorted for prompt cache
        friendliness: topic ASC, then created_at DESC (same order the partial
        index is built on). Any reshuffle invalidates the ``dynamic_tail``
        cache segment (see spec §6), so don't touch the sort without also
        re-checking ``cache_hit_rate_chat``.

        Expired facts (``expires_at < now()``) are filtered out — spec §3
        treats them as inactive for all reader purposes, even though no cron
        has yet set ``deactivated_at`` on them. A future cleanup job will
        back-fill ``deactivated_reason='expired'`` for audit; this filter
        keeps them out of the prompt immediately regardless.
        """
        return list(
            session.execute(
                select(cls)
                .where(
                    cls.user_id == user_id,
                    cls.deactivated_at.is_(None),
                    or_(cls.expires_at.is_(None), cls.expires_at > func.now()),
                )
                .order_by(cls.topic.asc(), cls.created_at.desc())
            )
            .scalars()
            .all()
        )

    @classmethod
    @dual
    def list_all(
        cls,
        user_id: int,
        include_inactive: bool = False,
        *,
        session: Session,
    ) -> list[UserFact]:
        """Used by ``list_facts`` MCP tool with ``include_inactive=True`` when
        the user asks "what do you remember about me, including stuff you
        forgot?". Without ``include_inactive``, expired facts are treated the
        same as deactivated ones (§3) and excluded.
        """
        stmt = select(cls).where(cls.user_id == user_id)
        if not include_inactive:
            stmt = stmt.where(
                cls.deactivated_at.is_(None),
                or_(cls.expires_at.is_(None), cls.expires_at > func.now()),
            )
        return list(session.execute(stmt.order_by(cls.topic.asc(), cls.created_at.desc())).scalars().all())

    @classmethod
    @dual
    def count_active(cls, user_id: int, *, session: Session) -> int:
        return _count_active(cls, user_id, session)


def _count_active(cls, user_id: int, session: Session) -> int:
    """Shared count helper — pulled out so internal ``save_with_cap`` paths
    and the public ``count_active`` hit the same query shape."""
    return int(
        session.execute(
            select(func.count(cls.id)).where(cls.user_id == user_id, cls.deactivated_at.is_(None))
        ).scalar_one()
    )
