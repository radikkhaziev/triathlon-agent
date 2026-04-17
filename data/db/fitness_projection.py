"""Fitness projection — CTL/ATL/rampRate curve from Intervals.icu FITNESS_UPDATED webhook.

Stores the projected decay of fitness metrics from today to race day under
zero future load assumption. Updated on every FITNESS_UPDATED webhook event.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from data.db.common import Base, Session
from data.db.decorator import dual


class FitnessProjection(Base):
    """Per-user daily fitness projection from Intervals.icu."""

    __tablename__ = "fitness_projection"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_fitness_projection_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date: Mapped[str] = mapped_column(String, nullable=False)  # "YYYY-MM-DD", can be future
    ctl: Mapped[float | None] = mapped_column(Float, nullable=True)
    atl: Mapped[float | None] = mapped_column(Float, nullable=True)
    ramp_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @classmethod
    @dual
    def save_bulk(cls, user_id: int, records: list[dict], *, session: Session) -> int:
        """Upsert fitness projection records from webhook payload.

        Inserts new (user_id, date) rows and updates existing ones.
        Rows for dates not present in ``records`` are left untouched.
        """
        if not records:
            return 0

        now = datetime.now(timezone.utc)
        rows = [
            {
                "user_id": user_id,
                "date": r["id"],
                "ctl": r.get("ctl"),
                "atl": r.get("atl"),
                "ramp_rate": r.get("rampRate"),
                "updated_at": now,
            }
            for r in records
        ]
        stmt = insert(cls).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_fitness_projection_user_date",
            set_={
                "ctl": stmt.excluded.ctl,
                "atl": stmt.excluded.atl,
                "ramp_rate": stmt.excluded.ramp_rate,
                "updated_at": now,
            },
        )
        session.execute(stmt)
        session.commit()
        return len(records)

    @classmethod
    @dual
    def get_projection(cls, user_id: int, *, session: Session) -> list[FitnessProjection]:
        """Get all projection records for a user, ordered by date."""
        result = session.execute(select(cls).where(cls.user_id == user_id).order_by(cls.date))
        return list(result.scalars().all())
