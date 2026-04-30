"""Unit tests for data/card_renderer.py — Pillow-based workout card renderer."""

import struct

import pytest

from data.card_renderer import (
    RaceRecapCardData,
    RaceSplit,
    WorkoutCardData,
    _build_metrics,
    _format_distance,
    _format_duration,
    _format_pace,
    _format_swim_pace,
    _goal_delta,
    render_race_recap_card,
    render_workout_card,
)

# ---------------------------------------------------------------------------
#  _format_distance
# ---------------------------------------------------------------------------


class TestFormatDistance:
    """_format_distance: meters vs km formatting, force_meters flag."""

    def test_below_1000m_shows_meters(self):
        assert _format_distance(500.0) == "500 m"

    def test_exactly_1000m_shows_1km(self):
        assert _format_distance(1000.0) == "1 km"

    def test_round_km_has_no_decimal(self):
        assert _format_distance(5000.0) == "5 km"

    def test_fractional_km_shows_two_decimals(self):
        assert _format_distance(5500.0) == "5.50 km"

    def test_fractional_km_precision(self):
        assert _format_distance(10250.0) == "10.25 km"

    def test_force_meters_overrides_km_conversion(self):
        """Swim distances always display in metres regardless of value."""
        assert _format_distance(1500.0, force_meters=True) == "1500 m"

    def test_force_meters_below_1000_unchanged(self):
        assert _format_distance(400.0, force_meters=True) == "400 m"

    def test_truncates_to_int(self):
        """Fractional metres are dropped — no sub-metre precision."""
        assert _format_distance(750.9) == "750 m"


# ---------------------------------------------------------------------------
#  _format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    """_format_duration: MM:SS without hours, H:MM:SS with hours."""

    def test_under_one_hour_no_hours_field(self):
        assert _format_duration(3599) == "59:59"

    def test_exactly_one_hour(self):
        assert _format_duration(3600) == "1:00:00"

    def test_multi_hour(self):
        assert _format_duration(7322) == "2:02:02"

    def test_zero_seconds(self):
        assert _format_duration(0) == "0:00"

    def test_minutes_and_seconds_zero_padded(self):
        assert _format_duration(65) == "1:05"

    def test_short_duration(self):
        assert _format_duration(5) == "0:05"


# ---------------------------------------------------------------------------
#  _format_pace
# ---------------------------------------------------------------------------


class TestFormatPace:
    """_format_pace: seconds per km → min:sec /km string."""

    def test_round_minutes(self):
        assert _format_pace(300.0) == "5:00 /km"

    def test_with_seconds(self):
        assert _format_pace(323.0) == "5:23 /km"

    def test_seconds_zero_padded(self):
        """Seconds below 10 must be zero-padded."""
        assert _format_pace(305.0) == "5:05 /km"

    def test_fast_pace(self):
        assert _format_pace(180.0) == "3:00 /km"


# ---------------------------------------------------------------------------
#  _format_swim_pace
# ---------------------------------------------------------------------------


class TestFormatSwimPace:
    """_format_swim_pace: seconds per 100m → min:sec /100m string."""

    def test_round_minutes(self):
        assert _format_swim_pace(120.0) == "2:00 /100m"

    def test_with_seconds(self):
        assert _format_swim_pace(95.0) == "1:35 /100m"

    def test_seconds_zero_padded(self):
        assert _format_swim_pace(63.0) == "1:03 /100m"

    def test_sub_minute(self):
        assert _format_swim_pace(58.0) == "0:58 /100m"


# ---------------------------------------------------------------------------
#  _build_metrics
# ---------------------------------------------------------------------------


