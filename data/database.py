"""SQLAlchemy async models and CRUD operations for the triathlon training agent."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, delete, func, select
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
# ORM Models + CRUD
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
    ai_recommendation_gemini: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- CRUD ---

    @classmethod
    async def get_hrv_history(cls, days: int = 60, *, session: AsyncSession | None = None) -> list[float]:
        """Return last N HRV values (oldest first), skipping nulls and zeroes."""

        async def _query(s: AsyncSession) -> list[float]:
            result = await s.execute(
                select(cls.hrv).where(cls.hrv.isnot(None)).where(cls.hrv > 0).order_by(cls.id.desc()).limit(days)
            )
            return [float(row[0]) for row in reversed(result.all())]

        if session:
            return await _query(session)
        async with get_session() as s:
            return await _query(s)

    @classmethod
    async def get_rhr_history(cls, days: int = 60, *, session: AsyncSession | None = None) -> list[float]:
        """Return last N resting_hr values (oldest first), skipping nulls and zeroes."""

        async def _query(s: AsyncSession) -> list[float]:
            result = await s.execute(
                select(cls.resting_hr)
                .where(cls.resting_hr.isnot(None))
                .where(cls.resting_hr > 0)
                .order_by(cls.id.desc())
                .limit(days)
            )
            return [float(row[0]) for row in reversed(result.all())]

        if session:
            return await _query(session)
        async with get_session() as s:
            return await _query(s)

    @classmethod
    async def get(cls, dt: date) -> WellnessRow | None:
        """Fetch a single wellness row by date."""
        async with get_session() as session:
            return await session.get(cls, str(dt))

    @classmethod
    async def save(
        cls,
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
            row = await session.get(cls, wellness.id or str(dt))
            if row is None:
                row = cls(id=wellness.id or str(dt))
                session.add(row)

            # --- Skip if data hasn't changed AND all computed fields are populated ---
            pipeline_complete = row.recovery_score is not None and row.ess_today is not None
            ai_pending = run_ai and (row.ai_recommendation is None or row.ai_recommendation_gemini is None)
            if (
                row.updated
                and wellness.updated
                and row.updated == wellness.updated
                and pipeline_complete
                and not ai_pending
            ):
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
                banister_rows = await ActivityRow.get_for_banister(
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
            # Runs Claude always + Gemini in parallel if configured.
            ai_is_new = False
            need_claude = run_ai and row.ai_recommendation is None
            need_gemini = run_ai and row.ai_recommendation_gemini is None
            if need_claude or need_gemini:
                try:
                    # Inline import: claude_agent imports from data.database (circular)
                    from ai.claude_agent import ClaudeAgent, build_morning_prompt

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

                    # Build prompts — separate templates for Claude and Gemini
                    prompt_kwargs = dict(
                        wellness_row=row,
                        hrv_flatt=hrv_flatt,
                        hrv_aie=hrv_aie,
                        rhr_row=rhr_row,
                        scheduled_workouts=today_workouts,
                    )

                    # Prepare tasks for parallel execution
                    tasks: dict[str, asyncio.Task] = {}
                    if need_claude:
                        agent = ClaudeAgent()

                        async def _claude():
                            from config import settings as _settings

                            if _settings.AI_USE_TOOL_USE:
                                try:
                                    return await agent.get_morning_recommendation_v2(date.fromisoformat(row.id))
                                except Exception:
                                    logger.warning("Tool-use V2 failed, falling back to V1", exc_info=True)
                            # V1 fallback
                            prompt_claude = await build_morning_prompt(**prompt_kwargs)
                            return await agent.get_morning_recommendation(
                                wellness_row=row,
                                hrv_flatt=hrv_flatt,
                                hrv_aie=hrv_aie,
                                rhr_row=rhr_row,
                                prompt=prompt_claude,
                            )

                        tasks["claude"] = asyncio.ensure_future(_claude())

                    # Gemini — only if GOOGLE_AI_API_KEY is configured
                    if need_gemini:
                        from ai.gemini_agent import GeminiAgent, is_gemini_enabled

                        if not is_gemini_enabled():
                            need_gemini = False
                    if need_gemini:
                        from ai.prompts import MORNING_REPORT_PROMPT_GEMINI

                        prompt_gemini = await build_morning_prompt(
                            **prompt_kwargs, template=MORNING_REPORT_PROMPT_GEMINI
                        )
                        gemini = GeminiAgent()

                        async def _gemini():
                            return await gemini.get_morning_recommendation(prompt_gemini)

                        tasks["gemini"] = asyncio.ensure_future(_gemini())

                    # Run in parallel
                    if tasks:
                        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
                        task_names = list(tasks.keys())
                        for name, result in zip(task_names, results):
                            if isinstance(result, Exception):
                                logger.error("AI recommendation (%s) failed: %s", name, result, exc_info=result)
                                continue
                            if name == "claude":
                                row.ai_recommendation = result
                                ai_is_new = True
                            elif name == "gemini":
                                row.ai_recommendation_gemini = result
                                ai_is_new = True
                except Exception:
                    logger.exception("AI recommendation failed")

            await session.commit()

            return row, ai_is_new


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

    # --- CRUD ---

    @classmethod
    async def get(cls, dt: str, algorithm: str) -> HrvAnalysisRow | None:
        """Fetch HRV analysis for a date and algorithm."""
        async with get_session() as session:
            return await session.get(cls, (dt, algorithm))


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

    # --- CRUD ---

    @classmethod
    async def get(cls, dt: str) -> RhrAnalysisRow | None:
        """Fetch RHR analysis for a date."""
        async with get_session() as session:
            return await session.get(cls, dt)


class ActivityRow(Base):
    """Completed activity synced from Intervals.icu."""

    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # Intervals.icu activity ID (e.g. "i12345")
    start_date_local: Mapped[str] = mapped_column(String)  # "YYYY-MM-DD"
    type: Mapped[str | None] = mapped_column(String, nullable=True)  # Ride, Run, Swim, ...
    icu_training_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    moving_time: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    average_hr: Mapped[float | None] = mapped_column(Float, nullable=True)  # avg heart rate
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- CRUD ---

    @classmethod
    async def save_bulk(cls, activities: list[Activity]) -> int:
        """Upsert completed activities from Intervals.icu. Returns count of upserted rows."""
        if not activities:
            return 0

        now = datetime.now(timezone.utc)
        async with get_session() as session:
            values = [
                {
                    "id": a.id,
                    "start_date_local": str(a.start_date_local)[:10],
                    "type": a.type,
                    "icu_training_load": a.icu_training_load,
                    "moving_time": a.moving_time,
                    "average_hr": a.average_hr,
                    "last_synced_at": now,
                }
                for a in activities
            ]
            stmt = insert(cls).values(values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "start_date_local": stmt.excluded.start_date_local,
                    "type": stmt.excluded.type,
                    "icu_training_load": stmt.excluded.icu_training_load,
                    "moving_time": stmt.excluded.moving_time,
                    "average_hr": stmt.excluded.average_hr,
                    "last_synced_at": stmt.excluded.last_synced_at,
                },
            )
            await session.execute(stmt)
            await session.commit()
        return len(values)

    @classmethod
    async def get_for_ctl(cls, days: int = 90, as_of: date | None = None) -> list[ActivityRow]:
        """Return activities for CTL calculation, ordered by date (oldest first).

        Args:
            days: Window size in days.
            as_of: Reference date (default: today). Activities from
                   (as_of - days) to as_of are returned.

        Returned objects are detached from session — safe to access simple columns
        but not lazy-loaded relationships.
        """
        ref = as_of or date.today()
        cutoff = str(ref - timedelta(days=days))
        newest = str(ref)
        async with get_session() as session:
            result = await session.execute(
                select(cls)
                .where(cls.start_date_local >= cutoff)
                .where(cls.start_date_local <= newest)
                .where(cls.icu_training_load.isnot(None))
                .order_by(cls.start_date_local.asc())
            )
            return list(result.scalars().all())

    @classmethod
    async def get_for_banister(
        cls,
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
                select(cls)
                .where(cls.start_date_local >= cutoff)
                .where(cls.start_date_local <= newest)
                .where(cls.average_hr.isnot(None))
                .where(cls.average_hr > 0)
                .order_by(cls.start_date_local.asc())
            )
            return list(result.scalars().all())

        if session:
            return await _query(session)
        async with get_session() as s:
            return await _query(s)

    @classmethod
    async def get_for_date(cls, dt: date) -> list[ActivityRow]:
        """Get all activities for a specific date."""
        dt_str = str(dt)
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.start_date_local == dt_str).order_by(cls.id))
            return list(result.scalars().all())

    @classmethod
    async def get_range(cls, start: date, end: date) -> tuple[list[ActivityRow], datetime | None]:
        """Return activities in date range and MAX(last_synced_at)."""
        start_str, end_str = str(start), str(end)
        async with get_session() as session:
            result = await session.execute(
                select(cls)
                .where(cls.start_date_local >= start_str)
                .where(cls.start_date_local <= end_str)
                .order_by(cls.start_date_local, cls.id)
            )
            activities = list(result.scalars().all())

            sync_result = await session.execute(select(func.max(cls.last_synced_at)))
            last_synced_at = sync_result.scalar_one_or_none()

        return activities, last_synced_at

    @classmethod
    async def get_unprocessed(cls, batch_size: int = 5) -> list[ActivityRow]:
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
                select(cls)
                .where(cls.type.in_(_ELIGIBLE_TYPES))
                .where(cls.id.notin_(subq))
                .where(cls.moving_time >= 900)  # ≥15 min
                .order_by(cls.start_date_local.desc())
                .limit(batch_size)
            )
            return list(result.scalars().all())

    @classmethod
    async def get_without_details(
        cls,
        limit: int = 0,
        since_date: str | None = None,
    ) -> list[ActivityRow]:
        """Return activities that don't have a corresponding activity_details row.

        Args:
            limit: Max number of rows to return. 0 = no limit.
            since_date: Only include activities on or after this date ("YYYY-MM-DD").
        """
        async with get_session() as session:
            subq = select(ActivityDetailRow.activity_id)
            stmt = select(cls).where(cls.id.notin_(subq)).order_by(cls.start_date_local.desc())
            if since_date:
                stmt = stmt.where(cls.start_date_local >= since_date)
            if limit > 0:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())


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

    # --- CRUD ---

    @classmethod
    async def save(cls, row: ActivityHrvRow) -> None:
        """Upsert an activity HRV analysis row."""
        async with get_session() as session:
            existing = await session.get(cls, row.activity_id)
            if existing:
                for col in cls.__table__.columns:
                    if col.name != "activity_id":
                        setattr(existing, col.name, getattr(row, col.name))
            else:
                session.add(row)
            await session.commit()

    @classmethod
    async def get_for_date(cls, dt: date) -> list[ActivityHrvRow]:
        """Get all activity_hrv rows for activities on a specific date."""
        dt_str = str(dt)
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.date == dt_str).order_by(cls.activity_id))
            return list(result.scalars().all())


class PaBaselineRow(Base):
    """Pa (power/pace at fixed DFA a1) baseline for Ra calculation."""

    __tablename__ = "pa_baseline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_type: Mapped[str] = mapped_column(String)  # "Ride" | "Run"
    date: Mapped[str] = mapped_column(String)  # "YYYY-MM-DD"
    pa_value: Mapped[float] = mapped_column(Float)  # Power/pace at fixed a1 (warmup)
    dfa_a1_ref: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- CRUD ---

    @classmethod
    async def save(
        cls, activity_type: str, dt: str, pa_value: float, dfa_a1_ref: float | None = None, quality: str | None = None
    ) -> None:
        """Save or update a Pa baseline data point (dedup on activity_type + date)."""
        async with get_session() as session:
            # Check for existing entry on same date + type
            result = await session.execute(
                select(cls).where(cls.activity_type == activity_type).where(cls.date == dt).limit(1)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.pa_value = pa_value
                existing.dfa_a1_ref = dfa_a1_ref
                existing.quality = quality
            else:
                session.add(
                    cls(
                        activity_type=activity_type,
                        date=dt,
                        pa_value=pa_value,
                        dfa_a1_ref=dfa_a1_ref,
                        quality=quality,
                    )
                )
            await session.commit()

    @classmethod
    async def get_average(cls, activity_type: str, days: int = 14, as_of: date | None = None) -> float | None:
        """Return average Pa over last N days for a sport, or None if <3 data points."""
        ref = as_of or date.today()
        cutoff = str(ref - timedelta(days=days))
        async with get_session() as session:
            result = await session.execute(
                select(cls.pa_value)
                .where(cls.activity_type == activity_type)
                .where(cls.date >= cutoff)
                .where(cls.quality != "poor")
                .order_by(cls.date.desc())
            )
            values = [row[0] for row in result.all()]
        if len(values) < 3:
            return None
        return sum(values) / len(values)


class ActivityDetailRow(Base):
    """Extended activity statistics from Intervals.icu API."""

    __tablename__ = "activity_details"

    activity_id: Mapped[str] = mapped_column(String, ForeignKey("activities.id"), primary_key=True)
    max_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_power: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalized_power: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    pace: Mapped[float | None] = mapped_column(Float, nullable=True)
    gap: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_gain: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_cadence: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_stride: Mapped[float | None] = mapped_column(Float, nullable=True)
    calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    intensity_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    variability_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    efficiency_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    power_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    decoupling: Mapped[float | None] = mapped_column(Float, nullable=True)
    trimp: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_zones: Mapped[list | None] = mapped_column(JSON, nullable=True)
    power_zones: Mapped[list | None] = mapped_column(JSON, nullable=True)
    pace_zones: Mapped[list | None] = mapped_column(JSON, nullable=True)
    intervals: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Mapping: Intervals.icu JSON key → ActivityDetailRow column
    _DETAIL_FIELD_MAP = {
        "max_heartrate": "max_hr",
        "icu_average_watts": "avg_power",
        "icu_weighted_avg_watts": "normalized_power",
        "max_speed": "max_speed",
        "average_speed": "avg_speed",
        "pace": "pace",
        "gap": "gap",
        "distance": "distance",
        "total_elevation_gain": "elevation_gain",
        "average_cadence": "avg_cadence",
        "average_stride": "avg_stride",
        "calories": "calories",
        "icu_intensity": "intensity_factor",
        "icu_variability_index": "variability_index",
        "icu_efficiency_factor": "efficiency_factor",
        "icu_power_hr": "power_hr",
        "decoupling": "decoupling",
        "trimp": "trimp",
        "icu_hr_zones": "hr_zones",
        "icu_power_zones": "power_zones",
        "pace_zones": "pace_zones",
    }

    # --- CRUD ---

    @classmethod
    async def save(
        cls,
        activity_id: str,
        detail_json: dict,
        intervals_json: list[dict] | None = None,
    ) -> None:
        """Upsert activity details from Intervals.icu API response."""
        async with get_session() as session:
            row = await session.get(cls, activity_id)
            if row is None:
                row = cls(activity_id=activity_id)
                session.add(row)

            for api_key, col_name in cls._DETAIL_FIELD_MAP.items():
                if api_key in detail_json:
                    setattr(row, col_name, detail_json[api_key])

            if intervals_json is not None:
                row.intervals = intervals_json

            await session.commit()

    @classmethod
    async def get(cls, activity_id: str) -> ActivityDetailRow | None:
        """Fetch activity details by activity ID."""
        async with get_session() as session:
            return await session.get(cls, activity_id)

    @classmethod
    async def get_existing_ids(cls, activity_ids: list[str]) -> set[str]:
        """Return the subset of activity_ids that already have an activity_details row."""
        if not activity_ids:
            return set()
        async with get_session() as session:
            result = await session.execute(select(cls.activity_id).where(cls.activity_id.in_(activity_ids)))
            return {r[0] for r in result}


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
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- CRUD ---

    @classmethod
    async def save_bulk(
        cls,
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

                row = await session.get(cls, w.id)
                if row is None:
                    row = cls(id=w.id)
                    session.add(row)

                row.start_date_local = str(w.start_date_local)
                row.name = w.name
                row.category = w.category
                row.type = w.type
                row.description = w.description
                row.moving_time = w.moving_time
                row.distance = w.distance
                row.workout_doc = w.workout_doc
                row.last_synced_at = datetime.now(timezone.utc)
                if w.end_date_local:
                    row.end_date_local = str(w.end_date_local)
                if w.updated:
                    row.updated = w.updated
                count += 1

            # --- delete stale rows in the synced date range ---
            if oldest is not None and newest is not None and incoming_ids:
                oldest_str = oldest.strftime("%Y-%m-%d")
                newest_str = newest.strftime("%Y-%m-%d")

                stale_q = delete(cls).where(
                    cls.start_date_local >= oldest_str,
                    cls.start_date_local <= newest_str,
                    cls.id.notin_(incoming_ids),
                )
                result = await session.execute(stale_q)

                if result.rowcount:
                    logger.info(
                        "Deleted %d stale scheduled workouts (%s → %s)", result.rowcount, oldest_str, newest_str
                    )

            await session.commit()
        return count

    @classmethod
    async def get_for_date(cls, dt: date) -> list[ScheduledWorkoutRow]:
        """Return all scheduled workouts for a given date."""
        dt_str = str(dt)
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.start_date_local == dt_str))
            return list(result.scalars().all())

    @classmethod
    async def get_range(cls, start: date, end: date) -> tuple[list[ScheduledWorkoutRow], datetime | None]:
        """Return scheduled workouts in date range and MAX(last_synced_at)."""
        start_str, end_str = str(start), str(end)
        async with get_session() as session:
            result = await session.execute(
                select(cls)
                .where(cls.start_date_local >= start_str)
                .where(cls.start_date_local <= end_str)
                .order_by(cls.start_date_local)
            )
            workouts = list(result.scalars().all())

            sync_result = await session.execute(select(func.max(cls.last_synced_at)))
            last_synced_at = sync_result.scalar_one_or_none()

        return workouts, last_synced_at


class MoodCheckinRow(Base):
    """Daily mood and emotional state check-ins."""

    __tablename__ = "mood_checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    energy: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    mood: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    anxiety: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    social: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- CRUD ---

    @classmethod
    async def save(
        cls,
        energy: int | None = None,
        mood: int | None = None,
        anxiety: int | None = None,
        social: int | None = None,
        note: str | None = None,
    ) -> MoodCheckinRow:
        """Create a mood check-in with optional fields.

        At least one field must be provided. All numeric fields (1-5) are optional.
        Returns the newly created and persisted MoodCheckinRow.

        Args:
            energy: Energy level (1=low, 5=high)
            mood: Overall mood (1=poor, 5=excellent)
            anxiety: Anxiety level (1=calm, 5=high anxiety)
            social: Social desire (1=withdrawn, 5=very social)
            note: Optional text note
        """
        if all(x is None for x in [energy, mood, anxiety, social, note]):
            raise ValueError("At least one field must be provided")

        # Validate ranges
        for field, value in [("energy", energy), ("mood", mood), ("anxiety", anxiety), ("social", social)]:
            if value is not None and not (1 <= value <= 5):
                raise ValueError(f"{field} must be between 1 and 5")

        async with get_session() as session:
            row = cls(
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
    async def get_range(
        cls,
        target_date: str | None = None,
        days_back: int = 7,
    ) -> list[MoodCheckinRow]:
        """Get mood check-ins for a date range.

        Args:
            target_date: Reference date in YYYY-MM-DD format. Defaults to today.
            days_back: Number of days to look back (inclusive). Default is 7.

        Returns:
            List of MoodCheckinRow objects, ordered by timestamp (oldest first).
        """
        if target_date:
            ref_date = date.fromisoformat(target_date)
        else:
            ref_date = date.today()

        cutoff_date = ref_date - timedelta(days=days_back - 1)
        cutoff_dt = datetime.combine(cutoff_date, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(ref_date, datetime.max.time(), tzinfo=timezone.utc)

        async with get_session() as session:
            result = await session.execute(
                select(cls)
                .where(cls.timestamp >= cutoff_dt)
                .where(cls.timestamp <= end_dt)
                .order_by(cls.timestamp.asc())
            )
            return list(result.scalars().all())


class IqosDailyRow(Base):
    """Daily IQOS stick counter. One row per date."""

    __tablename__ = "iqos_daily"

    date: Mapped[str] = mapped_column(String, primary_key=True)  # "YYYY-MM-DD"
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    # --- CRUD ---

    @classmethod
    async def increment(cls, target_date: date | None = None) -> IqosDailyRow:
        """Increment IQOS stick count for the given date (default: today).

        Creates a new row if none exists for the date, otherwise increments count by 1.
        Returns the updated IqosDailyRow.
        """
        dt = target_date or date.today()
        date_str = str(dt)

        async with get_session() as session:
            stmt = (
                insert(cls)
                .values(date=date_str, count=1, updated=datetime.now(timezone.utc))
                .on_conflict_do_update(
                    index_elements=["date"],
                    set_={"count": cls.count + 1, "updated": datetime.now(timezone.utc)},
                )
                .returning(cls)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.scalars().one()

    @classmethod
    async def get(cls, target_date: date | None = None) -> IqosDailyRow | None:
        """Get IQOS stick count for a single date (default: today)."""
        dt = target_date or date.today()
        date_str = str(dt)

        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.date == date_str))
            return result.scalar_one_or_none()

    @classmethod
    async def get_range(
        cls,
        target_date: str | None = None,
        days_back: int = 7,
    ) -> list[IqosDailyRow]:
        """Get IQOS stick counts for a date range.

        Args:
            target_date: Reference date in YYYY-MM-DD format. Defaults to today.
            days_back: Number of days to look back (inclusive). Default is 7.

        Returns:
            List of IqosDailyRow objects, ordered by date (oldest first).
        """
        ref = date.fromisoformat(target_date) if target_date else date.today()
        from_date = ref - timedelta(days=days_back - 1)

        async with get_session() as session:
            result = await session.execute(
                select(cls).where(cls.date >= str(from_date)).where(cls.date <= str(ref)).order_by(cls.date.asc())
            )
            return list(result.scalars().all())


class AiWorkoutRow(Base):
    """AI-generated workout pushed to Intervals.icu (Phase 1: Adaptive Training Plan)."""

    __tablename__ = "ai_workouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False)  # "YYYY-MM-DD"
    sport: Mapped[str] = mapped_column(String(30), nullable=False)
    slot: Mapped[str] = mapped_column(String(10), nullable=False, default="morning")
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    intervals_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Intervals.icu event ID
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_tss: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    # --- CRUD ---

    @classmethod
    async def save(
        cls,
        *,
        date_str: str,
        sport: str,
        slot: str,
        external_id: str,
        intervals_id: int | None,
        name: str,
        description: str | None,
        duration_minutes: int | None,
        target_tss: int | None,
        rationale: str | None,
    ) -> AiWorkoutRow:
        """Upsert an AI-generated workout (by external_id)."""
        async with get_session() as session:
            stmt = (
                insert(cls)
                .values(
                    date=date_str,
                    sport=sport,
                    slot=slot,
                    external_id=external_id,
                    intervals_id=intervals_id,
                    name=name,
                    description=description,
                    duration_minutes=duration_minutes,
                    target_tss=target_tss,
                    rationale=rationale,
                    status="active",
                )
                .on_conflict_do_update(
                    index_elements=["external_id"],
                    set_={
                        "intervals_id": intervals_id,
                        "name": name,
                        "description": description,
                        "duration_minutes": duration_minutes,
                        "target_tss": target_tss,
                        "rationale": rationale,
                        "status": "active",
                        "updated_at": datetime.now(timezone.utc),
                    },
                )
                .returning(cls)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.scalar_one()

    @classmethod
    async def get_by_external_id(cls, external_id: str) -> AiWorkoutRow | None:
        """Fetch an AI workout by its external_id."""
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.external_id == external_id))
            return result.scalar_one_or_none()

    @classmethod
    async def get_upcoming(cls, days_ahead: int = 7) -> list[AiWorkoutRow]:
        """Fetch active AI workouts for the upcoming days."""
        today_str = str(date.today())
        end_str = str(date.today() + timedelta(days=days_ahead))
        async with get_session() as session:
            result = await session.execute(
                select(cls)
                .where(cls.date >= today_str)
                .where(cls.date <= end_str)
                .where(cls.status == "active")
                .order_by(cls.date.asc())
            )
            return list(result.scalars().all())

    @classmethod
    async def get_for_date(cls, dt: date) -> list[AiWorkoutRow]:
        """Fetch active AI workouts for a specific date."""
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.date == str(dt)).where(cls.status == "active"))
            return list(result.scalars().all())

    @classmethod
    async def cancel(cls, external_id: str) -> AiWorkoutRow | None:
        """Mark an AI workout as cancelled."""
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.external_id == external_id))
            row = result.scalar_one_or_none()
            if row:
                row.status = "cancelled"
                row.updated_at = datetime.now(timezone.utc)
                await session.commit()
            return row


class TrainingLogRow(Base):
    """Training log entry — pre-context, actual, post-outcome (ATP Phase 3)."""

    __tablename__ = "training_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False)  # "YYYY-MM-DD"
    sport: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # What was planned
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # humango | ai | adapted | none
    original_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Adaptation (if any)
    adapted_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    adapted_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    adapted_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adaptation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Pre-workout context
    pre_recovery_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    pre_recovery_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pre_hrv_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pre_hrv_delta_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pre_rhr_today: Mapped[float | None] = mapped_column(Float, nullable=True)
    pre_rhr_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pre_tsb: Mapped[float | None] = mapped_column(Float, nullable=True)
    pre_ctl: Mapped[float | None] = mapped_column(Float, nullable=True)
    pre_atl: Mapped[float | None] = mapped_column(Float, nullable=True)
    pre_ra_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pre_sleep_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Actual (filled after activity sync)
    actual_activity_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    actual_sport: Mapped[str | None] = mapped_column(String(30), nullable=True)
    actual_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_tss: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_max_zone_time: Mapped[str | None] = mapped_column(String(10), nullable=True)
    compliance: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Post-outcome (filled next morning)
    post_recovery_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    post_hrv_delta_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    post_rhr_today: Mapped[float | None] = mapped_column(Float, nullable=True)
    post_sleep_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    post_ra_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovery_delta: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # --- CRUD ---

    @classmethod
    async def create(cls, **kwargs) -> TrainingLogRow:
        """Create a training log entry with pre-context."""
        async with get_session() as session:
            row = cls(**kwargs)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    @classmethod
    async def get_for_date(cls, dt: date | str) -> list[TrainingLogRow]:
        """Fetch training log entries for a specific date."""
        date_str = str(dt)
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.date == date_str).order_by(cls.id.asc()))
            return list(result.scalars().all())

    @classmethod
    async def get_range(cls, days_back: int = 14) -> list[TrainingLogRow]:
        """Fetch training log entries for the last N days."""
        from_date = str(date.today() - timedelta(days=days_back))
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.date >= from_date).order_by(cls.date.desc()))
            return list(result.scalars().all())

    @classmethod
    async def get_unfilled_actual(cls) -> list[TrainingLogRow]:
        """Fetch log entries with no actual data yet (compliance is NULL).

        Uses 1-day buffer to avoid marking 'skipped' prematurely
        (Garmin sync can be delayed).
        """
        cutoff = str(date.today())
        async with get_session() as session:
            result = await session.execute(
                select(cls).where(cls.compliance.is_(None)).where(cls.date < cutoff).order_by(cls.date.asc())
            )
            return list(result.scalars().all())

    @classmethod
    async def get_unfilled_post(cls) -> list[TrainingLogRow]:
        """Fetch log entries with actual data but no post-outcome yet."""
        async with get_session() as session:
            result = await session.execute(
                select(cls)
                .where(cls.compliance.isnot(None))
                .where(cls.post_recovery_score.is_(None))
                .where(cls.date < str(date.today()))
                .order_by(cls.date.asc())
            )
            return list(result.scalars().all())

    @classmethod
    async def update(cls, log_id: int, **kwargs) -> TrainingLogRow | None:
        """Update a training log entry with actual or post data."""
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.id == log_id))
            row = result.scalar_one_or_none()
            if row:
                for k, v in kwargs.items():
                    setattr(row, k, v)
                row.updated_at = datetime.now(timezone.utc)
                await session.commit()
            return row


class ExerciseCardRow(Base):
    """Exercise card in the workout library."""

    __tablename__ = "exercise_cards"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name_ru: Mapped[str] = mapped_column(String(200), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(200), nullable=True)
    muscles: Mapped[str | None] = mapped_column(String(200), nullable=True)
    equipment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    group_tag: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_sets: Mapped[int] = mapped_column(Integer, default=2)
    default_reps: Mapped[int] = mapped_column(Integer, default=15)
    default_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    steps: Mapped[list] = mapped_column(JSON, nullable=False)
    focus: Mapped[str | None] = mapped_column(Text, nullable=True)
    breath: Mapped[str | None] = mapped_column(String(100), nullable=True)
    animation_html: Mapped[str] = mapped_column(Text, nullable=False)
    animation_css: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    # --- CRUD ---

    @classmethod
    async def save(
        cls,
        *,
        exercise_id: str,
        name_ru: str,
        name_en: str | None = None,
        muscles: str | None = None,
        equipment: str | None = None,
        group_tag: str | None = None,
        default_sets: int = 2,
        default_reps: int = 15,
        default_duration_sec: int | None = None,
        steps: list[str],
        focus: str | None = None,
        breath: str | None = None,
        animation_html: str,
        animation_css: str,
    ) -> ExerciseCardRow:
        """Upsert an exercise card (by id)."""
        values = dict(
            id=exercise_id,
            name_ru=name_ru,
            name_en=name_en,
            muscles=muscles,
            equipment=equipment,
            group_tag=group_tag,
            default_sets=default_sets,
            default_reps=default_reps,
            default_duration_sec=default_duration_sec,
            steps=steps,
            focus=focus,
            breath=breath,
            animation_html=animation_html,
            animation_css=animation_css,
        )
        update_values = {k: v for k, v in values.items() if k != "id"}
        update_values["updated_at"] = datetime.now(timezone.utc)

        async with get_session() as session:
            stmt = (
                insert(cls)
                .values(**values)
                .on_conflict_do_update(index_elements=["id"], set_=update_values)
                .returning(cls)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.scalar_one()

    @classmethod
    async def get(cls, exercise_id: str) -> ExerciseCardRow | None:
        """Fetch a single exercise card by ID."""
        async with get_session() as session:
            return await session.get(cls, exercise_id)

    @classmethod
    async def get_list(
        cls,
        equipment: str | None = None,
        group_tag: str | None = None,
        muscles: str | None = None,
    ) -> list[ExerciseCardRow]:
        """List exercise cards with optional filters."""
        async with get_session() as session:
            query = select(cls)
            if equipment:
                query = query.where(cls.equipment.ilike(f"%{equipment}%"))
            if group_tag:
                query = query.where(cls.group_tag.ilike(f"%{group_tag}%"))
            if muscles:
                query = query.where(cls.muscles.ilike(f"%{muscles}%"))
            query = query.order_by(cls.group_tag, cls.name_ru)
            result = await session.execute(query)
            return list(result.scalars().all())

    @classmethod
    async def get_by_ids(cls, ids: list[str]) -> list[ExerciseCardRow]:
        """Fetch multiple exercise cards by IDs."""
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.id.in_(ids)))
            return list(result.scalars().all())

    @classmethod
    async def update_fields(cls, exercise_id: str, **kwargs) -> ExerciseCardRow | None:
        """Update specific fields of an exercise card."""
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.id == exercise_id))
            row = result.scalar_one_or_none()
            if row:
                for k, v in kwargs.items():
                    setattr(row, k, v)
                row.updated_at = datetime.now(timezone.utc)
                await session.commit()
            return row


class WorkoutCardRow(Base):
    """Composed workout from exercise library cards."""

    __tablename__ = "workout_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sport: Mapped[str] = mapped_column(String(30), nullable=False, default="Other", server_default="Other")
    exercises: Mapped[list] = mapped_column(JSON, nullable=False)
    total_duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    equipment_summary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    intervals_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # --- CRUD ---

    @classmethod
    async def save(
        cls,
        *,
        date_str: str,
        name: str,
        sport: str = "Other",
        exercises: list[dict],
        total_duration_min: int | None = None,
        equipment_summary: str | None = None,
        intervals_id: int | None = None,
    ) -> WorkoutCardRow:
        """Create a workout card entry."""
        async with get_session() as session:
            row = cls(
                date=date_str,
                name=name,
                sport=sport,
                exercises=exercises,
                total_duration_min=total_duration_min,
                equipment_summary=equipment_summary,
                intervals_id=intervals_id,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    @classmethod
    async def get_by_id(cls, card_id: int) -> WorkoutCardRow | None:
        """Fetch a single workout card by ID."""
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.id == card_id))
            return result.scalar_one_or_none()

    @classmethod
    async def delete(cls, card_id: int) -> bool:
        """Delete a workout card by ID. Returns True if deleted."""
        async with get_session() as session:
            result = await session.execute(select(cls).where(cls.id == card_id))
            row = result.scalar_one_or_none()
            if not row:
                return False
            await session.delete(row)
            await session.commit()
            return True

    @classmethod
    async def get_list(cls, days_back: int = 30) -> list[WorkoutCardRow]:
        """Fetch workout cards for the last N days, newest first."""
        cutoff = str(date.today() - timedelta(days=days_back))
        async with get_session() as session:
            result = await session.execute(
                select(cls).where(cls.date >= cutoff).order_by(cls.date.desc(), cls.id.desc())
            )
            return list(result.scalars().all())
