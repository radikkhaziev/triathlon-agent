"""Tests for `data/personal_patterns.py`."""

from datetime import date, timedelta

from data.db import TrainingLog
from data.db.user import User
from data.personal_patterns import MIN_COMPLETE_ENTRIES, compute_personal_patterns


async def _create_complete_row(*, user_id: int, dt: str, category: str, hrv: str, zone: str, delta: float):
    """Create a training_log row with all the fields the aggregator filters on."""
    await TrainingLog.create(
        user_id=user_id,
        date=dt,
        sport="Ride",
        source="humango",
        pre_recovery_score=70.0,
        pre_recovery_category=category,
        pre_hrv_status=hrv,
        actual_activity_id="i" + dt.replace("-", ""),
        actual_sport="Ride",
        actual_duration_sec=3600,
        actual_max_zone_time=zone,
        compliance="followed_original",
        post_recovery_score=70.0 + delta,
        recovery_delta=delta,
    )


def _datestr(days_ago: int) -> str:
    return str(date.today() - timedelta(days=days_ago))


class TestComputePersonalPatterns:
    async def test_returns_counts_only_below_threshold(self, _test_db):
        for i in range(5):
            await _create_complete_row(user_id=1, dt=_datestr(i), category="good", hrv="green", zone="Z2", delta=2.0)

        result = await compute_personal_patterns(user_id=1)
        assert result["entries_complete"] == 5
        assert result["entries_total"] == 5
        assert "recovery_response_by_category" not in result

    async def test_returns_full_dict_at_threshold(self, _test_db):
        for i in range(MIN_COMPLETE_ENTRIES):
            await _create_complete_row(user_id=1, dt=_datestr(i), category="good", hrv="green", zone="Z2", delta=2.0)

        result = await compute_personal_patterns(user_id=1)
        assert result["entries_complete"] == MIN_COMPLETE_ENTRIES
        assert result["entries_total"] == MIN_COMPLETE_ENTRIES
        assert "recovery_response_by_category" in result

    async def test_aggregates_by_category(self, _test_db):
        # 15 good rows with +5 delta + 15 moderate with -10 delta = 30 complete
        for i in range(15):
            await _create_complete_row(user_id=1, dt=_datestr(i), category="good", hrv="green", zone="Z2", delta=5.0)
        for i in range(15, 30):
            await _create_complete_row(
                user_id=1, dt=_datestr(i), category="moderate", hrv="yellow", zone="Z3", delta=-10.0
            )

        result = await compute_personal_patterns(user_id=1)
        by_cat = result["recovery_response_by_category"]
        assert by_cat["good"]["count"] == 15
        assert by_cat["good"]["avg_delta"] == 5.0
        assert by_cat["moderate"]["count"] == 15
        assert by_cat["moderate"]["avg_delta"] == -10.0
        # HRV sensitivity should mirror that split
        hrv = result["hrv_sensitivity"]
        assert hrv["green"]["avg_delta"] == 5.0
        assert hrv["yellow"]["avg_delta"] == -10.0

    async def test_excludes_incomplete_rows(self, _test_db):
        # 30 complete rows
        for i in range(MIN_COMPLETE_ENTRIES):
            await _create_complete_row(user_id=1, dt=_datestr(i), category="good", hrv="green", zone="Z2", delta=1.0)
        # 5 rows missing post_recovery_score — counted in total but not complete
        for i in range(MIN_COMPLETE_ENTRIES, MIN_COMPLETE_ENTRIES + 5):
            await TrainingLog.create(
                user_id=1,
                date=_datestr(i),
                source="humango",
                pre_recovery_score=70.0,
                pre_recovery_category="good",
                compliance="followed_original",
                # post_recovery_score is None → row is "actual but no outcome"
            )

        result = await compute_personal_patterns(user_id=1)
        assert result["entries_complete"] == MIN_COMPLETE_ENTRIES
        assert result["entries_total"] == MIN_COMPLETE_ENTRIES + 5

    async def test_cross_tenant_isolation(self, _test_db):
        # Resolve session factories lazily — conftest's monkeypatch installs the
        # test-DB factory after module import, so a top-level import binds None.
        from data.db.common import _AsyncSessionLocal, _SyncSessionLocal

        async with _AsyncSessionLocal() as session:
            existing = await session.get(User, 2)
            if not existing:
                session.add(User(id=2, chat_id="tenant_2", role="athlete"))
                await session.commit()
        with _SyncSessionLocal() as session:
            existing = session.get(User, 2)
            if not existing:
                session.add(User(id=2, chat_id="tenant_2", role="athlete"))
                session.commit()

        # User 1 has 30 complete rows; user 2 has nothing.
        for i in range(MIN_COMPLETE_ENTRIES):
            await _create_complete_row(user_id=1, dt=_datestr(i), category="good", hrv="green", zone="Z2", delta=2.0)

        u1 = await compute_personal_patterns(user_id=1)
        u2 = await compute_personal_patterns(user_id=2)
        assert "recovery_response_by_category" in u1
        assert "recovery_response_by_category" not in u2
        assert u2["entries_complete"] == 0
