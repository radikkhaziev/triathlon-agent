"""Tests for User.detect_threshold_drift — latest-ramp HRVT2 path.

The drift detector compares the *latest* ramp-test HRVT2 reading against
``athlete_settings`` (LTHR for Run/Ride, plus pace at HRVT2 for Run).
Gate: ``|drift| > 5%`` AND ``R² ≥ 0.7`` on the most recent valid row.
"""

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


class TestDriftAlertHelpers:
    """Pure-function tests for `_drift_alert_lthr` and `_drift_alert_pace`.

    Cheap (no DB), covers edge cases that the SQL-level integration tests miss
    (None values, R²-boundary, sign of drift). The helpers must agree with the
    backend gating constants in `data.db.dto` — same constants pulled by the
    formatter's `_drift_button_status`, so divergence becomes a UI-vs-backend
    desync bug.
    """

    def test_lthr_returns_none_on_missing_hrvt2(self):
        from data.db.user import _drift_alert_lthr

        assert _drift_alert_lthr("Run", hrvt2_hr=None, r_squared=0.9, config_lthr=153) is None

    def test_lthr_returns_none_on_missing_r_squared(self):
        from data.db.user import _drift_alert_lthr

        assert _drift_alert_lthr("Run", hrvt2_hr=172.0, r_squared=None, config_lthr=153) is None

    def test_lthr_returns_none_below_r_squared_gate(self):
        from data.db.user import _drift_alert_lthr

        assert _drift_alert_lthr("Run", hrvt2_hr=172.0, r_squared=0.69, config_lthr=153) is None

    def test_lthr_returns_none_below_drift_gate(self):
        from data.db.user import _drift_alert_lthr

        # 156 vs 153 = +1.96% — under 5%
        assert _drift_alert_lthr("Run", hrvt2_hr=156.0, r_squared=0.9, config_lthr=153) is None

    def test_lthr_at_r_squared_boundary_fires(self):
        """R²=0.7 exactly → gate passes (>=, not >)."""
        from data.db.user import _drift_alert_lthr

        alert = _drift_alert_lthr("Run", hrvt2_hr=172.0, r_squared=0.70, config_lthr=153)
        assert alert is not None
        assert alert.metric == "LTHR"
        assert alert.measured == 172
        assert alert.config_value == 153
        assert alert.diff_pct > 5

    def test_lthr_negative_drift_fires(self):
        """Drift < -5% (config too high) also fires."""
        from data.db.user import _drift_alert_lthr

        alert = _drift_alert_lthr("Run", hrvt2_hr=140.0, r_squared=0.85, config_lthr=160)
        assert alert is not None
        assert alert.diff_pct < -5
        assert alert.measured == 140

    def test_pace_returns_none_on_missing_pace(self):
        from data.db.user import _drift_alert_pace

        assert _drift_alert_pace("Run", hrvt2_pace=None, r_squared=0.9, config_pace_sec=295) is None

    def test_pace_returns_none_on_invalid_pace_string(self):
        """`parse_pace_to_sec` returns None → helper bails out cleanly."""
        from data.db.user import _drift_alert_pace

        assert _drift_alert_pace("Run", hrvt2_pace="abc", r_squared=0.9, config_pace_sec=295) is None

    def test_pace_returns_none_below_r_squared_gate(self):
        from data.db.user import _drift_alert_pace

        assert _drift_alert_pace("Run", hrvt2_pace="4:20", r_squared=0.5, config_pace_sec=295) is None

    def test_pace_returns_none_below_drift_gate(self):
        from data.db.user import _drift_alert_pace

        # 290 vs 295 = -1.7% — under 5%
        assert _drift_alert_pace("Run", hrvt2_pace="4:50", r_squared=0.9, config_pace_sec=295) is None

    def test_pace_fires_with_proper_drift(self):
        from data.db.user import _drift_alert_pace

        # 4:20 = 260 s/km vs 295 = -11.9%
        alert = _drift_alert_pace("Run", hrvt2_pace="4:20", r_squared=0.85, config_pace_sec=295)
        assert alert is not None
        assert alert.metric == "THRESHOLD_PACE"
        assert alert.measured == 260
        assert alert.config_value == 295
        assert alert.diff_pct < -5

    # ----- FTP -----

    def test_ftp_returns_none_on_missing_power(self):
        from data.db.user import _drift_alert_ftp

        assert _drift_alert_ftp("Ride", hrvt2_power=None, r_squared=0.9, config_ftp=208) is None

    def test_ftp_returns_none_below_r_squared_gate(self):
        from data.db.user import _drift_alert_ftp

        assert _drift_alert_ftp("Ride", hrvt2_power=240.0, r_squared=0.5, config_ftp=208) is None

    def test_ftp_returns_none_below_drift_gate(self):
        from data.db.user import _drift_alert_ftp

        # 212 vs 208 = +1.9% — under 5%
        assert _drift_alert_ftp("Ride", hrvt2_power=212.0, r_squared=0.9, config_ftp=208) is None

    def test_ftp_fires_with_proper_drift(self):
        from data.db.user import _drift_alert_ftp

        # 240 vs 208 = +15.4%
        alert = _drift_alert_ftp("Ride", hrvt2_power=240.0, r_squared=0.85, config_ftp=208)
        assert alert is not None
        assert alert.metric == "FTP"
        assert alert.measured == 240
        assert alert.config_value == 208
        assert alert.diff_pct > 5

    def test_ftp_negative_drift_fires(self):
        """Drift < -5% (FTP set too high) also fires."""
        from data.db.user import _drift_alert_ftp

        alert = _drift_alert_ftp("Ride", hrvt2_power=180.0, r_squared=0.85, config_ftp=210)
        assert alert is not None
        assert alert.diff_pct < -5
        assert alert.measured == 180


