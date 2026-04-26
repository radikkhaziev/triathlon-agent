from __future__ import annotations

import datetime as _dt
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .common import Base
from .decorator import with_session


class MoodCheckin(Base):
    """Daily mood and emotional state check-ins."""

    __tablename__ = "mood_checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    energy: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    mood: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    anxiety: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    social: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- CRUD ---

    @classmethod
    @with_session
    async def save(
        cls,
        user_id: int,
        energy: int | None = None,
        mood: int | None = None,
        anxiety: int | None = None,
        social: int | None = None,
        note: str | None = None,
        *,
        session: AsyncSession,
    ) -> MoodCheckin:
        """Create a mood check-in with optional fields."""
        if all(x is None for x in [energy, mood, anxiety, social, note]):
            raise ValueError("At least one field must be provided")

        for field, value in [("energy", energy), ("mood", mood), ("anxiety", anxiety), ("social", social)]:
            if value is not None and not (1 <= value <= 5):
                raise ValueError(f"{field} must be between 1 and 5")

        row = cls(
            user_id=user_id,
            timestamp=datetime.now(timezone.utc),
            energy=energy,
            mood=mood,
            anxiety=anxiety,
            social=social,
            note=note,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row

    @classmethod
    @with_session
    async def get_range(
        cls,
        user_id: int,
        target_date: str | None = None,
        days_back: int = 7,
        *,
        session: AsyncSession,
    ) -> list[MoodCheckin]:
        """Get mood check-ins for a date range."""
        if target_date:
            ref_date = _dt.date.fromisoformat(target_date)
        else:
            ref_date = _dt.date.today()

        cutoff_date = ref_date - timedelta(days=days_back - 1)
        cutoff_dt = datetime.combine(cutoff_date, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(ref_date, datetime.max.time(), tzinfo=timezone.utc)

        result = await session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.timestamp >= cutoff_dt, cls.timestamp <= end_dt)
            .order_by(cls.timestamp.asc())
        )
        return list(result.scalars().all())


class ApiUsageDaily(Base):
    """Daily API token usage per user. One row per user per date."""

    __tablename__ = "api_usage_daily"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_api_usage_daily_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)  # "YYYY-MM-DD"
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # --- CRUD ---

    @classmethod
    @with_session
    async def increment(
        cls,
        user_id: int,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        *,
        session: AsyncSession,
    ) -> ApiUsageDaily:
        """Atomic upsert: increment daily token counters."""
        dt = str(_dt.date.today())
        stmt = (
            insert(cls)
            .values(
                user_id=user_id,
                date=dt,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
                request_count=1,
            )
            .on_conflict_do_update(
                constraint="uq_api_usage_daily_user_date",
                set_={
                    "input_tokens": cls.input_tokens + input_tokens,
                    "output_tokens": cls.output_tokens + output_tokens,
                    "cache_read_tokens": cls.cache_read_tokens + cache_read_tokens,
                    "cache_creation_tokens": cls.cache_creation_tokens + cache_creation_tokens,
                    "request_count": cls.request_count + 1,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            .returning(cls)
        )
        row = (await session.execute(stmt)).scalars().one()
        await session.commit()
        return row

    @classmethod
    @with_session
    async def get_today_request_count(cls, user_id: int, *, session: AsyncSession) -> int:
        """Return today's ``request_count`` for ``user_id``, or 0 if no row.

        Used by ``handle_chat_message`` / ``handle_photo_message`` to enforce
        ``settings.CHAT_DAILY_LIMIT`` before invoking Claude. Cheap (PK lookup
        on the unique ``(user_id, date)`` constraint), called once per chat
        message.
        """
        dt = str(_dt.date.today())
        result = await session.execute(select(cls.request_count).where(cls.user_id == user_id, cls.date == dt))
        return result.scalar_one_or_none() or 0

    @classmethod
    @with_session
    async def get_range(
        cls,
        user_id: int,
        target_date: str | None = None,
        days_back: int = 30,
        *,
        session: AsyncSession,
    ) -> list[ApiUsageDaily]:
        """Get daily usage for a date range."""
        ref = _dt.date.fromisoformat(target_date) if target_date else _dt.date.today()
        from_date = ref - timedelta(days=days_back - 1)
        result = await session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.date >= str(from_date), cls.date <= str(ref))
            .order_by(cls.date.asc())
        )
        return list(result.scalars().all())


