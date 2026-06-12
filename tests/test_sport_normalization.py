"""Tests for sport type normalization (data/utils.py)."""

import pytest

from data.utils import (
    HRV_ELIGIBLE_TYPES,
    extract_sport_atl,
    extract_sport_ctl,
    extract_sport_eftp,
    is_bike,
    is_run,
    normalize_sport,
)


class TestNormalizeSport:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Ride", "Ride"),
            ("VirtualRide", "Ride"),
            ("GravelRide", "Ride"),
            ("MountainBikeRide", "Ride"),
            ("EBikeRide", "Ride"),
            ("EMountainBikeRide", "Ride"),
            ("TrackRide", "Ride"),
            ("Velomobile", "Ride"),
            ("Handcycle", "Ride"),
            ("Run", "Run"),
            ("VirtualRun", "Run"),
            ("TrailRun", "Run"),
            ("Swim", "Swim"),
            ("OpenWaterSwim", "Swim"),
            ("WeightTraining", "Other"),
            ("Yoga", "Other"),
            ("Hike", "Other"),
            ("Walk", "Other"),
            ("Rowing", "Other"),
            ("Other", "Other"),
        ],
    )
    def test_known_types(self, raw, expected):
        assert normalize_sport(raw) == expected

    def test_none(self):
        assert normalize_sport(None) is None

    def test_unknown_type_returns_other(self):
        assert normalize_sport("UnknownSport") == "Other"


class TestHelpers:
    def test_is_bike(self):
        assert is_bike("Ride") is True
        assert is_bike("Run") is False
        assert is_bike(None) is False

    def test_is_run(self):
        assert is_run("Run") is True
        assert is_run("Ride") is False


class TestConstants:
    def test_hrv_eligible(self):
        assert HRV_ELIGIBLE_TYPES == {"Ride", "Run"}


class TestExtractSportCtl:
    def test_ride_key(self):
        """After renaming bike→ride, extract_sport_ctl returns 'ride' key."""
        sport_info = [{"type": "Ride", "ctl": 25.3}]
        result = extract_sport_ctl(sport_info)
        assert "ride" in result
        assert "bike" not in result
        assert result["ride"] == 25.3

    def test_all_sports(self):
        sport_info = [
            {"type": "Swim", "ctl": 10.0},
            {"type": "Ride", "ctl": 30.0},
            {"type": "Run", "ctl": 20.0},
        ]
        result = extract_sport_ctl(sport_info)
        assert result == {"swim": 10.0, "ride": 30.0, "run": 20.0}

    def test_empty(self):
        assert extract_sport_ctl(None) == {"swim": None, "ride": None, "run": None}
        assert extract_sport_ctl([]) == {"swim": None, "ride": None, "run": None}

    def test_legacy_ctl_load_key(self):
        """Older Intervals.icu payloads used 'ctlLoad' — extractor falls back."""
        result = extract_sport_ctl([{"type": "Run", "ctlLoad": 18.7}])
        assert result["run"] == 18.7


class TestExtractSportAtl:
    def test_all_sports(self):
        sport_info = [
            {"type": "Swim", "atl": 5.0, "ctl": 10.0},
            {"type": "Ride", "atl": 28.0, "ctl": 30.0},
            {"type": "Run", "atl": 22.0, "ctl": 20.0},
        ]
        result = extract_sport_atl(sport_info)
        assert result == {"swim": 5.0, "ride": 28.0, "run": 22.0}

    def test_empty(self):
        assert extract_sport_atl(None) == {"swim": None, "ride": None, "run": None}
        assert extract_sport_atl([]) == {"swim": None, "ride": None, "run": None}

    def test_legacy_atl_load_key(self):
        result = extract_sport_atl([{"type": "Ride", "atlLoad": 33.1}])
        assert result["ride"] == 33.1

    def test_atl_independent_from_ctl(self):
        """Entry with only ctl set → atl extraction returns None for that sport."""
        sport_info = [{"type": "Run", "ctl": 20.0}]
        assert extract_sport_atl(sport_info) == {"swim": None, "ride": None, "run": None}


class TestExtractSportEftp:
    def test_ride_eftp_rounded(self):
        """Real Intervals.icu payload shape — eftp carries full float precision."""
        sport_info = [{"type": "Ride", "eftp": 207.82047, "wPrime": 17460.5, "ctl": 24.8}]
        result = extract_sport_eftp(sport_info)
        assert result == {"swim": None, "ride": 207.8, "run": None}

    def test_no_power_sports_stay_none(self):
        """Swim never has eftp; Run only for run-power users."""
        sport_info = [
            {"type": "Swim", "ctl": 6.8},
            {"type": "Run", "ctl": 20.8},
            {"type": "Ride", "eftp": 210.5, "ctl": 24.0},
        ]
        result = extract_sport_eftp(sport_info)
        assert result == {"swim": None, "ride": 210.5, "run": None}

    def test_empty(self):
        assert extract_sport_eftp(None) == {"swim": None, "ride": None, "run": None}
        assert extract_sport_eftp([]) == {"swim": None, "ride": None, "run": None}


class TestDTONormalization:
    def test_activity_dto_normalizes_type(self):
        from data.intervals.dto import ActivityDTO

        dto = ActivityDTO(id="i1", start_date_local="2026-04-09", type="VirtualRide")
        assert dto.type == "Ride"

    def test_activity_dto_unknown_becomes_other(self):
        from data.intervals.dto import ActivityDTO

        dto = ActivityDTO(id="i2", start_date_local="2026-04-09", type="WeightTraining")
        assert dto.type == "Other"

    def test_activity_dto_none_stays_none(self):
        from data.intervals.dto import ActivityDTO

        dto = ActivityDTO(id="i3", start_date_local="2026-04-09", type=None)
        assert dto.type is None

    def test_scheduled_workout_dto_normalizes_type(self):
        from data.intervals.dto import ScheduledWorkoutDTO

        dto = ScheduledWorkoutDTO(id=1, start_date_local="2026-04-09", type="TrailRun")
        assert dto.type == "Run"
