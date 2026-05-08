from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Integer, String, Text, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, Session, mapped_column

from data.crypto import decrypt_field, encrypt_field

# Backward-compatible re-exports (moved to data.db.dto)
from data.db.dto import (  # noqa: F401, E402
    DRIFT_FTP_WATTS,
    DRIFT_LTHR_BPM,
    DRIFT_PACE_SEC_PER_KM,
    DRIFT_R2_MEDIUM,
    DriftAlertDTO,
    ThresholdDriftDTO,
    ThresholdFreshnessDTO,
    ThresholdTestDTO,
    UserDTO,
)

from .common import Base
from .decorator import dual, with_session


def parse_pace_to_sec(pace: str | None) -> int | None:
    """Parse a 'M:SS' pace string to seconds (per km), validated.

    Tolerates surrounding whitespace. Rejects:
      - non-string / no colon / non-numeric parts → None
      - negative minutes or seconds → None
      - seconds outside [0, 59] → None (e.g. "4:60" is bogus, not 5:00)

    Drift detection skips ``None`` rather than rejecting the whole batch.
    """
    if not isinstance(pace, str):
        return None
    pace = pace.strip()
    if ":" not in pace:
        return None
    try:
        m_str, s_str = pace.split(":", 1)
        m = int(m_str)
        s = int(s_str)
    except (ValueError, TypeError):
        return None
    if m < 0 or s < 0 or s >= 60:
        return None
    return m * 60 + s


def _drift_alert_lthr(
    sport: str,
    hrvt2_hr: float | None,
    r_squared: float | None,
    config_lthr: int,
) -> DriftAlertDTO | None:
    """LTHR drift: latest ramp-test only. Pushes HRVT2 (anaerobic threshold).

    Intervals.icu's `lthr` field is conceptually the lactate threshold = HRVT2.
    Pushing HRVT1 there (older behavior) shifted all zones down ~10-15%.

    Gate (RAMP_TEST_BIKE_SPEC §8): R² ≥ 0.7 (medium-confidence floor) and
    absolute |Δ bpm| ≥ 3. Older 5% relative gate accepted ~8 bpm delta on
    typical LTHR=160 — clinically too loose.
    """
    if hrvt2_hr is None or r_squared is None or r_squared < DRIFT_R2_MEDIUM:
        return None
    delta = round(hrvt2_hr) - config_lthr
    if abs(delta) < DRIFT_LTHR_BPM:
        return None
    pct = delta / config_lthr * 100
    return DriftAlertDTO(
        sport=sport,
        metric="LTHR",
        measured=round(hrvt2_hr),
        config_value=config_lthr,
        diff_pct=round(pct, 1),
        message=(
            f"HRVT2 = {round(hrvt2_hr)} bpm (R²={r_squared:.2f}). "
            f"Current LTHR {sport}: {config_lthr} bpm (Δ {delta:+d} bpm). "
            "Consider updating LTHR."
        ),
    )


def _drift_alert_pace(
    sport: str,
    hrvt2_pace: str | None,
    r_squared: float | None,
    config_pace_sec: float,
) -> DriftAlertDTO | None:
    """Run threshold-pace drift (sec/km). Latest ramp-test only, HRVT2 pace.

    Lower s/km = faster — sign of delta flips vs LTHR but the |Δ| ≥ 5 sec/km
    gate is direction-agnostic.
    """
    if r_squared is None or r_squared < DRIFT_R2_MEDIUM:
        return None
    sec = parse_pace_to_sec(hrvt2_pace)
    if sec is None or sec <= 0:
        return None
    config = int(round(config_pace_sec))
    delta = sec - config
    if abs(delta) < DRIFT_PACE_SEC_PER_KM:
        return None
    pct = delta / config * 100
    return DriftAlertDTO(
        sport=sport,
        metric="THRESHOLD_PACE",
        measured=sec,
        config_value=config,
        diff_pct=round(pct, 1),
        message=(
            f"HRVT2 pace = {sec} s/km (R²={r_squared:.2f}). "
            f"Current threshold {sport}: {config} s/km (Δ {delta:+d} s/km). "
            "Consider updating threshold pace."
        ),
    )


