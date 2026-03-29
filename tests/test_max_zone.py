"""Tests for compute_max_zone() — actual_max_zone_time in training_log."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.utils import compute_max_zone


def _detail(**kwargs):
    """Create a mock ActivityDetailRow with zone time arrays."""
    defaults = {
        "hr_zone_times": None,
        "power_zone_times": None,
        "pace_zone_times": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
@patch("bot.utils.ActivityDetailRow.get", new_callable=AsyncMock)
class TestComputeMaxZone:
    """Tests for compute_max_zone()."""

    async def test_hr_run_6elem(self, mock_get):
        """Run with 6-element hr_zone_times (below_z1 + Z1-Z5) → Z2."""
        mock_get.return_value = _detail(hr_zone_times=[30, 60, 1800, 600, 120, 30])
        assert await compute_max_zone("a1", sport="Run") == "Z2"

    async def test_hr_run_5elem(self, mock_get):
        """Run with 5-element hr_zone_times (Z1-Z5) → Z2."""
        mock_get.return_value = _detail(hr_zone_times=[60, 1800, 600, 120, 30])
        assert await compute_max_zone("a1", sport="Run") == "Z2"

    async def test_power_ride(self, mock_get):
        """Ride with power_zone_times → uses power over hr."""
        mock_get.return_value = _detail(
            power_zone_times=[0, 120, 300, 1200, 600, 60],
            hr_zone_times=[0, 1800, 600, 200, 50, 10],
        )
        assert await compute_max_zone("a1", sport="Ride") == "Z3"

    async def test_ride_fallback_hr(self, mock_get):
        """Ride without power_zone_times → fallback to hr_zone_times."""
        mock_get.return_value = _detail(hr_zone_times=[0, 60, 1800, 600, 120, 30])
        assert await compute_max_zone("a1", sport="Ride") == "Z2"

    async def test_swim_pace(self, mock_get):
        """Swim with pace_zone_times → uses pace."""
        mock_get.return_value = _detail(pace_zone_times=[0, 200, 400, 800, 100, 0])
        assert await compute_max_zone("a1", sport="Swim") == "Z3"

    async def test_no_detail(self, mock_get):
        """No ActivityDetailRow → None."""
        mock_get.return_value = None
        assert await compute_max_zone("a1") is None

    async def test_empty_zones(self, mock_get):
        """Empty hr_zone_times → None."""
        mock_get.return_value = _detail(hr_zone_times=[])
        assert await compute_max_zone("a1") is None

    async def test_all_zeros(self, mock_get):
        """All zone times are 0 → None."""
        mock_get.return_value = _detail(hr_zone_times=[0, 0, 0, 0, 0, 0])
        assert await compute_max_zone("a1") is None

    async def test_short_array(self, mock_get):
        """hr_zone_times < 5 elements → None."""
        mock_get.return_value = _detail(hr_zone_times=[100, 200, 300])
        assert await compute_max_zone("a1") is None

    async def test_tie_takes_lower_zone(self, mock_get):
        """Equal time in Z2 and Z4 → prefer Z2 (lower zone)."""
        mock_get.return_value = _detail(hr_zone_times=[0, 100, 500, 200, 500, 50])
        assert await compute_max_zone("a1") == "Z2"

    async def test_no_sport_uses_hr(self, mock_get):
        """No sport specified → uses hr_zone_times."""
        mock_get.return_value = _detail(hr_zone_times=[0, 100, 1800, 600, 120, 30])
        assert await compute_max_zone("a1") == "Z2"

    async def test_z5_dominant(self, mock_get):
        """Z5 has highest time → Z5."""
        mock_get.return_value = _detail(hr_zone_times=[0, 10, 20, 30, 40, 2000])
        assert await compute_max_zone("a1") == "Z5"
