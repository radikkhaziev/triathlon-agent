"""SQLAlchemy async models and CRUD operations for the triathlon training agent."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings
from data.models import SleepData


async def send_telegram_message(text: str, *, bot) -> None:
    if bot is None:
        return
    """Send a message to the configured Telegram chat."""
    await bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=text)


# ---------------------------------------------------------------------------
# Engine / Session helpers
# ---------------------------------------------------------------------------

_engine = None
_SessionLocal: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    """Return a singleton async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.DATABASE_URL, echo=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the async session factory (singleton)."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = async_sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session with automatic close."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Declarative Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class DailyMetricsRow(Base):
    __tablename__ = "daily_metrics"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sleep_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sleep_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sleep_stress_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_hrv_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_heart_rate_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # hrv_last: Mapped[float | None] = mapped_column(Float, nullable=True)
    # hrv_baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    # body_battery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # resting_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    # stress_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # readiness_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # readiness_level: Mapped[str | None] = mapped_column(String, nullable=True)
    # ctl: Mapped[float | None] = mapped_column(Float, nullable=True)
    # atl: Mapped[float | None] = mapped_column(Float, nullable=True)
    # tsb: Mapped[float | None] = mapped_column(Float, nullable=True)
    # ctl_swim: Mapped[float | None] = mapped_column(Float, nullable=True)
    # ctl_bike: Mapped[float | None] = mapped_column(Float, nullable=True)
    # ctl_run: Mapped[float | None] = mapped_column(Float, nullable=True)
    # ai_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# CRUD — Daily Metrics
# ---------------------------------------------------------------------------


async def save_daily_metrics(
    dt: date,
    *,
    sleep_data: SleepData,
    bot=None,
) -> DailyMetricsRow:
    """Insert or update a daily metrics row (upsert by date)."""

    async with get_session() as session:
        row = await session.get(DailyMetricsRow, str(dt))
        is_new = row is None
        if is_new:
            row = DailyMetricsRow(date=str(dt))
            session.add(row)

        for key, val in sleep_data.model_dump(exclude_none=True, exclude={"date", "start", "end"}).items():
            setattr(row, f"sleep_{key}", val)

        if sleep_data.start is not None:
            row.sleep_start = datetime.fromtimestamp(sleep_data.start / 1000, tz=timezone.utc)
        if sleep_data.end is not None:
            row.sleep_end = datetime.fromtimestamp(sleep_data.end / 1000, tz=timezone.utc)

        await session.commit()
        await session.refresh(row)

        if is_new and row.sleep_end is not None and row.sleep_end.date() == dt:
            await send_telegram_message("Пробуждение зафиксировано", bot=bot)

        return row
