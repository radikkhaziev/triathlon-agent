"""SQLAlchemy models and CRUD operations for the triathlon training agent."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import Index, String, Integer, Float, Text, create_engine, func, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    Session,
    sessionmaker,
)

from config import settings

# ---------------------------------------------------------------------------
# Engine / Session helpers
# ---------------------------------------------------------------------------

_engine = None
_SessionLocal = None


def get_engine():
    """Return a singleton SQLAlchemy engine."""
    global _engine
    if _engine is None:
        _engine = create_engine(settings.DATABASE_URL, echo=False)
    return _engine


def get_session() -> Session:
    """Return a new SQLAlchemy session."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


# ---------------------------------------------------------------------------
# Declarative Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(get_engine())


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class DailyMetricsRow(Base):
    __tablename__ = "daily_metrics"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    sleep_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hrv_last: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_battery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resting_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    readiness_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    readiness_level: Mapped[str | None] = mapped_column(String, nullable=True)
    ctl: Mapped[float | None] = mapped_column(Float, nullable=True)
    atl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tsb: Mapped[float | None] = mapped_column(Float, nullable=True)
    ctl_swim: Mapped[float | None] = mapped_column(Float, nullable=True)
    ctl_bike: Mapped[float | None] = mapped_column(Float, nullable=True)
    ctl_run: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ActivityRow(Base):
    __tablename__ = "activities"

    activity_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    date: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    sport: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    norm_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    tss: Mapped[float | None] = mapped_column(Float, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ScheduledWorkoutRow(Base):
    __tablename__ = "scheduled_workouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheduled_date: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    sport: Mapped[str | None] = mapped_column(String, nullable=True)
    workout_name: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_tss: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String, default="garmin")


class TSSHistoryRow(Base):
    __tablename__ = "tss_history"

    date: Mapped[str] = mapped_column(String, primary_key=True)
    sport: Mapped[str] = mapped_column(String, primary_key=True)
    tss: Mapped[float | None] = mapped_column(Float, nullable=True)


# ---------------------------------------------------------------------------
# CRUD — Daily Metrics
# ---------------------------------------------------------------------------


def save_daily_metrics(
    dt: date,
    *,
    sleep_score: int | None = None,
    sleep_duration: int | None = None,
    hrv_last: float | None = None,
    hrv_baseline: float | None = None,
    body_battery: int | None = None,
    resting_hr: float | None = None,
    stress_score: int | None = None,
    readiness_score: int | None = None,
    readiness_level: str | None = None,
    ctl: float | None = None,
    atl: float | None = None,
    tsb: float | None = None,
    ctl_swim: float | None = None,
    ctl_bike: float | None = None,
    ctl_run: float | None = None,
    ai_recommendation: str | None = None,
) -> DailyMetricsRow:
    """Insert or update a daily metrics row (upsert by date)."""
    session = get_session()
    try:
        row = session.get(DailyMetricsRow, str(dt))
        if row is None:
            row = DailyMetricsRow(date=str(dt))
            session.add(row)

        # Update all provided fields
        fields = {
            "sleep_score": sleep_score,
            "sleep_duration": sleep_duration,
            "hrv_last": hrv_last,
            "hrv_baseline": hrv_baseline,
            "body_battery": body_battery,
            "resting_hr": resting_hr,
            "stress_score": stress_score,
            "readiness_score": readiness_score,
            "readiness_level": readiness_level,
            "ctl": ctl,
            "atl": atl,
            "tsb": tsb,
            "ctl_swim": ctl_swim,
            "ctl_bike": ctl_bike,
            "ctl_run": ctl_run,
            "ai_recommendation": ai_recommendation,
        }
        for key, value in fields.items():
            if value is not None:
                setattr(row, key, value)

        session.commit()
        session.refresh(row)
        return row
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_daily_metrics(dt: date) -> DailyMetricsRow | None:
    """Fetch a single day's metrics by date."""
    session = get_session()
    try:
        return session.get(DailyMetricsRow, str(dt))
    finally:
        session.close()


