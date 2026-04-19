"""Progression model run records — training metrics + model path."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from data.db.common import Base, Session
from data.db.decorator import dual


class ProgressionModelRun(Base):
    __tablename__ = "progression_model_runs"
    __table_args__ = (UniqueConstraint("user_id", "sport", "trained_at", name="uq_progression_user_sport_trained"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    sport: Mapped[str] = mapped_column(String(16), nullable=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    n_examples: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    r2: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    shap_global_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    @classmethod
    @dual
    def save_run(
        cls,
        *,
        user_id: int,
        sport: str,
        n_examples: int,
        mae: float,
        r2: float,
        model_path: str,
        shap_global_json: dict,
        session: Session,
    ) -> ProgressionModelRun:
        now = datetime.now(timezone.utc)
        row = cls(
            user_id=user_id,
            sport=sport,
            trained_at=now,
            n_examples=n_examples,
            mae=mae,
            r2=r2,
            model_path=model_path,
            shap_global_json=shap_global_json,
        )
        session.add(row)
        session.commit()
        return row

    @classmethod
    @dual
    def get_latest(cls, user_id: int, sport: str, *, session: Session) -> ProgressionModelRun | None:
        result = session.execute(
            select(cls).where(cls.user_id == user_id, cls.sport == sport).order_by(cls.trained_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()
