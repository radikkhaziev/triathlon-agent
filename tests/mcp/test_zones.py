"""Tests for ``mcp_server/tools/zones.py``.

Coverage matrix:

- HR zones — Intervals-synced + LTHR-fallback paths, sport-tagged.
- Power zones — Intervals-synced (dual-unit), FTP-fallback (Coggan, dual-unit).
  Bike + Run can both have their own FTP; tags must not collide.
- Pace zones — Intervals-synced (dual-unit), threshold-pace-only fallback.
- Sentinel handling: a 999 boundary collapses to «no upper bound»; phantom
  zones whose lower bound IS the sentinel are dropped.
- ``css`` for Swim layered on top of pace zones, never overwrites real zones.
- Untagged ``power_zones`` / ``pace_zones`` keys must be absent (regression
  guard for the bug fixed in this PR).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from data.db.dto import AthleteThresholdsDTO

_MODULE = "mcp_server.tools.zones"


def _settings(
    sport: str,
    *,
    lthr=None,
    ftp=None,
    threshold_pace=None,
    pace_units=None,
    hr_zones=None,
    hr_zone_names=None,
    power_zones=None,
    power_zone_names=None,
    pace_zones=None,
    pace_zone_names=None,
):
    """Stand-in for an ``AthleteSettings`` row. Only the attributes
    ``_build_sport_zones`` reads."""
    return SimpleNamespace(
        sport=sport,
        lthr=lthr,
        ftp=ftp,
        threshold_pace=threshold_pace,
        pace_units=pace_units,
        hr_zones=hr_zones,
        hr_zone_names=hr_zone_names,
        power_zones=power_zones,
        power_zone_names=power_zone_names,
        pace_zones=pace_zones,
        pace_zone_names=pace_zone_names,
    )


def _thresholds(*, max_hr=191, age=33, css=None) -> AthleteThresholdsDTO:
    return AthleteThresholdsDTO(max_hr=max_hr, age=age, css=css)


async def _call_get_zones(all_settings: list, t: AthleteThresholdsDTO = None) -> dict:
    """Invoke ``get_zones`` with mocked ORM accessors. ``t`` defaults to a
    minimal owner-shape thresholds DTO."""
    from mcp_server.tools.zones import get_zones

    if t is None:
        t = _thresholds()
    with (
        patch(f"{_MODULE}.get_current_user_id", return_value=1),
        patch(f"{_MODULE}.AthleteSettings.get_all", new=AsyncMock(return_value=all_settings)),
        patch(f"{_MODULE}.AthleteSettings.get_thresholds", new=AsyncMock(return_value=t)),
    ):
        return await get_zones()


# ---------------------------------------------------------------------------
# HR zones
# ---------------------------------------------------------------------------


class TestHrZones:
    @pytest.mark.asyncio
    async def test_intervals_synced_run_uses_absolute_bpm(self):
        result = await _call_get_zones(
            [
                _settings(
                    "Run",
                    lthr=172,
                    hr_zones=[145, 153, 162, 171, 176, 181, 190],
                    hr_zone_names=["Recovery", "Aerobic", "Tempo", "SubT", "Z5", "Z6", "Z7"],
                )
            ]
        )
        block = result["hr_zones_run"]
        assert block["lthr"] == 172
        assert block["source"] == "intervals.icu"
        # Z2: 146-153 (boundary[0]+1 → boundary[1])
        z2 = block["zones"][1]
        assert z2["min_hr"] == 146
        assert z2["max_hr"] == 153
        # Top zone opens upward
        top = block["zones"][-1]
        assert "max_hr" not in top

    @pytest.mark.asyncio
    async def test_lthr_fallback_when_zones_unsynced(self):
        result = await _call_get_zones([_settings("Run", lthr=170)])
        block = result["hr_zones_run"]
        assert block["source"] == "calculated"
        # Friel Run: Z2 Aerobic = 0.85-0.89 of 170 = 144-151 (int truncation)
        assert any(z["name"] == "Aerobic" for z in block["zones"])

    @pytest.mark.asyncio
    async def test_no_block_without_lthr(self):
        result = await _call_get_zones([_settings("Run")])
        assert "hr_zones_run" not in result


# ---------------------------------------------------------------------------
# Power zones — sport-tagged + dual-unit (the core bug fix)
# ---------------------------------------------------------------------------


class TestPowerZonesShape:
    @pytest.mark.asyncio
    async def test_owner_shape_bike_and_run_coexist(self):
        """Bike has Intervals-synced %FTP zones; Run has FTP only → fallback.
        Both keys must be present in the response (regression guard)."""
        result = await _call_get_zones(
            [
                _settings(
                    "Ride",
                    ftp=208,
                    power_zones=[55, 75, 90, 105, 120, 150, 999],
                    power_zone_names=[
                        "Active Recovery",
                        "Endurance",
                        "Tempo",
                        "Threshold",
                        "VO2 Max",
                        "Anaerobic",
                        "Neuromuscular",
                    ],
                ),
                _settings("Run", ftp=366),
            ]
        )
        assert "power_zones_bike" in result
        assert "power_zones_run" in result
        assert result["power_zones_bike"]["ftp"] == 208
        assert result["power_zones_run"]["ftp"] == 366

    @pytest.mark.asyncio
    async def test_dual_unit_keys_on_intervals_synced_zone(self):
        result = await _call_get_zones([_settings("Ride", ftp=208, power_zones=[55, 75, 90, 105, 120, 150, 999])])
        z2 = result["power_zones_bike"]["zones"][1]
        # Endurance 55-75% × 208 = 114-156W
        assert z2["min_pct"] == 55
        assert z2["max_pct"] == 75
        assert z2["min_w"] == 114
        assert z2["max_w"] == 156

    @pytest.mark.asyncio
    async def test_dual_unit_keys_on_fallback_zone(self):
        result = await _call_get_zones([_settings("Run", ftp=366)])
        z4 = result["power_zones_run"]["zones"][3]
        # Coggan Threshold = 90-105% × 366 = 329-384W
        assert z4["name"] == "Threshold"
        assert z4["min_pct"] == 90
        assert z4["max_pct"] == 105
        assert z4["min_w"] == 329
        assert z4["max_w"] == 384

    @pytest.mark.asyncio
    async def test_sentinel_collapses_to_open_top(self):
        """Last boundary == 999 → top zone has no max_pct/max_w. Phantom
        zone whose lower bound is the sentinel must be dropped."""
        result = await _call_get_zones([_settings("Ride", ftp=208, power_zones=[55, 75, 90, 105, 120, 150, 999])])
        zones = result["power_zones_bike"]["zones"]
        assert len(zones) == 7  # not 8 — phantom zone dropped
        top = zones[-1]
        assert "max_pct" not in top
        assert "max_w" not in top
        assert top["min_pct"] == 150
        assert top["min_w"] == 312

    @pytest.mark.asyncio
    async def test_no_untagged_power_zones_key(self):
        """Regression guard for the original bug — last sport must not write
        an untagged ``power_zones`` key."""
        result = await _call_get_zones(
            [
                _settings("Ride", ftp=208, power_zones=[55, 75, 999]),
                _settings("Run", ftp=366),
            ]
        )
        assert "power_zones" not in result

    @pytest.mark.asyncio
    async def test_no_block_without_ftp(self):
        result = await _call_get_zones([_settings("Ride")])  # no ftp, no power_zones
        assert "power_zones_bike" not in result


# ---------------------------------------------------------------------------
# Pace zones — same dual-unit shape, inverted asymmetry
# ---------------------------------------------------------------------------


class TestPaceZonesShape:
    @pytest.mark.asyncio
    async def test_run_pace_dual_unit(self):
        """Run threshold_pace=287 s/km (4:47/km). Z3 boundary 87.7-94.3 →
        287×100/87.7 ≈ 327, 287×100/94.3 ≈ 304. Higher pct = faster pace."""
        result = await _call_get_zones(
            [
                _settings(
                    "Run",
                    threshold_pace=287.0,
                    pace_zones=[77.5, 87.7, 94.3, 100.0, 103.4, 111.5, 999.0],
                )
            ]
        )
        zones = result["pace_zones_run"]["zones"]
        # Z3: boundary[1]=87.7 → boundary[2]=94.3
        z3 = zones[2]
        assert z3["min_pct"] == 87.7
        assert z3["max_pct"] == 94.3
        # Slow side (low pct) → bigger sec_per_km
        assert z3["max_sec_per_km"] == 327
        # Fast side (high pct) → smaller sec_per_km
        assert z3["min_sec_per_km"] == 304

    @pytest.mark.asyncio
    async def test_swim_pace_uses_per_100m_unit(self):
        result = await _call_get_zones(
            [
                _settings(
                    "Swim",
                    threshold_pace=141.0,
                    pace_units="SECS_100M",
                    pace_zones=[77.5, 87.7, 94.3, 100.0, 103.4, 111.5, 999.0],
                )
            ]
        )
        zones = result["pace_zones_swim"]["zones"]
        z3 = zones[2]
        assert "max_sec_per_100m" in z3
        assert "max_sec_per_km" not in z3
        # 141 × 100/87.7 ≈ 161
        assert z3["max_sec_per_100m"] == 161

    @pytest.mark.asyncio
    async def test_first_zone_has_no_max_sec(self):
        """Z1 has min_pct=0 → no slower limit, so max_sec_per_X must be omitted
        (would otherwise be infinity)."""
        result = await _call_get_zones([_settings("Run", threshold_pace=287.0, pace_zones=[77.5, 87.7, 999.0])])
        z1 = result["pace_zones_run"]["zones"][0]
        assert z1["min_pct"] == 0
        assert "max_sec_per_km" not in z1
        # Fast edge present
        assert z1["min_sec_per_km"] == 370  # 287 × 100/77.5

    @pytest.mark.asyncio
    async def test_top_zone_has_no_min_sec_at_sentinel(self):
        """Last zone bordered by 999 sentinel → no faster limit, so
        min_sec_per_X must be omitted."""
        result = await _call_get_zones([_settings("Run", threshold_pace=287.0, pace_zones=[77.5, 87.7, 999.0])])
        zones = result["pace_zones_run"]["zones"]
        top = zones[-1]
        assert "min_sec_per_km" not in top
        assert "max_pct" not in top
        # Slow edge present (came from boundary[1]=87.7)
        assert top["max_sec_per_km"] == 327

    @pytest.mark.asyncio
    async def test_threshold_pace_only_no_zones_emits_summary(self):
        """No pace_zones synced but threshold_pace exists → fallback to
        threshold-only block (no zones array)."""
        result = await _call_get_zones([_settings("Run", threshold_pace=287.0, pace_units="MINS_KM")])
        block = result["pace_zones_run"]
        assert block["threshold_pace_sec"] == 287.0
        assert block["threshold_pace_formatted"] == "4:47/MINS_KM"

    @pytest.mark.asyncio
    async def test_no_untagged_pace_zones_key(self):
        result = await _call_get_zones(
            [
                _settings("Run", threshold_pace=287.0, pace_zones=[77.5, 999.0]),
                _settings("Swim", threshold_pace=141.0, pace_zones=[77.5, 999.0]),
            ]
        )
        assert "pace_zones" not in result


# ---------------------------------------------------------------------------
# CSS layering for Swim
# ---------------------------------------------------------------------------


class TestCssLayer:
    @pytest.mark.asyncio
    async def test_css_layered_on_existing_swim_block(self):
        result = await _call_get_zones(
            [_settings("Swim", threshold_pace=141.0, pace_zones=[77.5, 999.0])],
            t=_thresholds(css=141.0),
        )
        block = result["pace_zones_swim"]
        assert "zones" in block  # original synced data preserved
        assert block["css"] == 141.0
        assert block["css_formatted"] == "2:21/100m"

    @pytest.mark.asyncio
    async def test_css_creates_swim_block_if_missing(self):
        """When an athlete has CSS but no Swim AthleteSettings row, the tool
        should still surface CSS — never silently drop it."""
        result = await _call_get_zones([], t=_thresholds(css=90.0))
        assert result["pace_zones_swim"]["css"] == 90.0
        assert result["pace_zones_swim"]["css_formatted"] == "1:30/100m"


# ---------------------------------------------------------------------------
# Top-level shape regression
# ---------------------------------------------------------------------------


class TestTopLevelShape:
    @pytest.mark.asyncio
    async def test_owner_full_shape(self):
        """Mirrors the real owner row — three sports, intervals-synced for
        Ride+Swim, FTP-only for Run."""
        result = await _call_get_zones(
            [
                _settings(
                    "Ride",
                    lthr=165,
                    ftp=208,
                    hr_zones=[133, 147, 153, 164, 169, 174, 191],
                    power_zones=[55, 75, 90, 105, 120, 150, 999],
                ),
                _settings("Run", lthr=172, ftp=366, threshold_pace=287.0),
                _settings(
                    "Swim",
                    lthr=172,
                    threshold_pace=141.0,
                    hr_zones=[145, 153, 162, 171, 176, 181, 190],
                    pace_zones=[77.5, 87.7, 94.3, 100.0, 103.4, 111.5, 999.0],
                ),
            ],
            t=_thresholds(css=141.0),
        )
        # All three sports present per kind
        assert {"hr_zones_bike", "hr_zones_run", "hr_zones_swim"}.issubset(result)
        assert {"power_zones_bike", "power_zones_run"}.issubset(result)
        assert {"pace_zones_run", "pace_zones_swim"}.issubset(result)
        # Untagged keys absent
        assert "power_zones" not in result
        assert "pace_zones" not in result
        # Run's power block came from FTP fallback (no power_zones synced)
        assert result["power_zones_run"]["source"] == "calculated"
        # Bike's power block came from Intervals
        assert result["power_zones_bike"]["source"] == "intervals.icu"
