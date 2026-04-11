"""Garmin GDPR export parser — discovers and parses chunked JSON files."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from .dto import (
    GarminAbnormalHrEventDTO,
    GarminBioMetricsDTO,
    GarminDailySummaryDTO,
    GarminFitnessMetricsDTO,
    GarminHealthStatusDTO,
    GarminRacePredictionsDTO,
    GarminSleepDTO,
    GarminTrainingLoadDTO,
    GarminTrainingReadinessDTO,
)

logger = logging.getLogger(__name__)

# File patterns per data type
_PATTERNS = {
    "sleep": ("DI-Connect-Wellness", "*_sleepData.json"),
    "daily": ("DI-Connect-Aggregator", "UDSFile_*.json"),
    "readiness": ("DI-Connect-Metrics", "TrainingReadinessDTO_*.json"),
    "health": ("DI-Connect-Wellness", "*_healthStatusData.json"),
    "load": ("DI-Connect-Metrics", "MetricsAcuteTrainingLoad_*.json"),
    "vo2max": ("DI-Connect-Metrics", "ActivityVo2Max_*.json"),
    "endurance": ("DI-Connect-Metrics", "EnduranceScore_*.json"),
    "race": ("DI-Connect-Metrics", "RunRacePredictions_*.json"),
    "max_met": ("DI-Connect-Metrics", "MetricsMaxMetData_*.json"),
    "bio": ("DI-Connect-Wellness", "*_userBioMetrics.json"),
    "abnormal_hr": ("DI-Connect-Wellness", "*_AbnormalHrEvents.json"),
}


class GarminExportParser:
    """Locates and parses Garmin GDPR export files."""

    def __init__(self, export_dir: str | Path):
        self.root = self._find_di_connect(Path(export_dir))
        logger.info("Garmin export root: %s", self.root)

    def _find_di_connect(self, path: Path) -> Path:
        """Scan for DI_CONNECT directory (may be nested in UUID subdir)."""
        if (path / "DI_CONNECT").is_dir():
            return path / "DI_CONNECT"

        for child in path.iterdir():
            if child.is_dir() and (child / "DI_CONNECT").is_dir():
                return child / "DI_CONNECT"

        raise FileNotFoundError(f"DI_CONNECT directory not found in {path}")

    def _load_chunked_files(self, subdir: str, pattern: str) -> list[dict]:
        """Load and merge all date-range-partitioned JSON files."""
        target = self.root / subdir
        if not target.is_dir():
            logger.warning("Directory not found: %s", target)
            return []

        files = sorted(target.glob(pattern))
        if not files:
            logger.warning("No files matching %s in %s", pattern, target)
            return []

        all_entries: list[dict] = []
        for f in files:
            try:
                with open(f) as fh:
                    data = json.load(fh)
                    if isinstance(data, list):
                        all_entries.extend(data)
                    else:
                        all_entries.append(data)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning("Skipping corrupt file %s: %s", f.name, e)

        logger.info("Loaded %d entries from %d files (%s/%s)", len(all_entries), len(files), subdir, pattern)
        return all_entries

    def _filter_by_period(self, entries: list[dict], period: tuple[date, date] | None, date_key: str) -> list[dict]:
        """Filter entries by date range."""
        if not period:
            return entries

        start, end = period
        start_str, end_str = str(start), str(end)
        return [e for e in entries if start_str <= (e.get(date_key) or "")[:10] <= end_str]

    def parse_sleep(self, period: tuple[date, date] | None = None) -> list[GarminSleepDTO]:
        subdir, pattern = _PATTERNS["sleep"]
        entries = self._load_chunked_files(subdir, pattern)
        entries = [e for e in entries if e.get("calendarDate")]
        entries = self._filter_by_period(entries, period, "calendarDate")
        return [GarminSleepDTO.from_garmin(e) for e in entries]

    def parse_daily_summary(self, period: tuple[date, date] | None = None) -> list[GarminDailySummaryDTO]:
        subdir, pattern = _PATTERNS["daily"]
        entries = self._load_chunked_files(subdir, pattern)
        entries = [e for e in entries if e.get("calendarDate")]
        entries = self._filter_by_period(entries, period, "calendarDate")
        return [GarminDailySummaryDTO.from_garmin(e) for e in entries]

    def parse_training_readiness(self, period: tuple[date, date] | None = None) -> list[GarminTrainingReadinessDTO]:
        subdir, pattern = _PATTERNS["readiness"]
        entries = self._load_chunked_files(subdir, pattern)
        entries = [e for e in entries if e.get("calendarDate")]
        entries = self._filter_by_period(entries, period, "calendarDate")
        return [GarminTrainingReadinessDTO.from_garmin(e) for e in entries]

    def parse_health_status(self, period: tuple[date, date] | None = None) -> list[GarminHealthStatusDTO]:
        subdir, pattern = _PATTERNS["health"]
        entries = self._load_chunked_files(subdir, pattern)
        entries = [e for e in entries if e.get("calendarDate")]
        entries = self._filter_by_period(entries, period, "calendarDate")
        return [GarminHealthStatusDTO.from_garmin(e) for e in entries]

    def parse_training_load(self, period: tuple[date, date] | None = None) -> list[GarminTrainingLoadDTO]:
        subdir, pattern = _PATTERNS["load"]
        entries = self._load_chunked_files(subdir, pattern)
        entries = [e for e in entries if e.get("calendarDate")]
        # calendarDate is ms epoch — convert for filtering, DTO handles conversion
        dtos = [GarminTrainingLoadDTO.from_garmin(e) for e in entries]
        if period:
            start_str, end_str = str(period[0]), str(period[1])
            dtos = [d for d in dtos if start_str <= d.calendar_date <= end_str]
        # Deduplicate by date — keep last entry (most recent timestamp)
        by_date: dict[str, GarminTrainingLoadDTO] = {}
        for d in dtos:
            by_date[d.calendar_date] = d
        return list(by_date.values())

    def parse_fitness_metrics(self, period: tuple[date, date] | None = None) -> list[GarminFitnessMetricsDTO]:
        """Merge ActivityVo2Max + EnduranceScore + MetricsMaxMetData by date → one DTO per day."""
        merged: dict[str, dict] = {}

        # VO2max (ISO dates)
        for e in self._load_chunked_files(*_PATTERNS["vo2max"]):
            if not e.get("calendarDate"):
                continue
            dt = str(e["calendarDate"])[:10]
            merged.setdefault(dt, {})
            sport = e.get("sport", "")
            if "RUNNING" in sport:
                merged[dt]["vo2max_running"] = e.get("vo2MaxValue")
            elif "CYCLING" in sport:
                merged[dt]["vo2max_cycling"] = e.get("vo2MaxValue")
            if e.get("activityId"):
                merged[dt]["source_activity_id"] = str(e["activityId"])

        # Endurance Score (ms epoch dates)
        for e in self._load_chunked_files(*_PATTERNS["endurance"]):
            if not e.get("calendarDate"):
                continue
            dto = GarminFitnessMetricsDTO.from_endurance(e)
            merged.setdefault(dto.calendar_date, {})
            merged[dto.calendar_date]["endurance_score"] = dto.endurance_score

        # Max MET (ISO dates)
        for e in self._load_chunked_files(*_PATTERNS["max_met"]):
            if not e.get("calendarDate"):
                continue
            dt = str(e["calendarDate"])[:10]
            merged.setdefault(dt, {})
            merged[dt]["max_met"] = e.get("maxMet")
            if e.get("fitnessAge"):
                merged[dt]["fitness_age"] = e.get("fitnessAge")

        # Build DTOs
        dtos = [GarminFitnessMetricsDTO(calendar_date=dt, **vals) for dt, vals in sorted(merged.items())]
        if period:
            start_str, end_str = str(period[0]), str(period[1])
            dtos = [d for d in dtos if start_str <= d.calendar_date <= end_str]
        return dtos

    def parse_race_predictions(self, period: tuple[date, date] | None = None) -> list[GarminRacePredictionsDTO]:
        subdir, pattern = _PATTERNS["race"]
        entries = self._load_chunked_files(subdir, pattern)
        entries = [e for e in entries if e.get("calendarDate")]
        entries = self._filter_by_period(entries, period, "calendarDate")
        # Deduplicate by date — keep last
        by_date: dict[str, dict] = {}
        for e in entries:
            by_date[e["calendarDate"]] = e
        return [GarminRacePredictionsDTO.from_garmin(e) for e in by_date.values()]

    def parse_bio_metrics(self, period: tuple[date, date] | None = None) -> list[GarminBioMetricsDTO]:
        subdir, pattern = _PATTERNS["bio"]
        entries = self._load_chunked_files(subdir, pattern)
        # BioMetrics has nested metaData.calendarDate, from_garmin returns None for empty entries
        dtos = [GarminBioMetricsDTO.from_garmin(e) for e in entries]
        dtos = [d for d in dtos if d is not None]
        if period:
            start_str, end_str = str(period[0]), str(period[1])
            dtos = [d for d in dtos if start_str <= d.calendar_date <= end_str]
        # Deduplicate by date — keep last (most recent)
        by_date: dict[str, GarminBioMetricsDTO] = {}
        for d in dtos:
            by_date[d.calendar_date] = d
        return list(by_date.values())

    def parse_abnormal_hr_events(self, period: tuple[date, date] | None = None) -> list[GarminAbnormalHrEventDTO]:
        subdir, pattern = _PATTERNS["abnormal_hr"]
        entries = self._load_chunked_files(subdir, pattern)
        entries = [e for e in entries if e.get("abnormalHrEventGMT")]
        dtos = [GarminAbnormalHrEventDTO.from_garmin(e) for e in entries]
        if period:
            start_str, end_str = str(period[0]), str(period[1])
            dtos = [d for d in dtos if start_str <= d.calendar_date <= end_str]
        return dtos
