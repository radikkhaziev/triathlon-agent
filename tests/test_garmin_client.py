from data.garmin_client import _map_sport
from data.models import SportType


class TestMapSport:
    def test_running(self):
        assert _map_sport("running") == SportType.RUN

    def test_trail_running(self):
        assert _map_sport("trail_running") == SportType.RUN

    def test_cycling(self):
        assert _map_sport("cycling") == SportType.BIKE

    def test_indoor_cycling(self):
        assert _map_sport("indoor_cycling") == SportType.BIKE

    def test_lap_swimming(self):
        assert _map_sport("lap_swimming") == SportType.SWIM

    def test_open_water(self):
        assert _map_sport("open_water_swimming") == SportType.SWIM

    def test_strength(self):
        assert _map_sport("strength_training") == SportType.STRENGTH

    def test_unknown(self):
        assert _map_sport("yoga") == SportType.OTHER

    def test_empty_string(self):
        assert _map_sport("") == SportType.OTHER

    def test_case_insensitive(self):
        assert _map_sport("Running") == SportType.RUN

    def test_spaces_to_underscores(self):
        assert _map_sport("trail running") == SportType.RUN