class IqosDaily(Base):
    """Daily IQOS stick counter. One row per date."""

    __tablename__ = "iqos_daily"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_iqos_daily_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date: Mapped[str] = mapped_column(String, nullable=False)  # "YYYY-MM-DD"
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    # --- CRUD ---

    @classmethod
    @with_session
    async def increment(
        cls,
        user_id: int,
        target_date: _dt.date | None = None,
        *,
        session: AsyncSession,
    ) -> IqosDaily:
        """Increment IQOS stick count for the given date (default: today)."""
        dt = target_date or _dt.date.today()
        date_str = str(dt)

        stmt = (
            insert(cls)
            .values(user_id=user_id, date=date_str, count=1, updated=datetime.now(timezone.utc))
            .on_conflict_do_update(
                constraint="uq_iqos_daily_user_date",
                set_={"count": cls.count + 1, "updated": datetime.now(timezone.utc)},
            )
            .returning(cls)
        )
        row = (await session.execute(stmt)).scalars().one()
        await session.commit()
        return row

    @classmethod
    @with_session
    async def get(
        cls,
        user_id: int,
        target_date: _dt.date | None = None,
        *,
        session: AsyncSession,
    ) -> IqosDaily | None:
        """Get IQOS stick count for a single date (default: today)."""
        dt = target_date or _dt.date.today()
        date_str = str(dt)

        result = await session.execute(select(cls).where(cls.user_id == user_id, cls.date == date_str))
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def get_range(
        cls,
        user_id: int,
        target_date: str | None = None,
        days_back: int = 7,
        *,
        session: AsyncSession,
    ) -> list[IqosDaily]:
        """Get IQOS stick counts for a date range."""
        ref = _dt.date.fromisoformat(target_date) if target_date else _dt.date.today()
        from_date = ref - timedelta(days=days_back - 1)

        result = await session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.date >= str(from_date), cls.date <= str(ref))
            .order_by(cls.date.asc())
        )
        return list(result.scalars().all())


class StarTransaction(Base):
    """Telegram Stars (XTR) donation ledger. See docs/DONATE_SPEC.md.

    `charge_id` is the idempotency key — `successful_payment` webhook may
    be retried by Telegram, so `create()` relies on the UNIQUE constraint
    to drop duplicates. `refunded_at` is set manually via shell when the
    operator issues `bot.refund_star_payment()`.
    """

    __tablename__ = "star_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    charge_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    payload: Mapped[str] = mapped_column(String, nullable=False)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    @classmethod
    @with_session
    async def create(
        cls,
        user_id: int,
        amount: int,
        charge_id: str,
        payload: str,
        *,
        session: AsyncSession,
    ) -> StarTransaction | None:
        """Idempotent insert via `ON CONFLICT (charge_id) DO NOTHING`.

        Returns the new row on first insert, `None` if a row with the same
        `charge_id` already exists (Telegram webhook retry). Single statement —
        no rollback/reselect dance, and any real error (FK violation,
        connection loss) propagates untouched.
        """
        stmt = (
            insert(cls)
            .values(user_id=user_id, amount=amount, charge_id=charge_id, payload=payload)
            .on_conflict_do_nothing(index_elements=["charge_id"])
            .returning(cls)
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        await session.commit()
        return row

    @classmethod
    @with_session
    async def get_by_charge_id(cls, charge_id: str, *, session: AsyncSession) -> StarTransaction | None:
        result = await session.execute(select(cls).where(cls.charge_id == charge_id))
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def get_by_user(
        cls,
        user_id: int,
        days_back: int = 30,
        *,
        session: AsyncSession,
    ) -> list[StarTransaction]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        result = await session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.refunded_at.is_(None), cls.created_at >= cutoff)
            .order_by(cls.created_at.desc())
        )
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def mark_refunded(cls, charge_id: str, *, session: AsyncSession) -> None:
        await session.execute(update(cls).where(cls.charge_id == charge_id).values(refunded_at=func.now()))
        await session.commit()