class TestBuildMetrics:
    """_build_metrics: correct label/value order and sport-specific logic."""

    # --- run ---

    def test_run_order_distance_pace_time(self):
        data = WorkoutCardData(
            sport_type="Run",
            distance_m=10000.0,
            avg_pace_sec_per_km=300.0,
            duration_sec=3000,
        )
        metrics = _build_metrics(data)
        labels = [m[0] for m in metrics]
        assert labels == ["Distance", "Pace", "Time"]

    def test_run_pace_value(self):
        data = WorkoutCardData(sport_type="Run", avg_pace_sec_per_km=330.0, duration_sec=None)
        metrics = _build_metrics(data)
        pace_entry = next(m for m in metrics if m[0] == "Pace")
        assert pace_entry[1] == "5:30 /km"

    def test_run_distance_in_km(self):
        data = WorkoutCardData(sport_type="Run", distance_m=10000.0)
        metrics = _build_metrics(data)
        dist_entry = next(m for m in metrics if m[0] == "Distance")
        assert dist_entry[1] == "10 km"

    # --- swim ---

    def test_swim_distance_in_meters(self):
        """Swim distance must always display in metres, not km."""
        data = WorkoutCardData(sport_type="Swim", distance_m=1500.0, avg_pace_sec_per_km=3000.0)
        metrics = _build_metrics(data)
        dist_entry = next(m for m in metrics if m[0] == "Distance")
        assert dist_entry[1] == "1500 m"

    def test_swim_pace_per_100m(self):
        """Swim pace is converted from sec/km to sec/100m (÷10)."""
        # 3000 sec/km → 300 sec/100m → 5:00 /100m
        data = WorkoutCardData(sport_type="Swim", avg_pace_sec_per_km=3000.0)
        metrics = _build_metrics(data)
        pace_entry = next(m for m in metrics if m[0] == "Pace")
        assert pace_entry[1] == "5:00 /100m"

    # --- ride ---

    def test_ride_shows_power_not_pace(self):
        data = WorkoutCardData(
            sport_type="Ride",
            distance_m=40000.0,
            avg_pace_sec_per_km=200.0,
            avg_power=250,
            duration_sec=3600,
        )
        metrics = _build_metrics(data)
        labels = [m[0] for m in metrics]
        assert "Power" in labels
        assert "Pace" not in labels

    def test_ride_power_value(self):
        data = WorkoutCardData(sport_type="Ride", avg_power=275)
        metrics = _build_metrics(data)
        power_entry = next(m for m in metrics if m[0] == "Power")
        assert power_entry[1] == "275 W"

    def test_ride_order_distance_power_time(self):
        data = WorkoutCardData(
            sport_type="Ride",
            distance_m=40000.0,
            avg_power=250,
            duration_sec=3600,
        )
        metrics = _build_metrics(data)
        labels = [m[0] for m in metrics]
        assert labels == ["Distance", "Power", "Time"]

    # --- missing fields ---

    def test_no_metrics_when_all_none(self):
        data = WorkoutCardData(sport_type="Run")
        assert _build_metrics(data) == []

    def test_only_distance(self):
        data = WorkoutCardData(sport_type="Run", distance_m=5000.0)
        labels = [m[0] for m in _build_metrics(data)]
        assert labels == ["Distance"]

    def test_only_duration(self):
        data = WorkoutCardData(sport_type="Run", duration_sec=1800)
        labels = [m[0] for m in _build_metrics(data)]
        assert labels == ["Time"]

    def test_fallback_power_without_pace_for_run(self):
        """Power is shown as fallback when pace is absent for non-bike sports."""
        data = WorkoutCardData(sport_type="Run", avg_power=300)
        metrics = _build_metrics(data)
        labels = [m[0] for m in metrics]
        assert "Power" in labels


# ---------------------------------------------------------------------------
#  render_workout_card — PNG output
# ---------------------------------------------------------------------------


def _png_dimensions(png_bytes: bytes) -> tuple[int, int]:
    """Read width and height from the PNG IHDR chunk without Pillow."""
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n", "Not a PNG file"
    # IHDR starts at byte 8: 4 (length) + 4 (type) + 4 (width) + 4 (height) + ...
    width = struct.unpack(">I", png_bytes[16:20])[0]
    height = struct.unpack(">I", png_bytes[20:24])[0]
    return width, height


