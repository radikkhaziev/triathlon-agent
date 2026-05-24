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
    @dual
    def get_latest_weight(cls, user_id: int, *, session: Session) -> float | None:
        """Most recent non-null body weight (kg) for the user, if any."""
        result = session.execute(
            select(cls.weight)
            .where(
                cls.user_id == user_id,
                cls.weight.isnot(None),
            )
            .order_by(cls.date.desc())
            .limit(1)
        )
        value = result.scalar_one_or_none()
        return float(value) if value is not None else None

    @classmethod
    @dual
    def get_latest_vo2max(cls, user_id: int, *, session: Session) -> float | None:
        """Most recent non-null VO₂max for the user, if any (Garmin-sourced)."""
        result = session.execute(
            select(cls.vo2max)
            .where(
                cls.user_id == user_id,
                cls.vo2max.isnot(None),
            )
            .order_by(cls.date.desc())
            .limit(1)
        )
        value = result.scalar_one_or_none()
        return float(value) if value is not None else None

    @classmethod
    @dual
    def get_sleep_series(
        cls,
        user_id: int,
        end_date: str,
        days: int,
        *,
        session: Session,
    ) -> list[float | None]:
        """Sleep scores over ``[end_date − (days−1), end_date]``, oldest first
        (today last). The list length is exactly ``days`` — missing days are
        filled with ``None`` so the frontend can render a "missed night" bar
        without the array indices drifting from the calendar.

        Backs the Sleep card's last-N-nights bar-strip on /wellness.
        """
        end = _dt.date.fromisoformat(end_date)
        start = end - _dt.timedelta(days=days - 1)
        result = session.execute(
            select(cls.date, cls.sleep_score).where(
                cls.user_id == user_id,
                cls.date >= start.isoformat(),
                cls.date <= end.isoformat(),
            )
        )
        rows = {str(d): (float(s) if s is not None else None) for d, s in result.all()}
        out: list[float | None] = []
        cur = start
        while cur <= end:
            out.append(rows.get(cur.isoformat()))
            cur += _dt.timedelta(days=1)
        return out

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
    def update_loads(
        cls,
        user_id: int,
        dt: DateDTO,
        *,
        ctl: float | None,
        atl: float | None,
        ramp_rate: float | None,
        ctl_load: float | None,
        atl_load: float | None,
        session: Session,
    ) -> bool:
        """In-place UPDATE of training-load columns for `(user_id, dt)`.

        Used by the FITNESS_UPDATED webhook to push new CTL/ATL into today's
        wellness row without round-tripping to Intervals.icu — payload already
        carries the recalculated values. No-op if the row doesn't exist yet
        (next regular wellness sync will materialize it).
        """
        row = session.execute(
            select(cls).where(cls.user_id == user_id, cls.date == dt.isoformat())
        ).scalar_one_or_none()
        if row is None:
            return False
        row.ctl = ctl
        row.atl = atl
        row.ramp_rate = ramp_rate
        row.ctl_load = ctl_load
        row.atl_load = atl_load
        session.commit()
        return True

    @classmethod
    @with_sync_session
    def update_sport_load(
        cls,
        user_id: int,
        dt: DateDTO,
        sport_ctl: dict[str, float],
        sport_atl: dict[str, float],
        *,
        session: Session,
    ) -> None:
        """Merge per-sport CTL+ATL into wellness.sport_info JSON.

        Preserves existing per-sport fields (eftp, wPrime, pMax from Intervals.icu);
        only the ctl/atl keys are overwritten.
        """
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

        # JSON column is NOT MutableList-wrapped → SQLAlchemy compares the new
        # list value to its loaded one by equality. If we mutate dicts in place
        # via shared references and then reassign, both sides see the mutation
        # and compare equal → no UPDATE issued (silently). Build the new list
        # with FRESH dict copies so identity differs from the loaded value.
        new_info: list[dict] = [dict(e) for e in (row.sport_info or [])]
        existing_types = {e["type"].lower(): i for i, e in enumerate(new_info) if e.get("type")}

        for canonical_sport in ("swim", "ride", "run"):
            ctl_val = sport_ctl.get(canonical_sport)
            atl_val = sport_atl.get(canonical_sport)
            ctl_ok = ctl_val is not None and ctl_val >= 0
            atl_ok = atl_val is not None and atl_val >= 0
            if not ctl_ok and not atl_ok:
                continue

            iv_type = _CANONICAL_TO_TYPE[canonical_sport]
            iv_type_lower = iv_type.lower()
            if iv_type_lower in existing_types:
                entry = new_info[existing_types[iv_type_lower]]
            else:
                entry = {"type": iv_type}
                new_info.append(entry)
                existing_types[iv_type_lower] = len(new_info) - 1

            if ctl_ok:
                entry["ctl"] = ctl_val
            if atl_ok:
                entry["atl"] = atl_val

        row.sport_info = new_info
        session.commit()
