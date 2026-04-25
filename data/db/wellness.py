from __future__ import annotations

import datetime as _dt
import logging
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from data.intervals.dto import WellnessDTO
from tasks.dto import ORMDTO, DateDTO

from .common import Base, Session
from .decorator import dual, with_sync_session

logger = logging.getLogger(__name__)


_CANONICAL_TO_TYPE = {"swim": "Swim", "ride": "Ride", "run": "Run"}


# Backward-compatible re-export (moved to data.db.dto)
from data.db.dto import WellnessPostDTO  # noqa: F401, E402


class Wellness(Base):
    __tablename__ = "wellness"

    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_wellness_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date: Mapped[str] = mapped_column(String, nullable=False)  # "YYYY-MM-DD"

    # --- Intervals.icu fields ---
    ctl: Mapped[float | None] = mapped_column(Float, nullable=True)
    atl: Mapped[float | None] = mapped_column(Float, nullable=True)
    ramp_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    ctl_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    atl_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    sport_info: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
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

    @classmethod
    def _merge_sport_info(
        cls,
        existing: list[dict] | None,
        incoming: list[dict],
    ) -> list[dict]:
        """Merge incoming sport_info entries into existing by 'type' key.

        Updates fields from incoming entries but preserves fields (e.g. 'ctl')
        that exist in the DB but are absent in the incoming data.
        """
        merged = {(e.get("type") or "").lower(): dict(e) for e in (existing or [])}
        for entry in incoming:
            key = (entry.get("type") or "").lower()
            if key in merged:
                merged[key].update(entry)
            else:
                merged[key] = dict(entry)
        return list(merged.values())

    # --- CRUD ---

    @classmethod
    @with_sync_session
    def get_hrv_history(
        cls,
        user_id: int,
        *,
        dt: DateDTO,
        days: int = 60,
        session: Session,
    ) -> list[float]:
        """Return last N HRV values (oldest first), skipping nulls and zeroes."""
        values = (
            session.execute(
                select(cls.hrv)
                .where(
                    cls.user_id == user_id,
                    cls.hrv.isnot(None),
                    cls.hrv > 0,
                    cls.date <= dt.isoformat(),
                )
                .order_by(cls.date.desc())
                .limit(days)
            )
            .scalars()
            .all()
        )
        return [float(v) for v in reversed(values)]

    @classmethod
    @with_sync_session
    def get_rhr_history(
        cls,
        user_id: int,
        *,
        dt: DateDTO,
        days: int = 60,
        session: Session,
    ) -> list[float]:
        """Return last N resting_hr values (oldest first), skipping nulls and zeroes."""
        values = (
            session.execute(
                select(cls.resting_hr)
                .where(
                    cls.user_id == user_id,
                    cls.resting_hr.isnot(None),
                    cls.resting_hr > 0,
                    cls.date <= dt.isoformat(),
                )
                .order_by(cls.date.desc())
                .limit(days)
            )
            .scalars()
            .all()
        )
        return [float(v) for v in reversed(values)]

    @classmethod
    @dual
    def get(
        cls,
        user_id: int,
        dt: _dt.date | DateDTO | str,
        *,
        session: Session,
    ) -> Wellness | None:
        """Fetch a single wellness row by date and user."""
        date_str = dt if isinstance(dt, str) else dt.isoformat()

        result = session.execute(
            select(cls).where(
                cls.user_id == user_id,
                cls.date == date_str,
            )
        )
        return result.scalar_one_or_none()

    @classmethod
    def _apply_intervals_fields(cls, row: "Wellness", wellness: WellnessDTO) -> None:
        # Apply non-None Intervals.icu fields onto a wellness row. ``sport_info``
        # is merged (not replaced) so locally-tracked sport CTL survives.
        for field, val in wellness.intervals_dict().items():
            if val is None:
                continue
            if field == "sport_info":
                val = cls._merge_sport_info(row.sport_info, val)
            setattr(row, field, val)

    @classmethod
    @with_sync_session
    def save(
        cls,
        user_id: int,
        *,
        wellness: WellnessDTO,
        session: Session,
    ) -> ORMDTO:

        date_str = wellness.id

        row = session.execute(select(cls).where(cls.user_id == user_id, cls.date == date_str)).scalar_one_or_none()

        is_new = row is None
        if is_new:
            row = cls(date=date_str, user_id=user_id, sport_info=[])
            session.add(row)
        elif row.updated == wellness.updated:
            return ORMDTO(is_new=False, is_changed=False, row=row)

        cls._apply_intervals_fields(row, wellness)

        try:
            session.commit()
        except IntegrityError as e:
            # Narrow recovery to the specific (user_id, date) unique-violation
            # race (issue #255): bootstrap chunk retries and WELLNESS_UPDATED
            # webhooks can both try to create the same row. SQLSTATE 23505 +
            # constraint name pins this to ``uq_wellness_user_date`` only —
            # FK / CHECK / NOT NULL violations re-raise so real data defects
            # still surface (otherwise ``scalar_one()`` below would mask them
            # as ``NoResultFound``).
            pgcode = getattr(e.orig, "pgcode", None)
            constraint = getattr(getattr(e.orig, "diag", None), "constraint_name", None)
            if pgcode != "23505" or constraint != "uq_wellness_user_date":
                raise
            session.rollback()
            row = session.execute(select(cls).where(cls.user_id == user_id, cls.date == date_str)).scalar_one()
            if row.updated == wellness.updated:
                return ORMDTO(is_new=False, is_changed=False, row=row)
            cls._apply_intervals_fields(row, wellness)
            # Second commit is UPDATE-only: ``uq_wellness_user_date`` cannot
            # fire on UPDATE (SQLSTATE 23505 is INSERT-time), so this commit
            # doesn't need its own retry guard.
            session.commit()
            is_new = False

        session.refresh(row)
        return ORMDTO(is_new=is_new, is_changed=True, row=row)

    @classmethod
    @with_sync_session
    def update_sport_ctl(
        cls,
        user_id: int,
        dt: DateDTO,
        sport_ctl: dict[str, float],
        *,
        session: Session,
    ) -> None:
        date_str = dt.isoformat()
        result = session.execute(
            select(cls).where(
                cls.user_id == user_id,
                cls.date == date_str,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return

        existing_info = list(row.sport_info or [])  # copy to trigger SQLAlchemy change detection
        existing_types = {e["type"].lower(): i for i, e in enumerate(existing_info) if e.get("type")}

        for canonical_sport, ctl_val in sport_ctl.items():
            if ctl_val < 0:
                continue
            iv_type = _CANONICAL_TO_TYPE[canonical_sport]

            iv_type_lower = iv_type.lower()
            if iv_type_lower in existing_types:
                existing_info[existing_types[iv_type_lower]]["ctl"] = ctl_val
            else:
                existing_info.append({"type": iv_type, "ctl": ctl_val})

        row.sport_info = existing_info
        session.commit()
