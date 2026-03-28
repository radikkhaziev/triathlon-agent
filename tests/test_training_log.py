"""Tests for ATP Phase 3: Training Log."""

from datetime import date

from data.database import (
    create_training_log,
    get_training_log_for_date,
    get_training_log_range,
    get_training_log_unfilled_actual,
    get_training_log_unfilled_post,
    update_training_log,
)


class TestTrainingLogCRUD:
    async def test_create_and_get(self, _test_db):
        row = await create_training_log(
            date="2026-04-01",
            sport="Ride",
            source="humango",
            original_name="Z2 Endurance",
            original_duration_sec=3600,
            pre_recovery_score=78.0,
            pre_recovery_category="good",
            pre_hrv_status="green",
            pre_tsb=5.0,
        )
        assert row.id is not None
        assert row.source == "humango"
        assert row.pre_recovery_score == 78.0

        fetched = await get_training_log_for_date("2026-04-01")
        assert len(fetched) >= 1
        assert any(r.original_name == "Z2 Endurance" for r in fetched)

    async def test_get_range(self, _test_db):
        await create_training_log(
            date=str(date.today()),
            source="none",
            pre_recovery_score=60.0,
            pre_recovery_category="moderate",
        )

        rows = await get_training_log_range(days_back=7)
        assert len(rows) >= 1

    async def test_unfilled_actual(self, _test_db):
        row = await create_training_log(
            date="2026-03-20",
            sport="Run",
            source="ai",
            original_name="Easy Run",
            pre_recovery_score=65.0,
            pre_recovery_category="moderate",
        )

        unfilled = await get_training_log_unfilled_actual()
        assert any(r.id == row.id for r in unfilled)

    async def test_update_actual(self, _test_db):
        row = await create_training_log(
            date="2026-03-21",
            sport="Ride",
            source="humango",
            pre_recovery_score=80.0,
            pre_recovery_category="good",
        )

        updated = await update_training_log(
            row.id,
            actual_activity_id="i12345",
            actual_sport="Ride",
            actual_duration_sec=3600,
            actual_avg_hr=142.0,
            actual_tss=65.0,
            compliance="followed_original",
        )
        assert updated.compliance == "followed_original"
        assert updated.actual_activity_id == "i12345"

    async def test_unfilled_post(self, _test_db):
        row = await create_training_log(
            date="2026-03-22",
            sport="Run",
            source="adapted",
            pre_recovery_score=55.0,
            pre_recovery_category="moderate",
        )
        await update_training_log(row.id, compliance="followed_adapted")

        unfilled = await get_training_log_unfilled_post()
        assert any(r.id == row.id for r in unfilled)

    async def test_update_post(self, _test_db):
        row = await create_training_log(
            date="2026-03-23",
            sport="Swim",
            source="humango",
            pre_recovery_score=70.0,
            pre_recovery_category="good",
        )
        await update_training_log(row.id, compliance="followed_original")

        updated = await update_training_log(
            row.id,
            post_recovery_score=75.0,
            post_hrv_delta_pct=3.2,
            post_sleep_score=82.0,
            recovery_delta=5.0,
        )
        assert updated.post_recovery_score == 75.0
        assert updated.recovery_delta == 5.0


class TestComplianceDetection:
    def test_followed_original(self):
        from bot.scheduler import _detect_compliance
        from types import SimpleNamespace

        log = SimpleNamespace(
            source="humango",
            original_duration_sec=3600,
            adapted_duration_sec=None,
        )
        activity = SimpleNamespace(
            moving_time=3400,
            icu_training_load=65,
            average_hr=142,
        )
        assert _detect_compliance(log, activity) == "followed_original"

    def test_followed_adapted(self):
        from bot.scheduler import _detect_compliance
        from types import SimpleNamespace

        log = SimpleNamespace(
            source="adapted",
            original_duration_sec=3600,
            adapted_duration_sec=2400,
        )
        activity = SimpleNamespace(
            moving_time=2500,
            icu_training_load=45,
            average_hr=135,
        )
        assert _detect_compliance(log, activity) == "followed_adapted"

    def test_followed_ai(self):
        from bot.scheduler import _detect_compliance
        from types import SimpleNamespace

        log = SimpleNamespace(
            source="ai",
            original_duration_sec=2700,
            adapted_duration_sec=None,
        )
        activity = SimpleNamespace(
            moving_time=2800,
            icu_training_load=50,
            average_hr=138,
        )
        assert _detect_compliance(log, activity) == "followed_ai"

    def test_modified(self):
        from bot.scheduler import _detect_compliance
        from types import SimpleNamespace

        log = SimpleNamespace(
            source="humango",
            original_duration_sec=3600,
            adapted_duration_sec=None,
        )
        activity = SimpleNamespace(
            moving_time=1200,  # way shorter
            icu_training_load=20,
            average_hr=120,
        )
        assert _detect_compliance(log, activity) == "modified"
