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
        "sleep_quality": 3,
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
        """HRV with only mean DFA, no warmup/ra/hrvt1/da."""
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
        assert len(lines) == 2  # header + DFA a1 line


# ---------------------------------------------------------------------------
# Tests: ramp test message
# ---------------------------------------------------------------------------


class TestBuildRampTestMessage:
    def test_detected_with_drift_shows_button(self):
        activity = _make_activity(type="Run")
        hrv = _make_hrv(activity_type="Run", hrvt1_hr=165.0, hrvt1_power=None, hrvt1_pace="5:20")
        msg, show_button = build_ramp_test_message(activity, hrv, config_lthr=153, hrvt1_sample_count=3)
        assert "Ramp Test" in msg
        assert "HRVT1: 165 bpm" in msg
        assert "5:20" in msg
        assert "153" in msg
        assert "+7.8%" in msg
        assert show_button is True

    def test_detected_within_tolerance_no_button(self):
        hrv = _make_hrv(hrvt1_hr=155.0, hrvt1_power=None)
        _, show_button = build_ramp_test_message(_make_activity(type="Run"), hrv, config_lthr=153, hrvt1_sample_count=5)
        assert show_button is False

    def test_single_sample_suppresses_button(self):
        """With only 1 HRVT1 sample total, drift detection can't fire — button hidden."""
        hrv = _make_hrv(hrvt1_hr=165.0, hrvt1_power=None)
        msg, show_button = build_ramp_test_message(
            _make_activity(type="Run"), hrv, config_lthr=153, hrvt1_sample_count=1
        )
        assert show_button is False
        # Still informs the user drift was seen, just asks for another test
        assert "ramp test" in msg.lower()

    def test_detection_failed_shows_reason(self):
        hrv = _make_hrv(
            hrvt1_hr=None,
            hrvt1_power=None,
            hrvt1_pace=None,
            hrvt2_hr=None,
            threshold_r_squared=None,
            threshold_confidence=None,
        )
        reason = {"code": "noisy_fit", "r_squared": 0.33}
        msg, show_button = build_ramp_test_message(
            _make_activity(type="Run"), hrv, config_lthr=153, failure_reason=reason
        )
        assert show_button is False
        assert "0.33" in msg

    def test_detection_failed_no_reason(self):
        hrv = _make_hrv(hrvt1_hr=None, hrvt1_power=None, hrvt1_pace=None)
        msg, show_button = build_ramp_test_message(_make_activity(type="Run"), hrv, config_lthr=153)
        assert show_button is False


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
