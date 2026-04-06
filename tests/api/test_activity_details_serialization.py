from data.db import ActivityDetail
from data.utils import serialize_activity_details


def test_serialize_activity_details_includes_zone_times() -> None:
    row = ActivityDetail(
        activity_id="i123",
        hr_zones=[145, 153, 162, 171, 176],
        power_zones=[100, 140, 170, 210, 260],
        pace_zones=[420, 390, 360, 330, 300],
        hr_zone_times=[1200, 300, 0, 0, 0],
        power_zone_times=[600, 900, 300, 0, 0],
        pace_zone_times=[0, 1200, 600, 0, 0],
    )

    payload = serialize_activity_details(row)

    assert payload["hr_zone_times"] == [1200, 300, 0, 0, 0]
    assert payload["power_zone_times"] == [600, 900, 300, 0, 0]
    assert payload["pace_zone_times"] == [0, 1200, 600, 0, 0]
