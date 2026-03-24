"""SQLAlchemy async models and CRUD operations for the triathlon training agent."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings
from data.models import RecoveryScore, RhrStatus, RmssdStatus, ScheduledWorkout, Wellness

logger = logging.getLogger(__name__)


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


class WellnessRow(Base):
    __tablename__ = "wellness"

    # --- Intervals.icu fields ---
    id: Mapped[str] = mapped_column(String, primary_key=True)  # "YYYY-MM-DD"
    ctl: Mapped[float | None] = mapped_column(Float, nullable=True)
    atl: Mapped[float | None] = mapped_column(Float, nullable=True)
    ramp_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    ctl_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    atl_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    sport_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    resting_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hrv: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    vo2max: Mapped[float | None] = mapped_column(Float, nullable=True)
    steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- ESS and Banister ---
    ess_today: Mapped[float | None] = mapped_column(Float, nullable=True)
    banister_recovery: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- Combined recovery (computed) ---
    recovery_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovery_category: Mapped[str | None] = mapped_column(String, nullable=True)
    recovery_recommendation: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- Readiness (computed) ---
    readiness_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    readiness_level: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- AI output ---
    ai_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)


class HrvAnalysisRow(Base):
    __tablename__ = "hrv_analysis"

    date: Mapped[str] = mapped_column(String, ForeignKey("wellness.id"), primary_key=True)
    algorithm: Mapped[str] = mapped_column(String, primary_key=True)  # "flatt_esco" | "ai_endurance"

    status: Mapped[str] = mapped_column(String)  # green | yellow | red | insufficient_data
    rmssd_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rmssd_sd_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rmssd_60d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rmssd_sd_60d: Mapped[float | None] = mapped_column(Float, nullable=True)
    lower_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    cv_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    swc: Mapped[float | None] = mapped_column(Float, nullable=True)
    days_available: Mapped[int] = mapped_column(Integer)

    trend_direction: Mapped[str | None] = mapped_column(String, nullable=True)
    trend_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_r_squared: Mapped[float | None] = mapped_column(Float, nullable=True)


class RhrAnalysisRow(Base):
    __tablename__ = "rhr_analysis"

    date: Mapped[str] = mapped_column(String, ForeignKey("wellness.id"), primary_key=True)

    status: Mapped[str] = mapped_column(String)  # green | yellow | red | insufficient_data
    rhr_today: Mapped[float | None] = mapped_column(Float, nullable=True)
    rhr_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rhr_sd_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rhr_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rhr_sd_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rhr_60d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rhr_sd_60d: Mapped[float | None] = mapped_column(Float, nullable=True)
    lower_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    cv_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    days_available: Mapped[int] = mapped_column(Integer)

    trend_direction: Mapped[str | None] = mapped_column(String, nullable=True)
    trend_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_r_squared: Mapped[float | None] = mapped_column(Float, nullable=True)


class ScheduledWorkoutRow(Base):
    __tablename__ = "scheduled_workouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Intervals.icu event ID
    start_date_local: Mapped[str] = mapped_column(String)  # "YYYY-MM-DD"
    end_date_local: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String)  # WORKOUT | RACE_A | RACE_B ...
    type: Mapped[str | None] = mapped_column(String, nullable=True)  # Ride, Run, Swim, WeightTraining
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    moving_time: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    distance: Mapped[float | None] = mapped_column(Float, nullable=True)  # km
    workout_doc: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# CRUD — Wellness
# ---------------------------------------------------------------------------


async def get_hrv_history(days: int = 60, *, session: AsyncSession | None = None) -> list[float]:
    """Return last N HRV values (oldest first), skipping nulls and zeroes."""

    async def _query(s: AsyncSession) -> list[float]:
        result = await s.execute(
            select(WellnessRow.hrv)
            .where(WellnessRow.hrv.isnot(None))
            .where(WellnessRow.hrv > 0)
            .order_by(WellnessRow.id.desc())
            .limit(days)
        )
        return [float(row[0]) for row in reversed(result.all())]

    if session:
        return await _query(session)
    async with get_session() as s:
        return await _query(s)


async def get_hrv_analysis(dt: str, algorithm: str) -> HrvAnalysisRow | None:
    """Fetch HRV analysis for a date and algorithm."""
    async with get_session() as session:
        return await session.get(HrvAnalysisRow, (dt, algorithm))


async def get_rhr_analysis(dt: str) -> RhrAnalysisRow | None:
    """Fetch RHR analysis for a date."""
    async with get_session() as session:
        return await session.get(RhrAnalysisRow, dt)


async def get_rhr_history(days: int = 60, *, session: AsyncSession | None = None) -> list[float]:
    """Return last N resting_hr values (oldest first), skipping nulls and zeroes."""

    async def _query(s: AsyncSession) -> list[float]:
        result = await s.execute(
            select(WellnessRow.resting_hr)
            .where(WellnessRow.resting_hr.isnot(None))
            .where(WellnessRow.resting_hr > 0)
            .order_by(WellnessRow.id.desc())
            .limit(days)
        )
        return [float(row[0]) for row in reversed(result.all())]

    if session:
        return await _query(session)
    async with get_session() as s:
        return await _query(s)


async def get_wellness(dt: date) -> WellnessRow | None:
    """Fetch a single wellness row by date."""
    async with get_session() as session:
        return await session.get(WellnessRow, str(dt))


async def save_wellness(
    dt: date,
    *,
    wellness: Wellness,
    run_ai: bool = False,
) -> WellnessRow:
    """Upsert wellness data and run recovery pipeline.

    Maps Intervals.icu Wellness response to WellnessRow, computes RMSSD/RHR
    baselines and combined recovery score when sleep data is available.
    """
    from data.metrics import calculate_rhr_status, calculate_rmssd_status, combined_recovery_score

    async with get_session() as session:
        row = await session.get(WellnessRow, wellness.id or str(dt))
        if row is None:
            row = WellnessRow(id=wellness.id or str(dt))
            session.add(row)

        # --- Map Intervals.icu fields (whitelist, skip computed columns) ---
        _INTERVALS_FIELDS = {
            "ctl",
            "atl",
            "ramp_rate",
            "ctl_load",
            "atl_load",
            "sport_info",
            "weight",
            "resting_hr",
            "hrv",
            "sleep_secs",
            "sleep_score",
            "sleep_quality",
            "body_fat",
            "vo2max",
            "steps",
            "updated",
        }
        for field in _INTERVALS_FIELDS:
            val = getattr(wellness, field, None)
            if val is not None:
                setattr(row, field, val)

        await session.commit()
        await session.refresh(row)

        # --- Recovery pipeline: run if sleep data available ---
        # Recompute when sleep arrives later or data changes
        if row.sleep_score:

            # 1. RMSSD — run both algorithms, save to hrv_analysis
            algorithms = ["flatt_esco", "ai_endurance"]
            rmssd_results: dict[str, RmssdStatus] = {}
            for algo in algorithms:
                rmssd = await calculate_rmssd_status(algorithm=algo, session=session)
                rmssd_results[algo] = rmssd
                if rmssd.status != "insufficient_data":
                    hrv_row = await session.get(HrvAnalysisRow, (row.id, algo))
                    if hrv_row is None:
                        hrv_row = HrvAnalysisRow(date=row.id, algorithm=algo)
                        session.add(hrv_row)
                    hrv_row.status = rmssd.status
                    hrv_row.rmssd_7d = rmssd.rmssd_7d
                    hrv_row.rmssd_sd_7d = rmssd.rmssd_sd_7d
                    hrv_row.rmssd_60d = rmssd.rmssd_60d
                    hrv_row.rmssd_sd_60d = rmssd.rmssd_sd_60d
                    hrv_row.lower_bound = rmssd.lower_bound
                    hrv_row.upper_bound = rmssd.upper_bound
                    hrv_row.cv_7d = rmssd.cv_7d
                    hrv_row.swc = rmssd.swc
                    hrv_row.days_available = rmssd.days_available
                    if rmssd.trend:
                        hrv_row.trend_direction = rmssd.trend.direction
                        hrv_row.trend_slope = rmssd.trend.slope
                        hrv_row.trend_r_squared = rmssd.trend.r_squared

            # Use primary algorithm for recovery score
            rmssd = rmssd_results.get(settings.HRV_ALGORITHM, rmssd_results.get("flatt_esco"))

            # 2. RHR baseline → rhr_analysis table
            rhr: RhrStatus = await calculate_rhr_status(session=session)
            if rhr.status != "insufficient_data":
                rhr_row = await session.get(RhrAnalysisRow, row.id)
                if rhr_row is None:
                    rhr_row = RhrAnalysisRow(date=row.id)
                    session.add(rhr_row)
                rhr_row.status = rhr.status
                rhr_row.rhr_today = rhr.rhr_today
                rhr_row.rhr_7d = rhr.rhr_7d
                rhr_row.rhr_sd_7d = rhr.rhr_sd_7d
                rhr_row.rhr_30d = rhr.rhr_30d
                rhr_row.rhr_sd_30d = rhr.rhr_sd_30d
                rhr_row.rhr_60d = rhr.rhr_60d
                rhr_row.rhr_sd_60d = rhr.rhr_sd_60d
                rhr_row.lower_bound = rhr.lower_bound
                rhr_row.upper_bound = rhr.upper_bound
                rhr_row.cv_7d = rhr.cv_7d
                rhr_row.days_available = rhr.days_available
                if rhr.trend:
                    rhr_row.trend_direction = rhr.trend.direction
                    rhr_row.trend_slope = rhr.trend.slope
                    rhr_row.trend_r_squared = rhr.trend.r_squared

            # 3. Combined recovery score
            recovery: RecoveryScore = combined_recovery_score(
                rmssd_status=rmssd,
                rhr_status=rhr,
                banister_recovery=row.banister_recovery if row.banister_recovery is not None else 50.0,
                sleep_score=int(row.sleep_score) if row.sleep_score is not None else 0,
            )
            row.recovery_score = recovery.score
            row.recovery_category = recovery.category
            row.recovery_recommendation = recovery.recommendation

            # 4. Readiness (derived from recovery)
            row.readiness_score = int(recovery.score)
            _CATEGORY_TO_READINESS = {"excellent": "green", "good": "green", "moderate": "yellow", "low": "red"}
            row.readiness_level = _CATEGORY_TO_READINESS.get(recovery.category, "yellow")

            # TODO: ESS/Banister pipeline

            # 5. AI recommendation (only for today, not backfill)
            if run_ai and row.ai_recommendation is None:
                try:
                    from ai.claude_agent import ClaudeAgent

                    agent = ClaudeAgent()
                    hrv_flatt = await session.get(HrvAnalysisRow, (row.id, "flatt_esco"))
                    hrv_aie = await session.get(HrvAnalysisRow, (row.id, "ai_endurance"))
                    rhr_row = await session.get(RhrAnalysisRow, row.id)

                    # Fetch today's scheduled workouts
                    today_workouts = (
                        (
                            await session.execute(
                                select(ScheduledWorkoutRow).where(ScheduledWorkoutRow.start_date_local == row.id)
                            )
                        )
                        .scalars()
                        .all()
                    )

                    row.ai_recommendation = await agent.get_morning_recommendation(
                        wellness_row=row,
                        hrv_flatt=hrv_flatt,
                        hrv_aie=hrv_aie,
                        rhr_row=rhr_row,
                        scheduled_workouts=today_workouts,
                    )
                except Exception:
                    logger.exception("AI recommendation failed")

            await session.commit()

        return row


# ---------------------------------------------------------------------------
# CRUD — Scheduled Workouts
# ---------------------------------------------------------------------------


async def save_scheduled_workouts(workouts: list[ScheduledWorkout]) -> int:
    """Upsert scheduled workouts from Intervals.icu. Returns count of upserted rows."""
    if not workouts:
        return 0

    async with get_session() as session:
        count = 0
        for w in workouts:
            row = await session.get(ScheduledWorkoutRow, w.id)
            if row is None:
                row = ScheduledWorkoutRow(id=w.id)
                session.add(row)

            row.start_date_local = str(w.start_date_local)
            row.name = w.name
            row.category = w.category
            row.type = w.type
            row.description = w.description
            row.moving_time = w.moving_time
            row.distance = w.distance
            row.workout_doc = w.workout_doc
            if w.end_date_local:
                row.end_date_local = str(w.end_date_local)
            if w.updated:
                row.updated = w.updated
            count += 1

        await session.commit()
    return count
