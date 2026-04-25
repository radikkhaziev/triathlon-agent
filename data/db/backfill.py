from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from .common import Base
from .decorator import dual

logger = logging.getLogger(__name__)


class UserBackfillState(Base):
    """Per-user cursor for the OAuth bootstrap backfill.

    One row per user. The chunk-recursive ``actor_bootstrap_step`` reads
    ``cursor_dt`` on entry, processes a window, and advances the cursor in a
    single atomic UPDATE. See ``docs/OAUTH_BOOTSTRAP_SYNC_SPEC.md``.
    """

    __tablename__ = "user_backfill_state"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    period_days: Mapped[int] = mapped_column(Integer, nullable=False)
    oldest_dt: Mapped[date] = mapped_column(Date, nullable=False)
    newest_dt: Mapped[date] = mapped_column(Date, nullable=False)
    cursor_dt: Mapped[date] = mapped_column(Date, nullable=False)
    chunks_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_step_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Timestamp of the post-onboarding "hey, you can chat" reminder (issue #258).
    # NULL = not yet sent. Reset to NULL on ``start()`` --force so a re-run can
    # in principle nudge again, but the cron filter ``status='completed'``
    # prevents that until the new bootstrap actually finishes.
    hey_message: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- CRUD ---

    @classmethod
    @dual
    def get(cls, user_id: int, *, session: Session) -> UserBackfillState | None:
        return session.execute(select(cls).where(cls.user_id == user_id)).scalar_one_or_none()

    @classmethod
    @dual
    def start(
        cls,
        user_id: int,
        *,
        period_days: int,
        oldest_dt: date,
        newest_dt: date,
        session: Session,
    ) -> UserBackfillState:
        """Create or reset the backfill row so the next ``actor_bootstrap_step``
        starts from ``oldest_dt``.

        Uses PostgreSQL ``INSERT ... ON CONFLICT`` so a previously-finished row
        is overwritten atomically — ``status='running'`` and ``cursor_dt=oldest``
        always match after this returns.

        ``last_step_at`` is seeded to ``now()`` here (not NULL) so that the
        Phase 2 watchdog's ``list_stuck(cls.last_step_at < cutoff)`` filter can
        still detect this row if the first chunk crashes before the initial
        ``advance_cursor`` fires — otherwise a row stuck with NULL ``last_step_at``
        would be invisible to the watchdog forever.
        """
        stmt = (
            insert(cls)
            .values(
                user_id=user_id,
                status="running",
                period_days=period_days,
                oldest_dt=oldest_dt,
                newest_dt=newest_dt,
                cursor_dt=oldest_dt,
                chunks_done=0,
                started_at=func.now(),
                finished_at=None,
                last_step_at=func.now(),
                last_error=None,
                hey_message=None,
            )
            .on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "status": "running",
                    "period_days": period_days,
                    "oldest_dt": oldest_dt,
                    "newest_dt": newest_dt,
                    "cursor_dt": oldest_dt,
                    "chunks_done": 0,
                    "started_at": func.now(),
                    "finished_at": None,
                    "last_step_at": func.now(),
                    "last_error": None,
                    "hey_message": None,
                },
            )
        )
        session.execute(stmt)
        session.commit()
        return session.execute(select(cls).where(cls.user_id == user_id)).scalar_one()

    @classmethod
    @dual
    def advance_cursor(cls, user_id: int, cursor_dt: date, *, session: Session) -> None:
        """Atomic cursor advance + chunks_done bump + last_step_at touch.

        Also clears ``last_error`` — during ``status='running'`` the only thing
        that writes to it is the watchdog's ``watchdog_kick_N`` counter, and
        a successful advance means the chain recovered and we shouldn't treat
        future stuck events as continuing the same kick streak.

        Guarded by ``status='running'`` so a concurrent mark_failed/mark_finished
        cannot be silently undone.
        """
        session.execute(
            update(cls)
            .where(cls.user_id == user_id, cls.status == "running")
            .values(
                cursor_dt=cursor_dt,
                chunks_done=cls.chunks_done + 1,
                last_step_at=func.now(),
                last_error=None,
            )
        )
        session.commit()

    @classmethod
    @dual
    def mark_finished(
        cls,
        user_id: int,
        status: str,
        last_error: str | None = None,
        *,
        session: Session,
    ) -> None:
        session.execute(
            update(cls)
            .where(cls.user_id == user_id, cls.status == "running")
            .values(status=status, finished_at=func.now(), last_error=last_error)
        )
        session.commit()

    @classmethod
    @dual
    def bump_watchdog_kick(cls, user_id: int, kick_number: int, *, session: Session) -> None:
        """Record that the watchdog re-dispatched a stuck running row.

        We reuse ``last_error`` as a counter (``watchdog_kick_N``) instead of
        adding a new column — keeps Phase 2 schemaless. The caller decides
        when ``N`` exceeds the retry budget and transitions to ``mark_failed``
        with the ``watchdog_exhausted`` sentinel.

        Guarded by ``status='running'`` so a concurrent ``mark_finished`` /
        ``mark_failed`` cannot be silently reopened.
        """
        session.execute(
            update(cls)
            .where(cls.user_id == user_id, cls.status == "running")
            .values(last_error=f"watchdog_kick_{kick_number}")
        )
        session.commit()

    @classmethod
    @dual
    def mark_failed(cls, user_id: int, error: str, *, session: Session) -> None:
        """Mark the row as failed. ``error`` is truncated to 500 chars and shows
        through the ``/api/auth/backfill-status`` API.

        Security — **never pass raw ``str(e)`` from an HTTP client here.**
        httpx / requests exception strings can embed request URLs with query
        params and, in future clients, Authorization headers. Intervals.icu
        today is header-auth'd and URL-clean, but this is a hot path for
        leaking if a future caller is careless. Sanitize to a stable sentinel
        or a class-name description before passing in.
        """
        session.execute(
            update(cls)
            .where(cls.user_id == user_id, cls.status == "running")
            .values(
                status="failed",
                finished_at=func.now(),
                last_error=(error or "")[:500],
            )
        )
        session.commit()

    @classmethod
    @dual
    def list_stuck(cls, threshold_min: int, *, session: Session) -> list[UserBackfillState]:
        """Rows in ``status='running'`` whose last_step_at is older than
        ``threshold_min`` minutes — i.e. the chain broke after exhausting
        Dramatiq retries. Used by the watchdog cron (Phase 2)."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_min)
        rows = (
            session.execute(
                select(cls).where(
                    cls.status == "running",
                    cls.last_step_at < cutoff,
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    @classmethod
    @dual
    def list_eligible_for_hey(
        cls,
        *,
        min_age: timedelta,
        max_age: timedelta,
        session: Session,
    ) -> list[int]:
        """User IDs that finished bootstrap inside the ``[min_age, max_age]``
        window and haven't been nudged yet (issue #258).

        Filters live entirely on ``user_backfill_state`` — by construction a
        row only exists for users who started OAuth onboarding, so we don't
        re-check ``User.is_active`` / ``role`` here. The ``hey_message IS NULL``
        guard is the idempotency boundary together with ``mark_hey_sent``.

        Window is keyed on ``finished_at``, not ``started_at`` — a slow
        bootstrap (>24h: heavy retries, ``EMPTY_INTERVALS`` pause-then-resume)
        would otherwise miss the window entirely. ``status='completed'``
        guarantees ``finished_at IS NOT NULL``.

        Window comparison runs in SQL (``func.now()``) — ``finished_at`` and
        ``hey_message`` are stamped DB-side, so aligning the read with DB
        time keeps eligibility consistent if app and DB clocks drift.
        """
        rows = (
            session.execute(
                select(cls.user_id).where(
                    cls.status == "completed",
                    cls.hey_message.is_(None),
                    cls.finished_at >= func.now() - max_age,
                    cls.finished_at <= func.now() - min_age,
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    @classmethod
    @dual
    def mark_hey_sent(cls, user_id: int, *, session: Session) -> bool:
        """Atomically stamp ``hey_message=now()``; return ``True`` only if THIS
        call won the race. The actor uses the return value to decide whether
        to actually send the Telegram message — see issue #258 follow-up.

        Two writers can both pick up the same user_id between SELECT (in the
        cron) and dispatch. The ``hey_message IS NULL`` guard makes the UPDATE
        idempotent (no double-write), but only ``RETURNING`` lets the actor
        detect that another instance already committed — without it, both
        actors would proceed to ``tg.send_message`` and the user would get
        the nudge twice.
        """
        result = session.execute(
            update(cls)
            .where(cls.user_id == user_id, cls.hey_message.is_(None))
            .values(hey_message=func.now())
            .returning(cls.user_id)
        )
        row = result.scalar_one_or_none()
        session.commit()
        return row is not None

    # --- Derived helpers ---

    def progress_pct(self) -> float:
        span = (self.newest_dt - self.oldest_dt).days
        if span <= 0:
            return 100.0
        done = (self.cursor_dt - self.oldest_dt).days
        return max(0.0, min(100.0, done / span * 100.0))

    def is_empty_import(self) -> bool:
        return self.status == "completed" and self.last_error == "EMPTY_INTERVALS"
