from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text, select, update
from sqlalchemy.exc import IntegrityError
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
    __table_args__ = (
        CheckConstraint(
            "intervals_auth_method IN ('api_key', 'oauth', 'none')",
            name="ck_users_intervals_auth_method",
        ),
    )

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
    last_donation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Intervals.icu OAuth — see api/routers/intervals/oauth.py
    # `intervals_auth_method` is the source of truth for which credential path
    # `IntervalsClient.for_user()` should use. `"api_key"` is the legacy default,
    # `"oauth"` is set after a successful OAuth callback, `"none"` means the user
    # has no Intervals credentials configured at all (cleared after revoke).
    intervals_access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-encrypted
    intervals_oauth_scope: Mapped[str | None] = mapped_column(String, nullable=True)
    intervals_auth_method: Mapped[str] = mapped_column(String(10), default="api_key", nullable=False)

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

    # --- Intervals.icu OAuth (see api/routers/intervals/oauth.py) ---

    def set_oauth_tokens(self, access_token: str, scope: str) -> None:
        """Store Intervals.icu OAuth credentials.

        Does NOT touch `role`, `athlete_id`, or `mcp_token` — the caller is
        responsible for those side effects (Phase 2+ will handle role promotion
        and mcp_token generation in the callback).
        """
        self.intervals_access_token_encrypted = encrypt_field(access_token)
        self.intervals_oauth_scope = scope
        self.intervals_auth_method = "oauth"

    @property
    def intervals_access_token(self) -> str | None:
        if not self.intervals_access_token_encrypted:
            return None
        return decrypt_field(self.intervals_access_token_encrypted)

    def clear_oauth_tokens(self) -> None:
        """Wipe OAuth state. Called on disconnect or 401 from Intervals.icu.

        Fallback `intervals_auth_method`:
        - `"api_key"` if the user still has a legacy api_key (so sync keeps working)
        - `"none"` otherwise (user must reconnect)
        """
        self.intervals_access_token_encrypted = None
        self.intervals_oauth_scope = None
        self.intervals_auth_method = "api_key" if self.api_key_encrypted else "none"

    # --- CRUD ---

    @classmethod
    @dual
    def get_by_id(cls, user_id: int, *, session: Session) -> User | None:
        return session.get(cls, user_id)

    @classmethod
    @dual
    def get_active_athletes(cls, *, session: Session) -> list[User]:
        result = session.execute(
            select(cls).where(
                cls.is_active.is_(True),
                cls.athlete_id.isnot(None),
            )
        )
        return list(result.scalars().all())

    @classmethod
    @with_session
    async def get_by_mcp_token(cls, token: str, *, session: AsyncSession) -> User | None:
        result = await session.execute(select(cls).where(cls.mcp_token == token, cls.is_active.is_(True)))
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def get_by_athlete_id(cls, athlete_id: str, *, session: AsyncSession) -> User | None:
        """Find user by Intervals.icu athlete_id (e.g. `i317960`).

        Used by the webhook receiver to map incoming events to the correct
        tenant. **Does NOT filter `is_active`** — after a user blocks the bot
        (`is_active=False`) or is deactivated, Intervals.icu may keep pushing
        events for some time, and we still want to record them so the history
        remains consistent if the user returns. Downstream handlers decide
        per event type whether to ignore or process for an inactive user.
        """
        result = await session.execute(select(cls).where(cls.athlete_id == athlete_id))
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def get_by_chat_id(
        cls,
        chat_id: int | str,
        include_inactive: bool = False,
        *,
        session: AsyncSession,
    ) -> User | None:
        query = select(cls).where(cls.chat_id == str(chat_id))
        if not include_inactive:
            query = query.where(cls.is_active.is_(True))
        result = await session.execute(query)
        return result.scalar_one_or_none()

    @classmethod
    @with_session
    async def get_owner(cls, *, session: AsyncSession) -> User | None:
        result = await session.execute(select(cls).where(cls.role == "owner").order_by(cls.id.asc()).limit(1))
        return result.scalar_one_or_none()

    @classmethod
    @dual
    def set_active_by_chat_id(cls, chat_id: int | str, active: bool, *, session: Session) -> None:
        """Toggle `is_active` by chat_id. Called from `my_chat_member` handler and 403 fallbacks."""
        session.execute(update(cls).where(cls.chat_id == str(chat_id)).values(is_active=active))
        session.commit()

    @classmethod
    @with_session
    async def mark_donation(cls, user_id: int, *, session: AsyncSession) -> None:
        """Set `last_donation_at = now()`. Called from successful_payment_callback
        to drive the donate-nudge suppression window (see DONATE_SPEC §11.2a).
        """
        await session.execute(update(cls).where(cls.id == user_id).values(last_donation_at=datetime.now(timezone.utc)))
        await session.commit()

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

    @classmethod
    async def get_or_create_from_telegram(
        cls,
        chat_id: str,
        *,
        username: str | None = None,
        display_name: str | None = None,
    ) -> User:
        """Fetch an existing user or create a new `viewer` from Telegram identity.

        Used by every entry point that authenticates via Telegram: `/start`
        command, Mini App initData, and Telegram Login Widget. Race-safe: if
        a concurrent caller inserts the row between our SELECT and INSERT we
        catch the `IntegrityError` and re-fetch. Any other failure in
        `create()` propagates untouched.

        New users are always pinned to `role="viewer"` here — promotion to
        `athlete` stays manual via `cli shell`. We intentionally don't rely
        on `create()`'s default role, so a future change to that default
        cannot silently widen the widget-auth security posture.
        """
        # `include_inactive=True` so a blocked user (is_active=False) is still
        # findable — prevents `IntegrityError` on the UNIQUE `chat_id` if we
        # tried to `create()` a fresh row. Reactivation is NOT done here: it
        # belongs to explicit re-engagement paths (`/start` handler and
        # `my_chat_member` MEMBER transition), not to webapp/Login Widget auth.
        # See `docs/MULTI_TENANT_SECURITY.md` §T14 for rationale.
        user = await cls.get_by_chat_id(chat_id, include_inactive=True)
        if user:
            return user
        try:
            return await cls.create(
                chat_id=chat_id,
                role="viewer",
                username=username,
                display_name=display_name,
            )
        except IntegrityError:
            # Concurrent insert: another request created the row first.
            refetched = await cls.get_by_chat_id(chat_id)
            if refetched:
                return refetched
            raise
