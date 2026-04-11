"""Tests for mcp_server/tools/garmin.py."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

_MODULE = "mcp_server.tools.garmin"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sleep_row(
    calendar_date="2026-04-01",
    deep_sleep_secs=5400,
    light_sleep_secs=9000,
    rem_sleep_secs=6000,
    awake_sleep_secs=600,
    overall_score=78,
    quality_score=80,
    duration_score=72,
    recovery_score=75,
    deep_score=70,
    rem_score=65,
    restfulness_score=82,
    avg_respiration=14.5,
    lowest_respiration=12.0,
    highest_respiration=18.0,
    avg_sleep_stress=25,
    awake_count=3,
    sleep_start_gmt="2026-04-01T22:00:00",
    sleep_end_gmt="2026-04-02T06:30:00",
) -> MagicMock:
    r = MagicMock()
    r.calendar_date = calendar_date
    r.sleep_start_gmt = sleep_start_gmt
    r.sleep_end_gmt = sleep_end_gmt
    r.deep_sleep_secs = deep_sleep_secs
    r.light_sleep_secs = light_sleep_secs
    r.rem_sleep_secs = rem_sleep_secs
    r.awake_sleep_secs = awake_sleep_secs
    r.overall_score = overall_score
    r.quality_score = quality_score
    r.duration_score = duration_score
    r.recovery_score = recovery_score
    r.deep_score = deep_score
    r.rem_score = rem_score
    r.restfulness_score = restfulness_score
    r.avg_respiration = avg_respiration
    r.lowest_respiration = lowest_respiration
    r.highest_respiration = highest_respiration
    r.avg_sleep_stress = avg_sleep_stress
    r.awake_count = awake_count
    return r


def _make_readiness_row(
    calendar_date="2026-04-01",
    score=72,
    level="GOOD",
    feedback_short="Ready for training",
    hrv_factor_pct=85,
    sleep_score_factor_pct=70,
    sleep_history_factor_pct=68,
    recovery_time=14,
    recovery_factor_pct=80,
    acwr_factor_pct=90,
    stress_history_factor_pct=75,
    hrv_weekly_avg=62.5,
    acute_load=320,
) -> MagicMock:
    r = MagicMock()
    r.calendar_date = calendar_date
    r.score = score
    r.level = level
    r.feedback_short = feedback_short
    r.hrv_factor_pct = hrv_factor_pct
    r.sleep_score_factor_pct = sleep_score_factor_pct
    r.sleep_history_factor_pct = sleep_history_factor_pct
    r.recovery_time = recovery_time
    r.recovery_factor_pct = recovery_factor_pct
    r.acwr_factor_pct = acwr_factor_pct
    r.stress_history_factor_pct = stress_history_factor_pct
    r.hrv_weekly_avg = hrv_weekly_avg
    r.acute_load = acute_load
    return r


def _make_daily_row(
    calendar_date="2026-04-01",
    avg_stress=32,
    max_stress=65,
    stress_high_secs=3600,
    stress_low_secs=7200,
    stress_rest_secs=1800,
    body_battery_high=88,
    body_battery_low=42,
    body_battery_charged=46,
    body_battery_drained=38,
    total_steps=8500,
    resting_hr=52,
) -> MagicMock:
    r = MagicMock()
    r.calendar_date = calendar_date
    r.avg_stress = avg_stress
    r.max_stress = max_stress
    r.stress_high_secs = stress_high_secs
    r.stress_low_secs = stress_low_secs
    r.stress_rest_secs = stress_rest_secs
    r.body_battery_high = body_battery_high
    r.body_battery_low = body_battery_low
    r.body_battery_charged = body_battery_charged
    r.body_battery_drained = body_battery_drained
    r.total_steps = total_steps
    r.resting_hr = resting_hr
    return r


def _make_health_row(
    calendar_date="2026-04-01",
    hrv_value=58.0,
    hrv_status="BALANCED",
    hr_value=52,
    hr_status="BALANCED",
    spo2_value=97.0,
    spo2_status="NORMAL",
    respiration_value=14.0,
    respiration_status="NORMAL",
) -> MagicMock:
    r = MagicMock()
    r.calendar_date = calendar_date
    r.hrv_value = hrv_value
    r.hrv_status = hrv_status
    r.hr_value = hr_value
    r.hr_status = hr_status
    r.spo2_value = spo2_value
    r.spo2_status = spo2_status
    r.respiration_value = respiration_value
    r.respiration_status = respiration_status
    return r


def _make_load_row(
    calendar_date="2026-04-01",
    acwr=0.85,
    acwr_status="OPTIMAL",
    acute_load=310,
    chronic_load=365,
) -> MagicMock:
    r = MagicMock()
    r.calendar_date = calendar_date
    r.acwr = acwr
    r.acwr_status = acwr_status
    r.acute_load = acute_load
    r.chronic_load = chronic_load
    return r


def _make_race_row(
    calendar_date="2026-04-01",
    prediction_5k_secs=1320,  # 22:00
    prediction_10k_secs=2760,  # 46:00
    prediction_half_secs=6300,  # 1:45:00
    prediction_marathon_secs=13500,  # 3:45:00
) -> MagicMock:
    r = MagicMock()
    r.calendar_date = calendar_date
    r.prediction_5k_secs = prediction_5k_secs
    r.prediction_10k_secs = prediction_10k_secs
    r.prediction_half_secs = prediction_half_secs
    r.prediction_marathon_secs = prediction_marathon_secs
    return r


def _make_fitness_row(
    calendar_date="2026-04-01",
    vo2max_running=None,
    vo2max_cycling=52.0,
    endurance_score=68,
    max_met=14.5,
    fitness_age=32,
) -> MagicMock:
    r = MagicMock()
    r.calendar_date = calendar_date
    r.vo2max_running = vo2max_running
    r.vo2max_cycling = vo2max_cycling
    r.endurance_score = endurance_score
    r.max_met = max_met
    r.fitness_age = fitness_age
    return r


def _make_hr_event_row(
    calendar_date="2026-04-01",
    timestamp_gmt="2026-04-01T14:30:00",
    hr_value=165,
    threshold_value=160,
) -> MagicMock:
    r = MagicMock()
    r.calendar_date = calendar_date
    r.timestamp_gmt = timestamp_gmt
    r.hr_value = hr_value
    r.threshold_value = threshold_value
    return r


def _patch_freshness(freshness_return: dict):
    """Patch _data_freshness to return a fixed dict."""
    return patch(f"{_MODULE}._data_freshness", new=AsyncMock(return_value=freshness_return))


_FRESH = {"data_covers_until": "2026-04-10", "days_stale": 0, "freshness_warning": None}


# ---------------------------------------------------------------------------
# _date_range
# ---------------------------------------------------------------------------


class TestDateRange:
    """_date_range computes (start, end) from target_date and days_back."""

    def test_specific_date_single_day(self):
        from mcp_server.tools.garmin import _date_range

        start, end = _date_range("2026-04-10", 1)
        assert end == "2026-04-10"
        assert start == "2026-04-10"

    def test_specific_date_days_back(self):
        from mcp_server.tools.garmin import _date_range

        start, end = _date_range("2026-04-10", 7)
        assert end == "2026-04-10"
        assert start == "2026-04-04"

    def test_specific_date_30_days_back(self):
        from mcp_server.tools.garmin import _date_range

        start, end = _date_range("2026-04-10", 30)
        assert end == "2026-04-10"
        expected_start = str(date(2026, 4, 10) - timedelta(days=29))
        assert start == expected_start

    def test_empty_string_uses_today(self):
        from mcp_server.tools.garmin import _date_range

        today = date.today()
        start, end = _date_range("", 1)
        assert end == str(today)

    def test_none_uses_today(self):
        from mcp_server.tools.garmin import _date_range

        today = date.today()
        start, end = _date_range(None, 1)
        assert end == str(today)

    def test_days_back_calculation(self):
        """start = ref - (days_back - 1) so the range spans exactly days_back days."""
        from mcp_server.tools.garmin import _date_range

        start, end = _date_range("2026-04-10", 3)
        assert start == "2026-04-08"
        assert end == "2026-04-10"

    def test_returns_strings(self):
        from mcp_server.tools.garmin import _date_range

        start, end = _date_range("2026-04-10", 7)
        assert isinstance(start, str)
        assert isinstance(end, str)


# ---------------------------------------------------------------------------
# _data_freshness
# ---------------------------------------------------------------------------


class TestDataFreshness:
    """_data_freshness computes staleness metadata from the latest GarminSleep date."""

    def _make_session_ctx(self, scalar_value):
        """Build an async context manager whose session.execute(...).scalar() returns scalar_value.

        session.execute is awaited in the source, so execute must be an AsyncMock.
        Its awaited return value must be a plain MagicMock so .scalar() is synchronous.
        """
        execute_result = MagicMock()
        execute_result.scalar.return_value = scalar_value
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=execute_result)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        return mock_ctx

    async def test_no_data_returns_none_fields(self):
        from mcp_server.tools.garmin import _data_freshness

        with patch(f"{_MODULE}.get_session", return_value=self._make_session_ctx(None)):
            result = await _data_freshness(user_id=1)

        assert result["data_covers_until"] is None
        assert result["days_stale"] is None
        assert "No Garmin data" in result["freshness_warning"]

    async def test_fresh_data_no_warning(self):
        from mcp_server.tools.garmin import _data_freshness

        today = str(date.today())
        with patch(f"{_MODULE}.get_session", return_value=self._make_session_ctx(today)):
            result = await _data_freshness(user_id=1)

        assert result["data_covers_until"] == today
        assert result["days_stale"] == 0
        assert result["freshness_warning"] is None

    async def test_stale_8_days_returns_trend_warning(self):
        from mcp_server.tools.garmin import _data_freshness

        stale_date = str(date.today() - timedelta(days=8))
        with patch(f"{_MODULE}.get_session", return_value=self._make_session_ctx(stale_date)):
            result = await _data_freshness(user_id=1)

        assert result["days_stale"] == 8
        # >7 days: trend warning (not the export warning)
        assert result["freshness_warning"] is not None
        assert "trends and patterns" in result["freshness_warning"]
        assert "garmin.com" not in result["freshness_warning"]

    async def test_stale_15_days_returns_export_warning(self):
        from mcp_server.tools.garmin import _data_freshness

        stale_date = str(date.today() - timedelta(days=15))
        with patch(f"{_MODULE}.get_session", return_value=self._make_session_ctx(stale_date)):
            result = await _data_freshness(user_id=1)

        assert result["days_stale"] == 15
        # >14 days: export warning
        assert result["freshness_warning"] is not None
        assert "garmin.com" in result["freshness_warning"]

    async def test_stale_exactly_7_days_no_warning(self):
        """Exactly 7 days is NOT stale enough for a warning (threshold is >7)."""
        from mcp_server.tools.garmin import _data_freshness

        stale_date = str(date.today() - timedelta(days=7))
        with patch(f"{_MODULE}.get_session", return_value=self._make_session_ctx(stale_date)):
            result = await _data_freshness(user_id=1)

        assert result["days_stale"] == 7
        assert result["freshness_warning"] is None

    async def test_stale_exactly_14_days_trend_warning(self):
        """Exactly 14 days gets the trend warning (threshold for export is >14)."""
        from mcp_server.tools.garmin import _data_freshness

        stale_date = str(date.today() - timedelta(days=14))
        with patch(f"{_MODULE}.get_session", return_value=self._make_session_ctx(stale_date)):
            result = await _data_freshness(user_id=1)

        assert result["days_stale"] == 14
        assert "trends and patterns" in result["freshness_warning"]


# ---------------------------------------------------------------------------
# get_garmin_sleep
# ---------------------------------------------------------------------------


class TestGetGarminSleep:
    """get_garmin_sleep returns sleep phases in minutes, scores, and respiration."""

    async def test_returns_data_freshness_key(self):
        from mcp_server.tools.garmin import get_garmin_sleep

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminSleep.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_sleep()

        assert "data_freshness" in result
        assert result["data_freshness"] == _FRESH

    async def test_empty_rows_returns_zero_count(self):
        from mcp_server.tools.garmin import get_garmin_sleep

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminSleep.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_sleep()

        assert result["count"] == 0
        assert result["entries"] == []

    async def test_phases_converted_to_minutes(self):
        from mcp_server.tools.garmin import get_garmin_sleep

        row = _make_sleep_row(
            deep_sleep_secs=5400,  # 90 min
            light_sleep_secs=9000,  # 150 min
            rem_sleep_secs=6000,  # 100 min
            awake_sleep_secs=600,  # 10 min
        )
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminSleep.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_sleep()

        phases = result["entries"][0]["phases"]
        assert phases["deep_min"] == 90
        assert phases["light_min"] == 150
        assert phases["rem_min"] == 100
        assert phases["awake_min"] == 10

    async def test_null_sleep_secs_gives_none_minutes(self):
        """None seconds must not cause ZeroDivisionError or crash."""
        from mcp_server.tools.garmin import get_garmin_sleep

        row = _make_sleep_row(deep_sleep_secs=None, rem_sleep_secs=None)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminSleep.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_sleep()

        phases = result["entries"][0]["phases"]
        assert phases["deep_min"] is None
        assert phases["rem_min"] is None

    async def test_scores_returned(self):
        from mcp_server.tools.garmin import get_garmin_sleep

        row = _make_sleep_row(overall_score=78, quality_score=80, rem_score=65)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminSleep.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_sleep()

        scores = result["entries"][0]["scores"]
        assert scores["overall"] == 78
        assert scores["quality"] == 80
        assert scores["rem"] == 65

    async def test_respiration_returned(self):
        from mcp_server.tools.garmin import get_garmin_sleep

        row = _make_sleep_row(avg_respiration=14.5, lowest_respiration=12.0, highest_respiration=18.0)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminSleep.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_sleep()

        resp = result["entries"][0]["respiration"]
        assert resp["avg"] == 14.5
        assert resp["low"] == 12.0
        assert resp["high"] == 18.0

    async def test_multiple_rows_returned(self):
        from mcp_server.tools.garmin import get_garmin_sleep

        rows = [_make_sleep_row(calendar_date=f"2026-04-0{i}") for i in range(1, 4)]
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminSleep.get_range", new=AsyncMock(return_value=rows)),
        ):
            result = await get_garmin_sleep()

        assert result["count"] == 3
        assert len(result["entries"]) == 3

    async def test_passes_date_range_to_get_range(self):
        from mcp_server.tools.garmin import get_garmin_sleep

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=5),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminSleep.get_range", new=AsyncMock(return_value=[])) as mock_get,
        ):
            await get_garmin_sleep(target_date="2026-04-10", days_back=3)

        mock_get.assert_called_once_with(5, "2026-04-08", "2026-04-10")


# ---------------------------------------------------------------------------
# get_garmin_readiness
# ---------------------------------------------------------------------------


class TestGetGarminReadiness:
    """get_garmin_readiness returns score, level, and factor breakdown."""

    async def test_returns_data_freshness_key(self):
        from mcp_server.tools.garmin import get_garmin_readiness

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminTrainingReadiness.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_readiness()

        assert "data_freshness" in result

    async def test_score_and_level_returned(self):
        from mcp_server.tools.garmin import get_garmin_readiness

        row = _make_readiness_row(score=72, level="GOOD")
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminTrainingReadiness.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_readiness()

        entry = result["entries"][0]
        assert entry["score"] == 72
        assert entry["level"] == "GOOD"

    async def test_factors_structure(self):
        from mcp_server.tools.garmin import get_garmin_readiness

        row = _make_readiness_row(
            hrv_factor_pct=85,
            sleep_score_factor_pct=70,
            recovery_time=14,
            acwr_factor_pct=90,
            stress_history_factor_pct=75,
        )
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminTrainingReadiness.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_readiness()

        factors = result["entries"][0]["factors"]
        assert factors["hrv_pct"] == 85
        assert factors["sleep_score_pct"] == 70
        assert factors["recovery_time_h"] == 14
        assert factors["acwr_pct"] == 90
        assert factors["stress_history_pct"] == 75

    async def test_hrv_weekly_avg_and_acute_load_in_entry(self):
        from mcp_server.tools.garmin import get_garmin_readiness

        row = _make_readiness_row(hrv_weekly_avg=62.5, acute_load=320)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminTrainingReadiness.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_readiness()

        entry = result["entries"][0]
        assert entry["hrv_weekly_avg"] == 62.5
        assert entry["acute_load"] == 320

    async def test_empty_rows(self):
        from mcp_server.tools.garmin import get_garmin_readiness

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminTrainingReadiness.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_readiness()

        assert result["count"] == 0
        assert result["entries"] == []


# ---------------------------------------------------------------------------
# get_garmin_daily_metrics
# ---------------------------------------------------------------------------


class TestGetGarminDailyMetrics:
    """get_garmin_daily_metrics merges daily summary, health status, and training load."""

    async def test_returns_data_freshness_key(self):
        from mcp_server.tools.garmin import get_garmin_daily_metrics

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminDailySummary.get_range", new=AsyncMock(return_value=[])),
            patch(f"{_MODULE}.GarminHealthStatus.get_range", new=AsyncMock(return_value=[])),
            patch(f"{_MODULE}.GarminTrainingLoad.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_daily_metrics()

        assert "data_freshness" in result

    async def test_merges_three_sources_by_date(self):
        """Dates from all 3 sources are unioned and each appears once."""
        from mcp_server.tools.garmin import get_garmin_daily_metrics

        daily = [_make_daily_row(calendar_date="2026-04-01")]
        health = [_make_health_row(calendar_date="2026-04-02")]
        load = [_make_load_row(calendar_date="2026-04-03")]

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminDailySummary.get_range", new=AsyncMock(return_value=daily)),
            patch(f"{_MODULE}.GarminHealthStatus.get_range", new=AsyncMock(return_value=health)),
            patch(f"{_MODULE}.GarminTrainingLoad.get_range", new=AsyncMock(return_value=load)),
        ):
            result = await get_garmin_daily_metrics()

        assert result["count"] == 3
        dates = [e["date"] for e in result["entries"]]
        assert "2026-04-01" in dates
        assert "2026-04-02" in dates
        assert "2026-04-03" in dates

    async def test_entries_sorted_by_date(self):
        from mcp_server.tools.garmin import get_garmin_daily_metrics

        daily = [
            _make_daily_row(calendar_date="2026-04-03"),
            _make_daily_row(calendar_date="2026-04-01"),
            _make_daily_row(calendar_date="2026-04-02"),
        ]
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminDailySummary.get_range", new=AsyncMock(return_value=daily)),
            patch(f"{_MODULE}.GarminHealthStatus.get_range", new=AsyncMock(return_value=[])),
            patch(f"{_MODULE}.GarminTrainingLoad.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_daily_metrics()

        dates = [e["date"] for e in result["entries"]]
        assert dates == sorted(dates)

    async def test_stress_fields_computed(self):
        from mcp_server.tools.garmin import get_garmin_daily_metrics

        row = _make_daily_row(avg_stress=32, max_stress=65, stress_high_secs=3600)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminDailySummary.get_range", new=AsyncMock(return_value=[row])),
            patch(f"{_MODULE}.GarminHealthStatus.get_range", new=AsyncMock(return_value=[])),
            patch(f"{_MODULE}.GarminTrainingLoad.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_daily_metrics()

        stress = result["entries"][0]["stress"]
        assert stress["avg"] == 32
        assert stress["max"] == 65
        assert stress["high_min"] == 60  # 3600 // 60

    async def test_missing_source_returns_none_fields(self):
        """A date that has only daily data returns None for health and load fields."""
        from mcp_server.tools.garmin import get_garmin_daily_metrics

        row = _make_daily_row(calendar_date="2026-04-01")
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminDailySummary.get_range", new=AsyncMock(return_value=[row])),
            patch(f"{_MODULE}.GarminHealthStatus.get_range", new=AsyncMock(return_value=[])),
            patch(f"{_MODULE}.GarminTrainingLoad.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_daily_metrics()

        entry = result["entries"][0]
        assert entry["training_load"]["acwr"] is None
        assert entry["training_load"]["status"] is None

    async def test_health_baselines_included(self):
        from mcp_server.tools.garmin import get_garmin_daily_metrics

        health = [
            _make_health_row(
                calendar_date="2026-04-01",
                hrv_value=58.0,
                hrv_status="BALANCED",
                spo2_value=97.0,
                spo2_status="NORMAL",
            )
        ]
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminDailySummary.get_range", new=AsyncMock(return_value=[])),
            patch(f"{_MODULE}.GarminHealthStatus.get_range", new=AsyncMock(return_value=health)),
            patch(f"{_MODULE}.GarminTrainingLoad.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_daily_metrics()

        baselines = result["entries"][0]["health_baselines"]
        assert baselines["hrv"]["value"] == 58.0
        assert baselines["hrv"]["status"] == "BALANCED"
        assert baselines["spo2"]["value"] == 97.0

    async def test_duplicate_dates_from_all_sources_counted_once(self):
        """Same date in all 3 sources yields a single merged entry."""
        from mcp_server.tools.garmin import get_garmin_daily_metrics

        daily = [_make_daily_row(calendar_date="2026-04-01")]
        health = [_make_health_row(calendar_date="2026-04-01")]
        load = [_make_load_row(calendar_date="2026-04-01")]

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminDailySummary.get_range", new=AsyncMock(return_value=daily)),
            patch(f"{_MODULE}.GarminHealthStatus.get_range", new=AsyncMock(return_value=health)),
            patch(f"{_MODULE}.GarminTrainingLoad.get_range", new=AsyncMock(return_value=load)),
        ):
            result = await get_garmin_daily_metrics()

        assert result["count"] == 1


# ---------------------------------------------------------------------------
# get_garmin_race_predictions
# ---------------------------------------------------------------------------


class TestGetGarminRacePredictions:
    """get_garmin_race_predictions formats seconds to HH:MM:SS or MM:SS strings."""

    async def test_returns_data_freshness_key(self):
        from mcp_server.tools.garmin import get_garmin_race_predictions

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminRacePredictions.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_race_predictions()

        assert "data_freshness" in result

    async def test_5k_formats_as_mm_ss(self):
        """22:00 = 1320 seconds, no hour component."""
        from mcp_server.tools.garmin import get_garmin_race_predictions

        row = _make_race_row(prediction_5k_secs=1320)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminRacePredictions.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_race_predictions()

        assert result["entries"][0]["5k"]["time"] == "22:00"
        assert result["entries"][0]["5k"]["secs"] == 1320

    async def test_half_marathon_formats_with_hours(self):
        """1:45:00 = 6300 seconds, has hour component."""
        from mcp_server.tools.garmin import get_garmin_race_predictions

        row = _make_race_row(prediction_half_secs=6300)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminRacePredictions.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_race_predictions()

        assert result["entries"][0]["half"]["time"] == "1:45:00"

    async def test_marathon_formats_with_hours(self):
        """3:45:00 = 13500 seconds."""
        from mcp_server.tools.garmin import get_garmin_race_predictions

        row = _make_race_row(prediction_marathon_secs=13500)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminRacePredictions.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_race_predictions()

        assert result["entries"][0]["marathon"]["time"] == "3:45:00"

    async def test_none_seconds_gives_none_time(self):
        from mcp_server.tools.garmin import get_garmin_race_predictions

        row = _make_race_row(prediction_5k_secs=None)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminRacePredictions.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_race_predictions()

        assert result["entries"][0]["5k"]["time"] is None
        assert result["entries"][0]["5k"]["secs"] is None

    async def test_10k_format_under_one_hour(self):
        """46:00 = 2760 seconds."""
        from mcp_server.tools.garmin import get_garmin_race_predictions

        row = _make_race_row(prediction_10k_secs=2760)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminRacePredictions.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_race_predictions()

        assert result["entries"][0]["10k"]["time"] == "46:00"

    async def test_zero_seconds_gives_none_time(self):
        """The _fmt helper treats falsy (0) as None."""
        from mcp_server.tools.garmin import get_garmin_race_predictions

        row = _make_race_row(prediction_5k_secs=0)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminRacePredictions.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_race_predictions()

        assert result["entries"][0]["5k"]["time"] is None


# ---------------------------------------------------------------------------
# get_garmin_vo2max_trend
# ---------------------------------------------------------------------------


class TestGetGarminVo2maxTrend:
    """get_garmin_vo2max_trend filters by sport and skips all-empty entries."""

    async def test_returns_data_freshness_key(self):
        from mcp_server.tools.garmin import get_garmin_vo2max_trend

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminFitnessMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_vo2max_trend()

        assert "data_freshness" in result

    async def test_sport_returned_in_result(self):
        from mcp_server.tools.garmin import get_garmin_vo2max_trend

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminFitnessMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_vo2max_trend(sport="running")

        assert result["sport"] == "running"

    async def test_cycling_uses_vo2max_cycling_field(self):
        from mcp_server.tools.garmin import get_garmin_vo2max_trend

        row = _make_fitness_row(vo2max_cycling=52.0, vo2max_running=None)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminFitnessMetrics.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_vo2max_trend(sport="cycling")

        assert result["count"] == 1
        assert result["entries"][0]["vo2max"] == 52.0

    async def test_running_uses_vo2max_running_field(self):
        from mcp_server.tools.garmin import get_garmin_vo2max_trend

        row = _make_fitness_row(vo2max_running=48.0, vo2max_cycling=None, endurance_score=None, max_met=None)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminFitnessMetrics.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_vo2max_trend(sport="running")

        assert result["count"] == 1
        assert result["entries"][0]["vo2max"] == 48.0

    async def test_entry_skipped_when_all_fields_empty(self):
        """Row with no vo2max, no endurance_score, no max_met is filtered out."""
        from mcp_server.tools.garmin import get_garmin_vo2max_trend

        row = _make_fitness_row(vo2max_cycling=None, endurance_score=None, max_met=None)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminFitnessMetrics.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_vo2max_trend(sport="cycling")

        assert result["count"] == 0
        assert result["entries"] == []

    async def test_entry_included_when_only_endurance_score_present(self):
        """Even if vo2max is None, a row with endurance_score should be included."""
        from mcp_server.tools.garmin import get_garmin_vo2max_trend

        row = _make_fitness_row(vo2max_cycling=None, endurance_score=65, max_met=None)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminFitnessMetrics.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_vo2max_trend(sport="cycling")

        assert result["count"] == 1
        assert result["entries"][0]["endurance_score"] == 65

    async def test_fitness_age_included_in_entry(self):
        from mcp_server.tools.garmin import get_garmin_vo2max_trend

        row = _make_fitness_row(vo2max_cycling=52.0, fitness_age=32)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminFitnessMetrics.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_vo2max_trend(sport="cycling")

        assert result["entries"][0]["fitness_age"] == 32

    async def test_sport_case_insensitive_running(self):
        """sport="Running" should still use the running field."""
        from mcp_server.tools.garmin import get_garmin_vo2max_trend

        row = _make_fitness_row(vo2max_running=48.0, vo2max_cycling=None, endurance_score=None, max_met=None)
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminFitnessMetrics.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_vo2max_trend(sport="Running")

        assert result["entries"][0]["vo2max"] == 48.0


# ---------------------------------------------------------------------------
# get_garmin_abnormal_hr_events
# ---------------------------------------------------------------------------


class TestGetGarminAbnormalHrEvents:
    """get_garmin_abnormal_hr_events returns events with timestamps, HR value, and threshold."""

    async def test_returns_data_freshness_key(self):
        from mcp_server.tools.garmin import get_garmin_abnormal_hr_events

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminAbnormalHrEvents.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_abnormal_hr_events()

        assert "data_freshness" in result

    async def test_empty_rows_returns_zero_count(self):
        from mcp_server.tools.garmin import get_garmin_abnormal_hr_events

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminAbnormalHrEvents.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_garmin_abnormal_hr_events()

        assert result["count"] == 0
        assert result["entries"] == []

    async def test_event_fields_returned(self):
        from mcp_server.tools.garmin import get_garmin_abnormal_hr_events

        row = _make_hr_event_row(
            calendar_date="2026-04-01",
            timestamp_gmt="2026-04-01T14:30:00",
            hr_value=165,
            threshold_value=160,
        )
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminAbnormalHrEvents.get_range", new=AsyncMock(return_value=[row])),
        ):
            result = await get_garmin_abnormal_hr_events()

        entry = result["entries"][0]
        assert entry["date"] == "2026-04-01"
        assert entry["timestamp"] == "2026-04-01T14:30:00"
        assert entry["hr_value"] == 165
        assert entry["threshold"] == 160

    async def test_multiple_events_all_returned(self):
        from mcp_server.tools.garmin import get_garmin_abnormal_hr_events

        rows = [
            _make_hr_event_row(calendar_date="2026-04-01", hr_value=165),
            _make_hr_event_row(calendar_date="2026-04-02", hr_value=170),
            _make_hr_event_row(calendar_date="2026-04-03", hr_value=168),
        ]
        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminAbnormalHrEvents.get_range", new=AsyncMock(return_value=rows)),
        ):
            result = await get_garmin_abnormal_hr_events()

        assert result["count"] == 3
        assert len(result["entries"]) == 3

    async def test_passes_days_back_to_date_range(self):
        """days_back is wired through _date_range; verify get_range receives a valid range."""
        from mcp_server.tools.garmin import get_garmin_abnormal_hr_events

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=7),
            _patch_freshness(_FRESH),
            patch(f"{_MODULE}.GarminAbnormalHrEvents.get_range", new=AsyncMock(return_value=[])) as mock_get,
        ):
            await get_garmin_abnormal_hr_events(days_back=14)

        mock_get.assert_called_once()
        args = mock_get.call_args[0]
        assert args[0] == 7  # user_id
        # start date must be before end date
        start, end = args[1], args[2]
        assert start < end
