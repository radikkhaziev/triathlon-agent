from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from data.db.common import Base, Session
from data.db.decorator import dual
from data.db.dto import AthleteGoalDTO, AthleteThresholdsDTO

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

    # Zone boundaries from Intervals.icu sport-settings (source of truth)
    hr_zones: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [129, 136, 144, 152, 157, 161]
    hr_zone_names: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["Recovery", "Aerobic", ...]
    power_zones: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [100, 140, 170, 210, 260]
    power_zone_names: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["Active Recovery", ...]
    pace_zones: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [420, 390, 360, 330, 300]
    pace_zone_names: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["Zone 1", "Zone 2", ...]

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
        hr_zones: list | None = None,
        hr_zone_names: list | None = None,
        power_zones: list | None = None,
        power_zone_names: list | None = None,
        pace_zones: list | None = None,
        pace_zone_names: list | None = None,
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
            hr_zones=hr_zones,
            hr_zone_names=hr_zone_names,
            power_zones=power_zones,
            power_zone_names=power_zone_names,
            pace_zones=pace_zones,
            pace_zone_names=pace_zone_names,
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
                "hr_zones": func.coalesce(excl.hr_zones, cls.hr_zones),
                "hr_zone_names": func.coalesce(excl.hr_zone_names, cls.hr_zone_names),
                "power_zones": func.coalesce(excl.power_zones, cls.power_zones),
                "power_zone_names": func.coalesce(excl.power_zone_names, cls.power_zone_names),
                "pace_zones": func.coalesce(excl.pace_zones, cls.pace_zones),
                "pace_zone_names": func.coalesce(excl.pace_zone_names, cls.pace_zone_names),
                "synced_at": now,
                "updated_at": now,
            },
        ).returning(cls)
        row = session.execute(stmt).scalar_one()
        session.commit()
        return row

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

    @classmethod
    @dual
    def get_thresholds(cls, user_id: int, *, session: Session) -> AthleteThresholdsDTO:
        from .user import User

        user = session.get(User, user_id)
        result = session.execute(select(cls).where(cls.user_id == user_id))
        all_settings = list(result.scalars().all())

        dto = AthleteThresholdsDTO(
            age=user.age if user else None,
            primary_sport=user.primary_sport if user else None,
        )

        for s in all_settings:
            if s.sport == "Run":
                dto.lthr_run = s.lthr
                dto.max_hr = dto.max_hr or s.max_hr
                dto.threshold_pace_run = s.threshold_pace
            elif s.sport == "Ride":
                dto.lthr_bike = s.lthr
                dto.max_hr = dto.max_hr or s.max_hr
                dto.ftp = s.ftp
            elif s.sport == "Swim":
                dto.css = s.threshold_pace
                dto.max_hr = dto.max_hr or s.max_hr

        return dto


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
    per_sport_targets: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"swim": 15, "ride": 35, "run": 25}

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
    def get_goal_dto(cls, user_id: int, *, session: Session) -> AthleteGoalDTO | None:
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.is_active.is_(True))
            .order_by(cls.category.asc(), cls.event_date.asc())
            .limit(1)
        )
        goal = result.scalar_one_or_none()
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
