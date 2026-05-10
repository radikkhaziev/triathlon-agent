"""Weekly training summary persistence.

Previously the weekly summary lived only in Telegram chat history. Telegram's
4096-char visible-text limit + occasional silent-drop on long messages made
the chat an unreliable archive. ``WeeklyReport`` is the durable copy — the
``actor_compose_weekly_report`` actor upserts here before pushing the chat
preview, and the webapp renders the full markdown from this table.

See migration ``bb8c9d0e1f2a_add_weekly_reports.py`` for schema rationale.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from data.db.common import Base, Session
from data.db.decorator import dual


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"
    __table_args__ = (UniqueConstraint("user_id", "week_start", name="uq_weekly_reports_user_week"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    # Monday of the summarised week (``today - today.weekday()`` in
    # settings.TIMEZONE). Pairs with user_id as the idempotency anchor.
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # --- CRUD ---

    @classmethod
    @dual
    def upsert(
        cls,
        *,
        user_id: int,
        week_start: date,
        content_md: str,
        model: str,
        session: Session,
    ) -> WeeklyReport:
        """Insert-or-overwrite the report for ``(user_id, week_start)``.

        Idempotent — a manual rerun, cron coalesce, or watchdog re-kick on the
        same week replaces the prior content instead of piling rows. We use
        Postgres ``ON CONFLICT`` rather than SELECT-then-update to keep the
        whole thing atomic; two concurrent actors for the same user+week
        (theoretically possible if scheduler dispatched twice and Dramatiq
        didn't dedupe) would otherwise race on read-modify-write.

        Bumps ``generated_at`` on overwrite so callers can distinguish the
        latest generation in the audit log.
        """
        # Materialise once so INSERT and ON CONFLICT both stamp the same
        # value — drift between the two would be invisible (only one branch
        # fires per call) but produce a confusing audit trail in the rare
        # case a query reads both sides.
        now_utc = datetime.now(timezone.utc)
        stmt = (
            pg_insert(cls)
            .values(
                user_id=user_id,
                week_start=week_start,
                content_md=content_md,
                model=model,
                generated_at=now_utc,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "week_start"],
                set_={
                    "content_md": content_md,
                    "model": model,
                    "generated_at": now_utc,
                },
            )
            .returning(cls)
        )
        # ``RETURNING cls`` populates every column pre-commit, and the project
        # sets ``expire_on_commit=False`` (data/db/common.py), so attributes
        # remain readable on the detached row after the session closes. No
        # ``session.refresh(row)`` needed — it would only add a SELECT
        # round-trip. Add one if a future revision introduces lazy-loaded
        # relationships on this model.
        row = session.execute(stmt).scalar_one()
        session.commit()
        return row

    @classmethod
    @dual
    def list_for_user(
        cls,
        user_id: int,
        *,
        limit: int,
        before: date | None = None,
        session: Session,
    ) -> list[WeeklyReport]:
        """Most-recent-first slice of an athlete's reports for the history view.

        Cursor pagination: ``before`` is the ``week_start`` of the oldest row
        the client already has; we return rows strictly older than it. ``None``
        means "first page". Strict ``<`` (not ``<=``) so the cursor itself
        isn't returned twice across page boundaries — same convention as
        Stripe / GitHub list APIs.

        Always scoped to ``user_id`` — the API endpoint resolves it from auth,
        never the URL, so a leaked URL can't surface another tenant's history.
        """
        stmt = select(cls).where(cls.user_id == user_id)
        if before is not None:
            stmt = stmt.where(cls.week_start < before)
        stmt = stmt.order_by(cls.week_start.desc()).limit(limit)
        return list(session.execute(stmt).scalars().all())

    @classmethod
    @dual
    def get_one(
        cls,
        user_id: int,
        week_start: date,
        *,
        session: Session,
    ) -> WeeklyReport | None:
        """Fetch a single report by (user_id, week_start) or None.

        ``user_id`` is the auth-resolved owner — week_start comes from the URL
        path, so the user_id filter is the cross-tenant guard. Without it a
        leaked iso-date in the path would surface someone else's report.
        """
        return session.execute(
            select(cls).where(cls.user_id == user_id, cls.week_start == week_start)
        ).scalar_one_or_none()