class TestRenderWorkoutCard:
    """render_workout_card: valid PNG bytes, correct dimensions, size constraints."""

    @pytest.fixture
    def base_data(self):
        return WorkoutCardData(
            sport_type="Run",
            distance_m=10000.0,
            duration_sec=3000,
            avg_pace_sec_per_km=300.0,
        )

    def test_returns_bytes(self, base_data):
        result = render_workout_card(base_data)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_valid_png_signature(self, base_data):
        result = render_workout_card(base_data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_dimensions_1080x1920(self, base_data):
        result = render_workout_card(base_data)
        width, height = _png_dimensions(result)
        assert width == 1080
        assert height == 1920

    def test_file_size_under_2mb(self, base_data):
        result = render_workout_card(base_data)
        assert len(result) < 2 * 1024 * 1024

    def test_with_gps_data(self):
        """GPS polyline path should not crash and still produce valid PNG."""
        latlng = [
            (48.8566, 2.3522),
            (48.8600, 2.3600),
            (48.8650, 2.3700),
            (48.8700, 2.3800),
        ]
        data = WorkoutCardData(
            sport_type="Run",
            distance_m=5000.0,
            duration_sec=1500,
            latlng=latlng,
        )
        result = render_workout_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = _png_dimensions(result)
        assert width == 1080
        assert height == 1920

    def test_without_gps_data(self):
        """No GPS: track area stays empty, rest of the layout still renders."""
        data = WorkoutCardData(
            sport_type="Ride",
            distance_m=40000.0,
            duration_sec=3600,
            avg_power=250,
            latlng=None,
        )
        result = render_workout_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_single_gps_point_does_not_crash(self):
        """A single GPS point is below the 2-point minimum — must not raise."""
        data = WorkoutCardData(
            sport_type="Run",
            distance_m=1000.0,
            latlng=[(48.8566, 2.3522)],
        )
        result = render_workout_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_latlng_with_none_values_filtered(self):
        """Issue #249 regression: Intervals.icu GPS streams occasionally emit
        ``(None, None)`` or ``(lat, None)`` sentinel samples from GPS dropouts
        (tunnels, indoor segments). ``min()`` / ``max()`` crashed on mixed
        ``float`` vs ``None`` comparison. Renderer must now filter those
        points before bbox math and still produce a valid PNG.
        """
        data = WorkoutCardData(
            sport_type="Run",
            distance_m=5000.0,
            duration_sec=1800,
            latlng=[
                (48.8566, 2.3522),
                (48.8570, 2.3530),
                (None, None),  # GPS dropout
                (48.8575, None),  # half-populated sample
                (None, 2.3540),
                (48.8580, 2.3545),
            ],
        )
        result = render_workout_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_latlng_all_none_falls_back_to_no_gps(self):
        """All-None latlng must render bit-for-bit identically to ``latlng=None``
        — after filtering, neither has any drawable points, so the no-GPS
        fallback (sport emoji) must kick in the same way.
        """
        all_none_data = WorkoutCardData(
            sport_type="Run",
            distance_m=5000.0,
            duration_sec=1800,
            latlng=[(None, None), (None, None), (None, None)],
        )
        no_gps_data = WorkoutCardData(
            sport_type="Run",
            distance_m=5000.0,
            duration_sec=1800,
            latlng=None,
        )
        assert render_workout_card(all_none_data) == render_workout_card(no_gps_data)

    def test_with_ai_text(self):
        data = WorkoutCardData(
            sport_type="Run",
            distance_m=10000.0,
            duration_sec=3000,
            ai_text="Great aerobic base session. Heart rate stayed in Z2 throughout.",
        )
        result = render_workout_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_without_ai_text(self, base_data):
        """ai_text=None must not raise."""
        base_data.ai_text = None
        result = render_workout_card(base_data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_long_ai_text_truncated_gracefully(self):
        """Very long AI text must not overflow or crash; card stays valid."""
        long_text = "word " * 200
        data = WorkoutCardData(sport_type="Run", distance_m=10000.0, ai_text=long_text)
        result = render_workout_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_swim_card(self):
        data = WorkoutCardData(
            sport_type="Swim",
            distance_m=2000.0,
            duration_sec=2400,
            avg_pace_sec_per_km=1200.0,
        )
        result = render_workout_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        assert _png_dimensions(result) == (1080, 1920)

    def test_other_sport_card(self):
        data = WorkoutCardData(sport_type="Other", duration_sec=3600)
        result = render_workout_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_unknown_sport_falls_back_gracefully(self):
        """Unknown sport type should not raise — falls back to Other color/label."""
        data = WorkoutCardData(sport_type="Yoga", duration_sec=1800)
        result = render_workout_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
#  _goal_delta — race recap goal-delta formatting
# ---------------------------------------------------------------------------


class TestGoalDelta:
    def test_under_goal_is_negative_and_green(self):
        text, color = _goal_delta(3500, 3600)
        assert text.startswith("−")
        assert "vs goal" in text
        assert color == "#00D26A"

    def test_over_goal_is_positive_and_red(self):
        text, color = _goal_delta(3700, 3600)
        assert text.startswith("+")
        assert color == "#FF6B6B"

    def test_on_goal_renders_explicit_label(self):
        text, color = _goal_delta(3600, 3600)
        assert text == "On goal"
        assert color == "#00D26A"

    def test_missing_goal_returns_empty(self):
        text, _color = _goal_delta(3600, None)
        assert text == ""

    def test_missing_finish_returns_empty(self):
        text, _color = _goal_delta(None, 3600)
        assert text == ""


# ---------------------------------------------------------------------------
#  render_race_recap_card — 1080x1080 PNG output
# ---------------------------------------------------------------------------


class TestRenderRaceRecapCard:
    """render_race_recap_card: square dimensions, valid PNG, graceful degradation."""

    @pytest.fixture
    def tri_data(self):
        return RaceRecapCardData(
            race_name="Ironman 70.3 Belgrade",
            sport_type="Triathlon",
            finish_time_sec=18540,  # 5:09:00
            goal_time_sec=18900,  # 5:15:00
            distance_m=70300.0,
            splits=[
                RaceSplit(label="Swim", time_sec=1980, distance_m=1900.0),
                RaceSplit(label="T1", time_sec=180),
                RaceSplit(label="Bike", time_sec=10800, distance_m=90000.0),
                RaceSplit(label="T2", time_sec=180),
                RaceSplit(label="Run", time_sec=5400, distance_m=21100.0),
            ],
            avg_hr_quarters=[148, 152, 158, 165],
            rpe=8,
            race_day_tsb=-3.0,
            race_day_recovery_score=0.78,
            ai_text="Smart pacing on the bike held HR steady through the run. Cap caffeine at half this race-day dose next time.",
        )

    def test_dimensions_1080x1080(self, tri_data):
        result = render_race_recap_card(tri_data)
        width, height = _png_dimensions(result)
        assert width == 1080
        assert height == 1080

    def test_returns_valid_png(self, tri_data):
        result = render_race_recap_card(tri_data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(result) > 0

    def test_file_size_under_2mb(self, tri_data):
        result = render_race_recap_card(tri_data)
        assert len(result) < 2 * 1024 * 1024

    def test_idempotent_same_inputs(self, tri_data):
        """Re-running with identical inputs must produce byte-identical PNGs.

        This is the contract behind the END-65 acceptance criterion
        ("Re-running is idempotent"). The actor decides not to dedup the
        send, so the renderer is what guarantees a stable artifact.
        """
        first = render_race_recap_card(tri_data)
        second = render_race_recap_card(tri_data)
        assert first == second

    def test_minimal_data_no_crash(self):
        """With only the bare race name + finish time, the card still renders.

        Real-world fallback: an athlete whose Race row is half-populated
        (no goal time, no splits, indoor pool with no HR streams) must not
        get an empty document — they get a thin but valid card.
        """
        data = RaceRecapCardData(
            race_name="Local 5K",
            sport_type="Run",
            finish_time_sec=1320,
        )
        result = render_race_recap_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        assert _png_dimensions(result) == (1080, 1080)

    def test_long_race_name_wraps_within_two_lines(self):
        """Ironman titles can blow past the canvas width — wrapper keeps the
        layout stable by clipping to two lines with an ellipsis.
        """
        data = RaceRecapCardData(
            race_name="Challenge Roth Long Distance Triathlon Championship Race 2026 Edition",
            sport_type="Triathlon",
            finish_time_sec=36000,
        )
        result = render_race_recap_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_partial_hr_quarters_render(self):
        """Half-populated HR quartiles still render — the missing slot shows
        a muted bar with an em-dash, not a crash.
        """
        data = RaceRecapCardData(
            race_name="Tune-up 10K",
            sport_type="Run",
            finish_time_sec=2400,
            avg_hr_quarters=[155, None, None, 168],
        )
        result = render_race_recap_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_recovery_score_clamped(self):
        """A buggy upstream recovery value > 1.0 must clamp to 100, not 140."""
        data = RaceRecapCardData(
            race_name="Sprint",
            sport_type="Run",
            finish_time_sec=1500,
            race_day_recovery_score=1.4,
        )
        # No exception, valid PNG, and the renderer never raises on the
        # bad input — the clamp is internal.
        result = render_race_recap_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"

    def test_more_than_six_splits_truncated(self):
        """Long lap lists are clipped so the splits panel cannot push the
        stat tiles or AI narrative below the canvas.
        """
        splits = [RaceSplit(label=f"K{i + 1}", time_sec=300 + i * 5) for i in range(20)]
        data = RaceRecapCardData(
            race_name="Marathon",
            sport_type="Run",
            finish_time_sec=14400,
            splits=splits,
        )
        result = render_race_recap_card(data)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
