"""Unit and integration tests for GarminExportParser (data/garmin/parser.py).

Unit tests use in-memory tmp directories with synthetic files.
Integration tests run against the real GDPR export at REAL_EXPORT_DIR and are
skipped automatically when the directory is absent.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from data.garmin.parser import GarminExportParser

# ---------------------------------------------------------------------------
# Real export path
# ---------------------------------------------------------------------------

REAL_EXPORT_DIR = Path("/Users/radik/Projects/triathlon-agent/garmin-export/e1e9a691-8ff2-4c25-85ab-077f0b306ff8_1")

_real_export_available = REAL_EXPORT_DIR.is_dir()

skip_if_no_export = pytest.mark.skipif(
    not _real_export_available,
    reason=f"Real Garmin export not found at {REAL_EXPORT_DIR}",
)


# ---------------------------------------------------------------------------
# Helpers for building synthetic export layouts
# ---------------------------------------------------------------------------


def _make_export(tmp_path: Path, entries_by_subdir: dict[str, list]) -> Path:
    """
    Build a minimal export directory tree:
      tmp_path/
        DI_CONNECT/
          <subdir>/
            <filename>.json  ← one file per (subdir, entries) pair

    entries_by_subdir maps  subdir -> list of (filename, data) tuples.
    Returns tmp_path (the root passed to GarminExportParser).
    """
    di = tmp_path / "DI_CONNECT"
    for subdir, files in entries_by_subdir.items():
        (di / subdir).mkdir(parents=True, exist_ok=True)
        for filename, data in files:
            (di / subdir / filename).write_text(json.dumps(data))
    return tmp_path


def _make_nested_export(tmp_path: Path, entries_by_subdir: dict[str, list]) -> Path:
    """Like _make_export but wraps DI_CONNECT one level deeper (UUID subdir)."""
    uuid_dir = tmp_path / "some-uuid-subdir"
    uuid_dir.mkdir()
    di = uuid_dir / "DI_CONNECT"
    for subdir, files in entries_by_subdir.items():
        (di / subdir).mkdir(parents=True, exist_ok=True)
        for filename, data in files:
            (di / subdir / filename).write_text(json.dumps(data))
    return tmp_path  # parser receives the outer root


# ---------------------------------------------------------------------------
# GarminExportParser._find_di_connect
# ---------------------------------------------------------------------------


class TestFindDiConnect:
    def test_finds_di_connect_at_root(self, tmp_path):
        (tmp_path / "DI_CONNECT").mkdir()
        parser = GarminExportParser(tmp_path)
        assert parser.root == tmp_path / "DI_CONNECT"

    def test_finds_di_connect_in_nested_uuid_subdir(self, tmp_path):
        uuid_dir = tmp_path / "e1e9a691-8ff2-4c25-85ab-077f0b306ff8_1"
        (uuid_dir / "DI_CONNECT").mkdir(parents=True)
        parser = GarminExportParser(tmp_path)
        assert parser.root == uuid_dir / "DI_CONNECT"

    def test_raises_when_di_connect_not_found(self, tmp_path):
        (tmp_path / "SOME_OTHER_DIR").mkdir()
        with pytest.raises(FileNotFoundError, match="DI_CONNECT"):
            GarminExportParser(tmp_path)

    def test_accepts_path_string(self, tmp_path):
        (tmp_path / "DI_CONNECT").mkdir()
        parser = GarminExportParser(str(tmp_path))
        assert parser.root.name == "DI_CONNECT"


# ---------------------------------------------------------------------------
# GarminExportParser._filter_by_period
# ---------------------------------------------------------------------------


class TestFilterByPeriod:
    @pytest.fixture
    def parser(self, tmp_path):
        (tmp_path / "DI_CONNECT").mkdir()
        return GarminExportParser(tmp_path)

    def test_no_period_returns_all_entries(self, parser):
        entries = [{"calendarDate": "2025-01-01"}, {"calendarDate": "2025-06-15"}]
        result = parser._filter_by_period(entries, None, "calendarDate")
        assert result == entries

    def test_period_filters_to_range_inclusive(self, parser):
        entries = [
            {"calendarDate": "2024-12-31"},
            {"calendarDate": "2025-01-01"},
            {"calendarDate": "2025-03-31"},
            {"calendarDate": "2025-04-01"},
        ]
        period = (date(2025, 1, 1), date(2025, 3, 31))
        result = parser._filter_by_period(entries, period, "calendarDate")

        dates = [e["calendarDate"] for e in result]
        assert "2025-01-01" in dates
        assert "2025-03-31" in dates
        assert "2024-12-31" not in dates
        assert "2025-04-01" not in dates

    def test_entry_with_missing_date_key_excluded(self, parser):
        entries = [
            {"calendarDate": "2025-01-15"},
            {},  # missing date key
            {"calendarDate": None},  # null date
        ]
        period = (date(2025, 1, 1), date(2025, 3, 31))
        result = parser._filter_by_period(entries, period, "calendarDate")

        # Only the entry with a valid date in range should be kept
        assert len(result) == 1
        assert result[0]["calendarDate"] == "2025-01-15"

    def test_empty_entries_returns_empty(self, parser):
        result = parser._filter_by_period([], (date(2025, 1, 1), date(2025, 3, 31)), "calendarDate")
        assert result == []

    def test_single_day_period(self, parser):
        entries = [{"calendarDate": "2025-04-05"}, {"calendarDate": "2025-04-06"}]
        period = (date(2025, 4, 5), date(2025, 4, 5))
        result = parser._filter_by_period(entries, period, "calendarDate")

        assert len(result) == 1
        assert result[0]["calendarDate"] == "2025-04-05"


# ---------------------------------------------------------------------------
# parse_sleep — unit tests with synthetic files
# ---------------------------------------------------------------------------


class TestParseSleep:
    def test_parses_entries_from_multiple_files(self, tmp_path):
        entries_a = [
            {
                "calendarDate": "2025-01-10",
                "deepSleepSeconds": 4800,
                "lightSleepSeconds": 12000,
                "remSleepSeconds": 5400,
                "awakeSleepSeconds": 300,
            }
        ]
        entries_b = [
            {
                "calendarDate": "2025-01-11",
                "deepSleepSeconds": 3600,
                "lightSleepSeconds": 10800,
            }
        ]
        root = _make_export(
            tmp_path,
            {
                "DI-Connect-Wellness": [
                    ("2025-01-01_2025-04-01_sleepData.json", entries_a),
                    ("2025-04-01_2025-07-01_sleepData.json", entries_b),
                ]
            },
        )
        parser = GarminExportParser(root)
        results = parser.parse_sleep()

        assert len(results) == 2
        dates = {r.calendar_date for r in results}
        assert "2025-01-10" in dates
        assert "2025-01-11" in dates

    def test_skips_entries_without_calendar_date(self, tmp_path):
        entries = [
            {"calendarDate": "2025-01-10", "deepSleepSeconds": 3600},
            {"deepSleepSeconds": 4800},  # no calendarDate
        ]
        root = _make_export(
            tmp_path,
            {"DI-Connect-Wellness": [("2025-01-01_2025-04-01_sleepData.json", entries)]},
        )
        parser = GarminExportParser(root)
        results = parser.parse_sleep()

        assert len(results) == 1
        assert results[0].calendar_date == "2025-01-10"

    def test_period_filter_applied(self, tmp_path):
        entries = [
            {"calendarDate": "2025-01-05"},
            {"calendarDate": "2025-02-15"},
            {"calendarDate": "2025-04-01"},
        ]
        root = _make_export(
            tmp_path,
            {"DI-Connect-Wellness": [("chunk_sleepData.json", entries)]},
        )
        parser = GarminExportParser(root)
        period = (date(2025, 1, 1), date(2025, 3, 31))
        results = parser.parse_sleep(period=period)

        assert len(results) == 2
        dates = {r.calendar_date for r in results}
        assert "2025-04-01" not in dates

    def test_missing_directory_returns_empty(self, tmp_path):
        (tmp_path / "DI_CONNECT").mkdir()
        parser = GarminExportParser(tmp_path)
        assert parser.parse_sleep() == []


# ---------------------------------------------------------------------------
# parse_training_load — unit tests (ms epoch dedup)
# ---------------------------------------------------------------------------


class TestParseTrainingLoad:
    def test_ms_epoch_date_converted(self, tmp_path):
        entries = [
            {
                "calendarDate": 1701043200000,  # 2023-11-27 UTC
                "dailyTrainingLoadAcute": 10,
                "dailyTrainingLoadChronic": 500,
                "acwrStatus": "NONE",
            }
        ]
        root = _make_export(
            tmp_path,
            {"DI-Connect-Metrics": [("MetricsAcuteTrainingLoad_20231127_20240306_test.json", entries)]},
        )
        parser = GarminExportParser(root)
        results = parser.parse_training_load()

        assert len(results) == 1
        assert results[0].calendar_date == "2023-11-27"

    def test_deduplication_keeps_last_entry_for_same_date(self, tmp_path):
        # Two entries with the same ms-epoch date — last one wins
        same_ms = 1701043200000  # 2023-11-27
        entries = [
            {"calendarDate": same_ms, "dailyTrainingLoadAcute": 10},
            {"calendarDate": same_ms, "dailyTrainingLoadAcute": 99},  # should win
        ]
        root = _make_export(
            tmp_path,
            {"DI-Connect-Metrics": [("MetricsAcuteTrainingLoad_chunk.json", entries)]},
        )
        parser = GarminExportParser(root)
        results = parser.parse_training_load()

        assert len(results) == 1
        assert results[0].acute_load == 99

    def test_period_filter_after_date_conversion(self, tmp_path):
        entries = [
            {"calendarDate": 1735689600000, "dailyTrainingLoadAcute": 5},  # 2025-01-01
            {"calendarDate": 1746057600000, "dailyTrainingLoadAcute": 8},  # 2025-05-01
        ]
        root = _make_export(
            tmp_path,
            {"DI-Connect-Metrics": [("MetricsAcuteTrainingLoad_chunk.json", entries)]},
        )
        parser = GarminExportParser(root)
        period = (date(2025, 1, 1), date(2025, 3, 31))
        results = parser.parse_training_load(period=period)

        assert len(results) == 1
        assert results[0].calendar_date == "2025-01-01"


# ---------------------------------------------------------------------------
# parse_bio_metrics — unit tests (None filtering, dedup)
# ---------------------------------------------------------------------------


class TestParseBioMetrics:
    def test_entries_without_any_metrics_skipped(self, tmp_path):
        entries = [
            # No weight, height, or lactate threshold — from_garmin returns None
            {"metaData": {"calendarDate": "2023-11-27T13:00:00.0"}, "userSetNullForHeight": True},
            # Has weight → should be included
            {
                "metaData": {"calendarDate": "2023-11-28T13:00:00.0"},
                "weight": {"weight": 77000.0},
            },
        ]
        root = _make_export(
            tmp_path,
            {"DI-Connect-Wellness": [("117899831_userBioMetrics.json", entries)]},
        )
        parser = GarminExportParser(root)
        results = parser.parse_bio_metrics()

        assert len(results) == 1
        assert results[0].calendar_date == "2023-11-28"

    def test_deduplication_keeps_last_entry_per_date(self, tmp_path):
        entries = [
            {"metaData": {"calendarDate": "2023-11-28T06:00:00.0"}, "weight": {"weight": 80000.0}},
            {"metaData": {"calendarDate": "2023-11-28T18:00:00.0"}, "weight": {"weight": 77000.0}},  # wins
        ]
        root = _make_export(
            tmp_path,
            {"DI-Connect-Wellness": [("117899831_userBioMetrics.json", entries)]},
        )
        parser = GarminExportParser(root)
        results = parser.parse_bio_metrics()

        assert len(results) == 1
        assert results[0].weight_kg == pytest.approx(77.0, abs=0.05)


# ---------------------------------------------------------------------------
# parse_race_predictions — dedup unit test
# ---------------------------------------------------------------------------


class TestParseRacePredictions:
    def test_deduplication_keeps_last_per_date(self, tmp_path):
        entries = [
            {"calendarDate": "2025-01-10", "raceTime5K": 1800, "raceTime10K": 3700},
            {"calendarDate": "2025-01-10", "raceTime5K": 1750, "raceTime10K": 3600},  # wins
        ]
        root = _make_export(
            tmp_path,
            {"DI-Connect-Metrics": [("RunRacePredictions_chunk.json", entries)]},
        )
        parser = GarminExportParser(root)
        results = parser.parse_race_predictions()

        assert len(results) == 1
        assert results[0].prediction_5k_secs == 1750


# ---------------------------------------------------------------------------
# parse_fitness_metrics — merge sources unit test
# ---------------------------------------------------------------------------


class TestParseFitnessMetrics:
    def test_merges_vo2max_and_endurance_by_date(self, tmp_path):
        vo2max_entries = [{"calendarDate": "2025-03-15", "sport": "RUNNING", "vo2MaxValue": 42.0, "activityId": 111}]
        # Endurance uses ms epoch: 1741996800000 = 2025-03-15 UTC
        endurance_entries = [{"calendarDate": 1741996800000, "overallScore": 68.5}]
        max_met_entries = [{"calendarDate": "2025-03-15", "maxMet": 12.0, "fitnessAge": 28}]

        root = _make_export(
            tmp_path,
            {
                "DI-Connect-Metrics": [
                    ("ActivityVo2Max_chunk.json", vo2max_entries),
                    ("EnduranceScore_chunk.json", endurance_entries),
                    ("MetricsMaxMetData_chunk.json", max_met_entries),
                ]
            },
        )
        parser = GarminExportParser(root)
        results = parser.parse_fitness_metrics()

        assert len(results) == 1
        dto = results[0]
        assert dto.calendar_date == "2025-03-15"
        assert dto.vo2max_running == pytest.approx(42.0)
        assert dto.endurance_score == pytest.approx(68.5)
        assert dto.max_met == pytest.approx(12.0)
        assert dto.fitness_age == 28
        assert dto.vo2max_cycling is None

    def test_separate_dates_produce_separate_dtos(self, tmp_path):
        vo2max_entries = [
            {"calendarDate": "2025-03-01", "sport": "RUNNING", "vo2MaxValue": 41.0},
            {"calendarDate": "2025-03-15", "sport": "CYCLING", "vo2MaxValue": 52.0},
        ]
        root = _make_export(
            tmp_path,
            {
                "DI-Connect-Metrics": [
                    ("ActivityVo2Max_chunk.json", vo2max_entries),
                    ("EnduranceScore_chunk.json", []),
                    ("MetricsMaxMetData_chunk.json", []),
                ]
            },
        )
        parser = GarminExportParser(root)
        results = parser.parse_fitness_metrics()

        assert len(results) == 2
        dates = {r.calendar_date for r in results}
        assert dates == {"2025-03-01", "2025-03-15"}

    def test_period_filter_applied(self, tmp_path):
        vo2max_entries = [
            {"calendarDate": "2025-01-10", "sport": "RUNNING", "vo2MaxValue": 40.0},
            {"calendarDate": "2025-06-10", "sport": "RUNNING", "vo2MaxValue": 43.0},
        ]
        root = _make_export(
            tmp_path,
            {
                "DI-Connect-Metrics": [
                    ("ActivityVo2Max_chunk.json", vo2max_entries),
                    ("EnduranceScore_chunk.json", []),
                    ("MetricsMaxMetData_chunk.json", []),
                ]
            },
        )
        parser = GarminExportParser(root)
        results = parser.parse_fitness_metrics(period=(date(2025, 1, 1), date(2025, 3, 31)))

        assert len(results) == 1
        assert results[0].calendar_date == "2025-01-10"


# ---------------------------------------------------------------------------
# parse_abnormal_hr_events — unit test
# ---------------------------------------------------------------------------


class TestParseAbnormalHrEvents:
    def test_parses_hr_events(self, tmp_path):
        entries = [
            {
                "abnormalHrEventGMT": "2025-03-12T07:45:00.0",
                "calendarDate": "2025-03-12",
                "abnormalHrValue": 157,
                "abnormalHrThresholdValue": 150,
            }
        ]
        root = _make_export(
            tmp_path,
            {"DI-Connect-Wellness": [("2025-03-12_2025-06-20_AbnormalHrEvents.json", entries)]},
        )
        parser = GarminExportParser(root)
        results = parser.parse_abnormal_hr_events()

        assert len(results) == 1
        assert results[0].hr_value == 157
        assert results[0].calendar_date == "2025-03-12"

    def test_entries_without_timestamp_skipped(self, tmp_path):
        entries = [
            {"calendarDate": "2025-03-12", "abnormalHrValue": 157},  # missing abnormalHrEventGMT
        ]
        root = _make_export(
            tmp_path,
            {"DI-Connect-Wellness": [("chunk_AbnormalHrEvents.json", entries)]},
        )
        parser = GarminExportParser(root)
        results = parser.parse_abnormal_hr_events()

        assert results == []


# ---------------------------------------------------------------------------
# Integration tests — real Garmin export
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_parser():
    """Shared parser instance for integration tests (expensive to create)."""
    return GarminExportParser(REAL_EXPORT_DIR)


@skip_if_no_export
class TestRealExportSleep:
    def test_parse_sleep_returns_823_entries(self, real_parser):
        results = real_parser.parse_sleep()
        assert len(results) == 823

    def test_sleep_entries_have_valid_calendar_dates(self, real_parser):
        results = real_parser.parse_sleep()
        for r in results:
            # Must be YYYY-MM-DD format
            assert len(r.calendar_date) == 10
            assert r.calendar_date[4] == "-"
            assert r.calendar_date[7] == "-"

    def test_sleep_period_filter_q1_2025(self, real_parser):
        period = (date(2025, 1, 1), date(2025, 3, 31))
        results = real_parser.parse_sleep(period=period)

        assert len(results) > 0
        for r in results:
            assert "2025-01-01" <= r.calendar_date <= "2025-03-31"

    def test_sleep_q1_2025_has_82_entries(self, real_parser):
        period = (date(2025, 1, 1), date(2025, 3, 31))
        results = real_parser.parse_sleep(period=period)
        assert len(results) == 82


@skip_if_no_export
class TestRealExportDailySummary:
    def test_parse_daily_summary_returns_865_entries(self, real_parser):
        results = real_parser.parse_daily_summary()
        assert len(results) == 865

    def test_daily_summary_entries_have_calendar_date(self, real_parser):
        results = real_parser.parse_daily_summary()
        assert all(r.calendar_date for r in results)

    def test_daily_summary_period_filter(self, real_parser):
        period = (date(2025, 1, 1), date(2025, 3, 31))
        results = real_parser.parse_daily_summary(period=period)

        assert len(results) > 0
        for r in results:
            assert "2025-01-01" <= r.calendar_date <= "2025-03-31"


@skip_if_no_export
class TestRealExportTrainingReadiness:
    def test_parse_training_readiness_returns_2073_entries(self, real_parser):
        results = real_parser.parse_training_readiness()
        assert len(results) == 2073

    def test_readiness_entries_have_input_context(self, real_parser):
        results = real_parser.parse_training_readiness()
        # Most entries should have an input_context field
        with_context = [r for r in results if r.input_context is not None]
        assert len(with_context) > 0

    def test_readiness_period_filter(self, real_parser):
        period = (date(2025, 1, 1), date(2025, 3, 31))
        results = real_parser.parse_training_readiness(period=period)

        assert len(results) > 0
        for r in results:
            assert "2025-01-01" <= r.calendar_date <= "2025-03-31"


@skip_if_no_export
class TestRealExportTrainingLoad:
    def test_parse_training_load_returns_865_entries_deduplicated(self, real_parser):
        results = real_parser.parse_training_load()
        assert len(results) == 865

    def test_no_duplicate_dates_after_dedup(self, real_parser):
        results = real_parser.parse_training_load()
        dates = [r.calendar_date for r in results]
        assert len(dates) == len(set(dates)), "Duplicate calendar_dates found after deduplication"

    def test_calendar_dates_are_iso_strings(self, real_parser):
        results = real_parser.parse_training_load()
        for r in results:
            # Must be converted from ms epoch to YYYY-MM-DD
            assert len(r.calendar_date) == 10
            assert "-" in r.calendar_date

    def test_period_filter(self, real_parser):
        period = (date(2025, 1, 1), date(2025, 3, 31))
        results = real_parser.parse_training_load(period=period)

        assert len(results) > 0
        for r in results:
            assert "2025-01-01" <= r.calendar_date <= "2025-03-31"


@skip_if_no_export
class TestRealExportFitnessMetrics:
    def test_parse_fitness_metrics_returns_865_entries(self, real_parser):
        results = real_parser.parse_fitness_metrics()
        assert len(results) == 865

    def test_no_duplicate_dates(self, real_parser):
        results = real_parser.parse_fitness_metrics()
        dates = [r.calendar_date for r in results]
        assert len(dates) == len(set(dates))

    def test_some_entries_have_vo2max_cycling(self, real_parser):
        results = real_parser.parse_fitness_metrics()
        cycling = [r for r in results if r.vo2max_cycling is not None]
        assert len(cycling) > 0

    def test_period_filter(self, real_parser):
        period = (date(2025, 1, 1), date(2025, 3, 31))
        results = real_parser.parse_fitness_metrics(period=period)

        assert len(results) > 0
        for r in results:
            assert "2025-01-01" <= r.calendar_date <= "2025-03-31"


@skip_if_no_export
class TestRealExportRacePredictions:
    def test_parse_race_predictions_returns_859_entries(self, real_parser):
        results = real_parser.parse_race_predictions()
        assert len(results) == 859

    def test_no_duplicate_dates(self, real_parser):
        results = real_parser.parse_race_predictions()
        dates = [r.calendar_date for r in results]
        assert len(dates) == len(set(dates))

    def test_some_entries_have_5k_prediction(self, real_parser):
        results = real_parser.parse_race_predictions()
        with_5k = [r for r in results if r.prediction_5k_secs is not None]
        assert len(with_5k) > 0

    def test_period_filter(self, real_parser):
        period = (date(2025, 1, 1), date(2025, 3, 31))
        results = real_parser.parse_race_predictions(period=period)

        assert len(results) > 0
        for r in results:
            assert "2025-01-01" <= r.calendar_date <= "2025-03-31"


@skip_if_no_export
class TestRealExportBioMetrics:
    def test_parse_bio_metrics_returns_424_entries_skipping_empty(self, real_parser):
        results = real_parser.parse_bio_metrics()
        assert len(results) == 424

    def test_no_none_entries_in_result(self, real_parser):
        results = real_parser.parse_bio_metrics()
        assert all(r is not None for r in results)

    def test_no_duplicate_dates(self, real_parser):
        results = real_parser.parse_bio_metrics()
        dates = [r.calendar_date for r in results]
        assert len(dates) == len(set(dates))

    def test_weight_values_are_plausible_kg(self, real_parser):
        results = real_parser.parse_bio_metrics()
        weights = [r.weight_kg for r in results if r.weight_kg is not None]
        assert len(weights) > 0
        # Sanity range: 40–200 kg
        for w in weights:
            assert 40.0 <= w <= 200.0, f"Implausible weight: {w} kg"

    def test_period_filter(self, real_parser):
        period = (date(2025, 1, 1), date(2025, 3, 31))
        results = real_parser.parse_bio_metrics(period=period)

        assert len(results) > 0
        for r in results:
            assert "2025-01-01" <= r.calendar_date <= "2025-03-31"


@skip_if_no_export
class TestRealExportAbnormalHrEvents:
    def test_parse_abnormal_hr_events_returns_9_entries(self, real_parser):
        results = real_parser.parse_abnormal_hr_events()
        assert len(results) == 9

    def test_all_entries_have_timestamp_and_date(self, real_parser):
        results = real_parser.parse_abnormal_hr_events()
        for r in results:
            assert r.timestamp_gmt
            assert len(r.calendar_date) == 10

    def test_hr_values_are_plausible(self, real_parser):
        results = real_parser.parse_abnormal_hr_events()
        for r in results:
            if r.hr_value is not None:
                assert 30 <= r.hr_value <= 250, f"Implausible HR: {r.hr_value}"
