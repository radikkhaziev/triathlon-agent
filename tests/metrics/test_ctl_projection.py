from datetime import date, timedelta

from data.metrics import PROJECTION_WINDOW_DAYS, project_ctl_target


def _series(today: date, ctls: list[float], step_days: int = 1) -> list[tuple[date, float]]:
    """Build (date, ctl) pairs ending today, one entry per ``step_days``."""
    n = len(ctls)
    return [(today - timedelta(days=(n - 1 - i) * step_days), c) for i, c in enumerate(ctls)]


class TestProjectCtlTarget:
    def test_window_constant(self):
        assert PROJECTION_WINDOW_DAYS == 14

    def test_no_target_returns_none(self):
        today = date(2026, 5, 9)
        assert project_ctl_target(_series(today, [50.0, 52.0]), None, today) is None
        assert project_ctl_target(_series(today, [50.0, 52.0]), 0, today) is None
        assert project_ctl_target(_series(today, [50.0, 52.0]), -10, today) is None

    def test_insufficient_rows(self):
        today = date(2026, 5, 9)
        out = project_ctl_target([(today, 50.0)], 80, today)
        assert out == {
            "ramp_per_week": None,
            "projected_date": None,
            "reason": "insufficient_data",
            "on_track": None,
        }

    def test_insufficient_span(self):
        today = date(2026, 5, 9)
        # Two rows but only 6 days apart — need 7+
        rows = [(today - timedelta(days=6), 50.0), (today, 52.0)]
        out = project_ctl_target(rows, 80, today)
        assert out["reason"] == "insufficient_data"
        assert out["projected_date"] is None

    def test_already_at_target(self):
        today = date(2026, 5, 9)
        # 14-day span landing above target — the gap≤0 branch fires before
        # we even read the slope sign, so any series ending above target works.
        rows = _series(today, [70.0] + [75.0] * 12 + [82.0])
        out = project_ctl_target(rows, 80, today)
        assert out["reason"] == "already_at_target"
        assert out["projected_date"] is None
        assert out["on_track"] is True

    def test_declining(self):
        today = date(2026, 5, 9)
        rows = _series(today, [70.0, 68.0, 66.0, 64.0, 62.0, 60.0, 58.0, 56.0])
        out = project_ctl_target(rows, 80, today)
        assert out["reason"] == "declining"
        assert out["projected_date"] is None
        assert out["on_track"] is False
        assert out["ramp_per_week"] < 0

    def test_flat(self):
        today = date(2026, 5, 9)
        rows = _series(today, [60.0] * 14)
        out = project_ctl_target(rows, 80, today)
        assert out["reason"] == "flat"
        assert out["projected_date"] is None
        assert out["on_track"] is False
        assert out["ramp_per_week"] == 0.0

    def test_normal_projection(self):
        today = date(2026, 5, 9)
        # Linear climb 60 → 67 over 14 days → 0.5 CTL/day → 3.5 CTL/week
        ctls = [60.0 + 0.5 * i for i in range(14)]
        rows = _series(today, ctls)
        out = project_ctl_target(rows, 80, today)
        assert out["reason"] is None
        # current=66.5; gap=13.5; weeks_to_target=13.5/3.5≈3.857; days≈27
        assert out["ramp_per_week"] == 3.5
        assert out["projected_date"] == (today + timedelta(days=27)).isoformat()
        # on_track stays None — caller fills it from event_date
        assert out["on_track"] is None

    def test_span_seven_boundary(self):
        today = date(2026, 5, 9)
        # Exactly 7-day span — should compute, not return insufficient_data
        rows = [(today - timedelta(days=7), 60.0), (today, 67.0)]
        out = project_ctl_target(rows, 80, today)
        assert out["reason"] is None
        assert out["ramp_per_week"] == 7.0  # 7 CTL gained over exactly 1 week

    def test_unsorted_input(self):
        today = date(2026, 5, 9)
        # Pass rows in a scrambled order; function must sort internally
        rows = [
            (today - timedelta(days=7), 60.0),
            (today, 70.0),
            (today - timedelta(days=14), 50.0),
        ]
        out = project_ctl_target(rows, 80, today)
        assert out["reason"] is None
        # Regression on a perfectly linear 50→60→70 series: slope=10/wk,
        # current=70, gap=10 → 1 week → 7 days.
        assert out["ramp_per_week"] == 10.0
        assert out["projected_date"] == (today + timedelta(days=7)).isoformat()

    def test_empty_series(self):
        today = date(2026, 5, 9)
        out = project_ctl_target([], 80, today)
        assert out["reason"] == "insufficient_data"
        assert out["on_track"] is None

    def test_event_date_on_track(self):
        today = date(2026, 5, 9)
        # Linear 60→67 over 14d → 3.5/wk, current=66.5, gap=13.5 → ~27 days.
        # Event in 60 days — comfortably on track.
        ctls = [60.0 + 0.5 * i for i in range(14)]
        rows = _series(today, ctls)
        out = project_ctl_target(rows, 80, today, event_date=today + timedelta(days=60))
        assert out["on_track"] is True
        assert out["projected_date"] is not None

    def test_event_date_off_track(self):
        today = date(2026, 5, 9)
        ctls = [60.0 + 0.5 * i for i in range(14)]
        rows = _series(today, ctls)
        # Event in 10 days — projected ~27 days out, definitely off track.
        out = project_ctl_target(rows, 80, today, event_date=today + timedelta(days=10))
        assert out["on_track"] is False
        assert out["projected_date"] is not None

    def test_event_date_boundary(self):
        today = date(2026, 5, 9)
        # Construct a series whose projection lands exactly on event_date.
        # 60→67 over 14d → ~27 days to hit 80 — set event_date 27 days out.
        ctls = [60.0 + 0.5 * i for i in range(14)]
        rows = _series(today, ctls)
        out = project_ctl_target(rows, 80, today, event_date=today + timedelta(days=27))
        # Boundary: days_to_target == days_remaining → on_track True (≤).
        assert out["on_track"] is True

    def test_event_date_already_at_target_branch(self):
        today = date(2026, 5, 9)
        rows = _series(today, [70.0] + [75.0] * 12 + [82.0])
        out = project_ctl_target(rows, 80, today, event_date=today + timedelta(days=10))
        # already_at_target wins regardless of event_date — already there.
        assert out["reason"] == "already_at_target"
        assert out["on_track"] is True

    def test_event_date_declining_branch(self):
        today = date(2026, 5, 9)
        rows = _series(today, [70.0, 68.0, 66.0, 64.0, 62.0, 60.0, 58.0, 56.0])
        out = project_ctl_target(rows, 80, today, event_date=today + timedelta(days=60))
        # Even with generous event_date, declining keeps on_track=False.
        assert out["reason"] == "declining"
        assert out["on_track"] is False

    def test_regression_smooths_endpoint_noise(self):
        today = date(2026, 5, 9)
        # Two series with the same trend but different endpoints — least-squares
        # should keep the ramp close; two-endpoint slope would diverge ~3x.
        clean = [60.0 + i * 0.5 for i in range(14)]  # endpoint=66.5, slope=3.5/wk
        noisy_endpoint = clean[:-1] + [69.0]  # endpoint+2.5 from trend
        out_clean = project_ctl_target(_series(today, clean), 90, today)
        out_noisy = project_ctl_target(_series(today, noisy_endpoint), 90, today)
        # Two-endpoint would give clean=3.5, noisy=4.85 (delta ≈ 1.35).
        # Regression gives clean=3.5, noisy=4.0 (delta = 0.5) — 2.7x tighter.
        assert abs(out_clean["ramp_per_week"] - out_noisy["ramp_per_week"]) <= 0.6

    def test_flat_tolerance_band(self):
        today = date(2026, 5, 9)
        # Tiny float wobble that two-endpoint slope would route to "declining"
        # — regression on a near-constant series gives ramp ≈ 0, caught by
        # the |ramp| < 0.05 tolerance.
        rows = _series(today, [60.0, 60.0, 60.0, 59.99, 60.01, 60.0, 60.0, 60.0, 60.0, 60.0, 60.0, 60.0, 60.0, 60.0])
        out = project_ctl_target(rows, 80, today)
        assert out["reason"] == "flat"
        assert out["on_track"] is False
