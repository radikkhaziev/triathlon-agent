"""Tests for ActivityDetail.save — upsert + EF fallback computation."""

from datetime import date

from data.db import Activity, ActivityDetail
from data.intervals.dto import ActivityDTO


def _seed_activity(activity_id: str = "i900") -> None:
    Activity.save_bulk(
        1,
        activities=[
            ActivityDTO(
                id=activity_id,
                start_date_local=date(2026, 4, 1),
                type="Run",
                moving_time=3600,
                average_hr=150.0,
            )
        ],
    )


class TestEfficiencyFactorFallback:
    def test_computes_ef_from_numeric_speed_and_hr(self):
        _seed_activity("i901")
        result = ActivityDetail.save(
            "i901",
            {"pace": 3.0, "average_heartrate": 150},
        )
        assert result.row.efficiency_factor == round((3.0 * 60) / 150, 6)

    def test_prefers_gap_over_pace(self):
        _seed_activity("i902")
        result = ActivityDetail.save(
            "i902",
            {"pace": 2.5, "gap": 3.5, "average_heartrate": 140},
        )
        assert result.row.efficiency_factor == round((3.5 * 60) / 140, 6)

    def test_string_pace_does_not_raise(self):
        """Regression for issue #275: Intervals.icu sometimes returns pace as a string."""
        _seed_activity("i903")
        result = ActivityDetail.save(
            "i903",
            {"pace": "5:30", "average_heartrate": 145},
        )
        # Coerced to None on assignment — pins the fix at the API boundary, not the EF check.
        assert result.row.pace is None
        assert result.row.efficiency_factor is None

    def test_string_gap_falls_back_to_pace(self):
        _seed_activity("i904")
        result = ActivityDetail.save(
            "i904",
            {"gap": "5:30", "pace": 3.0, "average_heartrate": 150},
        )
        assert result.row.efficiency_factor == round((3.0 * 60) / 150, 6)

    def test_string_avg_hr_does_not_raise(self):
        _seed_activity("i905")
        result = ActivityDetail.save(
            "i905",
            {"pace": 3.0, "average_heartrate": "n/a"},
        )
        assert result.row.efficiency_factor is None

    def test_zero_speed_skips_ef(self):
        _seed_activity("i906")
        result = ActivityDetail.save(
            "i906",
            {"pace": 0, "gap": 0, "average_heartrate": 150},
        )
        assert result.row.efficiency_factor is None

    def test_intervals_provided_ef_is_kept(self):
        _seed_activity("i907")
        result = ActivityDetail.save(
            "i907",
            {"pace": 3.0, "average_heartrate": 150, "icu_efficiency_factor": 1.42},
        )
        assert result.row.efficiency_factor == 1.42
