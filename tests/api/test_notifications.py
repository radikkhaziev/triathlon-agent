"""Tests for steps 10-12: post-activity notifications, evening report, morning DFA context."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from bot.formatter import build_evening_message, build_post_activity_message, format_duration, sport_emoji
from tasks.formatter import build_ramp_test_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_activity(**overrides):
    defaults = {
        "id": "i100",
        "start_date_local": "2026-03-24",
        "type": "Ride",
        "icu_training_load": 85.0,
        "moving_time": 4800,  # 1h20m
        "average_hr": 138.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_hrv(**overrides):
    defaults = {
        "activity_id": "i100",
        "date": "2026-03-24",
        "activity_type": "Ride",
        "processing_status": "processed",
        "hrv_quality": "good",
        "artifact_pct": 2.5,
        "rr_count": 3000,
        "dfa_a1_mean": 0.68,
        "dfa_a1_warmup": 0.92,
        "hrvt1_hr": 142.0,
        "hrvt1_power": 180.0,
        "hrvt1_pace": None,
        "hrvt2_hr": 165.0,
        "hrvt2_pace": None,
        "hrvt2_power": None,
        "ra_pct": 3.2,
        "pa_today": 185.0,
        "da_pct": -2.1,
        "threshold_r_squared": 0.85,
        "threshold_confidence": "high",
        "dfa_timeseries": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_wellness(**overrides):
    defaults = {
        "id": "2026-03-24",
        "hrv": 45.2,
        "resting_hr": 42,
        "sleep_score": 85.0,
        "sleep_secs": 28800,
        "recovery_score": 72.0,
        "recovery_category": "good",
        "recovery_recommendation": "zone2_ok",
        "readiness_score": 72,
        "readiness_level": "green",
        "ess_today": 95.3,
        "banister_recovery": 68.0,
        "ctl": 55.0,
        "atl": 60.0,
        "ramp_rate": 3.5,
        "ai_recommendation": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Tests: duration & emoji helpers
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_hours_and_minutes(self):
        assert format_duration(4800) == "1h20m"

    def test_minutes_only(self):
        assert format_duration(2400) == "40m"

    def test_none(self):
        assert format_duration(None) == "—"

    def test_zero(self):
        assert format_duration(0) == "—"

    def test_exact_hour(self):
        assert format_duration(3600) == "1h00m"


class TestSportEmoji:
    def test_ride(self):
        assert sport_emoji("Ride") == "🚴"

    def test_run(self):
        assert sport_emoji("Run") == "🏃"

    def test_swim(self):
        assert sport_emoji("Swim") == "🏊"

    def test_other(self):
        assert sport_emoji("Other") == "🏋️"

    def test_none(self):
        assert sport_emoji(None) == "🏋️"

    def test_unknown(self):
        assert sport_emoji("Yoga") == "🏋️"


# ---------------------------------------------------------------------------
# Tests: post-activity message
# ---------------------------------------------------------------------------


class TestBuildPostActivityMessage:
    def test_full_message(self):
        msg = build_post_activity_message(_make_activity(), _make_hrv())
        assert "🚴" in msg
        assert "Ride" in msg
        assert "1h20m" in msg
        assert "TSS 85" in msg
        assert "DFA a1:" in msg
        assert "0.92 (warmup)" in msg
        assert "0.68 (avg)" in msg
        assert "Ra: +3.2%" in msg
        assert "✅" in msg
        assert "HRVT1: 142 bpm / 180W" in msg

    def test_run_activity(self):
        activity = _make_activity(type="Run", moving_time=2400)
        hrv = _make_hrv(
            activity_type="Run",
            hrvt1_power=None,
            hrvt1_pace="5:30",
        )
        msg = build_post_activity_message(activity, hrv)
        assert "🏃" in msg
        assert "40m" in msg
        assert "5:30" in msg

    def test_under_recovered_ra(self):
        hrv = _make_hrv(ra_pct=-8.5)
        msg = build_post_activity_message(_make_activity(), hrv)
        assert "⚠️" in msg
        assert "-8.5%" in msg

    def test_no_ra(self):
        hrv = _make_hrv(ra_pct=None)
        msg = build_post_activity_message(_make_activity(), hrv)
        assert "Ra:" not in msg

    def test_no_hrvt1(self):
        hrv = _make_hrv(hrvt1_hr=None, hrvt1_power=None, hrvt1_pace=None)
        msg = build_post_activity_message(_make_activity(), hrv)
        assert "HRVT1" not in msg

    def test_da_shown_for_long_activity(self):
        activity = _make_activity(moving_time=4800)  # 80 min
        hrv = _make_hrv(da_pct=-2.1)
        msg = build_post_activity_message(activity, hrv)
        assert "Da: -2.1%" in msg

    def test_da_hidden_for_short_activity(self):
        activity = _make_activity(moving_time=2000)  # ~33 min
        hrv = _make_hrv(da_pct=-2.1)
        msg = build_post_activity_message(activity, hrv)
        assert "Da:" not in msg

    def test_no_tss(self):
        activity = _make_activity(icu_training_load=None)
        msg = build_post_activity_message(activity, _make_hrv())
        assert "TSS" not in msg

    def test_minimal_data(self):
        """HRV with only mean DFA, no warmup/ra/hrvt1/da.

        Activity carries ``average_hr=138.0`` (default fixture) → adds a 💓 summary
        line on top of the legacy header + DFA. ``detail=None`` so distance / EF /
        CTL / zone-bar blocks stay out of the minimal path.
        """
        hrv = _make_hrv(
            dfa_a1_warmup=None,
            ra_pct=None,
            hrvt1_hr=None,
            hrvt1_power=None,
            hrvt1_pace=None,
            da_pct=None,
        )
        msg = build_post_activity_message(_make_activity(), hrv)
        lines = msg.strip().split("\n")
        assert len(lines) == 3  # header + 💓 summary + DFA a1
        assert "💓 138" in msg
        assert "DFA a1: 0.68 (avg)" in msg


def _make_detail(**overrides):
    """Shared ActivityDetail sentinel — defaults model an indoor Ride.

    ``hr_zone_times`` / ``power_zone_times`` are short non-empty lists so zone
    bars render. EF / decoupling / VI are populated; weather + CTL/ATL are
    overridable per test.
    """
    defaults = {
        "distance": 28500.0,
        "elevation_gain": 0.0,
        "max_hr": 150,
        "avg_power": 131,
        "normalized_power": 139,
        "efficiency_factor": 1.05,
        "decoupling": 4.5,
        "variability_index": 1.06,
        "avg_cadence": 93.0,
        "avg_stride": None,
        "pace": None,
        "ctl_snapshot": 18.9,
        "atl_snapshot": 38.3,
        "polarization_index": None,
        "hr_zone_times": [1054, 2253, 0, 0, 0, 0, 0],
        "power_zone_times": [1619, 556, 1027, 92, 4, 1, 8],
        "pace_zone_times": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_weather(**overrides):
    defaults = {
        "avg_temp_c": 18.0,
        "avg_feels_like_c": 17.0,
        "avg_wind_speed_mps": 3.3,
        "avg_wind_gust_mps": 5.0,
        "prevailing_wind_deg": 67,
        "headwind_pct": 35.0,
        "tailwind_pct": 20.0,
        "max_rain_mm": 0.0,
        "max_snow_mm": 0.0,
        "avg_clouds": 0.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_achievement(**overrides):
    defaults = {
        "type": "BEST_POWER",
        "value": 500.0,
        "secs": 5,
        "ftp_at_time": 215,
        "ctl_at_time": 18.9,
        "extra": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestPostActivityEnrichment:
    """New blocks: distance/elevation, EF/decoupling, weather, CTL snapshot, achievements, zones."""

    def test_distance_and_elevation_in_header(self):
        activity = _make_activity(type="Run", moving_time=2700)  # 45m
        detail = _make_detail(distance=8500.0, elevation_gain=120.0, hr_zone_times=None, power_zone_times=None)
        msg = build_post_activity_message(activity, _make_hrv(activity_type="Run"), detail=detail)
        assert "8.50 km" in msg
        assert "↑120 m" in msg

    def test_sub_km_distance_shown_in_meters(self):
        """Pool sessions / warm-up jogs: distance < 1 km → meters, no decimal."""
        activity = _make_activity(type="Swim", moving_time=1800)
        detail = _make_detail(distance=750.0, elevation_gain=None, hr_zone_times=None, power_zone_times=None)
        msg = build_post_activity_message(activity, _make_hrv(), detail=detail)
        assert "750 m" in msg
        assert ".75 km" not in msg  # decimal form would be wrong for sub-km

    def test_ride_summary_uses_normalized_power(self):
        msg = build_post_activity_message(_make_activity(type="Ride"), _make_hrv(), detail=_make_detail())
        # NP differs from avg → both shown.
        assert "131W (NP 139W)" in msg
        assert "💓 138" in msg
        assert "150" in msg  # max_hr

    def test_run_summary_derives_pace(self):
        """Pace = moving_time / (distance_m/1000) — derived, not stored."""
        activity = _make_activity(type="Run", moving_time=2700)  # 45m
        detail = _make_detail(distance=8500.0, hr_zone_times=None, power_zone_times=None)
        msg = build_post_activity_message(activity, _make_hrv(activity_type="Run"), detail=detail)
        # 2700 / 8.5 = 317.6 sec/km = 5:18
        assert "5:18/km" in msg

    def test_swim_pace_per_100m(self):
        activity = _make_activity(type="Swim", moving_time=1800)
        detail = _make_detail(distance=1500.0, hr_zone_times=None, power_zone_times=None)
        msg = build_post_activity_message(activity, _make_hrv(), detail=detail)
        # 1800/15 = 120 sec/km = 12 sec/100m = wait, 1800/1.5km = 1200 sec/km, /10 = 2:00/100m
        assert "2:00/100m" in msg

    def test_efficiency_block(self):
        msg = build_post_activity_message(_make_activity(), _make_hrv(), detail=_make_detail())
        assert "EF 1.05" in msg
        assert "Drift 4.5%" in msg
        assert "🟢" in msg  # decoupling <5% → green
        assert "VI 1.06" in msg

    def test_decoupling_red_threshold(self):
        detail = _make_detail(decoupling=12.5)
        msg = build_post_activity_message(_make_activity(), _make_hrv(), detail=detail)
        assert "12.5%" in msg
        assert "🔴" in msg

    def test_decoupling_yellow_threshold(self):
        detail = _make_detail(decoupling=7.5)
        msg = build_post_activity_message(_make_activity(), _make_hrv(), detail=detail)
        assert "7.5%" in msg
        assert "🟡" in msg

    def test_fitness_snapshot(self):
        detail = _make_detail(ctl_snapshot=18.9, atl_snapshot=38.3)
        msg = build_post_activity_message(_make_activity(), _make_hrv(), detail=detail)
        # TSB = 18.9 - 38.3 = -19.4 → -19
        assert "CTL 19" in msg
        assert "ATL 38" in msg
        assert "TSB -19" in msg

    def test_fitness_snapshot_hidden_without_data(self):
        detail = _make_detail(ctl_snapshot=None, atl_snapshot=None)
        msg = build_post_activity_message(_make_activity(), _make_hrv(), detail=detail)
        assert "CTL" not in msg
        assert "ATL" not in msg

    def test_weather_outdoor(self):
        weather = _make_weather()
        msg = build_post_activity_message(_make_activity(type="Run"), _make_hrv(activity_type="Run"), weather=weather)
        assert "🌡 18°C" in msg
        # Default test locale is RU (no set_language called) → Russian source strings render as-is.
        assert "ощущается" in msg  # avg_feels_like differs from avg_temp by 1
        assert "💨 12 km/h" in msg  # 3.3 m/s * 3.6 = 11.88 → 12
        assert "встречный" in msg  # 35% > 25% threshold

    def test_weather_skipped_when_none(self):
        msg = build_post_activity_message(_make_activity(), _make_hrv())
        assert "🌡" not in msg
        assert "💨" not in msg

    def test_weather_rain(self):
        weather = _make_weather(max_rain_mm=2.5)
        msg = build_post_activity_message(_make_activity(), _make_hrv(), weather=weather)
        assert "🌧 2.5 mm" in msg

    def test_polarization_long_workout(self):
        activity = _make_activity(moving_time=4800)  # 80 min ≥60
        detail = _make_detail(polarization_index=1.85)
        msg = build_post_activity_message(activity, _make_hrv(), detail=detail)
        assert "PI 1.85" in msg

    def test_polarization_hidden_short_workout(self):
        """PI only meaningful for endurance sessions ≥60 min."""
        activity = _make_activity(moving_time=1800)  # 30 min
        detail = _make_detail(polarization_index=1.85)
        msg = build_post_activity_message(activity, _make_hrv(), detail=detail)
        assert "PI " not in msg

    def test_achievement_power_pr(self):
        ach = _make_achievement(type="BEST_POWER", value=500.0, secs=5)
        msg = build_post_activity_message(_make_activity(), _make_hrv(), achievements=[ach])
        assert "🏆" in msg
        assert "5s PR 500 W" in msg

    def test_achievement_ftp_change(self):
        ach = _make_achievement(
            type="FTP_CHANGE",
            value=215.0,
            secs=None,
            ftp_at_time=215,
            extra={"delta": 5},
        )
        msg = build_post_activity_message(_make_activity(), _make_hrv(), achievements=[ach])
        assert "⚡ FTP" in msg
        assert "+5" in msg
        assert "215" in msg

    def test_achievement_5min_pr(self):
        ach = _make_achievement(value=320.0, secs=300)
        msg = build_post_activity_message(_make_activity(), _make_hrv(), achievements=[ach])
        assert "5m PR 320 W" in msg

    def test_achievements_capped_at_four(self):
        many = [_make_achievement(value=float(100 + i), secs=5) for i in range(8)]
        msg = build_post_activity_message(_make_activity(), _make_hrv(), achievements=many)
        assert msg.count("🏆") == 4

    def test_achievement_ftp_change_promoted_above_power_prs(self):
        """FTP_CHANGE leads the block even if a flood of power PRs precedes it in DB order.

        Bulk-insert under one webhook gives every row the same ``created_at`` —
        the DB-default sort can drown a big FTP bump under 4 small power PRs.
        Priority sort fixes this without touching ``ActivityAchievement.save_bulk``.
        """
        ftp = _make_achievement(type="FTP_CHANGE", value=215.0, secs=None, ftp_at_time=215, extra={"delta": 5})
        prs = [_make_achievement(value=float(200 + i), secs=5) for i in range(5)]
        # FTP last in input order → must be first in output thanks to the priority sort.
        msg = build_post_activity_message(_make_activity(), _make_hrv(), achievements=prs + [ftp])
        first_ach_line = next(line for line in msg.split("\n") if "🏆" in line or "⚡ FTP" in line)
        assert first_ach_line.startswith("⚡ FTP")

    def test_achievement_power_prs_sorted_by_watts_desc(self):
        """Within BEST_POWER group: highest-watts PR first (headline number leads)."""
        prs = [
            _make_achievement(value=250.0, secs=300),  # 5min @ 250W
            _make_achievement(value=500.0, secs=5),  # 5s @ 500W — should lead
            _make_achievement(value=350.0, secs=60),  # 1min @ 350W
        ]
        msg = build_post_activity_message(_make_activity(), _make_hrv(), achievements=prs)
        achievement_lines = [line for line in msg.split("\n") if "🏆" in line]
        assert achievement_lines[0].endswith("500 W")  # 5s/500W headline
        assert achievement_lines[1].endswith("350 W")
        assert achievement_lines[2].endswith("250 W")

    def test_hr_zone_bar_for_ride(self):
        msg = build_post_activity_message(_make_activity(type="Ride"), _make_hrv(), detail=_make_detail())
        assert "HR  " in msg
        assert "Pwr " in msg
        # Hard zones (Z3-Z7) have 0 time in the HR zones → skipped in label row;
        # Z1 and Z2 surface.
        assert "Z1" in msg
        assert "Z2" in msg

    def test_pace_zone_bar_for_run(self):
        detail = _make_detail(
            pace_zone_times=[1200, 600, 300, 60, 0],
            power_zone_times=None,
            hr_zone_times=[800, 700, 300, 60, 0],
        )
        msg = build_post_activity_message(_make_activity(type="Run"), _make_hrv(activity_type="Run"), detail=detail)
        assert "HR  " in msg
        assert "Pace" in msg

    def test_zone_bar_full_width_padded(self):
        """Bar always renders at _BAR_WIDTH chars — no gaps left at the right edge."""
        from tasks.formatter import _BAR_WIDTH, _format_zone_bar

        # One zone with all the time → bar fills with █ all the way.
        bars = _format_zone_bar([3600, 0, 0, 0, 0], "HR  ")
        assert len(bars) == 2
        bar_line = bars[0]
        # Strip the leading "HR  " label + spaces, then check the bar width.
        bar = bar_line[len("HR  ") + 2 :]  # "  " separator
        assert len(bar) == _BAR_WIDTH

    def test_zone_bar_proportional_segments(self):
        """Mixed zones → bar shows █ (full) for the dominant zone, slivers for small ones."""
        from tasks.formatter import _format_zone_bar

        # 75% / 20% / 5% split.
        bars = _format_zone_bar([2700, 720, 180, 0, 0], "Pwr ")
        bar = bars[0].split("  ", 1)[1]
        # Dominant zone gets a long run of solid blocks; minor zones add partial blocks.
        assert "█" in bar
        # Padding shouldn't be needed for high totals but trailing chars must exist.
        assert len(bar) >= 18

    def test_zone_bar_skipped_when_empty(self):
        detail = _make_detail(hr_zone_times=[0, 0, 0, 0, 0], power_zone_times=None)
        msg = build_post_activity_message(_make_activity(), _make_hrv(), detail=detail)
        assert "HR  " not in msg
        assert "Pwr " not in msg

    def test_legacy_signature_still_works(self):
        """Old callers that pass only (activity, hrv) keep working — back-compat."""
        msg = build_post_activity_message(_make_activity(), _make_hrv())
        assert "🚴 Ride 1h20m" in msg
        assert "TSS 85" in msg


# ---------------------------------------------------------------------------
# Tests: ramp test message
# ---------------------------------------------------------------------------


class TestBuildRampTestMessage:
    """build_ramp_test_message returns (message, show_button, auto_update_fired).

    Confidence tiers (per RAMP_TEST_BIKE_SPEC §8 / `data/db/dto.py` constants):
      - high   (R² ≥ 0.85): auto_update_fired=True, show_button=False
      - medium (0.70 ≤ R² < 0.85): show_button=True, auto_update_fired=False
      - low    (R² < 0.70): both False, soft «низкое R²» hint
    Drift gate is absolute: 3 bpm / 5 s/km / 5 W.
    """

    def test_medium_r2_with_drift_shows_button(self):
        # HRVT2=172 vs 153 = Δ+19 bpm (>3 bpm gate). R²=0.80 → medium tier → button.
        hrv = _make_hrv(
            activity_type="Run",
            hrvt1_hr=157.0,
            hrvt1_power=None,
            hrvt1_pace="5:21",
            hrvt2_hr=172.0,
            hrvt2_pace="4:50",
            threshold_r_squared=0.80,
        )
        msg, show_button, auto = build_ramp_test_message(_make_activity(type="Run"), hrv, config_lthr=153)
        assert show_button is True
        assert auto is False
        assert "HRVT1: 157 bpm" in msg
        assert "HRVT2: 172 bpm" in msg
        assert "Δ +19 bpm" in msg

    def test_high_r2_with_drift_auto_updates(self):
        # Same drift but R²=0.90 → high tier → auto_update_fired, no button.
        hrv = _make_hrv(
            activity_type="Run",
            hrvt1_hr=157.0,
            hrvt1_power=None,
            hrvt2_hr=172.0,
            threshold_r_squared=0.90,
        )
        msg, show_button, auto = build_ramp_test_message(_make_activity(type="Run"), hrv, config_lthr=153)
        assert show_button is False
        assert auto is True
        assert "авто-обновление" in msg.lower()

    def test_within_tolerance_no_button(self):
        # HRVT2=155 vs 153 = Δ+2 bpm — under 3 bpm gate.
        hrv = _make_hrv(hrvt1_hr=140.0, hrvt1_power=None, hrvt2_hr=155.0)
        _, show_button, auto = build_ramp_test_message(_make_activity(type="Run"), hrv, config_lthr=153)
        assert show_button is False
        assert auto is False

    def test_low_r_squared_blocks_button(self):
        """R² < 0.70 → button hidden even with sizable drift, soft hint shown."""
        hrv = _make_hrv(
            hrvt1_hr=157.0,
            hrvt1_power=None,
            hrvt2_hr=172.0,  # Δ+19 bpm
            threshold_r_squared=0.50,
        )
        msg, show_button, auto = build_ramp_test_message(_make_activity(type="Run"), hrv, config_lthr=153)
        assert show_button is False
        assert auto is False
        assert "R²" in msg or "ramp test" in msg.lower()

    def test_detection_failed_shows_reason_and_advice(self):
        hrv = _make_hrv(
            hrvt1_hr=None,
            hrvt1_power=None,
            hrvt1_pace=None,
            hrvt2_hr=None,
            threshold_r_squared=None,
            threshold_confidence=None,
        )
        reason = {"code": "noisy_fit", "r_squared": 0.33}
        msg, show_button, auto = build_ramp_test_message(
            _make_activity(type="Run"), hrv, config_lthr=153, failure_reason=reason
        )
        assert show_button is False
        assert auto is False
        assert "0.33" in msg
        assert "тредмилл" in msg.lower() or "treadmill" in msg.lower()

    def test_detection_failed_advice_per_code(self):
        """Each known failure code emits its own actionable advice line."""
        hrv = _make_hrv(hrvt1_hr=None, hrvt1_power=None, hrvt1_pace=None, hrvt2_hr=None)
        msg, _, _ = build_ramp_test_message(
            _make_activity(type="Run"),
            hrv,
            config_lthr=153,
            failure_reason={"code": "too_few_points", "count": 8},
        )
        assert "30+" in msg
        msg, _, _ = build_ramp_test_message(
            _make_activity(type="Run"),
            hrv,
            config_lthr=153,
            failure_reason={"code": "a1_range_low", "min_a1": 0.85},
        )
        assert "темп" in msg.lower()
        msg, _, _ = build_ramp_test_message(
            _make_activity(type="Run"),
            hrv,
            config_lthr=153,
            failure_reason={"code": "positive_slope", "slope": 0.05},
        )
        assert "chest strap" in msg.lower() or "strap" in msg.lower()

    def test_detection_failed_no_reason(self):
        hrv = _make_hrv(hrvt1_hr=None, hrvt1_power=None, hrvt1_pace=None, hrvt2_hr=None)
        _, show_button, auto = build_ramp_test_message(_make_activity(type="Run"), hrv, config_lthr=153)
        assert show_button is False
        assert auto is False

    def test_pace_drift_lights_button_when_lthr_clean(self):
        """LTHR matches config but pace shows >5 s/km drift → button shown (medium R²)."""
        hrv = _make_hrv(
            hrvt1_hr=140.0,
            hrvt1_power=None,
            hrvt1_pace="4:55",
            hrvt2_hr=153.0,  # exact match → no LTHR drift
            hrvt2_pace="4:20",
            threshold_r_squared=0.80,  # medium tier
        )
        msg, show_button, auto = build_ramp_test_message(
            _make_activity(type="Run"),
            hrv,
            config_lthr=153,
            config_threshold_pace=295.0,
            hrvt2_pace_sec=260,  # Δ -35 s/km
        )
        assert show_button is True
        assert auto is False
        assert "threshold pace" in msg.lower()
        assert "4:55" in msg

    def test_both_drifts_button_shown_once(self):
        """LTHR and pace both drift, medium R² — single button surfaces."""
        hrv = _make_hrv(
            hrvt1_hr=155.0,
            hrvt1_power=None,
            hrvt1_pace="5:00",
            hrvt2_hr=170.0,  # Δ+17 bpm
            hrvt2_pace="4:20",
            threshold_r_squared=0.80,
        )
        msg, show_button, _ = build_ramp_test_message(
            _make_activity(type="Run"),
            hrv,
            config_lthr=153,
            config_threshold_pace=295.0,
            hrvt2_pace_sec=260,  # Δ-35 s/km
        )
        assert show_button is True
        assert "низкое R²" not in msg

    def test_pace_within_tolerance_no_button(self):
        """Both metrics within absolute gate (≤3 bpm / ≤5 s/km) — no button."""
        hrv = _make_hrv(
            hrvt1_hr=140.0,
            hrvt1_power=None,
            hrvt1_pace="5:30",
            hrvt2_hr=155.0,
            hrvt2_pace="4:54",  # 294 s/km, vs config 295 → Δ-1 s/km < 5
        )
        _, show_button, _ = build_ramp_test_message(
            _make_activity(type="Run"),
            hrv,
            config_lthr=153,  # Δ+2 bpm < 3
            config_threshold_pace=295.0,
            hrvt2_pace_sec=294,
        )
        assert show_button is False

    def test_ride_ftp_drift_lights_button(self):
        """Ride: hrvt2_power vs config_ftp Δ>5W with medium R² → button shown."""
        hrv = _make_hrv(
            activity_type="Ride",
            hrvt1_hr=140.0,
            hrvt1_power=180.0,
            hrvt1_pace=None,
            hrvt2_hr=160.0,  # exact match → no LTHR drift
            hrvt2_power=240.0,  # Δ+32 W
            threshold_r_squared=0.80,
        )
        msg, show_button, _ = build_ramp_test_message(
            _make_activity(type="Ride"),
            hrv,
            config_lthr=160,
            config_ftp=208,
        )
        assert show_button is True
        assert "240W" in msg
        assert "208 W" in msg
        assert "Δ +32 W" in msg

    def test_ride_ftp_within_tolerance_no_button(self):
        hrv = _make_hrv(
            activity_type="Ride",
            hrvt1_hr=140.0,
            hrvt1_power=180.0,
            hrvt2_hr=160.0,
            hrvt2_power=211.0,  # Δ+3 W < 5
        )
        _, show_button, _ = build_ramp_test_message(
            _make_activity(type="Ride"),
            hrv,
            config_lthr=160,
            config_ftp=208,
        )
        assert show_button is False

    def test_ride_lthr_and_ftp_both_drift_single_button(self):
        hrv = _make_hrv(
            activity_type="Ride",
            hrvt1_hr=140.0,
            hrvt1_power=180.0,
            hrvt2_hr=180.0,  # Δ+20 bpm
            hrvt2_power=240.0,  # Δ+32 W
            threshold_r_squared=0.80,
        )
        msg, show_button, _ = build_ramp_test_message(
            _make_activity(type="Ride"),
            hrv,
            config_lthr=160,
            config_ftp=208,
        )
        assert show_button is True
        assert "низкое R²" not in msg


# ---------------------------------------------------------------------------
# Tests: _drift_button_status (UI gating that mirrors User.detect_threshold_drift)
# ---------------------------------------------------------------------------


class TestDriftButtonStatus:
    """Pure helper. Each branch tested directly so the UI/backend gates can't drift apart.

    Returns ``(visible, hint, tier)``. Tiers:
      - ``high``   (R² ≥ 0.85, drift ≥ gate): visible=False, auto-update path
      - ``medium`` (0.70 ≤ R² < 0.85, drift ≥ gate): visible=True, button
      - ``low``    (R² < 0.70, drift ≥ gate): visible=False, soft hint
      - ``none``   (drift < gate): visible=False, no hint

    Drift gates are absolute per metric: LTHR 3 bpm, PACE 5 s/km, FTP 5 W.
    Pairs with `tests/db/test_threshold_drift.py` for the backend mirror.
    """

    def test_lthr_medium_r2_with_drift_shows_button(self):
        from tasks.formatter import _drift_button_status

        # 165 vs 153 = +12 bpm (>3), R²=0.80 → medium tier
        visible, hint, tier = _drift_button_status("LTHR", measured=165, config=153, r2=0.80)
        assert visible is True
        assert tier == "medium"
        assert "обновить" in hint.lower()

    def test_lthr_high_r2_auto_update_no_button(self):
        from tasks.formatter import _drift_button_status

        visible, hint, tier = _drift_button_status("LTHR", measured=165, config=153, r2=0.92)
        assert visible is False
        assert tier == "high"
        assert "авто-обновление" in hint.lower()

    def test_lthr_within_gate_no_hint(self):
        from tasks.formatter import _drift_button_status

        # 155 vs 153 = +2 bpm — under 3 bpm gate
        visible, hint, tier = _drift_button_status("LTHR", measured=155, config=153, r2=0.92)
        assert visible is False
        assert hint is None
        assert tier == "none"

    def test_lthr_drift_low_r2_soft_hint_only(self):
        from tasks.formatter import _drift_button_status

        # +22 bpm drift but R²=0.50 — gate clears, R² blocks → soft hint
        visible, hint, tier = _drift_button_status("LTHR", measured=175, config=153, r2=0.50)
        assert visible is False
        assert tier == "low"
        assert "R²" in hint or "ramp test" in hint.lower()

    def test_lthr_no_r2_treated_as_low(self):
        """R² absent (None) → low tier, button hidden, soft hint shown."""
        from tasks.formatter import _drift_button_status

        visible, hint, tier = _drift_button_status("LTHR", measured=175, config=153, r2=None)
        assert visible is False
        assert tier == "low"
        assert hint is not None

    def test_lthr_at_medium_boundary_fires(self):
        """R²=0.70 boundary → medium tier (>= gate)."""
        from tasks.formatter import _drift_button_status

        visible, _, tier = _drift_button_status("LTHR", measured=170, config=153, r2=0.70)
        assert visible is True
        assert tier == "medium"

    def test_lthr_at_high_boundary_auto(self):
        """R²=0.85 boundary → high tier."""
        from tasks.formatter import _drift_button_status

        visible, _, tier = _drift_button_status("LTHR", measured=170, config=153, r2=0.85)
        assert visible is False
        assert tier == "high"

    def test_pace_uses_seconds_gate(self):
        """PACE metric uses 5 s/km absolute gate, not bpm."""
        from tasks.formatter import _drift_button_status

        # 291 vs 295 = -4 s/km, abs(delta) < 5 → no fire
        _, _, tier = _drift_button_status("PACE", measured=291, config=295, r2=0.92)
        assert tier == "none"
        # 289 vs 295 = -6 s/km → fires (high R²)
        _, _, tier = _drift_button_status("PACE", measured=289, config=295, r2=0.92)
        assert tier == "high"

    def test_ftp_uses_watts_gate(self):
        """FTP metric uses 5 W absolute gate."""
        from tasks.formatter import _drift_button_status

        # 211 vs 208 = +3 W, abs(delta) < 5 → no fire
        _, _, tier = _drift_button_status("FTP", measured=211, config=208, r2=0.92)
        assert tier == "none"
        # 215 vs 208 = +7 W → fires
        _, _, tier = _drift_button_status("FTP", measured=215, config=208, r2=0.80)
        assert tier == "medium"


# ---------------------------------------------------------------------------
# Tests: evening message
# ---------------------------------------------------------------------------


class TestBuildEveningMessage:
    def test_full_evening(self):
        activities = [
            _make_activity(id="i1", type="Ride", moving_time=4800, icu_training_load=85),
            _make_activity(id="i2", type="Run", moving_time=2400, icu_training_load=35),
        ]
        hrv_analyses = [
            _make_hrv(activity_id="i1", ra_pct=3.2, activity_type="ride"),
            _make_hrv(activity_id="i2", ra_pct=-1.5, activity_type="run"),
        ]
        row = _make_wellness()
        msg = build_evening_message(row, activities, hrv_analyses)

        assert "📊 Итог дня" in msg
        assert "Тренировки: 2 | TSS: 120" in msg
        assert "🚴" in msg
        assert "🏃" in msg
        assert "Recovery: 72/100" in msg
        assert "ESS: 95.3" in msg
        assert "Banister: 68%" in msg
        assert "HRV:" in msg
        assert "45.2" in msg
        assert "DFA:" in msg

    def test_rest_day(self):
        row = _make_wellness()
        msg = build_evening_message(row, [], [])
        assert "День отдыха" in msg
        assert "Тренировки:" not in msg

    def test_no_wellness(self):
        activities = [_make_activity()]
        msg = build_evening_message(None, activities, [])
        assert "Тренировки: 1" in msg
        assert "Recovery:" not in msg

    def test_no_dfa_data(self):
        activities = [_make_activity()]
        msg = build_evening_message(_make_wellness(), activities, [])
        assert "DFA:" not in msg

    def test_unprocessed_hrv_excluded_from_dfa(self):
        activities = [_make_activity()]
        hrv_analyses = [_make_hrv(processing_status="no_rr_data", ra_pct=None)]
        msg = build_evening_message(_make_wellness(), activities, hrv_analyses)
        assert "DFA:" not in msg

    def test_ess_banister_none(self):
        row = _make_wellness(ess_today=None, banister_recovery=None)
        msg = build_evening_message(row, [], [])
        assert "ESS" not in msg
        assert "Banister" not in msg


# ---------------------------------------------------------------------------
# Tests: database queries for new functions
# ---------------------------------------------------------------------------


class TestGetActivitiesForDate:
    @pytest.mark.asyncio
    async def test_returns_activities(self):
        from data.db import Activity
        from data.intervals.dto import ActivityDTO

        dt = date(2026, 3, 24)
        await Activity.save_bulk(
            1,
            activities=[
                ActivityDTO(id="i701", start_date_local=dt, type="Ride", icu_training_load=80, moving_time=3600),
                ActivityDTO(id="i702", start_date_local=dt, type="Run", icu_training_load=40, moving_time=2400),
                ActivityDTO(
                    id="i703", start_date_local=date(2026, 3, 23), type="Swim", icu_training_load=30, moving_time=1800
                ),
            ],
        )

        result = await Activity.get_for_date(1, dt)
        assert len(result) == 2
        ids = {r.id for r in result}
        assert "i701" in ids
        assert "i702" in ids
        assert "i703" not in ids

    @pytest.mark.asyncio
    async def test_empty_date(self):
        from data.db import Activity

        result = await Activity.get_for_date(1, date(2099, 1, 1))
        assert result == []


class TestGetActivityHrvForDate:
    @pytest.mark.asyncio
    async def test_returns_hrv_rows(self):
        from data.db import Activity, ActivityHrv, get_session

        dt = date(2026, 3, 24)
        suffix = int(datetime.now(timezone.utc).timestamp() * 1000000) % 1000000000
        aid1 = f"i{suffix}"
        aid2 = f"i{suffix + 1}"
        # Create parent activities explicitly to satisfy FK on activity_hrv.
        async with get_session() as session:
            session.add(
                Activity(
                    id=aid1,
                    user_id=1,
                    start_date_local=str(dt),
                    type="Ride",
                    icu_training_load=80,
                    moving_time=3600,
                )
            )
            session.add(
                Activity(
                    id=aid2,
                    user_id=1,
                    start_date_local=str(dt),
                    type="Run",
                    icu_training_load=40,
                    moving_time=2400,
                )
            )
            await session.commit()

        await ActivityHrv.save(
            ActivityHrv(
                activity_id=aid1,
                activity_type="Ride",
                processing_status="processed",
            )
        )
        await ActivityHrv.save(
            ActivityHrv(
                activity_id=aid2,
                activity_type="Run",
                processing_status="no_rr_data",
            )
        )

        result = await ActivityHrv.get_for_date(1, dt)
        by_id = {r.activity_id: r for r in result}
        assert aid1 in by_id
        assert aid2 in by_id
        assert by_id[aid1].processing_status == "processed"
        assert by_id[aid2].processing_status == "no_rr_data"

    @pytest.mark.asyncio
    async def test_empty_date(self):
        from data.db import ActivityHrv

        result = await ActivityHrv.get_for_date(1, date(2099, 1, 1))
        assert result == []
