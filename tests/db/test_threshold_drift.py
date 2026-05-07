"""Tests for User.detect_threshold_drift — including first-sample bootstrap path."""

from datetime import date

import pytest

from data.db import Activity, ActivityHrv, AthleteSettings, User
from data.db.user import parse_pace_to_sec


class TestParsePaceToSec:
    """Pure-function tests, no DB. Belongs here because the parser feeds drift detection."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("4:30", 270),
            ("0:30", 30),
            ("10:05", 605),
            (" 4:30 ", 270),  # whitespace tolerated
        ],
    )
    def test_parses_valid_pace(self, raw, expected):
        assert parse_pace_to_sec(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "abc",
            "4-30",  # wrong separator
            "4:60",  # seconds out of range
            "4:99",  # seconds out of range
            "-4:30",  # negative minutes
            "4:-5",  # negative seconds
            ":30",  # missing minutes
            "4:",  # missing seconds
            123,  # not a string
        ],
    )
    def test_rejects_invalid_input(self, raw):
        assert parse_pace_to_sec(raw) is None


async def _seed_drift_setup(*, user_id: int, sport: str, lthr: int) -> None:
    """Make sure athlete_settings has an LTHR baseline for the sport."""
    await AthleteSettings.upsert(user_id=user_id, sport=sport, lthr=lthr)


async def _add_hrv_sample(
    *,
    user_id: int,
    sport: str,
    activity_id: str,
    dt: str,
    hrvt1_hr: float,
    r_squared: float | None = 0.90,
    quality: str = "good",
    hrvt1_pace: str | None = None,
) -> None:
    """Insert a processed Activity + ActivityHrv pair for drift detection to read."""
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
                processing_status="processed",
                hrv_quality=quality,
                hrvt1_hr=hrvt1_hr,
                hrvt1_pace=hrvt1_pace,
                threshold_r_squared=r_squared,
            )
        )
        await session.commit()


class TestThresholdDriftBootstrap:
    async def test_no_data_returns_none(self, _test_db):
        await _seed_drift_setup(user_id=1, sport="Run", lthr=153)
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None

    async def test_single_sample_below_drift_threshold_no_alert(self, _test_db):
        """1 sample, drift only ~8% — bootstrap requires >10% so no alert."""
        await _seed_drift_setup(user_id=1, sport="Run", lthr=153)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt1_hr=165.0,
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None

    async def test_single_sample_low_r_squared_no_alert(self, _test_db):
        """1 sample, big drift but R²=0.5 — bootstrap blocked by R² gate."""
        await _seed_drift_setup(user_id=1, sport="Run", lthr=153)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt1_hr=175.0,
            r_squared=0.50,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None

    async def test_single_sample_bootstrap_fires(self, _test_db):
        """1 sample, R²>0.85, drift>10% — bootstrap alert."""
        await _seed_drift_setup(user_id=1, sport="Run", lthr=153)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt1_hr=175.0,
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is not None
        assert len(result.alerts) == 1
        alert = result.alerts[0]
        assert alert.sport == "Run"
        assert alert.tests_count == 1
        assert alert.measured_avg == 175
        assert alert.diff_pct > 10
        assert "Bootstrap" in alert.message or "single test" in alert.message

    async def test_two_samples_use_standard_5pct_threshold(self, _test_db):
        """2 samples: drift >5% triggers standard alert (not the bootstrap path)."""
        await _seed_drift_setup(user_id=1, sport="Run", lthr=153)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt="2026-04-25",
            hrvt1_hr=164.0,
            r_squared=0.70,
        )
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i2",
            dt="2026-04-30",
            hrvt1_hr=166.0,
            r_squared=0.72,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is not None
        alert = result.alerts[0]
        assert alert.tests_count == 2
        assert "stable" in alert.message


class TestThresholdPaceDrift:
    """Run threshold_pace drift, mirrors LTHR gating but reads hrvt1_pace ('M:SS')."""

    async def _seed_run_with_pace(self, *, lthr: int = 153, threshold_pace: float = 295.0) -> None:
        # Seed both LTHR + threshold pace so the alerts are independent.
        await AthleteSettings.upsert(user_id=1, sport="Run", lthr=lthr, threshold_pace=threshold_pace)

    async def test_pace_bootstrap_fires_independently(self, _test_db):
        """Single pace sample with R²>0.85 and >10% drift → THRESHOLD_PACE alert."""
        await self._seed_run_with_pace()
        # Threshold 295 s/km (4:55/km). Measured 4:20/km = 260 s/km → -11.9% drift.
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt1_hr=153.0,  # equal to config — no LTHR drift
            hrvt1_pace="4:20",
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is not None
        metrics = {a.metric for a in result.alerts}
        assert "THRESHOLD_PACE" in metrics
        assert "LTHR" not in metrics
        pace_alert = next(a for a in result.alerts if a.metric == "THRESHOLD_PACE")
        assert pace_alert.measured_avg == 260
        assert pace_alert.config_value == 295
        assert pace_alert.tests_count == 1

    async def test_pace_bootstrap_blocked_by_low_r_squared(self, _test_db):
        await self._seed_run_with_pace()
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt1_hr=153.0,
            hrvt1_pace="4:20",
            r_squared=0.50,  # noisy fit
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None

    async def test_pace_two_samples_standard_path(self, _test_db):
        await self._seed_run_with_pace()
        # 280 / 282 → avg ~281 vs config 295 → -4.7% (under 5%, no alert)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt="2026-04-25",
            hrvt1_hr=153.0,
            hrvt1_pace="4:40",
            r_squared=0.70,
        )
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i2",
            dt="2026-04-30",
            hrvt1_hr=153.0,
            hrvt1_pace="4:42",
            r_squared=0.72,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None  # under 5% gate

    async def test_pace_two_samples_above_5pct_fires(self, _test_db):
        await self._seed_run_with_pace()
        # 270, 268 → avg ~269 vs 295 → -8.8% (>5% gate, fires)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt="2026-04-25",
            hrvt1_hr=153.0,
            hrvt1_pace="4:30",
            r_squared=0.70,
        )
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i2",
            dt="2026-04-30",
            hrvt1_hr=153.0,
            hrvt1_pace="4:28",
            r_squared=0.72,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is not None
        metrics = {a.metric for a in result.alerts}
        assert "THRESHOLD_PACE" in metrics

    async def test_lthr_and_pace_drift_both_alert(self, _test_db):
        """Both metrics drift independently → two alerts in one ThresholdDriftDTO."""
        await self._seed_run_with_pace()
        # LTHR: 153 → 175 = +14% bootstrap; pace: 295 → 260 = -11.9% bootstrap
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt1_hr=175.0,
            hrvt1_pace="4:20",
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is not None
        metrics = {a.metric for a in result.alerts}
        assert metrics == {"LTHR", "THRESHOLD_PACE"}

    async def test_pace_alert_only_for_run(self, _test_db):
        """Ride with no threshold_pace setting — never emits THRESHOLD_PACE."""
        await AthleteSettings.upsert(user_id=1, sport="Ride", lthr=148, ftp=233)
        # Add a Ride HRV sample with pace data (would be ignored anyway — pace is Run-only)
        await _add_hrv_sample(
            user_id=1,
            sport="Ride",
            activity_id="i1",
            dt=str(date.today()),
            hrvt1_hr=148.0,  # no LTHR drift
            hrvt1_pace="4:20",
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None  # nothing to alert about
