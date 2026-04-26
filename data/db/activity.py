from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .user import UserDTO

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ColumnElement,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from data.db.common import Base
from data.intervals.dto import ActivityDTO
from tasks.dto import ORMDTO, DateDTO

from .common import Session
from .decorator import dual, with_session, with_sync_session

logger = logging.getLogger(__name__)


class Activity(Base):
    """Completed activity synced from Intervals.icu."""

    __tablename__ = "activities"
    __table_args__ = (CheckConstraint("rpe IS NULL OR (rpe BETWEEN 1 AND 10)", name="ck_activities_rpe_range"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)  # Intervals.icu activity ID (e.g. "i12345")
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    start_date_local: Mapped[str] = mapped_column(String, index=True)  # "YYYY-MM-DD"
    type: Mapped[str | None] = mapped_column(String, nullable=True)  # Ride, Run, Swim, ...
    icu_training_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    moving_time: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    average_hr: Mapped[float | None] = mapped_column(Float, nullable=True)  # avg heart rate
    is_race: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sub_type: Mapped[str | None] = mapped_column(String, nullable=True)  # NONE|RACE|COMMUTE|WARMUP|COOLDOWN
    rpe: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Borg CR-10 (1-10), see docs/RPE_SPEC.md
    source: Mapped[str | None] = mapped_column(String, nullable=True)  # GARMIN_CONNECT, OAUTH_CLIENT, STRAVA, ...
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fit_file_path: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- CRUD ---

    @classmethod
    @dual
    def save_bulk(
        cls,
        user: int | UserDTO,
        activities: list[ActivityDTO],
        *,
        session: Session,
    ) -> list[str]:
        """Upsert completed activities from Intervals.icu. Returns IDs of newly created rows."""
        user_id = user if isinstance(user, int) else user.id
        if not activities:
            return []

        now = datetime.now(timezone.utc)
        values = [
            {
                "id": a.id,
                "user_id": user_id,
                "start_date_local": str(a.start_date_local)[:10],
                "type": a.type,
                "icu_training_load": a.icu_training_load,
                "moving_time": a.moving_time,
                "average_hr": a.average_hr,
                "is_race": getattr(a, "is_race", False),
                "sub_type": getattr(a, "sub_type", None),
                "source": getattr(a, "source", None),
                "rpe": getattr(a, "icu_rpe", None),
                "last_synced_at": now,
            }
            for a in activities
        ]

        incoming_ids = [v["id"] for v in values]
        existing_ids = set(row[0] for row in session.execute(select(cls.id).where(cls.id.in_(incoming_ids))))

        stmt = insert(cls).values(values)
        # is_race / sub_type: locally tagged races (via bot tag_race) must survive re-sync.
        # Intervals.icu is not the source of truth for race tagging, so we OR-merge the flag
        # and keep the existing sub_type when it was already set locally.
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "start_date_local": stmt.excluded.start_date_local,
                "type": stmt.excluded.type,
                "icu_training_load": stmt.excluded.icu_training_load,
                "moving_time": stmt.excluded.moving_time,
                "average_hr": stmt.excluded.average_hr,
                "is_race": cls.is_race | stmt.excluded.is_race,
                "sub_type": func.coalesce(cls.sub_type, stmt.excluded.sub_type),
                "source": stmt.excluded.source,
                "rpe": func.coalesce(cls.rpe, stmt.excluded.rpe),
                "last_synced_at": stmt.excluded.last_synced_at,
            },
        )
        session.execute(stmt)
        session.commit()
        return [aid for aid in incoming_ids if aid not in existing_ids]

    @classmethod
    @with_sync_session
    def get_windowed(
        cls,
        user_id: int,
        *,
        filters: tuple[ColumnElement, ...] = (),
        as_of: DateDTO | None = None,
        session: Session,
    ) -> list[Activity]:
        """Return activities within a date window with extra SA filters."""
        ref = as_of or date.today()
        days = 90

        cutoff = (ref - timedelta(days=days)).isoformat()
        newest = ref.isoformat()

        result = session.execute(
            select(cls)
            .where(
                cls.user_id == user_id,
                cls.start_date_local >= cutoff,
                cls.start_date_local <= newest,
                *filters,
            )
            .order_by(cls.start_date_local.asc())
        )
        return list(result.scalars().all())

    @classmethod
    @dual
    def get_for_date(
        cls,
        user_id: int,
        dt: date | DateDTO | str,
        *,
        session: Session,
    ) -> list[Activity]:
        """Get all activities for a specific date."""
        _dt = dt if isinstance(dt, str) else dt.isoformat()
        result = session.execute(
            select(cls)
            .where(
                cls.user_id == user_id,
                cls.start_date_local == _dt,
            )
            .order_by(cls.id)
        )
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
    ) -> tuple[list[Activity], datetime | None]:
        """Return activities in date range and MAX(last_synced_at)."""
        start_str, end_str = str(start), str(end)
        result = await session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.start_date_local >= start_str, cls.start_date_local <= end_str)
            .order_by(cls.start_date_local, cls.id)
        )
        activities = list(result.scalars().all())

        sync_result = await session.execute(select(func.max(cls.last_synced_at)).where(cls.user_id == user_id))
        last_synced_at = sync_result.scalar_one_or_none()

        return activities, last_synced_at

    @classmethod
    @with_session
    async def exists_for_user(
        cls,
        user_id: int,
        activity_id: str,
        *,
        session: AsyncSession,
    ) -> bool:
        """Tenant-safe existence check: True iff (user_id, activity_id) row exists.

        Used by ``_dispatch_achievements`` (and other webhook paths) to gate
        downstream writes against tampered/foreign ``activity.id`` payloads.
        Without this guard, an attacker with a leaked webhook secret could
        write achievement rows referencing another user's activity_id and
        surface them under their own ``user_id`` in tenant-scoped reads.
        See ``docs/MULTI_TENANT_SECURITY.md`` T19.
        """
        result = await session.execute(select(cls.id).where(cls.user_id == user_id, cls.id == activity_id).limit(1))
        return result.scalar_one_or_none() is not None


