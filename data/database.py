"""SQLAlchemy async models and CRUD operations for the triathlon training agent."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings
from data.models import SleepData


async def send_telegram_message(text: str, *, bot) -> None:
    """Send a message to the configured Telegram chat."""
    if bot is None:
        return
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

    # Identity
    date: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Sleep (from get_sleep)
    sleep_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sleep_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sleep_stress_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_hrv_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_heart_rate_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Raw signals
    body_battery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resting_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # RMSSD baseline (from calculate_rmssd_status)
    hrv_rmssd_last: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_mean_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_sd_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_lower_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_upper_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_cv_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_swc: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_status: Mapped[str | None] = mapped_column(String, nullable=True)
    hrv_days_available: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hrv_algorithm: Mapped[str | None] = mapped_column(String, nullable=True)

    # Resting HR baseline (from calculate_rhr_status)
    rhr_status: Mapped[str | None] = mapped_column(String, nullable=True)
    rhr_lower_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    rhr_upper_bound: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Training load
    ctl: Mapped[float | None] = mapped_column(Float, nullable=True)
    atl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tsb: Mapped[float | None] = mapped_column(Float, nullable=True)
    ctl_swim: Mapped[float | None] = mapped_column(Float, nullable=True)
    ctl_bike: Mapped[float | None] = mapped_column(Float, nullable=True)
    ctl_run: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ESS and Banister
    ess_today: Mapped[float | None] = mapped_column(Float, nullable=True)
    banister_recovery: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Combined recovery (from combined_recovery_score)
    recovery_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovery_category: Mapped[str | None] = mapped_column(String, nullable=True)
    recovery_recommendation: Mapped[str | None] = mapped_column(String, nullable=True)

    # Readiness (from calculate_readiness)
    readiness_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    readiness_level: Mapped[str | None] = mapped_column(String, nullable=True)

    # AI output
    ai_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)


class ActivityRow(Base):
    __tablename__ = "activities"

    activity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String, index=True)
    sport: Mapped[str] = mapped_column(String)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    norm_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    tss: Mapped[float | None] = mapped_column(Float, nullable=True)
    ess: Mapped[float | None] = mapped_column(Float, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduledWorkoutRow(Base):
    __tablename__ = "scheduled_workouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheduled_date: Mapped[str] = mapped_column(String, index=True)
    sport: Mapped[str] = mapped_column(String)
    workout_name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_tss: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String, default="garmin")


class TSSHistoryRow(Base):
    __tablename__ = "tss_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, index=True)
    sport: Mapped[str] = mapped_column(String)
    tss: Mapped[float] = mapped_column(Float)


# ---------------------------------------------------------------------------
# CRUD — Daily Metrics
# ---------------------------------------------------------------------------


async def get_hrv_history(days: int = 60) -> list[float]:
    """Return last N sleep_hrv_avg values (oldest first), skipping nulls and zeroes."""
    async with get_session() as session:
        result = await session.execute(
            select(DailyMetricsRow.sleep_hrv_avg)
            .where(DailyMetricsRow.sleep_hrv_avg.isnot(None))
            .where(DailyMetricsRow.sleep_hrv_avg > 0)
            .order_by(DailyMetricsRow.date.desc())
            .limit(days)
        )
        return [float(row[0]) for row in reversed(result.all())]


async def get_rhr_history(days: int = 30) -> list[float]:
    """Return last N resting_hr values (oldest first), skipping nulls and zeroes."""
    async with get_session() as session:
        result = await session.execute(
            select(DailyMetricsRow.resting_hr)
            .where(DailyMetricsRow.resting_hr.isnot(None))
            .where(DailyMetricsRow.resting_hr > 0)
            .order_by(DailyMetricsRow.date.desc())
            .limit(days)
        )
        return [float(row[0]) for row in reversed(result.all())]


async def get_daily_metrics(dt: date) -> DailyMetricsRow | None:
    """Fetch a single daily metrics row by date."""
    async with get_session() as session:
        return await session.get(DailyMetricsRow, str(dt))


async def get_daily_metrics_range(start: date, end: date) -> list[DailyMetricsRow]:
    """Fetch daily metrics rows for a date range."""
    async with get_session() as session:
        result = await session.execute(
            select(DailyMetricsRow)
            .where(DailyMetricsRow.date >= str(start))
            .where(DailyMetricsRow.date <= str(end))
            .order_by(DailyMetricsRow.date)
        )
        return list(result.scalars().all())


async def get_activities(start: date, end: date) -> list[ActivityRow]:
    """Fetch activities for a date range."""
    async with get_session() as session:
        result = await session.execute(
            select(ActivityRow)
            .where(ActivityRow.date >= str(start))
            .where(ActivityRow.date <= str(end))
            .order_by(ActivityRow.date)
        )
        return list(result.scalars().all())


async def get_scheduled_workouts_range(start: date, end: date) -> list[ScheduledWorkoutRow]:
    """Fetch scheduled workouts for a date range."""
    async with get_session() as session:
        result = await session.execute(
            select(ScheduledWorkoutRow)
            .where(ScheduledWorkoutRow.scheduled_date >= str(start))
            .where(ScheduledWorkoutRow.scheduled_date <= str(end))
            .order_by(ScheduledWorkoutRow.scheduled_date)
        )
        return list(result.scalars().all())


async def get_tss_history(start: date, end: date) -> list[TSSHistoryRow]:
    """Fetch TSS history for a date range."""
    async with get_session() as session:
        result = await session.execute(
            select(TSSHistoryRow)
            .where(TSSHistoryRow.date >= str(start))
            .where(TSSHistoryRow.date <= str(end))
            .order_by(TSSHistoryRow.date)
        )
        return list(result.scalars().all())


async def save_activity(activity: ActivityRow) -> None:
    """Insert or update an activity row."""
    async with get_session() as session:
        existing = await session.get(ActivityRow, activity.activity_id)
        if existing:
            for col in (
                "sport",
                "duration_sec",
                "distance_m",
                "avg_hr",
                "max_hr",
                "avg_power",
                "norm_power",
                "tss",
                "ess",
            ):
                setattr(existing, col, getattr(activity, col))
        else:
            session.add(activity)
        await session.commit()


async def save_daily_metrics(
    dt: date,
    *,
    sleep_data: SleepData,
    hrv_data=None,
    body_battery_morning: int | None = None,
    resting_hr: float | None = None,
    readiness=None,
    workouts=None,
    bot=None,
) -> DailyMetricsRow:
    """Insert or update a daily metrics row (upsert by date).

    On wake-up detection: computes HRV Level 1 status, persists all fields,
    and sends morning report via Telegram.
    """
    from data.metrics import calculate_rhr_status, calculate_rmssd_status, combined_recovery_score

    async with get_session() as session:
        row = await session.get(DailyMetricsRow, str(dt))
        is_new = row is None
        if is_new:
            row = DailyMetricsRow(date=str(dt))
            session.add(row)

        had_sleep_score = bool(row.sleep_score)

        # --- Sleep fields ---
        for key, val in sleep_data.model_dump(exclude_none=True, exclude={"date", "start", "end"}).items():
            setattr(row, f"sleep_{key}", val)

        if sleep_data.start is not None:
            row.sleep_start = datetime.fromtimestamp(sleep_data.start / 1000, tz=timezone.utc)
        if sleep_data.end is not None:
            row.sleep_end = datetime.fromtimestamp(sleep_data.end / 1000, tz=timezone.utc)

        # --- Raw signals ---
        if body_battery_morning is not None:
            row.body_battery = body_battery_morning
        if resting_hr is not None:
            row.resting_hr = resting_hr

        await session.commit()
        await session.refresh(row)

        # --- Wake-up detected: full recovery pipeline & morning report ---
        if not had_sleep_score and row.sleep_score and row.sleep_end and row.sleep_end.date() == dt:

            # 1. RMSSD Level 1
            rmssd = await calculate_rmssd_status()
            if rmssd.status != "insufficient_data":
                row.hrv_rmssd_last = float(row.sleep_hrv_avg) if row.sleep_hrv_avg else None
                row.hrv_mean_7d = rmssd.rmssd_7d
                row.hrv_lower_bound = rmssd.lower_bound
                row.hrv_upper_bound = rmssd.upper_bound
                row.hrv_cv_7d = rmssd.cv_7d
                row.hrv_swc = rmssd.swc
                row.hrv_status = rmssd.status
                row.hrv_days_available = rmssd.days_available
                row.hrv_algorithm = settings.HRV_ALGORITHM

            # 2. RHR baseline
            rhr = await calculate_rhr_status()
            if rhr.status != "insufficient_data":
                row.rhr_status = rhr.status
                row.rhr_lower_bound = rhr.lower_bound
                row.rhr_upper_bound = rhr.upper_bound

            # 3. Combined recovery score
            sleep_start_hour = None
            if row.sleep_start:
                sleep_start_hour = row.sleep_start.hour + row.sleep_start.minute / 60.0

            recovery = combined_recovery_score(
                rmssd_status=rmssd,
                rhr_status=rhr,
                banister_recovery=row.banister_recovery or 100.0,
                sleep_score=row.sleep_score or 0,
                body_battery=row.body_battery or 50,
                sleep_start_hour=sleep_start_hour,
            )
            row.recovery_score = recovery.score
            row.recovery_category = recovery.category
            row.recovery_recommendation = recovery.recommendation

            # TODO: ESS/Banister pipeline — sync activities, compute ESS per activity,
            # run calculate_banister_recovery(), persist ess_today + banister_recovery
            # TODO: Claude AI — call claude_agent.analyze_morning(), persist ai_recommendation

            await session.commit()

            # Send morning report
            from bot.formatter import build_morning_report

            report = build_morning_report(
                sleep_data=sleep_data,
                rmssd=rmssd,
                rhr=rhr,
                recovery=recovery,
                hrv_data=hrv_data,
                body_battery_morning=body_battery_morning,
                resting_hr=resting_hr,
                readiness=readiness,
                workouts=workouts,
            )
            await send_telegram_message(report, bot=bot)

        return row
