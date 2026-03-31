from __future__ import annotations

import datetime as _dt
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tasks.dto import DateDTO

    from .user import UserDTO

from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from .common import Base, Session
from .decorator import dual, with_sync_session


class HrvAnalysis(Base):
    __tablename__ = "hrv_analysis"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    date: Mapped[str] = mapped_column(String, primary_key=True)
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
    @dual
    def get(
        cls,
        user_id: int,
        dt: _dt.date | DateDTO | str,
        algorithm: str,
        *,
        session: Session,
    ) -> HrvAnalysis | None:
        """Fetch HRV analysis for a user, date and algorithm."""
        date_str = dt if isinstance(dt, str) else dt.isoformat()

        return session.get(cls, (user_id, date_str, algorithm))


class RhrAnalysis(Base):
    __tablename__ = "rhr_analysis"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    date: Mapped[str] = mapped_column(String, primary_key=True)

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
    @dual
    def get(
        cls,
        user_id: int,
        dt: _dt.date | DateDTO | str,
        *,
        session: Session,
    ) -> RhrAnalysis | None:
        """Fetch RHR analysis for a user and date."""
        date_str = dt if isinstance(dt, str) else dt.isoformat()
        return session.get(cls, (user_id, date_str))


class PaBaseline(Base):
    """Pa (power/pace at fixed DFA a1) baseline for Ra calculation."""

    __tablename__ = "pa_baseline"
    __table_args__ = (UniqueConstraint("user_id", "activity_type", "date", name="uq_pa_baseline_user_type_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    activity_type: Mapped[str] = mapped_column(String)  # "Ride" | "Run"
    date: Mapped[str] = mapped_column(String)  # "YYYY-MM-DD"
    pa_value: Mapped[float] = mapped_column(Float)  # Power/pace at fixed a1 (warmup)
    dfa_a1_ref: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- CRUD ---

    @classmethod
    @with_sync_session
    def get_average(
        cls,
        user: int | UserDTO,
        activity_type: str,
        days: int = 14,
        as_of: DateDTO | str | None = None,
        *,
        session: Session,
    ) -> float | None:
        """Return average Pa over last N days for a sport, or None if <3 data points."""
        user_id = user if isinstance(user, int) else user.id

        ref = as_of or _dt.date.today()
        cutoff = (ref - timedelta(days=days)).isoformat()
        newest = ref.isoformat()

        if as_of is None:
            ref = _dt.date.today()
        elif isinstance(as_of, str):
            ref = _dt.date.fromisoformat(as_of)
        else:
            ref = as_of
        cutoff = (ref - timedelta(days=days)).isoformat()

        result = session.execute(
            select(cls.pa_value)
            .where(
                cls.user_id == user_id,
                cls.activity_type == activity_type,
                cls.date >= cutoff,
                cls.date <= newest,
                (cls.quality != "poor") | (cls.quality.is_(None)),
            )
            .order_by(cls.date.desc())
        )
        values = list(result.scalars().all())
        if len(values) < 3:
            return
        return sum(values) / len(values)
