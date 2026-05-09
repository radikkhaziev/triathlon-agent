from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from data.db.common import Base, Session
from data.db.decorator import dual
from data.db.dto import AthleteGoalDTO, AthleteThresholdsDTO
from data.sport_map import RACE_SPORT_TYPES

# Sentinel for partial updates: distinguishes "field not provided" from
# "explicitly set to None" so a PATCH does not silently clear untouched
# columns. Used by AthleteGoal.update_local_fields.
_UNSET: object = object()

logger = logging.getLogger(__name__)


def _to_dto(goal: AthleteGoal) -> AthleteGoalDTO:
    """Convert AthleteGoal ORM row to its DTO. Single source of field mapping
    so adding a column requires touching one place, not every call site."""
    return AthleteGoalDTO(
        id=goal.id,
        event_name=goal.event_name,
        event_date=goal.event_date,
        sport_type=goal.sport_type,
        category=goal.category,
        ctl_target=goal.ctl_target,
        per_sport_targets=goal.per_sport_targets,
    )


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

    # Zone boundaries from Intervals.icu sport-settings (source of truth).
    # N threshold values → N+1 zones. Top zone opens upward (often with a sentinel
    # value like 999 as the last bound).
    # Units differ per kind — keep this contract in sync with consumers:
    #   hr_zones     — absolute bpm, ascending.    Example: [129, 136, 144, 152, 157, 161]
    #   power_zones  — **%FTP, ascending** (NOT absolute watts). Example: [55, 75, 90, 105, 120, 150, 999]
    #   pace_zones   — %threshold where 100.0 = threshold, ascending. Example: [77.5, 87.7, 94.3, 100.0, 103.4]
    hr_zones: Mapped[list | None] = mapped_column(JSON, nullable=True)
    hr_zone_names: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["Recovery", "Aerobic", ...]
    power_zones: Mapped[list | None] = mapped_column(JSON, nullable=True)
    power_zone_names: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["Active Recovery", ...]
    pace_zones: Mapped[list | None] = mapped_column(JSON, nullable=True)
    pace_zone_names: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["Zone 1", "Zone 2", ...]

    # WEBHOOK_DATA_CAPTURE Phase 1: MMP (Mean-Max Power) model from
    # SPORT_SETTINGS_UPDATED.mmp_model. Only Ride sport_settings carries this
    # block — Run/Swim rows leave these NULL.
    critical_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    w_prime: Mapped[float | None] = mapped_column(Float, nullable=True)  # anaerobic capacity (J)
    p_max: Mapped[float | None] = mapped_column(Float, nullable=True)  # peak power (W)
    mmp_ftp: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FTP from MMP curve (may differ from `ftp`)

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
        critical_power: float | None = None,
        w_prime: float | None = None,
        p_max: float | None = None,
        mmp_ftp: int | None = None,
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
            critical_power=critical_power,
            w_prime=w_prime,
            p_max=p_max,
            mmp_ftp=mmp_ftp,
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
                "critical_power": func.coalesce(excl.critical_power, cls.critical_power),
                "w_prime": func.coalesce(excl.w_prime, cls.w_prime),
                "p_max": func.coalesce(excl.p_max, cls.p_max),
                "mmp_ftp": func.coalesce(excl.mmp_ftp, cls.mmp_ftp),
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
            sports=user.sports if user else None,
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
    # Enum lives in `data.sport_map.RACE_SPORT_TYPES`. Set via
    # `resolve_race_sport_type` on writes from Intervals sync / `suggest_race`;
    # user-editable via Settings (#323 Strand B).
    sport_type: Mapped[str] = mapped_column(String(20), nullable=False)

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
        return _to_dto(goal)

    @classmethod
    @dual
    def get_all(cls, user_id: int, *, session: Session) -> list[AthleteGoal]:
        result = session.execute(select(cls).where(cls.user_id == user_id).order_by(cls.event_date.asc()))
        return list(result.scalars().all())

    @classmethod
    @dual
    def get_goals_for_settings(
        cls,
        user_id: int,
        today: date,
        *,
        session: Session,
    ) -> list[AthleteGoalDTO]:
        """Return ALL active future goals for the Settings list view (#323
        Strand C). Past races filtered out — they're not editable.

        Sort: ``event_date ASC`` so the nearest race is first in the UI. The
        category badge (RACE_A/B/C) is on each card so the athlete sees the
        season anchor regardless of position.
        """
        rows = (
            session.execute(
                select(cls)
                .where(
                    cls.user_id == user_id,
                    cls.is_active.is_(True),
                    cls.event_date >= today,
                )
                .order_by(cls.event_date.asc())
            )
            .scalars()
            .all()
        )
        return [_to_dto(g) for g in rows]

    @classmethod
    @dual
    def get_goals_for_prompt(
        cls,
        user_id: int,
        today: date,
        *,
        session: Session,
    ) -> list[AthleteGoalDTO]:
        """Return the goals to inject into Claude's system prompt (#323 Strand D).

        Returns 0/1/2 entries:
          * **0** — no active future races. Caller renders «Goals: не задана».
          * **1** — either there's only one upcoming race, OR the nearest race
            IS the RACE_A. Render as a single line.
          * **2** — RACE_A exists AND the nearest race is a different event
            (typically a tune-up B/C closer than the season A). First entry
            is RACE_A, second is the nearest. Render as a two-line block with
            «focus on RACE_A» hint.

        Past races filtered out — Claude doesn't need them in current-state
        context. ``get_races`` MCP tool covers race-history queries.
        """
        rows = (
            session.execute(
                select(cls)
                .where(
                    cls.user_id == user_id,
                    cls.is_active.is_(True),
                    cls.event_date >= today,
                )
                .order_by(cls.event_date.asc())
            )
            .scalars()
            .all()
        )
        if not rows:
            return []

        race_a = next((r for r in rows if r.category == "RACE_A"), None)
        nearest = rows[0]  # ordered by event_date ASC

        result: list[AthleteGoalDTO] = []
        if race_a is not None:
            result.append(_to_dto(race_a))
        if race_a is None or nearest.id != race_a.id:
            result.append(_to_dto(nearest))
        return result

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
        sport_type: str,
        session: Session,
    ) -> AthleteGoal:
        """Upsert goal from Intervals.icu event.

        Does NOT overwrite CTL targets or `sport_type` on existing rows. The
        user can fix the sport via Settings (#323 Strand B), and a re-sync
        should not stomp the user-edit. **Side effect of this trade-off:** if
        the user renames a race in Intervals.icu (Run → Triathlon), our local
        `sport_type` stays as the original — a WARN log fires so ops can spot
        the divergence. User has to fix via Settings if they care.
        """
        now = datetime.now(timezone.utc)
        existing = session.execute(
            select(cls).where(
                cls.user_id == user_id,
                cls.intervals_event_id == intervals_event_id,
            )
        ).scalar_one_or_none()

        if existing:
            if existing.sport_type != sport_type:
                logger.info(
                    "athlete_goal %d: Intervals reports sport_type=%r, keeping stored %r "
                    "(user may have edited via Settings)",
                    existing.id,
                    sport_type,
                    existing.sport_type,
                )
            existing.event_name = event_name
            existing.event_date = event_date
            existing.category = category
            existing.synced_at = now
            # Reactivate if this row was previously soft-deleted. Matching is
            # by intervals_event_id, so when the athlete re-creates a race
            # after delete_race_goal (same event_id can come back if Intervals
            # restore, or the sync picks up a fresh push) we bring the row
            # back into the active set.
            existing.is_active = True
            session.commit()
            return existing

        goal = cls(
            user_id=user_id,
            category=category,
            event_name=event_name,
            event_date=event_date,
            sport_type=sport_type,
            intervals_event_id=intervals_event_id,
            is_active=True,
            synced_at=now,
        )
        session.add(goal)
        session.commit()
        return goal

    @classmethod
    @dual
    def get_by_category(
        cls,
        user_id: int,
        category: str,
        *,
        include_past: bool = False,
        session: Session,
    ) -> AthleteGoal | None:
        """Return the nearest-upcoming active goal for (user_id, category) or None.

        Athletes routinely have multiple races per category in a season (e.g.
        two A-races — Ironman 70.3 in September + Oceanlava in October), so
        ``(user_id, category)`` is **not** a unique key. When ``suggest_race``
        asks "move my RACE_A", we default to the nearest future race —
        matching the most common interpretation of the bare command. Callers
        that need a specific race must disambiguate by ``intervals_event_id``
        or by passing the exact date.

        By default past races (``event_date < today``) are filtered out —
        they can't be "moved forward". Pass ``include_past=True`` to fall
        back to the most recent past active row when no upcoming exists;
        used by ``delete_race_goal`` so athletes can still remove a stale
        ``is_active=True`` row left behind by the sync actor after the
        race date has passed.
        """
        today = date.today()
        rows = (
            session.execute(
                select(cls)
                .where(
                    cls.user_id == user_id,
                    cls.category == category,
                    cls.is_active.is_(True),
                    cls.event_date >= today,
                )
                .order_by(cls.event_date.asc())
            )
            .scalars()
            .all()
        )
        if len(rows) > 1:
            logger.info(
                "AthleteGoal.get_by_category: %d upcoming %s rows for user_id=%d — picking nearest (id=%d date=%s)",
                len(rows),
                category,
                user_id,
                rows[0].id,
                rows[0].event_date,
            )
        if rows:
            return rows[0]

        if not include_past:
            return None

        past = session.execute(
            select(cls)
            .where(
                cls.user_id == user_id,
                cls.category == category,
                cls.is_active.is_(True),
                cls.event_date < today,
            )
            .order_by(cls.event_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        return past

    @classmethod
    @dual
    def deactivate_by_id(cls, goal_id: int, user_id: int, *, session: Session) -> AthleteGoal | None:
        """Soft-delete a goal by id, scoped to ``user_id`` as defense-in-depth.

        Callers already vet ownership (``delete_race_goal`` resolves the goal
        via ``get_by_category`` on the same tenant), but a leaked goal_id would
        otherwise cross-tenant write here.

        Preferred over :meth:`deactivate_by_category` when the exact target row
        is already known — with multiple races per category, picking "some
        active row" by id-desc can diverge from the row shown in the preview
        and actually deleted from Intervals.

        Returns the deactivated row or None if nothing matched.
        """
        goal = session.execute(
            select(cls).where(cls.id == goal_id, cls.user_id == user_id, cls.is_active.is_(True))
        ).scalar_one_or_none()
        if goal is None:
            return None
        goal.is_active = False
        session.commit()
        return goal

    @classmethod
    @dual
    def set_ctl_target(
        cls,
        goal_id: int,
        ctl_target: float | None,
        *,
        user_id: int,
        session: Session,
    ) -> None:
        """Overwrite ctl_target for a goal owned by ``user_id``.

        Scoped to ``user_id`` as defense-in-depth — callers already vet ownership
        (e.g. ``suggest_race`` just created the goal in the same tenant), but
        a goal_id leaked into the wrong code path would otherwise cross-tenant
        write here. Kept separate from ``upsert_from_intervals`` so the 30-min
        sync actor cannot stomp on user-entered CTL targets (see docstring on
        :meth:`upsert_from_intervals`).
        """
        session.execute(update(cls).where(cls.id == goal_id, cls.user_id == user_id).values(ctl_target=ctl_target))
        session.commit()

    @classmethod
    @dual
    def update_local_fields(
        cls,
        goal_id: int,
        *,
        user_id: int,
        ctl_target: float | None = _UNSET,
        per_sport_targets: dict | None = _UNSET,
        sport_type: str = _UNSET,
        session: Session,
    ) -> AthleteGoal | None:
        """Patch local-only overlay fields (``ctl_target``, ``per_sport_targets``,
        ``sport_type``).

        ``_UNSET`` sentinel distinguishes "field not provided" from "explicit
        clear" so the helper never silently stomps columns the caller didn't
        touch:
          * ``field=_UNSET`` — leave as-is.
          * ``ctl_target=None`` — clear to NULL.
          * ``per_sport_targets=None`` — clear the whole JSON blob.
          * ``per_sport_targets={"ride": 40}`` — **merge** into the existing
            blob, preserving other sport keys. PATCH-semantics: one sport at
            a time doesn't wipe the others.
          * ``sport_type="run"`` — overwrite. Caller is responsible for enum
            validation (router does this via Pydantic). Schema is NOT NULL so
            an explicit `None` is not a valid input.

        Returns the updated goal or ``None`` if not found or not owned by
        ``user_id``. Callers should 404 in the latter case (not 403) to avoid
        leaking existence of other users' goals — see
        ``docs/MULTI_TENANT_SECURITY_SPEC.md`` T1.
        """
        goal = session.execute(select(cls).where(cls.id == goal_id, cls.user_id == user_id)).scalar_one_or_none()
        if goal is None:
            return None

        if ctl_target is not _UNSET:
            goal.ctl_target = ctl_target
        if per_sport_targets is not _UNSET:
            if per_sport_targets is None:
                goal.per_sport_targets = None
            else:
                current = dict(goal.per_sport_targets or {})
                current.update(per_sport_targets)
                goal.per_sport_targets = current
        if sport_type is not _UNSET:
            # Defense-in-depth: API DTO already validates against the
            # Pydantic Literal, but a CLI / direct ORM call could bypass it.
            # The Settings dropdown invariant relies on a fixed value-set —
            # raise loudly rather than write garbage that breaks the UI.
            if sport_type not in RACE_SPORT_TYPES:
                raise ValueError(
                    f"sport_type={sport_type!r} not in RACE_SPORT_TYPES; " f"valid values: {sorted(RACE_SPORT_TYPES)}"
                )
            goal.sport_type = sport_type

        session.commit()
        return goal
