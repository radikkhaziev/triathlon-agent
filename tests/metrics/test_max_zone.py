"""Tests for compute_max_zone_sync() — actual_max_zone_time in training_log."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _detail(**kwargs):
    """Create a mock ActivityDetail with zone time arrays."""
    defaults = {
        "hr_zone_times": None,
        "power_zone_times": None,
        "pace_zone_times": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _mock_session(detail):
    """Create a mock session context manager that returns detail on .get()."""
    session = MagicMock()
    session.get.return_value = detail
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


@patch("tasks.utils.get_sync_session")
class TestComputeMaxZone:
    """Tests for compute_max_zone_sync()."""

    def test_hr_run_6elem(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(_detail(hr_zone_times=[30, 60, 1800, 600, 120, 30]))
        assert compute_max_zone_sync("a1", sport="Run") == "Z2"

    def test_hr_run_5elem(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(_detail(hr_zone_times=[60, 1800, 600, 120, 30]))
        assert compute_max_zone_sync("a1", sport="Run") == "Z2"

    def test_power_ride(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(
            _detail(power_zone_times=[0, 120, 300, 1200, 600, 60], hr_zone_times=[0, 1800, 600, 200, 50, 10])
        )
        assert compute_max_zone_sync("a1", sport="Ride") == "Z3"

    def test_ride_fallback_hr(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(_detail(hr_zone_times=[0, 60, 1800, 600, 120, 30]))
        assert compute_max_zone_sync("a1", sport="Ride") == "Z2"

    def test_swim_pace(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(_detail(pace_zone_times=[0, 200, 400, 800, 100, 0]))
        assert compute_max_zone_sync("a1", sport="Swim") == "Z3"

    def test_no_detail(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(None)
        assert compute_max_zone_sync("a1") is None

    def test_empty_zones(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(_detail(hr_zone_times=[]))
        assert compute_max_zone_sync("a1") is None

    def test_all_zeros(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(_detail(hr_zone_times=[0, 0, 0, 0, 0, 0]))
        assert compute_max_zone_sync("a1") is None

    def test_short_array(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(_detail(hr_zone_times=[100, 200, 300]))
        assert compute_max_zone_sync("a1") is None

    def test_tie_takes_lower_zone(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(_detail(hr_zone_times=[0, 100, 500, 200, 500, 50]))
        assert compute_max_zone_sync("a1") == "Z2"

    def test_no_sport_uses_hr(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(_detail(hr_zone_times=[0, 100, 1800, 600, 120, 30]))
        assert compute_max_zone_sync("a1") == "Z2"

    def test_z5_dominant(self, mock_session_fn):
        from tasks.utils import compute_max_zone_sync

        mock_session_fn.return_value = _mock_session(_detail(hr_zone_times=[0, 10, 20, 30, 40, 2000]))
        assert compute_max_zone_sync("a1") == "Z5"
