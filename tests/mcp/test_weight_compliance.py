"""Tests for mcp_server/tools/weight.py and mcp_server/tools/compliance.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_MODULE_W = "mcp_server.tools.weight"
_MODULE_C = "mcp_server.tools.compliance"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_async_session_ctx(execute_rows: list):
    """Build an async context manager whose session.execute(...).all() returns execute_rows."""
    execute_result = MagicMock()
    execute_result.all.return_value = execute_rows
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=execute_result)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


def _make_scalar_session_ctx(scalar_value):
    """Build an async context manager whose session.execute(...).scalar_one_or_none() returns scalar_value."""
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = scalar_value
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=execute_result)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


def _make_two_scalar_session_ctx(first_scalar, second_scalar):
    """Build an async context manager whose session.execute() returns two consecutive scalars."""
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = first_scalar
    second_result = MagicMock()
    second_result.scalar_one_or_none.return_value = second_scalar
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[first_result, second_result])
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


def _make_garmin_row(calendar_date: str, weight_kg: float | None) -> MagicMock:
    r = MagicMock()
    r.calendar_date = calendar_date
    r.weight_kg = weight_kg
    return r


def _make_wellness_row(dt: str, weight: float):
    """Return a (date, weight) tuple-like row as the ORM query returns."""
    return (dt, weight)


def _make_activity(
    activity_id: str = "A1",
    user_id: int = 1,
    start_date_local: str = "2026-04-10",
    activity_type: str = "Ride",
    moving_time: int | None = 3600,
    average_hr: float | None = 140.0,
    icu_training_load: float | None = 65.0,
) -> MagicMock:
    a = MagicMock()
    a.id = activity_id
    a.user_id = user_id
    a.start_date_local = start_date_local
    a.type = activity_type
    a.moving_time = moving_time
    a.average_hr = average_hr
    a.icu_training_load = icu_training_load
    return a


def _make_detail(activity_id: str = "A1", avg_power: float | None = 210.0) -> MagicMock:
    d = MagicMock()
    d.activity_id = activity_id
    d.avg_power = avg_power
    return d


def _make_workout(
    name: str = "Zone 2 Ride",
    moving_time: int | None = 3600,
    activity_type: str = "Ride",
    description: str | None = "Easy aerobic",
) -> MagicMock:
    w = MagicMock()
    w.name = name
    w.moving_time = moving_time
    w.type = activity_type
    w.description = description
    return w


def _make_log_entry(activity_id: str = "A1", compliance: str = "complete") -> MagicMock:
    entry = MagicMock()
    entry.actual_activity_id = activity_id
    entry.compliance = compliance
    entry.actual_max_zone_time = {"z1": 1800, "z2": 1200}
    return entry


# ---------------------------------------------------------------------------
# get_weight_trend — no data
# ---------------------------------------------------------------------------


class TestGetWeightTrendNoData:
    """Returns no_data status when neither wellness nor Garmin have weight entries."""

    async def test_no_data_returns_status_no_data(self):
        from mcp_server.tools.weight import get_weight_trend

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx([])),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_weight_trend(days_back=30, target_kg=0)

        assert result["status"] == "no_data"
        assert "message" in result


# ---------------------------------------------------------------------------
# get_weight_trend — single data point
# ---------------------------------------------------------------------------


class TestGetWeightTrendSinglePoint:
    """With only one data point there is no slope, direction stays stable."""

    async def test_single_point_no_slope(self):
        from mcp_server.tools.weight import get_weight_trend

        wellness_rows = [_make_wellness_row("2026-04-10", 82.5)]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx(wellness_rows)),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_weight_trend(days_back=30, target_kg=0)

        assert result["status"] == "ok"
        assert result["current_kg"] == 82.5
        assert result["data_points"] == 1
        assert result["trend_slope_kg_per_week"] == 0.0
        assert result["trend_direction"] == "stable"

    async def test_single_point_no_slope_with_target_gets_not_enough_data_note(self):
        from mcp_server.tools.weight import get_weight_trend

        wellness_rows = [_make_wellness_row("2026-04-10", 82.5)]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx(wellness_rows)),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_weight_trend(days_back=30, target_kg=78.0)

        assert result["target_kg"] == 78.0
        assert result["estimated_target_date"] is None
        assert "Not enough trend data" in result["target_note"]


# ---------------------------------------------------------------------------
# get_weight_trend — 3+ points, slope and direction
# ---------------------------------------------------------------------------


class TestGetWeightTrendSlope:
    """With 3+ points the slope is calculated and direction reflects it."""

    async def test_losing_direction_negative_slope(self):
        """Clear downward trend → direction losing, slope < -0.1 kg/week."""
        from mcp_server.tools.weight import get_weight_trend

        # 7 days of steadily decreasing weight
        wellness_rows = [
            _make_wellness_row("2026-04-04", 83.0),
            _make_wellness_row("2026-04-05", 82.8),
            _make_wellness_row("2026-04-06", 82.6),
            _make_wellness_row("2026-04-07", 82.4),
            _make_wellness_row("2026-04-08", 82.2),
            _make_wellness_row("2026-04-09", 82.0),
            _make_wellness_row("2026-04-10", 81.8),
        ]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx(wellness_rows)),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_weight_trend(days_back=30, target_kg=0)

        assert result["status"] == "ok"
        assert result["trend_direction"] == "losing"
        assert result["trend_slope_kg_per_week"] < -0.1

    async def test_gaining_direction_positive_slope(self):
        """Clear upward trend → direction gaining, slope > 0.1 kg/week."""
        from mcp_server.tools.weight import get_weight_trend

        wellness_rows = [
            _make_wellness_row("2026-04-04", 80.0),
            _make_wellness_row("2026-04-05", 80.3),
            _make_wellness_row("2026-04-06", 80.6),
            _make_wellness_row("2026-04-07", 80.9),
            _make_wellness_row("2026-04-08", 81.2),
            _make_wellness_row("2026-04-09", 81.5),
            _make_wellness_row("2026-04-10", 81.8),
        ]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx(wellness_rows)),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_weight_trend(days_back=30, target_kg=0)

        assert result["trend_direction"] == "gaining"
        assert result["trend_slope_kg_per_week"] > 0.1

    async def test_stable_direction_tiny_slope(self):
        """Negligible variation → direction stable."""
        from mcp_server.tools.weight import get_weight_trend

        # Slope will be ~0 kg/week — within the ±0.1 threshold
        wellness_rows = [
            _make_wellness_row("2026-04-04", 82.0),
            _make_wellness_row("2026-04-07", 82.01),
            _make_wellness_row("2026-04-10", 82.0),
        ]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx(wellness_rows)),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_weight_trend(days_back=30, target_kg=0)

        assert result["trend_direction"] == "stable"
        assert -0.1 <= result["trend_slope_kg_per_week"] <= 0.1

    async def test_aggregates_min_max_avg(self):
        from mcp_server.tools.weight import get_weight_trend

        wellness_rows = [
            _make_wellness_row("2026-04-04", 80.0),
            _make_wellness_row("2026-04-07", 82.0),
            _make_wellness_row("2026-04-10", 84.0),
        ]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx(wellness_rows)),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_weight_trend(days_back=30, target_kg=0)

        assert result["min_kg"] == 80.0
        assert result["max_kg"] == 84.0
        assert result["avg_kg"] == 82.0
        assert result["current_kg"] == 84.0


# ---------------------------------------------------------------------------
# get_weight_trend — target date estimation
# ---------------------------------------------------------------------------


class TestGetWeightTrendTargetDate:
    """Target date estimation when losing weight toward a lower target."""

    async def test_losing_toward_target_returns_estimated_date(self):
        from mcp_server.tools.weight import get_weight_trend

        # Steady loss ~1 kg/week; current 83, target 78
        wellness_rows = [
            _make_wellness_row("2026-04-04", 83.8),
            _make_wellness_row("2026-04-05", 83.6),
            _make_wellness_row("2026-04-06", 83.4),
            _make_wellness_row("2026-04-07", 83.2),
            _make_wellness_row("2026-04-08", 83.0),
            _make_wellness_row("2026-04-09", 82.8),
            _make_wellness_row("2026-04-10", 82.6),
        ]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx(wellness_rows)),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_weight_trend(days_back=30, target_kg=78.0)

        assert result["status"] == "ok"
        assert result["target_kg"] == 78.0
        assert result["estimated_target_date"] is not None
        # Date must be in the future relative to the period
        assert result["estimated_target_date"] > "2026-04-10"

    async def test_target_moving_away_returns_target_note(self):
        """Gaining weight while target is lower → trend moving away from target."""
        from mcp_server.tools.weight import get_weight_trend

        wellness_rows = [
            _make_wellness_row("2026-04-04", 80.0),
            _make_wellness_row("2026-04-06", 81.0),
            _make_wellness_row("2026-04-08", 82.0),
            _make_wellness_row("2026-04-10", 83.0),
        ]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx(wellness_rows)),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            # Target is 78 but weight is going up
            result = await get_weight_trend(days_back=30, target_kg=78.0)

        assert result["target_kg"] == 78.0
        assert result["estimated_target_date"] is None
        assert "moving away" in result["target_note"]

    async def test_target_kg_zero_no_target_fields(self):
        """target_kg=0 → no target estimation keys in result."""
        from mcp_server.tools.weight import get_weight_trend

        wellness_rows = [
            _make_wellness_row("2026-04-04", 83.0),
            _make_wellness_row("2026-04-07", 82.5),
            _make_wellness_row("2026-04-10", 82.0),
        ]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx(wellness_rows)),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=[])),
        ):
            result = await get_weight_trend(days_back=30, target_kg=0)

        assert "target_kg" not in result
        assert "estimated_target_date" not in result
        assert "target_note" not in result


# ---------------------------------------------------------------------------
# get_weight_trend — Garmin overrides wellness for same date
# ---------------------------------------------------------------------------


class TestGetWeightTrendGarminOverride:
    """Garmin bio_metrics weight takes precedence over wellness weight on the same date."""

    async def test_garmin_overrides_wellness_same_date(self):
        from mcp_server.tools.weight import get_weight_trend

        wellness_rows = [_make_wellness_row("2026-04-10", 83.0)]
        garmin_rows = [_make_garmin_row("2026-04-10", 81.5)]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx(wellness_rows)),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=garmin_rows)),
        ):
            result = await get_weight_trend(days_back=30, target_kg=0)

        assert result["current_kg"] == 81.5  # Garmin wins

    async def test_garmin_row_with_null_weight_not_applied(self):
        """Garmin row with weight_kg=None must not override wellness."""
        from mcp_server.tools.weight import get_weight_trend

        wellness_rows = [_make_wellness_row("2026-04-10", 83.0)]
        garmin_rows = [_make_garmin_row("2026-04-10", None)]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx(wellness_rows)),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=garmin_rows)),
        ):
            result = await get_weight_trend(days_back=30, target_kg=0)

        assert result["current_kg"] == 83.0  # wellness preserved

    async def test_garmin_only_data_no_wellness(self):
        """Only Garmin data available → still returns ok status."""
        from mcp_server.tools.weight import get_weight_trend

        garmin_rows = [_make_garmin_row("2026-04-10", 80.0)]

        with (
            patch(f"{_MODULE_W}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_W}.get_session", return_value=_make_async_session_ctx([])),
            patch(f"{_MODULE_W}.GarminBioMetrics.get_range", new=AsyncMock(return_value=garmin_rows)),
        ):
            result = await get_weight_trend(days_back=30, target_kg=0)

        assert result["status"] == "ok"
        assert result["current_kg"] == 80.0


# ---------------------------------------------------------------------------
# get_workout_compliance — activity not found
# ---------------------------------------------------------------------------


class TestGetWorkoutComplianceActivityNotFound:
    async def test_activity_not_found_returns_error(self):
        from mcp_server.tools.compliance import get_workout_compliance

        with (
            patch(f"{_MODULE_C}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_C}.get_session", return_value=_make_two_scalar_session_ctx(None, None)),
        ):
            result = await get_workout_compliance("MISSING_ID")

        assert "error" in result
        assert "MISSING_ID" in result["error"]


# ---------------------------------------------------------------------------
# get_workout_compliance — no scheduled workout → unplanned
# ---------------------------------------------------------------------------


class TestGetWorkoutComplianceUnplanned:
    async def test_no_scheduled_workout_returns_unplanned(self):
        from mcp_server.tools.compliance import get_workout_compliance

        activity = _make_activity(activity_id="A1", activity_type="Ride")
        detail = _make_detail(activity_id="A1", avg_power=200.0)

        with (
            patch(f"{_MODULE_C}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_C}.get_session", return_value=_make_two_scalar_session_ctx(activity, detail)),
            patch(f"{_MODULE_C}.ScheduledWorkout.get_for_date", new=AsyncMock(return_value=[])),
            patch(f"{_MODULE_C}.TrainingLog.get_for_date", new=AsyncMock(return_value=[])),
        ):
            result = await get_workout_compliance("A1")

        assert result["compliance"]["overall"] == "unplanned"
        assert result["planned"] is None


# ---------------------------------------------------------------------------
# get_workout_compliance — duration compliance ratings
# ---------------------------------------------------------------------------


class TestGetWorkoutComplianceDurationRatings:
    """Verifies compliance rating for matched workouts at various duration percentages."""

    async def _run(self, planned_secs: int, actual_secs: int) -> dict:
        from mcp_server.tools.compliance import get_workout_compliance

        activity = _make_activity(activity_id="A1", activity_type="Ride", moving_time=actual_secs)
        detail = _make_detail(activity_id="A1")
        workout = _make_workout(activity_type="Ride", moving_time=planned_secs)

        with (
            patch(f"{_MODULE_C}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_C}.get_session", return_value=_make_two_scalar_session_ctx(activity, detail)),
            patch(f"{_MODULE_C}.ScheduledWorkout.get_for_date", new=AsyncMock(return_value=[workout])),
            patch(f"{_MODULE_C}.TrainingLog.get_for_date", new=AsyncMock(return_value=[])),
        ):
            return await get_workout_compliance("A1")

    async def test_duration_100_pct_is_excellent(self):
        result = await self._run(planned_secs=3600, actual_secs=3600)
        assert result["compliance"]["overall"] == "excellent"
        assert result["compliance"]["duration_pct"] == 100

    async def test_duration_75_pct_is_good(self):
        result = await self._run(planned_secs=3600, actual_secs=2700)  # 75%
        assert result["compliance"]["overall"] == "good"
        assert result["compliance"]["duration_pct"] == 75

    async def test_duration_40_pct_is_missed(self):
        result = await self._run(planned_secs=3600, actual_secs=1440)  # 40%
        assert result["compliance"]["overall"] == "off_target"
        assert result["compliance"]["duration_pct"] == 40

    async def test_planned_and_actual_populated(self):
        result = await self._run(planned_secs=3600, actual_secs=3600)
        assert result["planned"]["duration_min"] == 60
        assert result["actual"]["duration_min"] == 60
        assert result["planned"]["name"] == "Zone 2 Ride"

    async def test_activity_id_and_date_in_result(self):
        result = await self._run(planned_secs=3600, actual_secs=3600)
        assert result["activity_id"] == "A1"
        assert result["date"] == "2026-04-10"


# ---------------------------------------------------------------------------
# get_workout_compliance — missing duration data → unknown
# ---------------------------------------------------------------------------


class TestGetWorkoutComplianceMissingDuration:
    async def test_planned_duration_none_returns_unknown(self):
        from mcp_server.tools.compliance import get_workout_compliance

        activity = _make_activity(activity_id="A1", activity_type="Ride", moving_time=3600)
        detail = _make_detail(activity_id="A1")
        workout = _make_workout(activity_type="Ride", moving_time=None)

        with (
            patch(f"{_MODULE_C}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_C}.get_session", return_value=_make_two_scalar_session_ctx(activity, detail)),
            patch(f"{_MODULE_C}.ScheduledWorkout.get_for_date", new=AsyncMock(return_value=[workout])),
            patch(f"{_MODULE_C}.TrainingLog.get_for_date", new=AsyncMock(return_value=[])),
        ):
            result = await get_workout_compliance("A1")

        assert result["compliance"]["overall"] == "unknown"

    async def test_actual_duration_none_returns_unknown(self):
        from mcp_server.tools.compliance import get_workout_compliance

        activity = _make_activity(activity_id="A1", activity_type="Ride", moving_time=None)
        detail = _make_detail(activity_id="A1")
        workout = _make_workout(activity_type="Ride", moving_time=3600)

        with (
            patch(f"{_MODULE_C}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_C}.get_session", return_value=_make_two_scalar_session_ctx(activity, detail)),
            patch(f"{_MODULE_C}.ScheduledWorkout.get_for_date", new=AsyncMock(return_value=[workout])),
            patch(f"{_MODULE_C}.TrainingLog.get_for_date", new=AsyncMock(return_value=[])),
        ):
            result = await get_workout_compliance("A1")

        assert result["compliance"]["overall"] == "unknown"


# ---------------------------------------------------------------------------
# get_workout_compliance — training_log_compliance included when log exists
# ---------------------------------------------------------------------------


class TestGetWorkoutComplianceTrainingLog:
    async def test_training_log_compliance_populated_when_log_present(self):
        from mcp_server.tools.compliance import get_workout_compliance

        activity = _make_activity(activity_id="A1", activity_type="Ride", moving_time=3600)
        detail = _make_detail(activity_id="A1")
        workout = _make_workout(activity_type="Ride", moving_time=3600)
        log = _make_log_entry(activity_id="A1", compliance="complete")

        with (
            patch(f"{_MODULE_C}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_C}.get_session", return_value=_make_two_scalar_session_ctx(activity, detail)),
            patch(f"{_MODULE_C}.ScheduledWorkout.get_for_date", new=AsyncMock(return_value=[workout])),
            patch(f"{_MODULE_C}.TrainingLog.get_for_date", new=AsyncMock(return_value=[log])),
        ):
            result = await get_workout_compliance("A1")

        assert result["training_log_compliance"] == "complete"

    async def test_training_log_compliance_none_when_no_log(self):
        from mcp_server.tools.compliance import get_workout_compliance

        activity = _make_activity(activity_id="A1", activity_type="Ride", moving_time=3600)
        detail = _make_detail(activity_id="A1")
        workout = _make_workout(activity_type="Ride", moving_time=3600)

        with (
            patch(f"{_MODULE_C}.get_current_user_id", return_value=1),
            patch(f"{_MODULE_C}.get_session", return_value=_make_two_scalar_session_ctx(activity, detail)),
            patch(f"{_MODULE_C}.ScheduledWorkout.get_for_date", new=AsyncMock(return_value=[workout])),
            patch(f"{_MODULE_C}.TrainingLog.get_for_date", new=AsyncMock(return_value=[])),
        ):
            result = await get_workout_compliance("A1")

        assert result["training_log_compliance"] is None


# ---------------------------------------------------------------------------
# _compute_compliance — pure unit tests (no DB, no async)
# ---------------------------------------------------------------------------


class TestComputeCompliance:
    """Direct unit tests for the _compute_compliance helper."""

    def _run(self, planned_min: int | None, actual_min: int | None) -> dict:
        from mcp_server.tools.compliance import _compute_compliance

        planned = (
            {"duration_min": planned_min, "name": "Test workout", "power_target": None}
            if planned_min is not None
            else None
        )
        actual = {"duration_min": actual_min, "avg_power": None}
        return _compute_compliance(planned, actual, None, None, None)

    def test_no_planned_returns_unplanned(self):
        from mcp_server.tools.compliance import _compute_compliance

        result = _compute_compliance(None, {"duration_min": 60}, None, None, None)
        assert result["overall"] == "unplanned"

    def test_both_durations_none_returns_unknown(self):
        result = self._run(60, None)
        assert result["overall"] == "unknown"

    def test_planned_duration_none_returns_unknown(self):
        """Scheduled workout exists but has no duration set → unknown, not unplanned."""
        from mcp_server.tools.compliance import _compute_compliance

        planned = {"duration_min": None, "name": "Test", "power_target": None}
        result = _compute_compliance(planned, {"duration_min": 60, "avg_power": None}, None, None, None)
        assert result["overall"] == "unknown"

    @pytest.mark.parametrize(
        "actual_pct,expected_rating",
        [
            (90, "excellent"),  # lower boundary of excellent
            (100, "excellent"),  # exact
            (110, "excellent"),  # upper boundary of excellent
            (70, "good"),  # lower boundary of good
            (130, "good"),  # upper boundary of good
            (50, "partial"),  # lower boundary of partial
            (150, "partial"),  # upper boundary of partial
            (49, "off_target"),  # just below partial
            (151, "off_target"),  # just above partial
        ],
    )
    def test_duration_pct_boundary_cases(self, actual_pct: int, expected_rating: str):
        planned_min = 100
        actual_min = actual_pct  # 1 min per % makes the math trivial
        result = self._run(planned_min, actual_min)
        assert (
            result["overall"] == expected_rating
        ), f"actual {actual_pct}% of planned expected '{expected_rating}', got '{result['overall']}'"

    def test_off_target_has_duration_pct(self):
        result = self._run(100, 40)
        assert result["overall"] == "off_target"
        assert result["duration_pct"] == 40

    def test_duration_pct_stored_in_result(self):
        result = self._run(100, 80)
        assert result["duration_pct"] == 80

    def test_excellent_has_no_note(self):
        result = self._run(100, 100)
        assert "note" not in result
