from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from data.db.common import Base, Session
from data.db.decorator import dual

logger = logging.getLogger(__name__)


class AthleteSettings(Base):
    """Per-user per-sport thresholds, synced from Intervals.icu sport-settings."""

    __tablename__ = "athlete_settings"
    __table_args__ = (UniqueConstraint("user_id", "sport", name="uq_athlete_settings_user_sport"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    sport: Mapped[str] = mapped_column(String(30), nullable=False)  # Ride / Run / Swim

    lthr: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Lactate threshold HR (bpm)
    max_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Max HR (bpm)
    ftp: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FTP (watts), Ride only
    threshold_pace: Mapped[float | None] = mapped_column(Float, nullable=True)  # Swim: sec/100m, Run: sec/km
    pace_units: Mapped[str | None] = mapped_column(String(20), nullable=True)  # SECS_100M / MINS_KM

    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # --- CRUD ---

    @classmethod
    @dual
    def upsert(
        cls,
        *,
        user_id: int,
        sport: str,
        lthr: int | None = None,
        max_hr: int | None = None,
        ftp: int | None = None,
        threshold_pace: float | None = None,
        pace_units: str | None = None,
        session: Session,
    ) -> AthleteSettings:
        now = datetime.now(timezone.utc)
        stmt = insert(cls).values(
            user_id=user_id,
            sport=sport,
            lthr=lthr,
            max_hr=max_hr,
            ftp=ftp,
            threshold_pace=threshold_pace,
            pace_units=pace_units,
            synced_at=now,
        )
        # On conflict: keep existing value when new value is None (COALESCE)
        excl = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            constraint="uq_athlete_settings_user_sport",
            set_={
                "lthr": func.coalesce(excl.lthr, cls.lthr),
                "max_hr": func.coalesce(excl.max_hr, cls.max_hr),
                "ftp": func.coalesce(excl.ftp, cls.ftp),
                "threshold_pace": func.coalesce(excl.threshold_pace, cls.threshold_pace),
                "pace_units": func.coalesce(excl.pace_units, cls.pace_units),
                "synced_at": now,
                "updated_at": now,
            },
        ).returning(cls)
        result = session.execute(stmt)
        session.commit()
        return result.scalar_one()

    @classmethod
    @dual
    def get(cls, user_id: int, sport: str, *, session: Session) -> AthleteSettings | None:
        result = session.execute(select(cls).where(cls.user_id == user_id, cls.sport == sport))
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_all(cls, user_id: int, *, session: Session) -> list[AthleteSettings]:
        result = session.execute(select(cls).where(cls.user_id == user_id).order_by(cls.sport))
        return list(result.scalars().all())


class AthleteGoal(Base):
    """Race goals with CTL targets, synced from Intervals.icu events (RACE_A/B/C)."""

    __tablename__ = "athlete_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(10), nullable=False)  # RACE_A / RACE_B / RACE_C

    event_name: Mapped[str] = mapped_column(String, nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    sport_type: Mapped[str] = mapped_column(String(20), nullable=False)  # triathlon/run/ride/swim/fitness
    disciplines: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["Swim", "Ride", "Run"]

    ctl_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    per_sport_targets: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"swim": 15, "bike": 35, "run": 25}

    intervals_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # --- CRUD ---

    @classmethod
    @dual
    def get_active(cls, user_id: int, *, session: Session) -> AthleteGoal | None:
        """Get the primary active goal (RACE_A first, then by date)."""
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.is_active.is_(True))
            .order_by(cls.category.asc(), cls.event_date.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_all(cls, user_id: int, *, session: Session) -> list[AthleteGoal]:
        result = session.execute(select(cls).where(cls.user_id == user_id).order_by(cls.event_date.asc()))
        return list(result.scalars().all())

    @classmethod
    @dual
    def upsert_from_intervals(
        cls,
        *,
        user_id: int,
        category: str,
        event_name: str,
        event_date: date,
        intervals_event_id: int,
        session: Session,
    ) -> AthleteGoal:
        """Upsert goal from Intervals.icu event. Does NOT overwrite CTL targets."""
        now = datetime.now(timezone.utc)
        existing = session.execute(
            select(cls).where(cls.user_id == user_id, cls.intervals_event_id == intervals_event_id)
        ).scalar_one_or_none()

        if existing:
            existing.event_name = event_name
            existing.event_date = event_date
            existing.category = category
            existing.synced_at = now
            session.commit()
            return existing

        goal = cls(
            user_id=user_id,
            category=category,
            event_name=event_name,
            event_date=event_date,
            sport_type="triathlon",
            intervals_event_id=intervals_event_id,
            is_active=True,
            synced_at=now,
        )
        session.add(goal)
        session.commit()
        return goal


# ---------------------------------------------------------------------------
# AthleteConfig — helper to read athlete settings from DB
# ---------------------------------------------------------------------------


# Backward-compatible re-exports (moved to data.db.dto)
from data.db.dto import AthleteGoalDTO, AthleteThresholdsDTO  # noqa: F401, E402


class AthleteConfig:
    """Per-user athlete configuration from DB (replaces config.py hardcoded values).

    Usage::

        t = AthleteConfig.get_thresholds(user_id=1)
        t.lthr_run   # 153
        t.ftp        # 233

        g = AthleteConfig.get_goal(user_id=1)
        g.event_name  # "Ironman 70.3"
    """

    @staticmethod
    def get_thresholds(user_id: int) -> AthleteThresholdsDTO:
        from .user import User

        user = User.get_by_id(user_id)
        all_settings = AthleteSettings.get_all(user_id)

        result = AthleteThresholdsDTO(
            age=user.age if user else None,
            primary_sport=user.primary_sport if user else None,
        )

        for s in all_settings:
            if s.sport == "Run":
                result.lthr_run = s.lthr
                result.max_hr = result.max_hr or s.max_hr
                result.threshold_pace_run = s.threshold_pace
            elif s.sport == "Ride":
                result.lthr_bike = s.lthr
                result.max_hr = result.max_hr or s.max_hr
                result.ftp = s.ftp
            elif s.sport == "Swim":
                result.css = s.threshold_pace
                result.max_hr = result.max_hr or s.max_hr

        return result

    @staticmethod
    def get_goal(user_id: int) -> AthleteGoalDTO | None:
        goal = AthleteGoal.get_active(user_id)
        if not goal:
            return None
        return AthleteGoalDTO(
            event_name=goal.event_name,
            event_date=goal.event_date,
            sport_type=goal.sport_type,
            disciplines=goal.disciplines,
            ctl_target=goal.ctl_target,
            per_sport_targets=goal.per_sport_targets,
        )
