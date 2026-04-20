"""Tests for the per-user zones block rendered into SYSTEM_PROMPT_CHAT.

Covers:
- _pct_ranges: sentinel trimming, top-zone capping.
- _pct_ranges_from_hr: bpm → %LTHR conversion.
- _zones_block: Intervals.icu sport-settings are preferred, Friel fallback
  kicks in when a sport has no synced boundaries.
"""

from types import SimpleNamespace

from bot.prompts import _pct_ranges, _pct_ranges_from_hr, _zones_block
from data.db.dto import AthleteThresholdsDTO


def _settings(**kw):
    defaults = dict(
        lthr=None,
        ftp=None,
        threshold_pace=None,
        hr_zones=None,
        power_zones=None,
        pace_zones=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestPctRanges:
    def test_trims_sentinel(self):
        # 999 is a common Intervals.icu sentinel for the open-ended top zone.
        ranges = _pct_ranges([55, 75, 90, 105, 120, 150, 999])
        assert ranges == [(0, 55), (55, 75), (75, 90), (90, 105), (105, 120), (120, 150), (150, 160)]

    def test_no_sentinel_still_appends_top(self):
        ranges = _pct_ranges([55, 75])
        assert ranges == [(0, 55), (55, 75), (75, 120)]


class TestPctRangesFromHr:
    def test_converts_bpm_to_percent_of_lthr(self):
        # forkbomb: LTHR 182, boundaries [153, 162, 171, 181, 186, 191, 200]
        ranges = _pct_ranges_from_hr([153, 162, 171, 181, 186, 191, 200], 182)
        # Z1 ..84, Z2 84-89, Z3 89-94, Z4 94-99, Z5 99-102, Z6 102-105, Z7 105-110, top
        assert ranges[0] == (0, 84)
        assert ranges[1] == (84, 89)
        assert ranges[2] == (89, 94)

    def test_monotonic_enforcement_prevents_zero_width_zones(self):
        # Two boundaries that would round to the same integer percent must be
        # bumped up so we never emit a (84, 84) range.
        # LTHR 200, boundaries [168, 169] → both round to 84 without monotonic fix.
        ranges = _pct_ranges_from_hr([168, 169], 200)
        assert ranges[0] == (0, 84)
        assert ranges[1] == (84, 85)  # bumped from 84 to 85
        # Strict monotonic: every range's end > its start.
        for lo, hi in ranges:
            assert hi > lo


class TestZonesBlock:
    def _thresholds(self, **kw):
        return AthleteThresholdsDTO(
            age=kw.get("age", 30),
            lthr_run=kw.get("lthr_run"),
            lthr_bike=kw.get("lthr_bike"),
            ftp=kw.get("ftp"),
            css=kw.get("css"),
        )

    def test_run_uses_intervals_zones_when_synced(self):
        settings = {
            "Run": _settings(lthr=182, hr_zones=[153, 162, 171, 181, 186, 191, 200]),
        }
        out = _zones_block(settings, self._thresholds(lthr_run=182))
        assert "LTHR = 182" in out
        assert "from your Intervals.icu sport-settings" in out
        assert "Z2 84-89%" in out

    def test_run_falls_back_without_synced_zones(self):
        out = _zones_block({}, self._thresholds(lthr_run=170))
        assert "Friel fallback" in out
        assert "Z2 72-82%" in out  # default Friel model

    def test_ride_power_zones_treated_as_percentages(self):
        # Intervals stores power_zones already as %FTP (not absolute watts).
        settings = {
            "Ride": _settings(lthr=175, ftp=250, power_zones=[55, 75, 90, 105, 120, 150, 999]),
        }
        out = _zones_block(settings, self._thresholds(ftp=250, lthr_bike=175))
        assert "FTP = 250W" in out
        assert "Z2 55-75%" in out  # endurance range, not 22-30% (the pre-fix bug)

    def test_ride_falls_back_to_friel_when_no_synced_power(self):
        out = _zones_block({}, self._thresholds(ftp=220, lthr_bike=170))
        assert "Friel fallback" in out
        assert "Z2 55-75%" in out

    def test_run_uses_dto_lthr_when_sport_row_lthr_is_missing(self):
        # Legit shape: some athlete_settings rows had hr_zones populated but
        # lthr=None. Before the guard relax, this fell to Friel — now it uses
        # t.lthr_run.
        settings = {
            "Run": _settings(lthr=None, hr_zones=[153, 162, 171, 181, 186, 191, 200]),
        }
        out = _zones_block(settings, self._thresholds(lthr_run=182))
        assert "from your Intervals.icu sport-settings" in out
        assert "Z2 84-89%" in out
        assert "Friel fallback" not in out.splitlines()[0]  # run line only

    def test_ride_power_zones_used_even_when_ride_ftp_missing(self):
        # power_zones are stored as %FTP, so the zones themselves don't need FTP.
        # Fall back to t.ftp for the displayed watts reference.
        settings = {
            "Ride": _settings(lthr=175, ftp=None, power_zones=[55, 75, 90, 105, 120, 150, 999]),
        }
        out = _zones_block(settings, self._thresholds(ftp=250, lthr_bike=175))
        ride_line = next(line for line in out.splitlines() if "**Ride**" in line)
        assert "from your Intervals.icu sport-settings" in ride_line
        assert "Z2 55-75%" in ride_line
        assert "FTP = 250W" in ride_line  # reference watts from DTO, not sport row

    def test_ride_power_zones_used_even_when_no_ftp_anywhere(self):
        # Edge: zones synced, but no FTP on any side → show "—" for the watts.
        settings = {
            "Ride": _settings(ftp=None, power_zones=[55, 75, 90, 105, 120, 150, 999]),
        }
        out = _zones_block(settings, self._thresholds())
        ride_line = next(line for line in out.splitlines() if "**Ride**" in line)
        assert "Z2 55-75%" in ride_line
        assert "FTP = —" in ride_line

    def test_ride_hr_fallback_uses_bike_constants_and_includes_example(self):
        # No synced power, no FTP at all → falls through to HR-based fallback.
        out = _zones_block({}, self._thresholds(lthr_bike=170))
        ride_line = next(line for line in out.splitlines() if "**Ride**" in line)
        assert "LTHR bike = 170" in ride_line
        assert "Z2 68-83%" in ride_line  # bike-specific fallback, not run's 72-82%
        assert "Z2 72-82%" not in ride_line
        assert 'Example Z2: `"hr":' in ride_line  # prompt contract requires example step

    def test_swim_css_formatted(self):
        out = _zones_block({}, self._thresholds(css=120.0))
        assert "CSS = 2:00/100m" in out

    def test_swim_css_missing(self):
        out = _zones_block({}, self._thresholds())
        assert "CSS = —" in out