class ActivityAchievement(Base):
    """Per-activity achievement from ``ACTIVITY_ACHIEVEMENTS`` webhook.

    Stores power PRs (5s/10s/30s/1m/5m/...), FTP changes, and any future
    Intervals.icu milestone types. Source of truth for the social-share UI.
    """

    __tablename__ = "activity_achievements"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "activity_id",
            "achievement_id",
            name="uq_activity_achievements_user_activity_achievement",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    activity_id: Mapped[str] = mapped_column(String, ForeignKey("activities.id", ondelete="CASCADE"), nullable=False)
    achievement_id: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ftp_at_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctl_at_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    point_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    _FTP_CHANGE_TYPE = "FTP_CHANGE"
    _FTP_CHANGE_ID = "ftp_change"

    @classmethod
    @with_session
    async def save_bulk(
        cls,
        user_id: int,
        activity_id: str,
        activity: dict,
        *,
        session: AsyncSession,
    ) -> int:
        """Idempotent upsert from a raw ``ACTIVITY_ACHIEVEMENTS`` webhook payload.

        Reads three sources in the activity dict:
          - ``icu_achievements[]`` — power/time PRs (BEST_POWER, ...)
          - ``icu_rolling_ftp_delta != 0`` — synthesised FTP_CHANGE row
          - ``icu_rolling_ftp`` / ``icu_ctl`` — snapshot for context

        ``ON CONFLICT DO NOTHING`` on the unique key keeps re-delivered webhooks
        from duplicating rows. Returns count of NEW rows inserted (for logging).
        """
        ftp = activity.get("icu_rolling_ftp")
        ctl = activity.get("icu_ctl")

        rows: list[dict] = []

        for ach in activity.get("icu_achievements") or []:
            if not isinstance(ach, dict):
                continue
            ach_id = ach.get("id")
            ach_type = ach.get("type")
            if not ach_id or not ach_type:
                # Drop achievements we cannot key on — without (id, type) we
                # cannot dedupe, and without dedupe we'd accumulate duplicates
                # on every webhook redelivery.
                continue
            rows.append(
                {
                    "user_id": user_id,
                    "activity_id": activity_id,
                    "achievement_id": str(ach_id),
                    "type": str(ach_type),
                    "value": _coerce_float(ach.get("watts")),
                    "secs": _coerce_int(ach.get("secs")),
                    "ftp_at_time": _coerce_int(ftp),
                    "ctl_at_time": _coerce_float(ctl),
                    "point_data": ach.get("point"),
                    "extra": ach,
                }
            )

        # FTP_CHANGE — synthetic achievement when rolling FTP changed. Surfaces
        # FTP PRs in the same query as power PRs for unified social-share lists.
        ftp_delta = activity.get("icu_rolling_ftp_delta")
        if ftp_delta is not None and ftp_delta != 0 and ftp is not None:
            rows.append(
                {
                    "user_id": user_id,
                    "activity_id": activity_id,
                    "achievement_id": cls._FTP_CHANGE_ID,
                    "type": cls._FTP_CHANGE_TYPE,
                    "value": _coerce_float(ftp),
                    "secs": None,
                    "ftp_at_time": _coerce_int(ftp),
                    "ctl_at_time": _coerce_float(ctl),
                    "point_data": None,
                    "extra": {"delta": ftp_delta},
                }
            )

        if not rows:
            return 0

        stmt = (
            insert(cls)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["user_id", "activity_id", "achievement_id"],
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        # rowcount on ON CONFLICT DO NOTHING reflects only inserted rows
        return result.rowcount or 0

    @classmethod
    @with_session
    async def get_for_activity(
        cls,
        user_id: int,
        activity_id: str,
        *,
        session: AsyncSession,
    ) -> list[ActivityAchievement]:
        """Tenant-safe fetch by (user_id, activity_id).

        Secondary order on ``id`` — ``save_bulk`` writes all rows in one
        ``INSERT`` so they share the same ``created_at`` (server `now()`),
        making a single-key sort nondeterministic across drivers/replicas.
        """
        result = await session.execute(
            select(cls)
            .where(cls.user_id == user_id, cls.activity_id == activity_id)
            .order_by(cls.created_at.asc(), cls.id.asc())
        )
        return list(result.scalars().all())


def _coerce_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class ActivityHrv(Base):
    """Post-activity HRV analysis (DFA alpha 1) — Level 2 pipeline."""

    __tablename__ = "activity_hrv"

    activity_id: Mapped[str] = mapped_column(String, ForeignKey("activities.id"), primary_key=True)
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
    @with_session
    async def save(cls, row: "ActivityHrv", *, session: AsyncSession) -> None:
        """Upsert an activity HRV analysis row."""
        existing = await session.get(cls, row.activity_id)
        if existing:
            for col in cls.__table__.columns:
                if col.name != "activity_id":
                    setattr(existing, col.name, getattr(row, col.name))
        else:
            session.add(row)
        await session.commit()

    @classmethod
    @dual
    def get_for_date(
        cls,
        user_id: int,
        dt: date | DateDTO | str,
        *,
        session: Session,
    ) -> list[ActivityHrv]:
        """Get all activity_hrv rows for activities on a specific date (via JOIN)."""
        _dt = dt if isinstance(dt, str) else dt.isoformat()

        result = session.execute(
            select(cls)
            .join(Activity, Activity.id == cls.activity_id)
            .where(
                Activity.user_id == user_id,
                Activity.start_date_local == _dt,
            )
            .order_by(cls.activity_id)
        )
        return list(result.scalars().all())

    @classmethod
    @dual
    def count_hrvt1_samples(
        cls,
        user_id: int,
        sport: str,
        *,
        session: Session,
    ) -> int:
        """Count processed HRVT1 measurements for (user, sport) — same filter as drift detection."""
        result = session.execute(
            select(func.count(cls.activity_id))
            .join(Activity, Activity.id == cls.activity_id)
            .where(
                Activity.user_id == user_id,
                Activity.type == sport,
                cls.processing_status == "processed",
                cls.hrvt1_hr.isnot(None),
                cls.hrv_quality.in_(["good", "moderate"]),
            )
        )
        return int(result.scalar_one() or 0)


class ActivityDetail(Base):
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
    hr_zone_times: Mapped[list | None] = mapped_column(JSON, nullable=True)  # seconds per HR zone
    power_zone_times: Mapped[list | None] = mapped_column(JSON, nullable=True)  # seconds per power zone
    pace_zone_times: Mapped[list | None] = mapped_column(JSON, nullable=True)  # seconds per pace zone
    intervals: Mapped[list | None] = mapped_column(JSON, nullable=True)
    pool_length: Mapped[float | None] = mapped_column(Float, nullable=True)  # meters (25 or 50)

    # Mapping: Intervals.icu JSON key → ActivityDetail column
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
        "icu_hr_zone_times": "hr_zone_times",
        "pace_zone_times": "pace_zone_times",
        "pool_length": "pool_length",
    }

    # --- CRUD ---

    @classmethod
    @with_sync_session
    def save(
        cls,
        activity_id: str,
        detail_json: dict,
        intervals_json: list[dict] | None = None,
        *,
        session: Session,
    ) -> ORMDTO:
        """Upsert activity details from Intervals.icu API response."""
        row = session.get(cls, activity_id)
        is_new = row is None
        if is_new:
            row = cls(activity_id=activity_id)
            session.add(row)

        for api_key, col_name in cls._DETAIL_FIELD_MAP.items():
            if api_key in detail_json:
                setattr(row, col_name, detail_json[api_key])

        # icu_zone_times is ZoneTime[] ({id, secs}) — extract seconds array
        raw_zt = detail_json.get("icu_zone_times")
        if raw_zt and isinstance(raw_zt, list):
            row.power_zone_times = [z.get("secs", 0) for z in raw_zt if isinstance(z, dict)]

        if intervals_json is not None:
            row.intervals = intervals_json

        # Compute EF fallback if Intervals.icu didn't provide it.
        # EF = speed (m/min) / avg HR — gives values ~1.0-1.5.
        # For Run: use GAP (grade-adjusted pace) to normalize terrain effects.
        if not row.efficiency_factor:
            speed = row.gap if row.gap and row.gap > 0 else row.pace
            avg_hr = detail_json.get("average_heartrate") or detail_json.get("average_hr")
            if speed and speed > 0 and avg_hr and avg_hr > 0:
                row.efficiency_factor = round((speed * 60) / avg_hr, 6)

        is_changed = is_new or session.is_modified(row)
        session.commit()
        return ORMDTO(is_new=is_new, is_changed=is_changed, row=row)

    @classmethod
    @with_session
    async def get(cls, activity_id: str, *, session: AsyncSession) -> ActivityDetail | None:
        """Fetch activity details by activity ID."""
        return await session.get(cls, activity_id)

    @classmethod
    @with_session
    async def get_bulk(cls, activity_ids: list[str], *, session: AsyncSession) -> dict[str, ActivityDetail]:
        """Fetch multiple activity details by IDs. Returns {activity_id: row}."""
        if not activity_ids:
            return {}
        result = await session.execute(select(cls).where(cls.activity_id.in_(activity_ids)))
        return {r.activity_id: r for r in result.scalars().all()}


