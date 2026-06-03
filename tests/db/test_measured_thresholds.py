"""Tests for ActivityHrv.get_latest_measured — the "our measured thresholds" feed.

Powers the Settings "measured vs auto-synced" card. Returns the latest
``processed`` good/moderate-quality ramp test per sport (Run, Ride) with a
detected HRVT2, carrying HRVT1/HRVT2 HR/pace/power + per-threshold confidence.
Filter mirrors ``User.detect_threshold_drift`` so card and drift alert agree.
"""

from datetime import date

from data.db import ActivityHrv


async def _add_hrv_sample(
    *,
    user_id: int,
    sport: str,
    activity_id: str,
    dt: str,
    hrvt2_hr: float = 165.0,
    quality: str = "good",
    hrvt2_pace: str | None = None,
    hrvt2_power: float | None = None,
    hrvt1_confidence: str | None = "high",
    hrvt2_confidence: str | None = "high",
    r_squared: float | None = 0.90,
    processing_status: str = "processed",
) -> None:
    from data.db import Activity
    from data.db.common import _AsyncSessionLocal

    async with _AsyncSessionLocal() as session:
        session.add(
            Activity(
                id=activity_id,
                user_id=user_id,
                start_date_local=dt,
                type=sport,
                moving_time=3000,
            )
        )
        session.add(
            ActivityHrv(
                activity_id=activity_id,
                activity_type=sport,
                processing_status=processing_status,
                hrv_quality=quality,
                hrvt1_hr=hrvt2_hr - 15,
                hrvt2_hr=hrvt2_hr,
                hrvt1_pace=None,
                hrvt2_pace=hrvt2_pace,
                hrvt1_power=(hrvt2_power - 30) if hrvt2_power is not None else None,
                hrvt2_power=hrvt2_power,
                hrvt1_confidence=hrvt1_confidence,
                hrvt2_confidence=hrvt2_confidence,
                threshold_r_squared=r_squared,
            )
        )
        await session.commit()


class TestGetLatestMeasured:
    async def test_no_data_returns_empty(self, _test_db):
        assert await ActivityHrv.get_latest_measured(1) == []

    async def test_returns_one_per_sport(self, _test_db):
        await _add_hrv_sample(user_id=1, sport="Run", activity_id="r1", dt=str(date.today()), hrvt2_hr=172.0)
        await _add_hrv_sample(
            user_id=1, sport="Ride", activity_id="b1", dt=str(date.today()), hrvt2_hr=160.0, hrvt2_power=240.0
        )
        out = await ActivityHrv.get_latest_measured(1)
        by_sport = {m.sport: m for m in out}
        assert set(by_sport) == {"Run", "Ride"}
        assert by_sport["Run"].hrvt2_hr == 172.0
        assert by_sport["Ride"].hrvt2_power == 240.0

    async def test_picks_latest_when_multiple(self, _test_db):
        await _add_hrv_sample(user_id=1, sport="Run", activity_id="r_old", dt="2026-04-01", hrvt2_hr=160.0)
        await _add_hrv_sample(user_id=1, sport="Run", activity_id="r_new", dt=str(date.today()), hrvt2_hr=174.0)
        out = await ActivityHrv.get_latest_measured(1)
        assert len(out) == 1
        assert out[0].hrvt2_hr == 174.0
        assert out[0].activity_id == "r_new"

    async def test_excludes_poor_quality(self, _test_db):
        await _add_hrv_sample(
            user_id=1, sport="Run", activity_id="r1", dt=str(date.today()), hrvt2_hr=172.0, quality="poor"
        )
        assert await ActivityHrv.get_latest_measured(1) == []

    async def test_excludes_unprocessed(self, _test_db):
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="r1",
            dt=str(date.today()),
            hrvt2_hr=172.0,
            processing_status="error",
        )
        assert await ActivityHrv.get_latest_measured(1) == []

    async def test_carries_confidence_and_date(self, _test_db):
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="r1",
            dt=str(date.today()),
            hrvt2_hr=172.0,
            hrvt2_confidence="medium",
        )
        out = await ActivityHrv.get_latest_measured(1)
        assert out[0].hrvt2_hr == 172.0
        assert out[0].hrvt2_confidence == "medium"
        assert out[0].measured_at == str(date.today())

    async def test_scoped_by_user(self, _test_db):
        from data.db import User
        from data.db.common import _AsyncSessionLocal

        async with _AsyncSessionLocal() as session:
            session.add(User(id=2, chat_id="other_user", role="viewer"))
            await session.commit()
        await _add_hrv_sample(user_id=2, sport="Run", activity_id="r1", dt=str(date.today()), hrvt2_hr=172.0)
        # user 1 has no samples → empty, even though user 2 does
        assert await ActivityHrv.get_latest_measured(1) == []