async def _seed_drift_setup(*, user_id: int, sport: str, lthr: int) -> None:
    """Make sure athlete_settings has an LTHR baseline for the sport."""
    await AthleteSettings.upsert(user_id=user_id, sport=sport, lthr=lthr)


async def _add_hrv_sample(
    *,
    user_id: int,
    sport: str,
    activity_id: str,
    dt: str,
    hrvt2_hr: float,
    r_squared: float | None = 0.90,
    quality: str = "good",
    hrvt2_pace: str | None = None,
    hrvt2_power: float | None = None,
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
                # HRVT1 mirrors HRVT2 in tests — drift detector reads HRVT2 only,
                # but the column is non-null in real data so we set both.
                hrvt1_hr=hrvt2_hr - 15,
                hrvt2_hr=hrvt2_hr,
                hrvt2_pace=hrvt2_pace,
                hrvt2_power=hrvt2_power,
                threshold_r_squared=r_squared,
            )
        )
        await session.commit()


class TestLthrDrift:
    async def test_no_data_returns_none(self, _test_db):
        await _seed_drift_setup(user_id=1, sport="Run", lthr=153)
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None

    async def test_below_5pct_no_alert(self, _test_db):
        """Drift <= 5% → silent."""
        await _seed_drift_setup(user_id=1, sport="Run", lthr=170)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=172.0,  # +1.2%
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None

    async def test_low_r_squared_no_alert(self, _test_db):
        """R² < 0.7 blocks even sizeable drift."""
        await _seed_drift_setup(user_id=1, sport="Run", lthr=153)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=175.0,  # +14% drift
            r_squared=0.50,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None

    async def test_above_5pct_with_decent_r2_fires(self, _test_db):
        """|drift| > 5% AND R² ≥ 0.7 → LTHR alert."""
        await _seed_drift_setup(user_id=1, sport="Run", lthr=153)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=172.0,  # +12.4%
            r_squared=0.72,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is not None
        assert len(result.alerts) == 1
        alert = result.alerts[0]
        assert alert.sport == "Run"
        assert alert.metric == "LTHR"
        assert alert.measured == 172
        assert alert.config_value == 153
        assert alert.diff_pct > 5

    async def test_uses_latest_when_multiple_samples(self, _test_db):
        """When several ramp tests exist, drift detector uses the latest."""
        await _seed_drift_setup(user_id=1, sport="Run", lthr=153)
        # Old test: HRVT2=160 (+4.6%, would not fire)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i_old",
            dt="2026-04-01",
            hrvt2_hr=160.0,
            r_squared=0.85,
        )
        # New test: HRVT2=172 (+12.4%, fires)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i_new",
            dt=str(date.today()),
            hrvt2_hr=172.0,
            r_squared=0.75,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is not None
        assert result.alerts[0].measured == 172  # latest, not avg


