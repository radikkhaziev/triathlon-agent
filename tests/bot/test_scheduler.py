"""Tests for bot/scheduler.py — integration test requiring real DB + Redis + API.

scheduler_wellness_job is decorated with @with_athletes and dispatches
dramatiq actors, so it needs a running Redis broker and real DB with active athletes.
Skip in normal test runs.
"""

import pytest


@pytest.mark.real_db
@pytest.mark.skip(reason="Integration: requires real DB + Redis + Intervals.icu API")
class TestSyncWellnessJobManual:

    @pytest.fixture(autouse=True)
    def test_session(self):
        """Override conftest — use real DB, no patching."""
        yield

    @pytest.mark.asyncio
    async def test_sync_wellness_today(self):
        from bot.scheduler import scheduler_wellness_job

        await scheduler_wellness_job()
