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
        rows = session.execute(select(cls).where(cls.status == "running", cls.last_step_at < cutoff)).scalars().all()
        return list(rows)

    # --- Derived helpers ---

    def progress_pct(self) -> float:
        span = (self.newest_dt - self.oldest_dt).days
        if span <= 0:
            return 100.0
        done = (self.cursor_dt - self.oldest_dt).days
        return max(0.0, min(100.0, done / span * 100.0))

    def is_empty_import(self) -> bool:
        return self.status == "completed" and self.last_error == "EMPTY_INTERVALS"
