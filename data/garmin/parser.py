"""Garmin GDPR export parser — discovers and parses chunked JSON files."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from .dto import GarminDailySummaryDTO, GarminHealthStatusDTO, GarminSleepDTO, GarminTrainingReadinessDTO

logger = logging.getLogger(__name__)

# File patterns per data type
_PATTERNS = {
    "sleep": ("DI-Connect-Wellness", "*_sleepData.json"),
    "daily": ("DI-Connect-Aggregator", "UDSFile_*.json"),
    "readiness": ("DI-Connect-Metrics", "TrainingReadinessDTO_*.json"),
    "health": ("DI-Connect-Wellness", "*_healthStatusData.json"),
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
