"""Tests for ActivityDetail.save — upsert + EF fallback computation."""

from datetime import date

from data.db import Activity, ActivityDetail, ActivityWeather
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


class TestActivityDetailPatch:
    """``patch`` adds webhook-only fields (rolling FTP, CTL/ATL snapshots, carbs)
    onto an existing row WITHOUT disturbing the EF/zone fields written by save().

    Sentinel ``_UNSET`` semantics: omitted kwarg → field stays as-is; explicit
    None → clears; value → sets. Pinning these is critical because
    ACTIVITY_ACHIEVEMENTS arrives ~60s after ACTIVITY_UPLOADED — a careless
    patch implementation could clobber the trimp/zone data the upload path
    just wrote.
    """

    def test_creates_row_if_missing(self):
        """ACTIVITY_ACHIEVEMENTS can race ahead of the details-fetch actor on
        first sync. patch() must succeed by creating a stub row."""
        _seed_activity("i910")
        # No prior ActivityDetail row exists for i910.
        ActivityDetail.patch("i910", rolling_ftp=215, ctl_snapshot=22.0)
        row = ActivityDetail.get_for_activity("i910") if hasattr(ActivityDetail, "get_for_activity") else None
        if row is None:
            from data.db.common import get_sync_session

            with get_sync_session() as s:
                row = s.get(ActivityDetail, "i910")
        assert row is not None
        assert row.rolling_ftp == 215
        assert row.ctl_snapshot == 22.0
        assert row.trimp is None  # untouched

    def test_omitted_field_stays_unchanged(self):
        _seed_activity("i911")
        ActivityDetail.save("i911", {"trimp": 80.0, "icu_efficiency_factor": 1.5})
        # Patch only rolling_ftp; trimp and EF must stay where save() left them.
        ActivityDetail.patch("i911", rolling_ftp=240)
        from data.db.common import get_sync_session

        with get_sync_session() as s:
            row = s.get(ActivityDetail, "i911")
        assert row.rolling_ftp == 240
        assert row.trimp == 80.0  # _UNSET → no-op, prior value preserved
        assert row.efficiency_factor == 1.5

    def test_explicit_none_clears_field(self):
        _seed_activity("i912")
        ActivityDetail.patch("i912", rolling_ftp=200)
        # Explicit None is distinct from the _UNSET sentinel — it must clear.
        ActivityDetail.patch("i912", rolling_ftp=None)
        from data.db.common import get_sync_session

        with get_sync_session() as s:
            row = s.get(ActivityDetail, "i912")
        assert row.rolling_ftp is None

    def test_idempotent_on_repeat(self):
        _seed_activity("i913")
        ActivityDetail.patch("i913", rolling_ftp=210, atl_snapshot=38.0)
        ActivityDetail.patch("i913", rolling_ftp=210, atl_snapshot=38.0)
        from data.db.common import get_sync_session

        with get_sync_session() as s:
            row = s.get(ActivityDetail, "i913")
        assert row.rolling_ftp == 210
        assert row.atl_snapshot == 38.0


class TestActivityWeatherUpsert:
    """``upsert_from_dto`` persists outdoor weather block from the webhook.
    Indoor activities (``has_weather=False``) must be filtered by the caller
    — this test asserts only that the upsert itself round-trips correctly.
    """

    def test_round_trip(self):
        _seed_activity("i920")
        dto = ActivityDTO(
            id="i920",
            start_date_local=date(2026, 5, 1),
            type="Run",
            has_weather=True,
            average_weather_temp=18.2,
            min_weather_temp=14.0,
            max_weather_temp=22.5,
            average_wind_speed=3.5,
            prevailing_wind_deg=180,
            average_clouds=20.0,
        )
        ActivityWeather.upsert_from_dto(dto)
        from data.db.common import get_sync_session

        with get_sync_session() as s:
            row = s.get(ActivityWeather, "i920")
        assert row is not None
        assert row.avg_temp_c == 18.2
        assert row.min_temp_c == 14.0
        assert row.avg_wind_speed_mps == 3.5
        assert row.prevailing_wind_deg == 180

    def test_overwrites_on_conflict(self):
        """Same activity_id, second upsert with different temps → row updated,
        not duplicated. Webhook redelivery (Intervals retries) must be safe."""
        _seed_activity("i921")
        ActivityWeather.upsert_from_dto(
            ActivityDTO(
                id="i921", start_date_local=date(2026, 5, 1), type="Run", has_weather=True, average_weather_temp=10.0
            )
        )
        ActivityWeather.upsert_from_dto(
            ActivityDTO(
                id="i921", start_date_local=date(2026, 5, 1), type="Run", has_weather=True, average_weather_temp=25.0
            )
        )
        from data.db.common import get_sync_session

        with get_sync_session() as s:
            row = s.get(ActivityWeather, "i921")
        assert row.avg_temp_c == 25.0  # second write wins
