"""ORM models for Garmin GDPR export data (9 tables)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from .common import Base, Session
from .decorator import dual


class GarminSleep(Base):
    """Detailed sleep data from Garmin GDPR export."""

    __tablename__ = "garmin_sleep"
    __table_args__ = (UniqueConstraint("user_id", "calendar_date", name="uq_garmin_sleep_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    calendar_date: Mapped[str] = mapped_column(String, nullable=False)

    sleep_start_gmt: Mapped[str | None] = mapped_column(String, nullable=True)
    sleep_end_gmt: Mapped[str | None] = mapped_column(String, nullable=True)

    # Phases (seconds)
    deep_sleep_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    light_sleep_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rem_sleep_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    awake_sleep_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Scores (0-100)
    overall_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recovery_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deep_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rem_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    restfulness_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Respiration
    avg_respiration: Mapped[float | None] = mapped_column(Float, nullable=True)
    lowest_respiration: Mapped[float | None] = mapped_column(Float, nullable=True)
    highest_respiration: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Stress / Other
    avg_sleep_stress: Mapped[float | None] = mapped_column(Float, nullable=True)
    awake_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    restless_moments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @classmethod
    @dual
    def get_for_date(cls, user_id: int, dt: date | str, *, session: Session) -> GarminSleep | None:
        _dt = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(select(cls).where(cls.user_id == user_id, cls.calendar_date == _dt))
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_range(cls, user_id: int, start: str, end: str, *, session: Session) -> list[GarminSleep]:
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.calendar_date >= start, cls.calendar_date <= end)
            .order_by(cls.calendar_date.asc())
        )
        return list(result.scalars().all())


class GarminDailySummary(Base):
    """UDS daily aggregates: stress, body battery, steps, HR."""

    __tablename__ = "garmin_daily_summary"
    __table_args__ = (UniqueConstraint("user_id", "calendar_date", name="uq_garmin_daily_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    calendar_date: Mapped[str] = mapped_column(String, nullable=False)

    total_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floors_ascended_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    highly_active_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)

    min_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resting_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)

    avg_stress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_stress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stress_high_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stress_medium_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stress_low_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stress_rest_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)

    body_battery_high: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_battery_low: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_battery_charged: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_battery_drained: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @classmethod
    @dual
    def get_for_date(cls, user_id: int, dt: date | str, *, session: Session) -> GarminDailySummary | None:
        _dt = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(select(cls).where(cls.user_id == user_id, cls.calendar_date == _dt))
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_range(cls, user_id: int, start: str, end: str, *, session: Session) -> list[GarminDailySummary]:
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.calendar_date >= start, cls.calendar_date <= end)
            .order_by(cls.calendar_date.asc())
        )
        return list(result.scalars().all())


class GarminTrainingReadiness(Base):
    """Training Readiness score with factor breakdown."""

    __tablename__ = "garmin_training_readiness"
    __table_args__ = (
        UniqueConstraint("user_id", "calendar_date", "input_context", name="uq_garmin_readiness_user_date_ctx"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    calendar_date: Mapped[str] = mapped_column(String, nullable=False)
    timestamp_gmt: Mapped[str | None] = mapped_column(String, nullable=True)

    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[str | None] = mapped_column(String, nullable=True)
    feedback_short: Mapped[str | None] = mapped_column(String, nullable=True)
    feedback_long: Mapped[str | None] = mapped_column(Text, nullable=True)

    sleep_score_factor_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    recovery_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recovery_factor_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    acwr_factor_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    stress_history_factor_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_factor_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_history_factor_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    hrv_weekly_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    acute_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_context: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @classmethod
    @dual
    def get_for_date(
        cls, user_id: int, dt: date | str, context: str = "AFTER_WAKEUP_RESET", *, session: Session
    ) -> GarminTrainingReadiness | None:
        _dt = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(
            select(cls).where(cls.user_id == user_id, cls.calendar_date == _dt, cls.input_context == context)
        )
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_range(
        cls, user_id: int, start: str, end: str, context: str = "AFTER_WAKEUP_RESET", *, session: Session
    ) -> list[GarminTrainingReadiness]:
        result = session.execute(
            select(cls)
            .where(
                cls.user_id == user_id,
                cls.calendar_date >= start,
                cls.calendar_date <= end,
                cls.input_context == context,
            )
            .order_by(cls.calendar_date.asc())
        )
        return list(result.scalars().all())


class GarminHealthStatus(Base):
    """Daily health baselines: HRV, HR, SpO2, skin temp, respiration."""

    __tablename__ = "garmin_health_status"
    __table_args__ = (UniqueConstraint("user_id", "calendar_date", name="uq_garmin_health_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    calendar_date: Mapped[str] = mapped_column(String, nullable=False)

    hrv_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_baseline_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_baseline_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_status: Mapped[str | None] = mapped_column(String, nullable=True)

    hr_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_baseline_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_baseline_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_status: Mapped[str | None] = mapped_column(String, nullable=True)

    spo2_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    spo2_baseline_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    spo2_baseline_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    spo2_status: Mapped[str | None] = mapped_column(String, nullable=True)

    skin_temp_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    skin_temp_baseline_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    skin_temp_baseline_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    skin_temp_status: Mapped[str | None] = mapped_column(String, nullable=True)

    respiration_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    respiration_baseline_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    respiration_baseline_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    respiration_status: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @classmethod
    @dual
    def get_for_date(cls, user_id: int, dt: date | str, *, session: Session) -> GarminHealthStatus | None:
        _dt = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(select(cls).where(cls.user_id == user_id, cls.calendar_date == _dt))
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_range(cls, user_id: int, start: str, end: str, *, session: Session) -> list[GarminHealthStatus]:
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.calendar_date >= start, cls.calendar_date <= end)
            .order_by(cls.calendar_date.asc())
        )
        return list(result.scalars().all())


class GarminTrainingLoad(Base):
    """ACWR + daily training load from Garmin."""

    __tablename__ = "garmin_training_load"
    __table_args__ = (UniqueConstraint("user_id", "calendar_date", name="uq_garmin_load_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    calendar_date: Mapped[str] = mapped_column(String, nullable=False)

    acute_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    chronic_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    acwr: Mapped[float | None] = mapped_column(Float, nullable=True)
    acwr_status: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @classmethod
    @dual
    def get_for_date(cls, user_id: int, dt: date | str, *, session: Session) -> GarminTrainingLoad | None:
        _dt = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(select(cls).where(cls.user_id == user_id, cls.calendar_date == _dt))
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_range(cls, user_id: int, start: str, end: str, *, session: Session) -> list[GarminTrainingLoad]:
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.calendar_date >= start, cls.calendar_date <= end)
            .order_by(cls.calendar_date.asc())
        )
        return list(result.scalars().all())


class GarminFitnessMetrics(Base):
    """Combined VO2max + Endurance Score + Max MET (sparse, ~1/week)."""

    __tablename__ = "garmin_fitness_metrics"
    __table_args__ = (UniqueConstraint("user_id", "calendar_date", name="uq_garmin_fitness_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    calendar_date: Mapped[str] = mapped_column(String, nullable=False)

    vo2max_running: Mapped[float | None] = mapped_column(Float, nullable=True)
    vo2max_cycling: Mapped[float | None] = mapped_column(Float, nullable=True)
    endurance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_met: Mapped[float | None] = mapped_column(Float, nullable=True)
    fitness_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_activity_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @classmethod
    @dual
    def get_for_date(cls, user_id: int, dt: date | str, *, session: Session) -> GarminFitnessMetrics | None:
        _dt = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(select(cls).where(cls.user_id == user_id, cls.calendar_date == _dt))
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_range(cls, user_id: int, start: str, end: str, *, session: Session) -> list[GarminFitnessMetrics]:
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.calendar_date >= start, cls.calendar_date <= end)
            .order_by(cls.calendar_date.asc())
        )
        return list(result.scalars().all())


class GarminRacePredictions(Base):
    """Race time predictions (5K/10K/HM/Marathon)."""

    __tablename__ = "garmin_race_predictions"
    __table_args__ = (UniqueConstraint("user_id", "calendar_date", name="uq_garmin_race_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    calendar_date: Mapped[str] = mapped_column(String, nullable=False)

    prediction_5k_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prediction_10k_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prediction_half_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prediction_marathon_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @classmethod
    @dual
    def get_for_date(cls, user_id: int, dt: date | str, *, session: Session) -> GarminRacePredictions | None:
        _dt = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(select(cls).where(cls.user_id == user_id, cls.calendar_date == _dt))
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_range(cls, user_id: int, start: str, end: str, *, session: Session) -> list[GarminRacePredictions]:
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.calendar_date >= start, cls.calendar_date <= end)
            .order_by(cls.calendar_date.asc())
        )
        return list(result.scalars().all())


class GarminBioMetrics(Base):
    """Weight / LT history (sparse, ~1/week). Use get_latest_before for fill-forward."""

    __tablename__ = "garmin_bio_metrics"
    __table_args__ = (UniqueConstraint("user_id", "calendar_date", name="uq_garmin_bio_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    calendar_date: Mapped[str] = mapped_column(String, nullable=False)

    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    lactate_threshold_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lactate_threshold_speed: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @classmethod
    @dual
    def get_latest_before(cls, user_id: int, dt: date | str, *, session: Session) -> GarminBioMetrics | None:
        """Get most recent bio metrics on or before the given date."""
        _dt = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.calendar_date <= _dt)
            .order_by(cls.calendar_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_range(cls, user_id: int, start: str, end: str, *, session: Session) -> list[GarminBioMetrics]:
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.calendar_date >= start, cls.calendar_date <= end)
            .order_by(cls.calendar_date.asc())
        )
        return list(result.scalars().all())


class GarminAbnormalHrEvents(Base):
    """Abnormal HR events (arrhythmia, pauses, high/low HR)."""

    __tablename__ = "garmin_abnormal_hr_events"
    __table_args__ = (UniqueConstraint("user_id", "timestamp_gmt", name="uq_garmin_abnormal_hr_user_ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    timestamp_gmt: Mapped[str] = mapped_column(String, nullable=False)
    calendar_date: Mapped[str] = mapped_column(String, nullable=False)

    hr_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    threshold_value: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @classmethod
    @dual
    def get_range(cls, user_id: int, start: str, end: str, *, session: Session) -> list[GarminAbnormalHrEvents]:
        result = session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.calendar_date >= start, cls.calendar_date <= end)
            .order_by(cls.timestamp_gmt.asc())
        )
        return list(result.scalars().all())
