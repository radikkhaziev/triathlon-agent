"""Integration tests for WeeklyReport ORM (real Postgres via test_session).

Mock-based shape tests would be tautological for ``upsert`` — the SQL is a
single ``ON CONFLICT DO UPDATE`` and the only logic worth verifying is that
it actually round-trips against Postgres and respects the
``(user_id, week_start)`` unique constraint. So this is integration-only.
"""

import asyncio
from datetime import date

from sqlalchemy import select

from data.db import User, WeeklyReport, get_session, get_sync_session


async def _seed_user(*, user_id: int, chat_id: str | None = None) -> None:
    """Insert a User if not already present. user_id=1 is auto-seeded by the
    test_session fixture (see tests/conftest.py:96), so call this only for
    additional user ids."""
    async with get_session() as session:
        existing = await session.get(User, user_id)
        if existing is None:
            session.add(User(id=user_id, chat_id=chat_id or str(user_id), role="athlete"))
            await session.commit()


class TestUpsertIdempotency:
    """Same ``(user_id, week_start)`` MUST overwrite, not pile up rows.

    Drives the «cron coalesce + manual rerun» case where the same Sunday
    weekly is generated twice — the actor calls ``upsert`` and we expect
    one row at the end, not two.
    """

    async def test_first_call_inserts_row(self):
        week = date(2026, 5, 4)  # a Monday
        row = await WeeklyReport.upsert(
            user_id=1,
            week_start=week,
            content_md="## Week 1\nFirst version",
            model="claude-sonnet-4-6",
        )
        assert row.id is not None
        assert row.user_id == 1
        assert row.week_start == week
        assert row.content_md == "## Week 1\nFirst version"
        assert row.model == "claude-sonnet-4-6"
        assert row.generated_at is not None

    async def test_second_call_same_week_overwrites(self):
        week = date(2026, 5, 4)
        first = await WeeklyReport.upsert(
            user_id=1,
            week_start=week,
            content_md="v1",
            model="claude-sonnet-4-6",
        )
        second = await WeeklyReport.upsert(
            user_id=1,
            week_start=week,
            content_md="v2",
            model="claude-sonnet-4-7",
        )

        # Same row id (UPDATE, not INSERT)
        assert second.id == first.id
        assert second.content_md == "v2"
        assert second.model == "claude-sonnet-4-7"
        # generated_at bumped on overwrite so audit log can distinguish runs
        assert second.generated_at >= first.generated_at

        # Verify only ONE row exists for this (user, week)
        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        select(WeeklyReport).where(WeeklyReport.user_id == 1, WeeklyReport.week_start == week)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1

    async def test_different_weeks_create_separate_rows(self):
        """Adjacent weeks for the same user must NOT collide on the unique key."""
        week_a = date(2026, 4, 27)
        week_b = date(2026, 5, 4)
        await WeeklyReport.upsert(user_id=1, week_start=week_a, content_md="A", model="m")
        await WeeklyReport.upsert(user_id=1, week_start=week_b, content_md="B", model="m")

        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        select(WeeklyReport).where(WeeklyReport.user_id == 1).order_by(WeeklyReport.week_start)
                    )
                )
                .scalars()
                .all()
            )
        assert [r.content_md for r in rows] == ["A", "B"]


