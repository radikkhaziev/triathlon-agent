"""Tests for the personal-patterns block in render_athlete_block."""

from datetime import date, timedelta

from bot.prompts import _render_personal_patterns, render_athlete_block
from data.db import TrainingLog


def _datestr(days_ago: int) -> str:
    return str(date.today() - timedelta(days=days_ago))


def _full_patterns_dict() -> dict:
    return {
        "entries_total": 42,
        "entries_complete": 35,
        "recovery_response_by_category": {
            "good": {"count": 20, "avg_delta": 4.5, "min_delta": -2.0, "max_delta": 12.0},
            "moderate": {"count": 15, "avg_delta": -3.2, "min_delta": -15.0, "max_delta": 5.0},
        },
        "hrv_sensitivity": {
            "green": {"count": 25, "avg_delta": 3.8},
            "yellow": {"count": 10, "avg_delta": -7.1},
        },
        "recovery_intensity_matrix": {
            "good": {"Z2": {"count": 12, "avg_delta": 5.0}, "Z3": {"count": 8, "avg_delta": 2.5}},
            "moderate": {"Z2": {"count": 9, "avg_delta": 1.0}, "Z4": {"count": 6, "avg_delta": -10.5}},
        },
        "compliance_rates": {
            "followed_original": {"count": 25, "pct": 71.4},
            "followed_adapted": {"count": 7, "pct": 20.0},
            "skipped": {"count": 3, "pct": 8.6},
        },
        "skipped_avg_delta": 8.0,
        "trained_avg_delta": -1.5,
    }


class TestRenderPersonalPatterns:
    def test_renders_russian_heading_by_default(self):
        out = _render_personal_patterns(_full_patterns_dict(), language="ru")
        assert out.startswith("## Персональные паттерны (training_log, 35/42 записей)")

    def test_renders_english_heading_when_requested(self):
        out = _render_personal_patterns(_full_patterns_dict(), language="en")
        assert out.startswith("## Personal patterns (training_log, 35/42 complete entries)")

    def test_includes_all_sections_when_data_present(self):
        out = _render_personal_patterns(_full_patterns_dict(), language="ru")
        # Recovery response: ordered excellent → good → moderate → low; only present buckets render
        assert "good (n=20): +4.5 [-2.0..+12.0]" in out
        assert "moderate (n=15): -3.2 [-15.0..+5.0]" in out
        # HRV sensitivity
        assert "green (n=25): +3.8" in out
        assert "yellow (n=10): -7.1" in out
        # Compliance distribution sorted by pct desc
        assert "followed_original: 71.4% (n=25)" in out
        # Recovery × max-zone
        assert "good: Z2 +5.0 (n=12), Z3 +2.5 (n=8)" in out
        # Rest vs training
        assert "skipped avg +8.0, trained avg -1.5" in out

    def test_skips_empty_sections(self):
        minimal = {
            "entries_total": 30,
            "entries_complete": 30,
            "recovery_response_by_category": {},
            "recovery_intensity_matrix": {},
            "compliance_rates": {},
            "hrv_sensitivity": {},
            "skipped_avg_delta": None,
            "trained_avg_delta": None,
        }
        out = _render_personal_patterns(minimal, language="ru")
        # Heading is the only line — no empty section headers leak through
        assert out == "## Персональные паттерны (training_log, 30/30 записей)"

    def test_returns_empty_below_threshold(self):
        # Counts-only dict (compute returned early because <30 complete) → no block.
        counts_only = {"entries_total": 12, "entries_complete": 12}
        assert _render_personal_patterns(counts_only, language="ru") == ""


class TestRenderAthleteBlockIntegration:
    async def test_no_patterns_block_when_below_threshold(self, _test_db):
        # 5 complete rows — below 30 threshold, block must not render.
        for i in range(5):
            await TrainingLog.create(
                user_id=1,
                date=_datestr(i),
                source="humango",
                pre_recovery_score=70.0,
                pre_recovery_category="good",
                pre_hrv_status="green",
                actual_max_zone_time="Z2",
                compliance="followed_original",
                post_recovery_score=72.0,
                recovery_delta=2.0,
            )

        block = await render_athlete_block(user_id=1, language="ru")
        assert "Персональные паттерны" not in block
        assert "Personal patterns" not in block

    async def test_patterns_block_rendered_at_threshold(self, _test_db):
        for i in range(30):
            await TrainingLog.create(
                user_id=1,
                date=_datestr(i),
                source="humango",
                pre_recovery_score=70.0,
                pre_recovery_category="good",
                pre_hrv_status="green",
                actual_max_zone_time="Z2",
                compliance="followed_original",
                post_recovery_score=72.0,
                recovery_delta=2.0,
            )

        block = await render_athlete_block(user_id=1, language="ru")
        assert "## Персональные паттерны (training_log, 30/30 записей)" in block
        assert "good (n=30): +2.0" in block
