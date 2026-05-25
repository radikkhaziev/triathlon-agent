"""Endurance Score — daily snapshot storage.

Stores the composite endurance score (`docs/ENDURANCE_SCORE_SPEC.md`) per
user, per day. Phase 2 of the spec — replaces Phase-1 on-the-fly computation
with O(N rows) reads.

Write paths (spec §7.0 triggers):
  · Level 1 — per-write hooks fire `actor_snapshot_endurance_scores(user_id)`
    after wellness/activities sync. Multiple times per day, idempotent via
    UNIQUE(user_id, snapshot_date).
  · Level 2 — daily 18:30 Belgrade cron via `_all_users` wrapper. Safety-net
    for users whose Level-1 actors didn't fire (Intervals.icu down, etc.) +
    captures natural decay of components rolling out of the 28d/8w windows.
  · Backfill CLI — `python -m cli backfill-endurance-scores` iterates per-user
    across N days, default-skip existing rows (`--force` to overwrite).

Read paths:
  · `/api/endurance-score` — endpoint reads `get_range()` + `get_latest()`.
  · Badge engine — pulls last 90/365/84 days of scores + zones for the four
    milestone rules in `data.endurance_score.compute_badge`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, Numeric, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from data.db.common import Base, Session
from data.db.decorator import dual


class EnduranceScore(Base):
    """Daily snapshot of a user's Endurance Score."""

    __tablename__ = "endurance_scores"
    __table_args__ = (UniqueConstraint("user_id", "snapshot_date", name="uq_endurance_scores_user_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    vo2max_composite: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    # {base, long_term, recent, duration, consistency, recovery, per_sport: [...]}.
    # See data.endurance_score.EnduranceComponents for the canonical shape.
    components: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @classmethod
    @dual
    def upsert(
        cls,
        user_id: int,
        snapshot_date: date,
        *,
        score: int,
        vo2max_composite: float | None,
        components: dict,
        session: Session,
    ) -> None:
        """Idempotent insert-or-update for one (user_id, snapshot_date) row.

        Safe to call from Level-1 actors (multiple times per day) and the
        daily Level-2 cron. ON CONFLICT updates score+components+vo2max with
        the new compute — older fields are not preserved (recompute is the
        single source of truth for the day).
        """
        now = datetime.now(timezone.utc)
        stmt = insert(cls).values(
            user_id=user_id,
            snapshot_date=snapshot_date,
            score=score,
            vo2max_composite=vo2max_composite,
            components=components,
            computed_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_endurance_scores_user_date",
            set_={
                "score": stmt.excluded.score,
                "vo2max_composite": stmt.excluded.vo2max_composite,
                "components": stmt.excluded.components,
                "computed_at": now,
            },
        )
        session.execute(stmt)
        session.commit()

    @classmethod
    @dual
    def get_latest(cls, user_id: int, *, session: Session) -> EnduranceScore | None:
        """Most-recent snapshot for the user (by ``snapshot_date DESC``).

        Used by morning report (Level 3, read-only) and by the endpoint's
        fallback path when today's row hasn't been computed yet.
        """
        result = session.execute(select(cls).where(cls.user_id == user_id).order_by(cls.snapshot_date.desc()).limit(1))
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_range(
        cls,
        user_id: int,
        start: date,
        end: date,
        *,
        session: Session,
    ) -> list[EnduranceScore]:
        """All snapshots in ``[start, end]`` ordered by ``snapshot_date ASC``.

        Drives the endpoint's `trend` series — caller picks the period (1m / 3m /
        6m / 1y) and we return rows in chronological order for direct chart
        rendering. Missing dates are not back-filled — gaps mean the actor
        didn't fire that day; the chart shows a connected line through them.
        """
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.snapshot_date >= start, cls.snapshot_date <= end)
            .order_by(cls.snapshot_date.asc())
        )
        return list(result.scalars().all())

    @classmethod
    @dual
    def get_history(
        cls,
        user_id: int,
        days: int,
        *,
        ref_date: date,
        session: Session,
    ) -> list[EnduranceScore]:
        """Last ``days`` snapshots ending at ``ref_date`` (inclusive).

        Powers the badge rule engine: #2 needs 90d of scores, #3 needs 365d,
        #4 needs the last 84d of zones. We fetch the full row (components
        unused here) because the cost is trivial and callers may want the
        zone classification by index.
        """
        start = ref_date - timedelta(days=days - 1)
        return cls.get_range(user_id, start, ref_date, session=session)

    @classmethod
    @dual
    def get_score_on(
        cls,
        user_id: int,
        target_date: date,
        *,
        session: Session,
    ) -> EnduranceScore | None:
        """Single-row lookup for a specific `(user_id, snapshot_date)`.

        Used by the zone-breakthrough badge (today's compute compares against
        yesterday's row) and by tests/CLI dry-run inspection.
        """
        result = session.execute(select(cls).where(cls.user_id == user_id, cls.snapshot_date == target_date))
        return result.scalar_one_or_none()