class Race(Base):
    """Extended race data — enriches Activity with race-specific context."""

    __tablename__ = "races"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    activity_id: Mapped[str] = mapped_column(String, ForeignKey("activities.id"), nullable=False, unique=True)

    name: Mapped[str] = mapped_column(String, nullable=False)
    race_type: Mapped[str] = mapped_column(String, default="C")  # A / B / C
    goal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    finish_time_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal_time_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    placement: Mapped[int | None] = mapped_column(Integer, nullable=True)
    placement_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    placement_ag: Mapped[str | None] = mapped_column(String, nullable=True)

    surface: Mapped[str | None] = mapped_column(String, nullable=True)
    weather: Mapped[str | None] = mapped_column(String, nullable=True)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    race_day_ctl: Mapped[float | None] = mapped_column(Float, nullable=True)
    race_day_atl: Mapped[float | None] = mapped_column(Float, nullable=True)
    race_day_tsb: Mapped[float | None] = mapped_column(Float, nullable=True)
    race_day_hrv_status: Mapped[str | None] = mapped_column(String, nullable=True)
    race_day_recovery_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    race_day_weight: Mapped[float | None] = mapped_column(Float, nullable=True)

    avg_pace_sec_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_pace_sec_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    splits: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    rpe: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @classmethod
    @dual
    def get_by_activity(cls, user_id: int, activity_id: str, *, session: Session) -> Race | None:
        result = session.execute(select(cls).where(cls.user_id == user_id, cls.activity_id == activity_id))
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def get_range(cls, user_id: int, start: str, end: str, *, session: Session) -> list[Race]:
        result = session.execute(
            select(cls)
            .join(Activity, Activity.id == cls.activity_id)
            .where(cls.user_id == user_id, Activity.start_date_local >= start, Activity.start_date_local <= end)
            .order_by(Activity.start_date_local.desc())
        )
        return list(result.scalars().all())
