"""Tests for the deterministic taper line injected into the morning report
(TAPER_PLANNER_SPEC Phase 5) — `tasks/actors/reports.py:_taper_report_line`.

The happy-path tests run a REAL `build_taper_plan` envelope (via `_build_envelope`)
through the line builder — a hand-crafted envelope with today at index ≥ 1 is
unrealizable (`build_taper_plan` always anchors today at index 0), so synthetic
success fixtures would give false confidence."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from data.taper_service import _build_envelope
from tasks.actors.reports import _taper_report_line

TODAY = date(2026, 6, 20)


def _real_envelope(*, today=TODAY, days_out, distance_class="standard"):
    """A real serialised service envelope from the deterministic core."""
    return _build_envelope(
        race_dt=today + timedelta(days=days_out),
        today=today,
        event_name="Test race",
        distance_class=distance_class,
        ctl_now=70.0,
        atl_now=62.0,
        peak_daily_load=95.0,
        peak_fallback=False,
    )


def _line(envelope, *, today=TODAY):
    with patch("tasks.actors.reports.local_today", return_value=today):
        return _taper_report_line(envelope)


# --- gate branches (realizable envelope shapes) ----------------------------


def test_unavailable_envelope_returns_none():
    assert _line({"available": False, "reason": "no_future_race"}) is None


def test_early_mode_empty_targets_returns_none():
    # >21d out → early mode → daily_targets withheld.
    assert _line(_real_envelope(days_out=40)) is None
    assert _line({"available": True, "days_to_race": 40, "daily_targets": []}) is None


def test_today_not_a_taper_day_returns_none():
    # Realizable: today precedes taper_start, so it's absent from daily_targets.
    env = {"available": True, "days_to_race": 12, "daily_targets": [{"date": "2099-01-01", "target_tss": 70}]}
    assert _line(env) is None


def test_zero_target_safety_guard_returns_none():
    env = {"available": True, "days_to_race": 3, "daily_targets": [{"date": TODAY.isoformat(), "target_tss": 0}]}
    assert _line(env) is None


# --- happy path on REAL envelopes ------------------------------------------


def test_real_envelope_in_window_reports_days_to_race_and_target():
    env = _real_envelope(days_out=5)
    # today is taper_start → daily_targets[0], with a real (>0) target.
    assert env["daily_targets"][0]["date"] == TODAY.isoformat()
    line = _line(env)
    assert line is not None
    assert "до гонки 5 дн." in line
    expected_tss = round(env["daily_targets"][0]["target_tss"])
    assert f"~{expected_tss} TSS" in line
    assert "длительность" in line  # the cut-duration-not-intensity rule


def test_real_envelope_countdown_decrements_across_mornings():
    # Regression for the original bug: the day number was stuck at "день 1/L".
    # Same race, two consecutive mornings → the reported countdown must change.
    race_day = date(2026, 7, 1)
    line_day1 = _line(_real_envelope(today=date(2026, 6, 26), days_out=5), today=date(2026, 6, 26))
    line_day2 = _line(_real_envelope(today=date(2026, 6, 27), days_out=4), today=date(2026, 6, 27))
    assert race_day == date(2026, 6, 26) + timedelta(days=5) == date(2026, 6, 27) + timedelta(days=4)
    assert "до гонки 5 дн." in line_day1
    assert "до гонки 4 дн." in line_day2  # decrements — not frozen at a constant