class TestThresholdPaceDrift:
    async def _seed_run_with_pace(self, *, lthr: int = 153, threshold_pace: float = 295.0) -> None:
        await AthleteSettings.upsert(user_id=1, sport="Run", lthr=lthr, threshold_pace=threshold_pace)

    async def test_pace_drift_fires(self, _test_db):
        """Pace at HRVT2 differs from config_threshold_pace by >5% → THRESHOLD_PACE alert."""
        await self._seed_run_with_pace(lthr=170, threshold_pace=295.0)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=172.0,  # +1.2%, no LTHR alert
            hrvt2_pace="4:20",  # 260 s/km, -11.9% vs 295
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is not None
        metrics = {a.metric for a in result.alerts}
        assert "THRESHOLD_PACE" in metrics
        assert "LTHR" not in metrics
        pace_alert = next(a for a in result.alerts if a.metric == "THRESHOLD_PACE")
        assert pace_alert.measured == 260
        assert pace_alert.config_value == 295

    async def test_pace_blocked_by_low_r_squared(self, _test_db):
        await self._seed_run_with_pace(lthr=170)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=172.0,
            hrvt2_pace="4:20",
            r_squared=0.50,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None

    async def test_pace_under_5pct_silent(self, _test_db):
        await self._seed_run_with_pace(lthr=170, threshold_pace=295.0)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=172.0,
            hrvt2_pace="4:50",  # 290 s/km, -1.7% vs 295
            r_squared=0.85,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None

    async def test_lthr_and_pace_both_alert(self, _test_db):
        """Both metrics drift independently → two alerts in one ThresholdDriftDTO."""
        await self._seed_run_with_pace(lthr=153, threshold_pace=295.0)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=172.0,  # +12.4% LTHR drift
            hrvt2_pace="4:20",  # -11.9% pace drift
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is not None
        metrics = {a.metric for a in result.alerts}
        assert metrics == {"LTHR", "THRESHOLD_PACE"}

    async def test_pace_alert_only_for_run(self, _test_db):
        """Ride: no threshold_pace setting → never emits THRESHOLD_PACE."""
        await AthleteSettings.upsert(user_id=1, sport="Ride", lthr=148, ftp=233)
        await _add_hrv_sample(
            user_id=1,
            sport="Ride",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=148.0,  # no LTHR drift
            hrvt2_pace="4:20",
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None


class TestFtpDrift:
    """FTP drift fires only on Ride, gated identically to LTHR/pace."""

    async def _seed_ride(self, *, lthr: int = 165, ftp: int = 208) -> None:
        await AthleteSettings.upsert(user_id=1, sport="Ride", lthr=lthr, ftp=ftp)

    async def test_ftp_drift_fires(self, _test_db):
        """Pow at HRVT2 differs from config_ftp by >5% → FTP alert."""
        await self._seed_ride(lthr=165, ftp=208)
        await _add_hrv_sample(
            user_id=1,
            sport="Ride",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=166.0,  # +0.6%, no LTHR alert
            hrvt2_power=240.0,  # +15.4% vs 208
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is not None
        metrics = {a.metric for a in result.alerts}
        assert "FTP" in metrics
        assert "LTHR" not in metrics
        ftp_alert = next(a for a in result.alerts if a.metric == "FTP")
        assert ftp_alert.measured == 240
        assert ftp_alert.config_value == 208

    async def test_ftp_blocked_by_low_r_squared(self, _test_db):
        await self._seed_ride(lthr=165, ftp=208)
        await _add_hrv_sample(
            user_id=1,
            sport="Ride",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=166.0,
            hrvt2_power=240.0,
            r_squared=0.5,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None

    async def test_ftp_under_5pct_silent(self, _test_db):
        await self._seed_ride(lthr=165, ftp=208)
        await _add_hrv_sample(
            user_id=1,
            sport="Ride",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=166.0,
            hrvt2_power=215.0,  # +3.4% vs 208
            r_squared=0.85,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is None

    async def test_lthr_and_ftp_both_alert(self, _test_db):
        """Both metrics drift independently on the same Ride row → two alerts."""
        await self._seed_ride(lthr=160, ftp=208)
        await _add_hrv_sample(
            user_id=1,
            sport="Ride",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=180.0,  # +12.5% vs 160
            hrvt2_power=240.0,  # +15.4% vs 208
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        assert result is not None
        metrics = {a.metric for a in result.alerts}
        assert metrics == {"LTHR", "FTP"}

    async def test_ftp_alert_only_for_ride(self, _test_db):
        """Run: no FTP push path even when hrvt2_power synthetically present."""
        await AthleteSettings.upsert(user_id=1, sport="Run", lthr=172, ftp=366)
        await _add_hrv_sample(
            user_id=1,
            sport="Run",
            activity_id="i1",
            dt=str(date.today()),
            hrvt2_hr=172.0,  # no LTHR drift
            hrvt2_power=420.0,  # would be +14.7% drift if Run had FTP-drift path
            r_squared=0.92,
        )
        result = await User.detect_threshold_drift(user_id=1)
        # No FTP alert: detector restricts FTP path to Ride only.
        assert result is None or all(a.metric != "FTP" for a in result.alerts)
