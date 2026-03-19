"""SQLAlchemy async models and CRUD operations for the triathlon training agent."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings
from data.models import SleepData

# ---------------------------------------------------------------------------
# Telegram bot reference (set from bot/main.py after app is built)
# ---------------------------------------------------------------------------
_bot = None


def set_bot(bot) -> None:
    global _bot
    _bot = bot


async def _send_telegram_message(text: str) -> None:
    if _bot is None:
        return
    await _bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=text)


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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    sleep_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sleep_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
) -> DailyMetricsRow:
    """Insert or update a daily metrics row (upsert by date)."""

    async with get_session() as session:
        row = await session.get(DailyMetricsRow, str(dt))
        is_new = row is None
        if is_new:
            row = DailyMetricsRow(date=str(dt))
            session.add(row)

        row.sleep_score = sleep_data.score
        row.sleep_duration = sleep_data.duration
        row.sleep_start = (
            datetime.fromtimestamp(sleep_data.start / 1000, tz=timezone.utc)
            if sleep_data.start is not None
            else None
        )
        row.sleep_end = (
            datetime.fromtimestamp(sleep_data.end / 1000, tz=timezone.utc)
            if sleep_data.end is not None
            else None
        )
        row.sleep_stress_avg = sleep_data.stress_avg
        row.sleep_hrv_avg = sleep_data.hrv_avg
        row.sleep_heart_rate_avg = sleep_data.heart_rate_avg

        await session.commit()
        await session.refresh(row)

        if is_new:
            await _send_telegram_message("Пробуждение зафиксировано")

        return row


async def get_daily_metrics(dt: date) -> DailyMetricsRow | None:
    """Fetch a single day's metrics by date."""
    async with get_session() as session:
        return await session.get(DailyMetricsRow, str(dt))


async def get_daily_metrics_range(start: date, end: date) -> list[DailyMetricsRow]:
    """Fetch metrics for a date range (inclusive)."""
    async with get_session() as session:
        stmt = (
            select(DailyMetricsRow)
            .where(DailyMetricsRow.date >= str(start))
            .where(DailyMetricsRow.date <= str(end))
            .order_by(DailyMetricsRow.date)
        )
        result = await session.scalars(stmt)
        return list(result.all())


# ---------------------------------------------------------------------------
# CRUD — Activities
# ---------------------------------------------------------------------------


async def save_activity(
    activity_id: int,
    *,
    dt: date | None = None,
    sport: str | None = None,
    duration_sec: int | None = None,
    distance_m: float | None = None,
    avg_hr: float | None = None,
    max_hr: float | None = None,
    avg_power: float | None = None,
    norm_power: float | None = None,
    tss: float | None = None,
) -> ActivityRow:
    """Insert or update an activity (upsert by activity_id)."""
    async with get_session() as session:
        row = await session.get(ActivityRow, activity_id)
        if row is None:
            row = ActivityRow(activity_id=activity_id)
            session.add(row)

        fields = {
            "date": str(dt) if dt is not None else None,
            "sport": sport,
            "duration_sec": duration_sec,
            "distance_m": distance_m,
            "avg_hr": avg_hr,
            "max_hr": max_hr,
            "avg_power": avg_power,
            "norm_power": norm_power,
            "tss": tss,
        }
        for key, value in fields.items():
            if value is not None:
                setattr(row, key, value)

        await session.commit()
        await session.refresh(row)
        return row


async def get_activities(start_date: date, end_date: date) -> list[ActivityRow]:
    """Fetch activities within a date range (inclusive)."""
    async with get_session() as session:
        stmt = (
            select(ActivityRow)
            .where(ActivityRow.date >= str(start_date))
            .where(ActivityRow.date <= str(end_date))
            .order_by(ActivityRow.date)
        )
        result = await session.scalars(stmt)
        return list(result.all())


# ---------------------------------------------------------------------------
# CRUD — Scheduled Workouts
# ---------------------------------------------------------------------------


async def save_scheduled_workout(
    scheduled_date: date,
    workout_name: str,
    sport: str,
    *,
    description: str | None = None,
    planned_tss: float | None = None,
    source: str = "garmin",
) -> ScheduledWorkoutRow:
    """Insert a new scheduled workout."""
    async with get_session() as session:
        row = ScheduledWorkoutRow(
            scheduled_date=str(scheduled_date),
            workout_name=workout_name,
            sport=sport,
            description=description,
            planned_tss=planned_tss,
            source=source,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def get_scheduled_workouts(dt: date) -> list[ScheduledWorkoutRow]:
    """Fetch all scheduled workouts for a given date."""
    async with get_session() as session:
        stmt = (
            select(ScheduledWorkoutRow)
            .where(ScheduledWorkoutRow.scheduled_date == str(dt))
            .order_by(ScheduledWorkoutRow.id)
        )
        result = await session.scalars(stmt)
        return list(result.all())


async def get_scheduled_workouts_range(
    start_date: date, end_date: date
) -> list[ScheduledWorkoutRow]:
    """Fetch all scheduled workouts within a date range (inclusive)."""
    async with get_session() as session:
        stmt = (
            select(ScheduledWorkoutRow)
            .where(ScheduledWorkoutRow.scheduled_date >= str(start_date))
            .where(ScheduledWorkoutRow.scheduled_date <= str(end_date))
            .order_by(ScheduledWorkoutRow.scheduled_date, ScheduledWorkoutRow.id)
        )
        result = await session.scalars(stmt)
        return list(result.all())


# ---------------------------------------------------------------------------
# CRUD — TSS History
# ---------------------------------------------------------------------------


async def save_tss_history(dt: date, sport: str, tss: float) -> TSSHistoryRow:
    """Insert or update a TSS history entry (upsert by date + sport)."""
    async with get_session() as session:
        row = await session.get(TSSHistoryRow, (str(dt), sport))
        if row is None:
            row = TSSHistoryRow(date=str(dt), sport=sport, tss=tss)
            session.add(row)
        else:
            row.tss = tss

        await session.commit()
        await session.refresh(row)
        return row


async def get_tss_history(days: int = 42) -> list[TSSHistoryRow]:
    """Fetch TSS history for the last N days, ordered by date."""
    cutoff = date.today() - timedelta(days=days)
    async with get_session() as session:
        stmt = (
            select(TSSHistoryRow)
            .where(TSSHistoryRow.date >= str(cutoff))
            .order_by(TSSHistoryRow.date, TSSHistoryRow.sport)
        )
        result = await session.scalars(stmt)
        return list(result.all())