def _drift_alert_ftp(
    sport: str,
    hrvt2_power: float | None,
    r_squared: float | None,
    config_ftp: int,
) -> DriftAlertDTO | None:
    """FTP drift (Ride only): latest ramp-test pow-at-HRVT2 vs ``settings.ftp``.

    Coggan's FTP definition ≈ pow at LT2 ≈ pow at HRVT2 (DFA α1 = 0.50).
    Pushing pow-at-HRVT1 (older HRVT1→LTHR pattern) would under-shift
    cycling zones the same ~13% way the LTHR mapping bug did.
    """
    if hrvt2_power is None or r_squared is None or r_squared < DRIFT_R2_MEDIUM:
        return None
    delta = round(hrvt2_power) - config_ftp
    if abs(delta) < DRIFT_FTP_WATTS:
        return None
    pct = delta / config_ftp * 100
    return DriftAlertDTO(
        sport=sport,
        metric="FTP",
        measured=round(hrvt2_power),
        config_value=config_ftp,
        diff_pct=round(pct, 1),
        message=(
            f"HRVT2 power = {round(hrvt2_power)} W (R²={r_squared:.2f}). "
            f"Current FTP {sport}: {config_ftp} W (Δ {delta:+d} W). "
            "Consider updating FTP."
        ),
    )


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
    # Multi-select list of sports the athlete actually does, drawn from
    # {"swim", "ride", "run"}. NULL = not yet picked → webapp shows
    # SportsPicker gate; never `[]` (API enforces min_length=1). See
    # docs/USER_SPORTS_SPEC.md.
    sports: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    is_silent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # True once the user has actually opened a chat with the bot (sent /start
    # or any message). Login Widget auth creates a User row from a chat_id
    # without requiring the bot chat to exist — Telegram returns
    # ``400 chat not found`` if we try to ``sendMessage`` in that state.
    # See issue #266; gated in TelegramTool + /api/intervals/auth/init.
    bot_chat_initialized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    @dual
    def update_sports(cls, user_id: int, sports: list[str], *, session: Session) -> None:
        """Persist the athlete's sport selection (`swim`/`ride`/`run`).

        API-layer DTO already enforces enum membership, ≥1 entry, ≤3 entries,
        no duplicates, and canonical sort — this method trusts that contract
        and writes verbatim. Releases the SportsPicker gate on next webapp
        load (see USER_SPORTS_SPEC §6 gate flow).
        """
        session.execute(update(cls).where(cls.id == user_id).values(sports=sports))
        session.commit()

    @classmethod
    @dual
    def set_bot_chat_initialized(cls, chat_id: int | str, value: bool, *, session: Session) -> None:
        """Toggle ``bot_chat_initialized`` for the given chat.

        Set True from the /start handler and ``my_chat_member`` MEMBER
        transition — both prove the chat exists. Set False from the
        ``_post_with_retries`` 400-chat-not-found self-healing branch when
        Telegram tells us the chat is gone (user deleted it after /start).
        That re-arms the OAuth-init gate + frontend banner so the user can
        re-engage without a perma-Sentry-storm.
        """
        session.execute(update(cls).where(cls.chat_id == str(chat_id)).values(bot_chat_initialized=value))
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
        """Compare the latest ramp-test HRVT2 reading with athlete_settings to detect drift.

        Three metrics, each gated by an absolute |Δ| floor (per metric) AND
        ``R² ≥ DRIFT_R2_MEDIUM (0.70)`` on the most recent valid ramp test
        (LIMIT 1). Floor values live in ``data.db.dto`` and are mirrored by
        ``tasks.formatter._drift_button_status`` so UI and backend cannot
        diverge:

          - LTHR (Ride + Run) — HRVT2 HR vs ``settings.lthr``,
            gate ``DRIFT_LTHR_BPM = 3 bpm``.
          - THRESHOLD_PACE (Run only) — pace at HRVT2 (sec/km) vs
            ``settings.threshold_pace``, gate ``DRIFT_PACE_SEC_PER_KM = 5 s/km``.
          - FTP (Ride only) — pow at HRVT2 (watts) vs ``settings.ftp``,
            gate ``DRIFT_FTP_WATTS = 5 W``.

        Absolute units replaced a flat 5% relative gate (2026-05-08, see
        ``docs/RAMP_TEST_BIKE_SPEC.md §8``) — 5% of LTHR ~8 bpm was clinically
        too loose, while 5% of FTP ~10 W was tighter than power-meter
        repeatability.

        Pushing HRVT2 (anaerobic threshold = LTHR ≈ FTP) instead of HRVT1
        (aerobic threshold ≈ 75-85% of LTHR) realigns Intervals.icu's `lthr` /
        `ftp` / `threshold_pace` fields with their intended physiological
        meaning. Without this each set of zones would slide ~13% low.
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
            if not settings_row:
                continue

            row = session.execute(
                select(
                    ActivityHrv.hrvt2_hr,
                    ActivityHrv.hrvt2_pace,
                    ActivityHrv.hrvt2_power,
                    ActivityHrv.threshold_r_squared,
                )
                .join(Activity, Activity.id == ActivityHrv.activity_id)
                .where(
                    Activity.user_id == user_id,
                    Activity.type == sport_label,
                    ActivityHrv.processing_status == "processed",
                    ActivityHrv.hrvt2_hr.isnot(None),
                    ActivityHrv.hrv_quality.in_(["good", "moderate"]),
                )
                .order_by(Activity.start_date_local.desc())
                .limit(1)
            ).first()
            if not row:
                continue
            hrvt2_hr, hrvt2_pace, hrvt2_power, r_squared = row

            if settings_row.lthr:
                alert = _drift_alert_lthr(sport_label, hrvt2_hr, r_squared, settings_row.lthr)
                if alert:
                    alerts.append(alert)

            if sport_label == "Run" and settings_row.threshold_pace:
                alert = _drift_alert_pace(sport_label, hrvt2_pace, r_squared, settings_row.threshold_pace)
                if alert:
                    alerts.append(alert)

            if sport_label == "Ride" and settings_row.ftp:
                alert = _drift_alert_ftp(sport_label, hrvt2_power, r_squared, settings_row.ftp)
                if alert:
                    alerts.append(alert)

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
        # See `docs/MULTI_TENANT_SECURITY_SPEC.md` §T14 for rationale.
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
