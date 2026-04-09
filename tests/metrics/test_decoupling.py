"""Tests for cardiac drift (decoupling) analysis functions."""

from data.metrics import decoupling_status, is_valid_for_decoupling

# ---------------------------------------------------------------------------
# decoupling_status — traffic light grading
# ---------------------------------------------------------------------------


class TestDecouplingStatus:
    def test_green_low(self):
        assert decoupling_status(2.0) == "green"

    def test_green_zero(self):
        assert decoupling_status(0.0) == "green"

    def test_green_boundary(self):
        assert decoupling_status(4.9) == "green"

    def test_yellow_boundary_low(self):
        assert decoupling_status(5.0) == "yellow"

    def test_yellow_mid(self):
        assert decoupling_status(7.5) == "yellow"

    def test_yellow_boundary_high(self):
        assert decoupling_status(10.0) == "yellow"

    def test_red_boundary(self):
        assert decoupling_status(10.1) == "red"

    def test_red_high(self):
        assert decoupling_status(25.0) == "red"

    def test_negative_green(self):
        """Negative drift (pulse drops) graded by abs value."""
        assert decoupling_status(-3.0) == "green"

    def test_negative_yellow(self):
        assert decoupling_status(-7.0) == "yellow"

    def test_negative_red(self):
        assert decoupling_status(-15.0) == "red"


# ---------------------------------------------------------------------------
# is_valid_for_decoupling — activity filter
# ---------------------------------------------------------------------------


class TestIsValidForDecoupling:
    """Test the decoupling analysis filter."""

    def test_valid_bike_ride(self):
        assert is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=3600,  # 60 min
            variability_index=1.00,
            hr_zone_times=[1800, 1500, 200, 100, 0],  # 91% Z1+Z2
            decoupling=5.3,
        )

    def test_valid_run(self):
        assert is_valid_for_decoupling(
            activity_type="Run",
            moving_time=2700,  # 45 min
            variability_index=1.05,
            hr_zone_times=[1500, 1000, 200, 0, 0],
            decoupling=8.0,
        )

    def test_swim_excluded(self):
        assert not is_valid_for_decoupling(
            activity_type="Swim",
            moving_time=3600,
            variability_index=1.00,
            hr_zone_times=[3600, 0, 0, 0, 0],
            decoupling=2.0,
        )

    def test_other_sport_excluded(self):
        assert not is_valid_for_decoupling(
            activity_type="Other",
            moving_time=3600,
            variability_index=1.00,
            hr_zone_times=[3600, 0, 0, 0, 0],
            decoupling=2.0,
        )

    def test_bike_too_short(self):
        """Bike needs >= 60 min."""
        assert not is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=3599,  # 59:59
            variability_index=1.00,
            hr_zone_times=[3000, 500, 0, 0, 0],
            decoupling=3.0,
        )

    def test_run_too_short(self):
        """Run needs >= 45 min."""
        assert not is_valid_for_decoupling(
            activity_type="Run",
            moving_time=2699,  # 44:59
            variability_index=1.00,
            hr_zone_times=[2000, 600, 0, 0, 0],
            decoupling=3.0,
        )

    def test_vi_too_high(self):
        """VI > 1.10 = interval session, exclude."""
        assert not is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=3600,
            variability_index=1.24,
            hr_zone_times=[3000, 500, 100, 0, 0],
            decoupling=5.0,
        )

    def test_vi_at_boundary(self):
        """VI exactly 1.10 is OK."""
        assert is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=3600,
            variability_index=1.10,
            hr_zone_times=[3000, 500, 100, 0, 0],
            decoupling=5.0,
        )

    def test_low_z12_fraction(self):
        """Less than 70% in Z1+Z2 = too much intensity."""
        assert not is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=3600,
            variability_index=1.05,
            hr_zone_times=[500, 500, 1000, 1000, 600],  # 28% Z1+Z2
            decoupling=5.0,
        )

    def test_z12_at_boundary(self):
        """Exactly 70% Z1+Z2 should pass."""
        assert is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=3600,
            variability_index=1.05,
            hr_zone_times=[700, 0, 300, 0, 0],  # 70%
            decoupling=5.0,
        )

    def test_decoupling_null(self):
        assert not is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=3600,
            variability_index=1.00,
            hr_zone_times=[3000, 500, 100, 0, 0],
            decoupling=None,
        )

    def test_moving_time_null(self):
        assert not is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=None,
            variability_index=1.00,
            hr_zone_times=[3000, 500, 100, 0, 0],
            decoupling=5.0,
        )

    def test_vi_null_passes(self):
        """If VI is not available, don't reject (data may be missing)."""
        assert is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=3600,
            variability_index=None,
            hr_zone_times=[3000, 500, 100, 0, 0],
            decoupling=5.0,
        )

    def test_hr_zone_times_null_passes(self):
        """If zone times are missing, don't reject."""
        assert is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=3600,
            variability_index=1.00,
            hr_zone_times=None,
            decoupling=5.0,
        )

    def test_hr_zone_times_empty_passes(self):
        """Empty list — don't reject."""
        assert is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=3600,
            variability_index=1.00,
            hr_zone_times=[],
            decoupling=5.0,
        )

    def test_negative_decoupling_valid(self):
        """Negative drift is still a valid decoupling value."""
        assert is_valid_for_decoupling(
            activity_type="Ride",
            moving_time=3600,
            variability_index=1.00,
            hr_zone_times=[3000, 500, 100, 0, 0],
            decoupling=-4.7,
        )
