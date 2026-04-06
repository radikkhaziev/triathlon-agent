from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from data.db.common import Base, Session
from data.db.decorator import dual, with_session, with_sync_session
from data.intervals.dto import ScheduledWorkoutDTO

logger = logging.getLogger(__name__)


class ScheduledWorkout(Base):
    __tablename__ = "scheduled_workouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Intervals.icu event ID
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
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
    @with_sync_session
    def save_bulk(
        cls,
        user_id: int,
        workouts: list[ScheduledWorkoutDTO],
        oldest: date | None = None,
        newest: date | None = None,
        *,
        session: Session | None = None,
    ) -> int:
        """Upsert scheduled workouts from Intervals.icu and delete stale ones.

        When oldest/newest are provided, any DB rows in that date range whose IDs
        are not in the incoming workouts list are deleted (workout removed or moved
        in Intervals.icu).

        Returns count of upserted rows.
        """
        # --- upsert ---
        incoming_ids: set[int] = set()
        count = 0
        for w in workouts:
            incoming_ids.add(w.id)

            row = session.get(cls, w.id)
            if row is None:
                row = cls(id=w.id, user_id=user_id)
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
        if oldest is not None and newest is not None:
            oldest_str = oldest.strftime("%Y-%m-%d")
            newest_str = newest.strftime("%Y-%m-%d")

            stale_q = delete(cls).where(
                cls.user_id == user_id,
                cls.start_date_local >= oldest_str,
                cls.start_date_local <= newest_str,
            )
            if incoming_ids:
                stale_q = stale_q.where(cls.id.notin_(incoming_ids))
            result = session.execute(stale_q)

            if result.rowcount:
                logger.info("Deleted %d stale scheduled workouts (%s → %s)", result.rowcount, oldest_str, newest_str)

        session.commit()
        return count

    @classmethod
    @dual
    def get_for_date(cls, user_id: int, dt: date, *, session: Session) -> list[ScheduledWorkout]:
        """Return all scheduled workouts for a given date."""
        dt_str = str(dt)
        result = session.execute(select(cls).where(cls.user_id == user_id, cls.start_date_local == dt_str))
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def get_range(
        cls,
        user_id: int,
        start: date,
        end: date,
        *,
        session: AsyncSession,
    ) -> tuple[list[ScheduledWorkout], datetime | None]:
        """Return scheduled workouts in date range and MAX(last_synced_at)."""
        start_str, end_str = str(start), str(end)
        result = await session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.start_date_local >= start_str, cls.start_date_local <= end_str)
            .order_by(cls.start_date_local)
        )
        workouts = list(result.scalars().all())

        sync_result = await session.execute(select(func.max(cls.last_synced_at)).where(cls.user_id == user_id))
        last_synced_at = sync_result.scalar_one_or_none()

        return workouts, last_synced_at


