"""Tests for bot/scheduler.py — integration test requiring real DB + Redis + API.

scheduler_wellness_and_reports_job is decorated with @with_athletes and dispatches
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
        from bot.scheduler import scheduler_wellness

        await scheduler_wellness()


class TestCreateSchedulerJobConfig:
    """User-facing report cron jobs MUST set ``misfire_grace_time`` and
    ``coalesce`` — APScheduler's default ``misfire_grace_time=1`` silently
    drops any tick firing >1s late, which is how the 2026-05-03 weekly
    report was lost during a deploy. Without explicit values these jobs
    regress to silent-skip on any process restart at the cron tick. See
    ``CLAUDE.md`` "Intervals.icu API" bullet for the canonical values.
    """

    @pytest.mark.asyncio
    async def test_weekly_report_has_misfire_grace_and_coalesce(self):
        from bot.scheduler import create_scheduler

        scheduler = await create_scheduler()
        job = scheduler.get_job("scheduler_weekly_report_job")
        assert job is not None
        assert job.misfire_grace_time == 7200
        assert job.coalesce is True

    @pytest.mark.asyncio
    async def test_evening_report_has_misfire_grace_and_coalesce(self):
        from bot.scheduler import create_scheduler

        scheduler = await create_scheduler()
        job = scheduler.get_job("scheduler_evening_report_job")
        assert job is not None
        assert job.misfire_grace_time == 3600
        assert job.coalesce is True

    @pytest.mark.asyncio
    async def test_progression_model_has_misfire_grace_and_coalesce(self):
        from bot.scheduler import create_scheduler

        scheduler = await create_scheduler()
        job = scheduler.get_job("scheduler_progression_model_job")
        assert job is not None
        assert job.misfire_grace_time == 7200
        assert job.coalesce is True

    @pytest.mark.asyncio
    async def test_high_frequency_jobs_keep_default_misfire(self):
        """Wellness / activities cron run every 10–30 min, so a missed tick
        is recovered by the next one — they do NOT need a grace window.
        Forgetting that and globally bumping ``misfire_grace_time`` would
        let stale ticks leak into the next window. APScheduler stores the
        value only when explicitly set; the attribute is absent otherwise
        and the scheduler-level default (1s) applies."""
        from bot.scheduler import create_scheduler

        scheduler = await create_scheduler()
        wellness = scheduler.get_job("scheduler_wellness_and_reports_job")
        activities = scheduler.get_job("scheduler_activities_job")
        assert getattr(wellness, "misfire_grace_time", None) in (None, 1)
        assert getattr(activities, "misfire_grace_time", None) in (None, 1)
