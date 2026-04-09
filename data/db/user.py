from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, Session, mapped_column

from data.crypto import decrypt_field, encrypt_field

# Backward-compatible re-exports (moved to data.db.dto)
from data.db.dto import (  # noqa: F401, E402
    DriftAlertDTO,
    ThresholdDriftDTO,
    ThresholdFreshnessDTO,
    ThresholdTestDTO,
    UserDTO,
)

from .common import Base
from .decorator import dual, with_session


class UserRole(str, Enum):
    owner = "owner"
    coach = "coach"
    athlete = "athlete"
    viewer = "viewer"


class User(Base):
    """Multi-tenant user table. Tenant = individual athlete."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String, unique=True)
    username: Mapped[str | None] = mapped_column(String)
    display_name: Mapped[str | None] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default=UserRole.viewer)  # UserRole

    athlete_id: Mapped[str | None] = mapped_column(String, unique=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)  # Fernet-encrypted
    mcp_token: Mapped[str | None] = mapped_column(String(64), unique=True)  # MCP Bearer token

    language: Mapped[str] = mapped_column(String(5), default="ru")
    preferred_model: Mapped[str | None] = mapped_column(String(30))

    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    primary_sport: Mapped[str | None] = mapped_column(String(20), nullable=True)  # triathlon/run/ride/swim/fitness

    is_silent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # --- Helpers ---

    def set_api_key(self, plaintext: str) -> None:
        self.api_key_encrypted = encrypt_field(plaintext)

    def _get_api_key(self) -> str | None:
        if not self.api_key_encrypted:
            return
        return decrypt_field(self.api_key_encrypted)

    @property
    def api_key(self) -> str | None:
        return self._get_api_key()

    def generate_mcp_token(self) -> str:
        """Generate a new MCP token, store it, return it."""
        self.mcp_token = secrets.token_hex(32)
        return self.mcp_token

    # --- CRUD ---

    @classmethod
    @dual
    def get_by_id(cls, user_id: int, *, session: Session) -> User | None:
        return session.get(cls, user_id)

    @classmethod
    @dual
    def get_active_athletes(cls, *, session: Session) -> list[User]:
        result = session.execute(select(cls).where(cls.is_active.is_(True), cls.athlete_id.isnot(None)))
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def get_by_mcp_token(cls, token: str, *, session: AsyncSession) -> User | None:
        result = await session.execute(select(cls).where(cls.mcp_token == token, cls.is_active.is_(True)))
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def get_by_chat_id(cls, chat_id: str, *, session: AsyncSession) -> User | None:
        result = await session.execute(select(cls).where(cls.chat_id == chat_id, cls.is_active.is_(True)))
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def detect_threshold_drift(
        cls,
        user_id: int,
        *,
        session: Session,
    ) -> ThresholdDriftDTO | None:
        """Compare recent HRVT1 values with athlete_settings LTHR to detect drift.

        Returns drift alerts if >5% divergence found across 2+ tests, or None.
        """
        from .activity import Activity, ActivityHrv
        from .athlete import AthleteSettings

        alerts: list[DriftAlertDTO] = []

        for sport_label in ("Ride", "Run"):
            settings_row = session.execute(
                select(AthleteSettings).where(
                    AthleteSettings.user_id == user_id,
                    AthleteSettings.sport == sport_label,
                )
            ).scalar_one_or_none()

            if not settings_row or not settings_row.lthr:
                continue

            result = session.execute(
                select(ActivityHrv.hrvt1_hr)
                .join(Activity, Activity.id == ActivityHrv.activity_id)
                .where(
                    Activity.user_id == user_id,
                    ActivityHrv.processing_status == "processed",
                    ActivityHrv.hrvt1_hr.isnot(None),
                    ActivityHrv.hrv_quality.in_(["good", "moderate"]),
                    Activity.type == sport_label,
                )
                .order_by(Activity.start_date_local.desc())
                .limit(3)
            )
            rows = result.all()

            if len(rows) < 2:
                continue

            avg_hrvt1 = sum(r[0] for r in rows) / len(rows)
            config_lthr = settings_row.lthr
            pct_diff = (avg_hrvt1 - config_lthr) / config_lthr * 100

            if abs(pct_diff) > 5:
                alerts.append(
                    DriftAlertDTO(
                        sport=sport_label,
                        metric="LTHR",
                        measured_avg=round(avg_hrvt1),
                        config_value=config_lthr,
                        diff_pct=round(pct_diff, 1),
                        tests_count=len(rows),
                        message=(
                            f"HRVT1 stable at {round(avg_hrvt1)} bpm ({len(rows)} tests). "
                            f"Current LTHR {sport_label}: {config_lthr} bpm ({pct_diff:+.1f}%). "
                            "Consider updating LTHR."
                        ),
                    )
                )

        return ThresholdDriftDTO(alerts=alerts) if alerts else None

    @classmethod
    @dual
    def get_threshold_freshness(
        cls,
        user_id: int,
        sport: str = "",
        *,
        session: Session,
    ) -> ThresholdFreshnessDTO:
        """Check how fresh HRVT1/HRVT2 thresholds are for a user."""
        from .activity import Activity, ActivityHrv

        query = (
            select(
                Activity.type,
                Activity.start_date_local,
                ActivityHrv.hrvt1_hr,
                ActivityHrv.hrvt2_hr,
            )
            .join(ActivityHrv, Activity.id == ActivityHrv.activity_id)
            .where(
                Activity.user_id == user_id,
                ActivityHrv.processing_status == "processed",
                ActivityHrv.hrvt1_hr.isnot(None),
            )
        )
        if sport:
            query = query.where(Activity.type == sport)

        query = query.order_by(Activity.start_date_local.desc()).limit(5)

        result = session.execute(query)
        rows = result.all()

        if not rows:
            return ThresholdFreshnessDTO(status="no_data", sport=sport or "all")

        last_date_str = rows[0][1]
        last_date = date.fromisoformat(last_date_str) if last_date_str else None
        days_since = (date.today() - last_date).days if last_date else 0

        return ThresholdFreshnessDTO(
            status="stale" if days_since and days_since > 21 else "fresh",
            sport=sport or "all",
            days_since=days_since,
            last_date=str(last_date) if last_date else None,
            last_hrvt1=rows[0][2],
            last_hrvt2=rows[0][3],
            recent_tests=[ThresholdTestDTO(sport=r[0], date=str(r[1]), hrvt1_hr=r[2], hrvt2_hr=r[3]) for r in rows],
        )

    @classmethod
    @with_session
    async def create(
        cls,
        chat_id: str,
        role: str = "viewer",
        username: str | None = None,
        display_name: str | None = None,
        athlete_id: str | None = None,
        api_key: str | None = None,
        language: str = "ru",
        *,
        session: AsyncSession,
    ) -> User:
        user = cls(
            chat_id=chat_id,
            role=role,
            username=username,
            display_name=display_name,
            athlete_id=athlete_id,
            language=language,
        )
        if api_key:
            user.set_api_key(api_key)

        session.add(user)
        await session.commit()
        await session.refresh(user)

        return user