class AiWorkout(Base):
    """AI-generated workout pushed to Intervals.icu (Phase 1: Adaptive Training Plan)."""

    __tablename__ = "ai_workouts"
    __table_args__ = (UniqueConstraint("user_id", "external_id", name="uq_ai_workouts_user_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date: Mapped[str] = mapped_column(String, nullable=False)  # "YYYY-MM-DD"
    sport: Mapped[str] = mapped_column(String(30), nullable=False)
    slot: Mapped[str] = mapped_column(String(30), nullable=False, default="morning")
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
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
    @dual
    def save(
        cls,
        *,
        user_id: int,
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
        session: Session,
    ) -> AiWorkout:
        """Upsert an AI-generated workout (by external_id)."""
        stmt = (
            insert(cls)
            .values(
                user_id=user_id,
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
                index_elements=["user_id", "external_id"],
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
        row = session.execute(stmt).scalar_one()
        session.commit()
        return row

    @classmethod
    @dual
    def get_by_external_id(
        cls,
        user_id: int,
        external_id: str,
        *,
        session: Session,
    ) -> AiWorkout | None:
        """Fetch an AI workout by its external_id."""
        result = session.execute(
            select(cls).where(
                cls.external_id == external_id,
                cls.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_upcoming(
        cls,
        user_id: int,
        days_ahead: int = 7,
        *,
        session: Session,
    ) -> list[AiWorkout]:
        """Fetch active AI workouts for the upcoming days."""
        today_str = str(date.today())
        end_str = str(date.today() + timedelta(days=days_ahead))
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.date >= today_str, cls.date <= end_str, cls.status == "active")
            .order_by(cls.date.asc())
        )
        return list(result.scalars().all())

    @classmethod
    @dual
    def get_for_date(cls, user_id: int, dt: date | str, *, session: Session) -> list[AiWorkout]:
        """Fetch active AI workouts for a specific date."""
        date_str = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(
            select(cls).where(cls.user_id == user_id, cls.date == date_str, cls.status == "active")
        )
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def cancel(cls, user_id: int, external_id: str, *, session: AsyncSession) -> AiWorkout | None:
        """Mark an AI workout as cancelled."""
        result = await session.execute(select(cls).where(cls.external_id == external_id, cls.user_id == user_id))
        row = result.scalar_one_or_none()
        if row:
            row.status = "cancelled"
            row.intervals_id = None
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
        return row


class TrainingLog(Base):
    """Training log entry — pre-context, actual, post-outcome (ATP Phase 3)."""

    __tablename__ = "training_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
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
    @dual
    def create(cls, *, user_id: int, session: Session, **kwargs) -> TrainingLog:
        """Create a training log entry with pre-context."""
        row = cls(user_id=user_id, **kwargs)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    @classmethod
    @dual
    def get_for_date(cls, dt: date | str, user_id: int, *, session: Session) -> list[TrainingLog]:
        """Fetch training log entries for a specific date."""
        date_str = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(select(cls).where(cls.user_id == user_id, cls.date == date_str).order_by(cls.id.asc()))
        return list(result.scalars().all())

    @classmethod
    @dual
    def delete_for_date(cls, user_id: int, dt: date | str, *, session: Session) -> int:
        """Delete all training log entries for a given date. Returns deleted count."""
        date_str = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(delete(cls).where(cls.user_id == user_id, cls.date == date_str))
        session.commit()
        return result.rowcount

    @classmethod
    @dual
    def get_range(cls, user_id: int, days_back: int = 14, *, session: Session) -> list[TrainingLog]:
        """Fetch training log entries for the last N days."""
        from_date = str(date.today() - timedelta(days=days_back - 1))
        result = session.execute(
            select(cls).where(cls.user_id == user_id, cls.date >= from_date).order_by(cls.date.desc())
        )
        return list(result.scalars().all())

    @classmethod
    @dual
    def get_unfilled_actual(cls, user_id: int, *, session: Session) -> list[TrainingLog]:
        """Fetch log entries with no actual data yet (compliance is NULL)."""
        cutoff = str(date.today())
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.compliance.is_(None), cls.date < cutoff)
            .order_by(cls.date.asc())
        )
        return list(result.scalars().all())

    @classmethod
    @dual
    def get_unfilled_post(cls, user_id: int, *, session: Session) -> list[TrainingLog]:
        """Fetch log entries with actual data but no post-outcome yet."""
        result = session.execute(
            select(cls)
            .where(
                cls.user_id == user_id,
                cls.compliance.isnot(None),
                cls.post_recovery_score.is_(None),
                cls.date < str(date.today()),
            )
            .order_by(cls.date.asc())
        )
        return list(result.scalars().all())

    @classmethod
    @dual
    def update(cls, log_id: int, *, user_id: int, session: Session, **kwargs) -> TrainingLog | None:
        """Update a training log entry with actual or post data."""
        result = session.execute(select(cls).where(cls.id == log_id, cls.user_id == user_id))
        row = result.scalar_one_or_none()
        if row:
            for k, v in kwargs.items():
                setattr(row, k, v)
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
        return row


class ExerciseCard(Base):
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
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)  # meters per rep (swim drills)
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
    @with_session
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
        distance_m: float | None = None,
        steps: list[str],
        focus: str | None = None,
        breath: str | None = None,
        animation_html: str,
        animation_css: str,
        session: AsyncSession,
    ) -> ExerciseCard:
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
            distance_m=distance_m,
            steps=steps,
            focus=focus,
            breath=breath,
            animation_html=animation_html,
            animation_css=animation_css,
        )
        update_values = {k: v for k, v in values.items() if k != "id"}
        update_values["updated_at"] = datetime.now(timezone.utc)

        stmt = (
            insert(cls).values(**values).on_conflict_do_update(index_elements=["id"], set_=update_values).returning(cls)
        )
        row = (await session.execute(stmt)).scalar_one()
        await session.commit()
        return row

    @classmethod
    @with_session
    async def get(cls, exercise_id: str, *, session: AsyncSession) -> ExerciseCard | None:
        """Fetch a single exercise card by ID."""
        return await session.get(cls, exercise_id)

    @classmethod
    @with_session
    async def get_list(
        cls,
        equipment: str | None = None,
        group_tag: str | None = None,
        muscles: str | None = None,
        *,
        session: AsyncSession,
    ) -> list[ExerciseCard]:
        """List exercise cards with optional filters."""
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
    @with_session
    async def get_by_ids(cls, ids: list[str], *, session: AsyncSession) -> list[ExerciseCard]:
        """Fetch multiple exercise cards by IDs."""
        result = await session.execute(select(cls).where(cls.id.in_(ids)))
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def update_fields(cls, exercise_id: str, *, session: AsyncSession, **kwargs) -> ExerciseCard | None:
        """Update specific fields of an exercise card."""
        result = await session.execute(select(cls).where(cls.id == exercise_id))
        row = result.scalar_one_or_none()
        if row:
            for k, v in kwargs.items():
                setattr(row, k, v)
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
        return row


class WorkoutCard(Base):
    """Composed workout from exercise library cards."""

    __tablename__ = "workout_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
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
    @with_session
    async def save(
        cls,
        *,
        user_id: int,
        date_str: str,
        name: str,
        sport: str = "Other",
        exercises: list[dict],
        total_duration_min: int | None = None,
        equipment_summary: str | None = None,
        intervals_id: int | None = None,
        session: AsyncSession,
    ) -> WorkoutCard:
        """Create a workout card entry."""
        row = cls(
            user_id=user_id,
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
    @with_session
    async def get_by_id(cls, card_id: int, user_id: int, *, session: AsyncSession) -> WorkoutCard | None:
        """Fetch a single workout card by ID."""
        result = await session.execute(select(cls).where(cls.id == card_id, cls.user_id == user_id))
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def delete(cls, card_id: int, user_id: int, *, session: AsyncSession) -> bool:
        """Delete a workout card by ID. Returns True if deleted."""
        result = await session.execute(select(cls).where(cls.id == card_id, cls.user_id == user_id))
        row = result.scalar_one_or_none()
        if not row:
            return False
        await session.delete(row)
        await session.commit()
        return True

    @classmethod
    @with_session
    async def get_list(cls, user_id: int, days_back: int = 30, *, session: AsyncSession) -> list[WorkoutCard]:
        """Fetch workout cards for the last N days, newest first."""
        cutoff = str(date.today() - timedelta(days=days_back - 1))
        result = await session.execute(
            select(cls).where(cls.user_id == user_id, cls.date >= cutoff).order_by(cls.date.desc(), cls.id.desc())
        )
        return list(result.scalars().all())