class TestListForUser:
    """``list_for_user`` is the source of truth for the history endpoint —
    must return user's own rows newest-first, support cursor pagination via
    strict ``<`` semantics, and never leak cross-tenant rows."""

    async def test_orders_newest_first(self):
        for week in (date(2026, 4, 27), date(2026, 5, 4), date(2026, 4, 20)):
            await WeeklyReport.upsert(user_id=1, week_start=week, content_md=f"w{week}", model="m")

        rows = await WeeklyReport.list_for_user(1, limit=10)
        assert [r.week_start for r in rows] == [date(2026, 5, 4), date(2026, 4, 27), date(2026, 4, 20)]

    async def test_limit_respected(self):
        for week in (date(2026, 4, 13), date(2026, 4, 20), date(2026, 4, 27), date(2026, 5, 4)):
            await WeeklyReport.upsert(user_id=1, week_start=week, content_md="x", model="m")

        rows = await WeeklyReport.list_for_user(1, limit=2)
        assert [r.week_start for r in rows] == [date(2026, 5, 4), date(2026, 4, 27)]

    async def test_before_cursor_strict_less_than(self):
        """Page boundary: ``before`` is excluded, not included, so the cursor
        row never duplicates across pages. Catches a regression where a
        future ``<=`` change would echo the boundary row."""
        weeks = [date(2026, 4, 13), date(2026, 4, 20), date(2026, 4, 27), date(2026, 5, 4)]
        for week in weeks:
            await WeeklyReport.upsert(user_id=1, week_start=week, content_md="x", model="m")

        page2 = await WeeklyReport.list_for_user(1, limit=10, before=date(2026, 4, 27))
        assert [r.week_start for r in page2] == [date(2026, 4, 20), date(2026, 4, 13)]

    async def test_excludes_other_users(self):
        """Defence-in-depth: even with a leaked user_id arg, a different
        athlete's rows never show up. ORM filter is ``WHERE user_id = ?``,
        not application-layer post-filtering."""
        await _seed_user(user_id=2)
        await WeeklyReport.upsert(user_id=1, week_start=date(2026, 5, 4), content_md="user 1", model="m")
        await WeeklyReport.upsert(user_id=2, week_start=date(2026, 5, 4), content_md="user 2", model="m")

        rows = await WeeklyReport.list_for_user(1, limit=10)
        assert len(rows) == 1
        assert rows[0].content_md == "user 1"


class TestGetOne:
    async def test_returns_row_when_owned(self):
        await WeeklyReport.upsert(user_id=1, week_start=date(2026, 5, 4), content_md="content", model="m")
        row = await WeeklyReport.get_one(1, date(2026, 5, 4))
        assert row is not None
        assert row.content_md == "content"

    async def test_returns_none_for_other_users_row(self):
        """Cross-tenant guard — same date, different user_id, must miss.
        Without ``WHERE user_id = ?`` a leaked ISO date in the URL path
        would surface another tenant's report."""
        await _seed_user(user_id=2)
        await WeeklyReport.upsert(user_id=2, week_start=date(2026, 5, 4), content_md="theirs", model="m")
        assert await WeeklyReport.get_one(1, date(2026, 5, 4)) is None

    async def test_returns_none_when_missing(self):
        assert await WeeklyReport.get_one(1, date(2026, 5, 4)) is None


class TestSyncPath:
    """The Sunday cron actor calls ``upsert`` from a Dramatiq worker (sync
    context, no event loop). ``@dual`` routes that to the sync session
    branch, which is structurally different from the async one tested
    elsewhere in this file. Without this test the sync branch is
    dead-untested — a future refactor could break it silently.
    """

    async def test_sync_upsert_round_trips(self):
        """Run upsert from a worker thread (no event loop in that thread,
        so ``@dual`` resolves to the sync branch). Round-trip the row via
        the sync session to verify it landed."""

        def _from_sync_thread() -> int:
            row = WeeklyReport.upsert(
                user_id=1,
                week_start=date(2026, 5, 4),
                content_md="sync-path",
                model="m",
            )
            assert row.content_md == "sync-path"
            with get_sync_session() as session:
                fetched = session.execute(
                    select(WeeklyReport).where(
                        WeeklyReport.user_id == 1,
                        WeeklyReport.week_start == date(2026, 5, 4),
                    )
                ).scalar_one()
            assert fetched.content_md == "sync-path"
            return fetched.id

        row_id = await asyncio.to_thread(_from_sync_thread)
        # Async-side read sees the same row (commit visibility across paths).
        async with get_session() as session:
            via_async = await session.get(WeeklyReport, row_id)
        assert via_async is not None
        assert via_async.content_md == "sync-path"


class TestCrossTenantIsolation:
    """Two users, same ``week_start`` — both rows coexist (unique is on the
    pair, not on week alone). Defends against a future regression that would
    e.g. drop user_id from the unique constraint."""

    async def test_same_week_different_users_both_persist(self):
        await _seed_user(user_id=2)
        week = date(2026, 5, 4)
        await WeeklyReport.upsert(user_id=1, week_start=week, content_md="user 1 text", model="m")
        await WeeklyReport.upsert(user_id=2, week_start=week, content_md="user 2 text", model="m")

        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        select(WeeklyReport).where(WeeklyReport.week_start == week).order_by(WeeklyReport.user_id)
                    )
                )
                .scalars()
                .all()
            )
        assert [r.user_id for r in rows] == [1, 2]
        assert [r.content_md for r in rows] == ["user 1 text", "user 2 text"]
