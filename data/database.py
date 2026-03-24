"""SQLAlchemy async models and CRUD operations for the triathlon training agent."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings
from data.models import Activity, RecoveryScore, RhrStatus, RmssdStatus, ScheduledWorkout, Wellness

logger = logging.getLogger(__name__)

_DEFAULT_RESTING_HR = 60  # fallback until Intervals.icu syncs today's wellness


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


class ActivityRow(Base):
    """Completed activity synced from Intervals.icu."""

    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # Intervals.icu activity ID (e.g. "i12345")
    start_date_local: Mapped[str] = mapped_column(String)  # "YYYY-MM-DD"
    type: Mapped[str | None] = mapped_column(String, nullable=True)  # Ride, Run, Swim, ...
    icu_training_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    moving_time: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    average_hr: Mapped[float | None] = mapped_column(Float, nullable=True)  # avg heart rate


class ActivityHrvRow(Base):
    """Post-activity HRV analysis (DFA alpha 1) — Level 2 pipeline."""

    __tablename__ = "activity_hrv"

    activity_id: Mapped[str] = mapped_column(String, ForeignKey("activities.id"), primary_key=True)
    date: Mapped[str] = mapped_column(String)  # "YYYY-MM-DD"
    activity_type: Mapped[str] = mapped_column(String)  # "Ride" | "Run"

    # Quality
    hrv_quality: Mapped[str | None] = mapped_column(String, nullable=True)  # good | moderate | poor
    artifact_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    rr_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # DFA alpha 1 summary
    dfa_a1_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    dfa_a1_warmup: Mapped[float | None] = mapped_column(Float, nullable=True)  # first 15 min

    # Thresholds (if detected)
    hrvt1_hr: Mapped[float | None] = mapped_column(Float, nullable=True)  # HR at a1=0.75
    hrvt1_power: Mapped[float | None] = mapped_column(Float, nullable=True)  # Power at a1=0.75 (bike)
    hrvt1_pace: Mapped[str | None] = mapped_column(String, nullable=True)  # Pace at a1=0.75 (run)
    hrvt2_hr: Mapped[float | None] = mapped_column(Float, nullable=True)  # HR at a1=0.50
    threshold_r_squared: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_confidence: Mapped[str | None] = mapped_column(String, nullable=True)  # high | moderate | low

    # Readiness (Ra)
    ra_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pa_today: Mapped[float | None] = mapped_column(Float, nullable=True)  # power/pace at fixed a1

    # Durability (Da)
    da_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Status: processed | no_rr_data | low_quality | too_short | error
    processing_status: Mapped[str] = mapped_column(String, default="processed")

    # Raw timeseries (JSON list) — for webapp charts
    dfa_timeseries: Mapped[list | None] = mapped_column(JSON, nullable=True)


class PaBaselineRow(Base):
    """Pa (power/pace at fixed DFA a1) baseline for Ra calculation."""

    __tablename__ = "pa_baseline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_type: Mapped[str] = mapped_column(String)  # "Ride" | "Run"
    date: Mapped[str] = mapped_column(String)  # "YYYY-MM-DD"
    pa_value: Mapped[float] = mapped_column(Float)  # Power/pace at fixed a1 (warmup)
    dfa_a1_ref: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str | None] = mapped_column(String, nullable=True)


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
) -> tuple[WellnessRow, bool]:
    """Upsert wellness data and run recovery pipeline.

    Maps Intervals.icu Wellness response to WellnessRow, computes RMSSD/RHR
    baselines and combined recovery score when sleep data is available.

    Returns:
        (row, ai_is_new) — the saved WellnessRow and whether AI recommendation
        was generated in this call.
    """
    from data.metrics import (
        calculate_banister_for_date,
        calculate_rhr_status,
        calculate_rmssd_status,
        combined_recovery_score,
    )

    async with get_session() as session:
        row = await session.get(WellnessRow, wellness.id or str(dt))
        if row is None:
            row = WellnessRow(id=wellness.id or str(dt))
            session.add(row)

        # --- Skip if data hasn't changed AND all computed fields are populated ---
        pipeline_complete = row.recovery_score is not None and row.ess_today is not None
        if row.updated and wellness.updated and row.updated == wellness.updated and pipeline_complete:
            logger.debug("Wellness %s unchanged (updated=%s), skipping pipeline", row.id, row.updated)
            return row, False

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

        # --- Recovery pipeline ---
        # HRV and RHR baselines don't depend on sleep — always compute them.
        # Combined recovery score excludes sleep when unavailable (weights renormalised).

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

        # 3. ESS / Banister pipeline
        # Reset before calculation so stale values don't leak into recovery score on failure
        row.ess_today = None
        row.banister_recovery = None
        try:
            dt_date = date.fromisoformat(row.id)
            banister_rows = await get_activities_for_banister(
                days=90,
                as_of=dt_date,
                session=session,
            )
            activities_by_date: dict[str, list] = defaultdict(list)
            for act in banister_rows:
                activities_by_date[act.start_date_local].append(act)

            hr_rest = row.resting_hr if row.resting_hr is not None else _DEFAULT_RESTING_HR
            banister_r, ess_today = calculate_banister_for_date(
                activities_by_date=activities_by_date,
                target_date=dt_date,
                hr_rest=hr_rest,
                hr_max=settings.ATHLETE_MAX_HR,
            )
            row.ess_today = ess_today
            row.banister_recovery = banister_r
        except Exception:
            logger.warning("ESS/Banister calculation failed for %s", row.id, exc_info=True)

        # 4. Combined recovery score (sleep=None → excluded, weights renormalised)
        recovery: RecoveryScore = combined_recovery_score(
            rmssd_status=rmssd,
            rhr_status=rhr,
            banister_recovery=row.banister_recovery if row.banister_recovery is not None else 50.0,
            sleep_score=int(row.sleep_score) if row.sleep_score is not None else None,
        )
        row.recovery_score = recovery.score
        row.recovery_category = recovery.category
        row.recovery_recommendation = recovery.recommendation

        # 5. Readiness (derived from recovery)
        row.readiness_score = int(recovery.score)
        _CATEGORY_TO_READINESS = {"excellent": "green", "good": "green", "moderate": "yellow", "low": "red"}
        row.readiness_level = _CATEGORY_TO_READINESS.get(recovery.category, "yellow")

        # 6. AI recommendation (only for today, not backfill)
        ai_is_new = False
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
                ai_is_new = row.ai_recommendation is not None
            except Exception:
                logger.exception("AI recommendation failed")

        await session.commit()

        return row, ai_is_new


# ---------------------------------------------------------------------------
# CRUD — Activities
# ---------------------------------------------------------------------------


async def save_activities(activities: list[Activity]) -> int:
    """Upsert completed activities from Intervals.icu. Returns count of upserted rows."""
    if not activities:
        return 0

    async with get_session() as session:
        values = [
            {
                "id": a.id,
                "start_date_local": str(a.start_date_local)[:10],
                "type": a.type,
                "icu_training_load": a.icu_training_load,
                "moving_time": a.moving_time,
                "average_hr": a.average_hr,
            }
            for a in activities
        ]
        stmt = insert(ActivityRow).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "start_date_local": stmt.excluded.start_date_local,
                "type": stmt.excluded.type,
                "icu_training_load": stmt.excluded.icu_training_load,
                "moving_time": stmt.excluded.moving_time,
                "average_hr": stmt.excluded.average_hr,
            },
        )
        await session.execute(stmt)
        await session.commit()
    return len(values)


async def get_activities_for_ctl(days: int = 90, as_of: date | None = None) -> list[ActivityRow]:
    """Return activities for CTL calculation, ordered by date (oldest first).

    Args:
        days: Window size in days.
        as_of: Reference date (default: today). Activities from
               (as_of - days) to as_of are returned.

    Returned objects are detached from session — safe to access simple columns
    but not lazy-loaded relationships.
    """
    from datetime import timedelta

    ref = as_of or date.today()
    cutoff = str(ref - timedelta(days=days))
    newest = str(ref)
    async with get_session() as session:
        result = await session.execute(
            select(ActivityRow)
            .where(ActivityRow.start_date_local >= cutoff)
            .where(ActivityRow.start_date_local <= newest)
            .where(ActivityRow.icu_training_load.isnot(None))
            .order_by(ActivityRow.start_date_local.asc())
        )
        return list(result.scalars().all())


async def get_activities_for_banister(
    days: int = 90,
    as_of: date | None = None,
    *,
    session: AsyncSession | None = None,
) -> list[ActivityRow]:
    """Return activities for Banister ESS calculation (need average_hr, not training_load).

    Args:
        days: Window size in days.
        as_of: Reference date (default: today).
        session: Optional session to reuse.
    """
    ref = as_of or date.today()
    cutoff = str(ref - timedelta(days=days))
    newest = str(ref)

    async def _query(s: AsyncSession) -> list[ActivityRow]:
        result = await s.execute(
            select(ActivityRow)
            .where(ActivityRow.start_date_local >= cutoff)
            .where(ActivityRow.start_date_local <= newest)
            .where(ActivityRow.average_hr.isnot(None))
            .where(ActivityRow.average_hr > 0)
            .order_by(ActivityRow.start_date_local.asc())
        )
        return list(result.scalars().all())

    if session:
        return await _query(session)
    async with get_session() as s:
        return await _query(s)


# ---------------------------------------------------------------------------
# CRUD — Scheduled Workouts
# ---------------------------------------------------------------------------


async def save_scheduled_workouts(
    workouts: list[ScheduledWorkout],
    oldest: date | None = None,
    newest: date | None = None,
) -> int:
    """Upsert scheduled workouts from Intervals.icu and delete stale ones.

    When oldest/newest are provided, any DB rows in that date range whose IDs
    are not in the incoming workouts list are deleted (workout removed or moved
    in Intervals.icu).

    Returns count of upserted rows.
    """
    async with get_session() as session:
        # --- upsert ---
        incoming_ids: set[int] = set()
        count = 0
        for w in workouts:
            incoming_ids.add(w.id)

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

        # --- delete stale rows in the synced date range ---
        if oldest is not None and newest is not None and incoming_ids:
            oldest_str = oldest.strftime("%Y-%m-%d")
            newest_str = newest.strftime("%Y-%m-%d")

            stale_q = delete(ScheduledWorkoutRow).where(
                ScheduledWorkoutRow.start_date_local >= oldest_str,
                ScheduledWorkoutRow.start_date_local <= newest_str,
                ScheduledWorkoutRow.id.notin_(incoming_ids),
            )
            result = await session.execute(stale_q)

            if result.rowcount:
                logger.info("Deleted %d stale scheduled workouts (%s → %s)", result.rowcount, oldest_str, newest_str)

        await session.commit()
    return count


async def get_scheduled_workouts_for_date(dt: date) -> list[ScheduledWorkoutRow]:
    """Return all scheduled workouts for a given date."""
    dt_str = str(dt)
    async with get_session() as session:
        result = await session.execute(
            select(ScheduledWorkoutRow).where(ScheduledWorkoutRow.start_date_local == dt_str)
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# CRUD — Activity HRV (Level 2)
# ---------------------------------------------------------------------------


async def get_unprocessed_activities(batch_size: int = 5) -> list[ActivityRow]:
    """Return bike/run activities not yet in activity_hrv, ≥15 min, newest first."""
    _ELIGIBLE_TYPES = (
        "Ride",
        "VirtualRide",
        "GravelRide",
        "MountainBikeRide",
        "Run",
        "VirtualRun",
        "TrailRun",
    )
    async with get_session() as session:
        subq = select(ActivityHrvRow.activity_id)
        result = await session.execute(
            select(ActivityRow)
            .where(ActivityRow.type.in_(_ELIGIBLE_TYPES))
            .where(ActivityRow.id.notin_(subq))
            .where(ActivityRow.moving_time >= 900)  # ≥15 min
            .order_by(ActivityRow.start_date_local.desc())
            .limit(batch_size)
        )
        return list(result.scalars().all())


async def save_activity_hrv(row: ActivityHrvRow) -> None:
    """Upsert an activity HRV analysis row."""
    async with get_session() as session:
        existing = await session.get(ActivityHrvRow, row.activity_id)
        if existing:
            for col in ActivityHrvRow.__table__.columns:
                if col.name != "activity_id":
                    setattr(existing, col.name, getattr(row, col.name))
        else:
            session.add(row)
        await session.commit()


async def save_pa_baseline(
    activity_type: str, dt: str, pa_value: float, dfa_a1_ref: float | None = None, quality: str | None = None
) -> None:
    """Save or update a Pa baseline data point (dedup on activity_type + date)."""
    async with get_session() as session:
        # Check for existing entry on same date + type
        result = await session.execute(
            select(PaBaselineRow)
            .where(PaBaselineRow.activity_type == activity_type)
            .where(PaBaselineRow.date == dt)
            .limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.pa_value = pa_value
            existing.dfa_a1_ref = dfa_a1_ref
            existing.quality = quality
        else:
            session.add(
                PaBaselineRow(
                    activity_type=activity_type,
                    date=dt,
                    pa_value=pa_value,
                    dfa_a1_ref=dfa_a1_ref,
                    quality=quality,
                )
            )
        await session.commit()


async def get_activities_for_date(dt: date) -> list[ActivityRow]:
    """Get all activities for a specific date."""
    dt_str = str(dt)
    async with get_session() as session:
        result = await session.execute(
            select(ActivityRow).where(ActivityRow.start_date_local == dt_str).order_by(ActivityRow.id)
        )
        return list(result.scalars().all())


async def get_activity_hrv_for_date(dt: date) -> list[ActivityHrvRow]:
    """Get all activity_hrv rows for activities on a specific date."""
    dt_str = str(dt)
    async with get_session() as session:
        result = await session.execute(
            select(ActivityHrvRow).where(ActivityHrvRow.date == dt_str).order_by(ActivityHrvRow.activity_id)
        )
        return list(result.scalars().all())


async def get_pa_baseline(activity_type: str, days: int = 14, as_of: date | None = None) -> float | None:
    """Return average Pa over last N days for a sport, or None if <3 data points."""
    ref = as_of or date.today()
    cutoff = str(ref - timedelta(days=days))
    async with get_session() as session:
        result = await session.execute(
            select(PaBaselineRow.pa_value)
            .where(PaBaselineRow.activity_type == activity_type)
            .where(PaBaselineRow.date >= cutoff)
            .where(PaBaselineRow.quality != "poor")
            .order_by(PaBaselineRow.date.desc())
        )
        values = [row[0] for row in result.all()]
    if len(values) < 3:
        return None
    return sum(values) / len(values)