def get_daily_metrics_range(
    start: date, end: date
) -> list[DailyMetricsRow]:
    """Fetch metrics for a date range (inclusive)."""
    session = get_session()
    try:
        stmt = (
            select(DailyMetricsRow)
            .where(DailyMetricsRow.date >= str(start))
            .where(DailyMetricsRow.date <= str(end))
            .order_by(DailyMetricsRow.date)
        )
        return list(session.scalars(stmt).all())
    finally:
        session.close()


# ---------------------------------------------------------------------------
# CRUD — Activities
# ---------------------------------------------------------------------------


def save_activity(
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
    session = get_session()
    try:
        row = session.get(ActivityRow, activity_id)
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

        session.commit()
        session.refresh(row)
        return row
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_activities(start_date: date, end_date: date) -> list[ActivityRow]:
    """Fetch activities within a date range (inclusive)."""
    session = get_session()
    try:
        stmt = (
            select(ActivityRow)
            .where(ActivityRow.date >= str(start_date))
            .where(ActivityRow.date <= str(end_date))
            .order_by(ActivityRow.date)
        )
        return list(session.scalars(stmt).all())
    finally:
        session.close()


# ---------------------------------------------------------------------------
# CRUD — Scheduled Workouts
# ---------------------------------------------------------------------------


def save_scheduled_workout(
    scheduled_date: date,
    workout_name: str,
    sport: str,
    *,
    description: str | None = None,
    planned_tss: float | None = None,
    source: str = "garmin",
) -> ScheduledWorkoutRow:
    """Insert a new scheduled workout."""
    session = get_session()
    try:
        row = ScheduledWorkoutRow(
            scheduled_date=str(scheduled_date),
            workout_name=workout_name,
            sport=sport,
            description=description,
            planned_tss=planned_tss,
            source=source,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_scheduled_workouts(dt: date) -> list[ScheduledWorkoutRow]:
    """Fetch all scheduled workouts for a given date."""
    session = get_session()
    try:
        stmt = (
            select(ScheduledWorkoutRow)
            .where(ScheduledWorkoutRow.scheduled_date == str(dt))
            .order_by(ScheduledWorkoutRow.id)
        )
        return list(session.scalars(stmt).all())
    finally:
        session.close()


def get_scheduled_workouts_range(
    start_date: date, end_date: date
) -> list[ScheduledWorkoutRow]:
    """Fetch all scheduled workouts within a date range (inclusive)."""
    session = get_session()
    try:
        stmt = (
            select(ScheduledWorkoutRow)
            .where(ScheduledWorkoutRow.scheduled_date >= str(start_date))
            .where(ScheduledWorkoutRow.scheduled_date <= str(end_date))
            .order_by(ScheduledWorkoutRow.scheduled_date, ScheduledWorkoutRow.id)
        )
        return list(session.scalars(stmt).all())
    finally:
        session.close()


# ---------------------------------------------------------------------------
# CRUD — TSS History
# ---------------------------------------------------------------------------


def save_tss_history(dt: date, sport: str, tss: float) -> TSSHistoryRow:
    """Insert or update a TSS history entry (upsert by date + sport)."""
    session = get_session()
    try:
        row = session.get(TSSHistoryRow, (str(dt), sport))
        if row is None:
            row = TSSHistoryRow(date=str(dt), sport=sport, tss=tss)
            session.add(row)
        else:
            row.tss = tss

        session.commit()
        session.refresh(row)
        return row
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_tss_history(days: int = 42) -> list[TSSHistoryRow]:
    """Fetch TSS history for the last N days, ordered by date."""
    cutoff = date.today() - timedelta(days=days)
    session = get_session()
    try:
        stmt = (
            select(TSSHistoryRow)
            .where(TSSHistoryRow.date >= str(cutoff))
            .order_by(TSSHistoryRow.date, TSSHistoryRow.sport)
        )
        return list(session.scalars(stmt).all())
    finally:
        session.close()
