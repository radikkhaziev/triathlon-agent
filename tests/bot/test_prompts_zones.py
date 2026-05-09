"""Tests for the per-user zones block rendered into SYSTEM_PROMPT_CHAT.

Covers:
- _pct_ranges: sentinel trimming, top-zone capping.
- _pct_ranges_from_hr: bpm → %LTHR conversion.
- _zones_block: Intervals.icu sport-settings are preferred, Friel fallback
  kicks in when a sport has no synced boundaries.
- _format_sports: rendered profile-line for User.sports.
- Phase-2 wiring: full prompt builders inject the Sports: line correctly.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.prompts import (
    _format_sports,
    _pct_ranges,
    _pct_ranges_from_hr,
    _primary_sport,
    _show_ride_progression,
    _zones_block,
    get_system_prompt_v2,
    get_system_prompt_weekly,
    render_athlete_block,
)
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


class TestZonesBlockSportsFilter:
    """Phase 2 of USER_SPORTS_SPEC: ``sports`` arg restricts rendered sections.

    NULL/missing keeps the legacy all-three rendering for backward compat
    during the gate rollout. Non-empty list filters by lowercase enum.
    """

    def _thresholds(self, **kw):
        return AthleteThresholdsDTO(
            age=kw.get("age", 30),
            lthr_run=kw.get("lthr_run"),
            lthr_bike=kw.get("lthr_bike"),
            ftp=kw.get("ftp"),
            css=kw.get("css"),
        )

    def test_runner_only_renders_run_only(self):
        out = _zones_block(
            {},
            self._thresholds(lthr_run=170, ftp=240, css=110.0),
            sports=["run"],
        )
        assert "**Run**" in out
        assert "**Ride**" not in out
        assert "**Swim**" not in out

    def test_swim_run_drops_ride(self):
        """Swim+Run athlete gets Run + Swim sections, no Ride."""
        out = _zones_block(
            {},
            self._thresholds(lthr_run=170, ftp=240, css=110.0),
            sports=["swim", "run"],
        )
        assert "**Run**" in out
        assert "**Swim**" in out
        assert "**Ride**" not in out

    def test_ride_only_drops_run_and_swim(self):
        out = _zones_block(
            {},
            self._thresholds(lthr_run=170, ftp=240, css=110.0),
            sports=["ride"],
        )
        assert "**Ride**" in out
        assert "**Run**" not in out
        assert "**Swim**" not in out

    def test_null_renders_all_three_for_backward_compat(self):
        """Athletes who haven't passed through the picker (NULL sports)
        keep seeing all three sections — legacy behaviour. The gate
        ensures only migrating users hit this branch."""
        out = _zones_block(
            {},
            self._thresholds(lthr_run=170, ftp=240, css=110.0),
            sports=None,
        )
        assert "**Run**" in out
        assert "**Ride**" in out
        assert "**Swim**" in out

    def test_triathlete_unchanged_regression_guard(self):
        """`sports=["swim","ride","run"]` must produce identical output to
        the no-sports default (legacy triathlete path). If a future tweak
        accidentally changes the all-three render path, this catches it.

        Byte-for-byte comparison depends on the stable Run→Ride→Swim
        section ordering inside ``_zones_block`` — input `sports` order
        is membership-only. If someone refactors the function to a
        dict-driven dispatch with non-deterministic iteration, this
        assertion will start flaking. Read the docstring on _zones_block
        for the order contract."""
        thr = self._thresholds(lthr_run=170, ftp=240, css=110.0)
        legacy = _zones_block({}, thr)
        triathlete = _zones_block({}, thr, sports=["swim", "ride", "run"])
        assert legacy == triathlete

    def test_empty_list_normalises_to_none(self):
        """USER_SPORTS_SPEC code-review H1 (2026-05-08): defensive paths
        must agree on the [] case. ``_zones_block([])`` and
        ``_zones_block(None)`` produce identical output (render all),
        matching ``_format_sports([]) == _format_sports(None) == "all"``.
        Without this normalisation, a stray empty list would render
        "Sports: all" + zero zone sections — a contradictory prompt."""
        thr = self._thresholds(lthr_run=170, ftp=240, css=110.0)
        all_via_none = _zones_block({}, thr, sports=None)
        all_via_empty = _zones_block({}, thr, sports=[])
        assert all_via_none == all_via_empty
        # Sanity: all three sport sections present.
        assert "**Run**" in all_via_empty
        assert "**Ride**" in all_via_empty
        assert "**Swim**" in all_via_empty

    def test_unknown_values_filtered(self):
        """Forward-compat: if the User.sports enum is ever widened
        (e.g. ``"fitness"``), unknown values are filtered out before
        the membership check. A list with one known + one unknown
        renders just the known section; a list of only-unknowns falls
        back to render-all (same safety as the empty-list path).
        Mirrors ``_format_sports``'s passthrough so the two helpers
        stay in lockstep when the enum grows."""
        thr = self._thresholds(lthr_run=170, ftp=240, css=110.0)
        mixed = _zones_block({}, thr, sports=["run", "fitness"])
        assert "**Run**" in mixed
        assert "**Ride**" not in mixed
        assert "**Swim**" not in mixed

        only_unknown = _zones_block({}, thr, sports=["fitness"])
        all_via_none = _zones_block({}, thr, sports=None)
        assert only_unknown == all_via_none


class TestFormatSports:
    """Phase 2: ``_format_sports`` renders the profile-line value."""

    def test_none_renders_all(self):
        assert _format_sports(None) == "all"

    def test_empty_list_renders_all(self):
        """Defensive parity with ``_zones_block`` — see H1 normalisation."""
        assert _format_sports([]) == "all"

    def test_single_sport(self):
        assert _format_sports(["run"]) == "Run"

    def test_canonical_three_preserves_input_order(self):
        """No re-sorting in the formatter — caller decides ordering. The
        API canonicalises alphabetically, so the typical input is
        ``["ride","run","swim"]`` and renders as 'Ride, Run, Swim'."""
        assert _format_sports(["ride", "run", "swim"]) == "Ride, Run, Swim"

    def test_unknown_enum_passes_through_silently(self):
        """Unknown values fall through ``.get(s, s)`` to the raw lowercase.
        Server-side ``Literal`` validation should prevent this from ever
        reaching us, but a future enum extension would flow through here
        without crashing — captured as documented behaviour."""
        assert _format_sports(["run", "fitness"]) == "Run, fitness"


class TestPromptBuilderSportsInjection:
    """Phase 2 smoke: the three callers actually pass ``sports`` to
    ``.format()``. A typo in any of them would surface as a runtime
    KeyError on the morning-report cron — these tests catch it pre-deploy."""

    def _thr(self, sports):
        """Minimal thresholds DTO with sports set; other fields default to
        None and the prompt still builds (renders '—' for missing values)."""
        return AthleteThresholdsDTO(
            age=30,
            sports=sports,
            lthr_run=170,
            lthr_bike=150,
            ftp=240,
            css=110.0,
        )

    def test_get_system_prompt_v2_renders_sports_line(self):
        with (
            patch("bot.prompts.AthleteSettings.get_thresholds", return_value=self._thr(["run"])),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_v2(user_id=1, language="ru")
        assert "Sports: Run" in out
        assert "Age 30" in out

    def test_get_system_prompt_weekly_renders_sports_line(self):
        with (
            patch("bot.prompts.AthleteSettings.get_thresholds", return_value=self._thr(["ride", "run"])),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_weekly(user_id=1, language="ru")
        assert "Sports: Ride, Run" in out

    def test_get_system_prompt_v2_renders_all_for_null_sports(self):
        """Backward compat for users still in the gate rollout window."""
        with (
            patch("bot.prompts.AthleteSettings.get_thresholds", return_value=self._thr(None)),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_v2(user_id=1, language="ru")
        assert "Sports: all" in out

    @pytest.mark.asyncio
    async def test_render_athlete_block_renders_sports_and_zones(self):
        """End-to-end: chat tail must show both ``Sports: Run`` (profile
        line) and only the Run zone section (no Ride/Swim) for a runner."""
        with (
            patch(
                "bot.prompts.AthleteSettings.get_thresholds",
                new=AsyncMock(return_value=self._thr(["run"])),
            ),
            patch(
                "bot.prompts.AthleteGoal.get_goals_for_prompt",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "bot.prompts.AthleteSettings.get_all",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "bot.prompts._safe_compute_personal_patterns",
                new=AsyncMock(return_value={"entries_total": 0, "entries_complete": 0}),
            ),
            patch(
                "data.db.UserFact.list_active",
                new=AsyncMock(return_value=[]),
            ),
        ):
            out = await render_athlete_block(user_id=1, language="ru")

        # Profile line
        assert "Sports: Run" in out
        # Zones section narrowed to Run only
        assert "**Run**" in out
        assert "**Ride**" not in out
        assert "**Swim**" not in out

    @pytest.mark.asyncio
    async def test_render_athlete_block_triathlete_keeps_all_three(self):
        """Regression guard at the integration level: a triathlete sees
        all three zone sections AND ``Sports: Ride, Run, Swim`` profile
        line. Catches drift between the two prompt segments."""
        with (
            patch(
                "bot.prompts.AthleteSettings.get_thresholds",
                new=AsyncMock(return_value=self._thr(["ride", "run", "swim"])),
            ),
            patch(
                "bot.prompts.AthleteGoal.get_goals_for_prompt",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "bot.prompts.AthleteSettings.get_all",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "bot.prompts._safe_compute_personal_patterns",
                new=AsyncMock(return_value={"entries_total": 0, "entries_complete": 0}),
            ),
            patch(
                "data.db.UserFact.list_active",
                new=AsyncMock(return_value=[]),
            ),
        ):
            out = await render_athlete_block(user_id=1, language="ru")

        assert "Sports: Ride, Run, Swim" in out
        assert "**Run**" in out
        assert "**Ride**" in out
        assert "**Swim**" in out


class TestPrimarySport:
    """Phase 3: ``_primary_sport`` picks the lowercase enum that fills
    hardcoded ``sport=...`` slots in morning/weekly prompt examples.

    Resolution order is ``RAMP_PRIORITY = ("Run","Ride","Swim")`` so a
    triathlete's primary stays Run (legacy expectation) while a single-
    sport athlete gets their own discipline. NULL/empty → ``"run"``.
    """

    def test_none_falls_back_to_run(self):
        assert _primary_sport(None) == "run"

    def test_empty_falls_back_to_run(self):
        assert _primary_sport([]) == "run"

    def test_triathlete_primary_is_run(self):
        """Run wins by RAMP_PRIORITY ordering — keeps legacy behaviour."""
        assert _primary_sport(["swim", "ride", "run"]) == "run"

    def test_runner_only_returns_run(self):
        assert _primary_sport(["run"]) == "run"

    def test_cyclist_only_returns_ride(self):
        assert _primary_sport(["ride"]) == "ride"

    def test_swimmer_only_returns_swim(self):
        assert _primary_sport(["swim"]) == "swim"

    def test_swim_ride_combo_picks_ride(self):
        """Run > Ride > Swim priority — without Run, Ride wins."""
        assert _primary_sport(["swim", "ride"]) == "ride"

    def test_run_swim_combo_picks_run(self):
        assert _primary_sport(["swim", "run"]) == "run"


class TestShowRideProgression:
    """Phase 3: weekly prompt's Ride-only blocks (progression call,
    ML insights section) gate on this helper."""

    def test_none_shows_for_legacy_compat(self):
        assert _show_ride_progression(None) is True

    def test_triathlete_shows(self):
        assert _show_ride_progression(["swim", "ride", "run"]) is True

    def test_cyclist_only_shows(self):
        assert _show_ride_progression(["ride"]) is True

    def test_runner_only_hides(self):
        assert _show_ride_progression(["run"]) is False

    def test_swimmer_only_hides(self):
        assert _show_ride_progression(["swim"]) is False

    def test_run_swim_hides(self):
        assert _show_ride_progression(["run", "swim"]) is False

    def test_empty_list_treated_as_none_for_legacy_compat(self):
        """USER_SPORTS_SPEC code-review H1 (Phase 3 round): empty-list and
        NULL must agree. Diverging once produced "Sports: all" + zero zones
        in the chat tail (Phase 2 H1) — same drift here would render the
        weekly profile-line as "Sports: all" but drop the Ride-progression
        instruction. Both branches must collapse to legacy "render all"."""
        assert _show_ride_progression([]) is True


class TestPhase3PromptIntegration:
    """End-to-end: morning + weekly templates honour user.sports.

    Catches the most likely regression (typo in `.format(sports=...)`,
    missed placeholder) and the new conditional-rendering paths."""

    def _thr(self, sports):
        return AthleteThresholdsDTO(
            age=30,
            sports=sports,
            lthr_run=170,
            lthr_bike=150,
            ftp=240,
            css=110.0,
        )

    def test_morning_runner_uses_run_in_polarization(self):
        with (
            patch("bot.prompts.AthleteSettings.get_thresholds", return_value=self._thr(["run"])),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_v2(user_id=1, language="ru")
        assert "get_polarization_index(sport='run')" in out

    def test_morning_cyclist_uses_ride_in_polarization(self):
        with (
            patch("bot.prompts.AthleteSettings.get_thresholds", return_value=self._thr(["ride"])),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_v2(user_id=1, language="ru")
        assert "get_polarization_index(sport='ride')" in out
        # Negative: ensure we didn't accidentally leave the legacy hardcode.
        assert "get_polarization_index(sport='run')" not in out

    def test_morning_triathlete_unchanged_regression(self):
        """Triathlete primary is Run by RAMP_PRIORITY — output identical
        to the pre-Phase-3 hardcoded baseline."""
        with (
            patch(
                "bot.prompts.AthleteSettings.get_thresholds",
                return_value=self._thr(["swim", "ride", "run"]),
            ),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_v2(user_id=1, language="ru")
        assert "get_polarization_index(sport='run')" in out

    def test_morning_null_sports_falls_back_to_run(self):
        """Backward compat for users still in the gate rollout window."""
        with (
            patch("bot.prompts.AthleteSettings.get_thresholds", return_value=self._thr(None)),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_v2(user_id=1, language="ru")
        assert "get_polarization_index(sport='run')" in out

    def test_weekly_runner_only_drops_ride_progression_step(self):
        """No Ride in user.sports → no `get_progression_analysis(sport='Ride')`
        instruction (the call would query nothing useful) and no ML insights
        section (would just say 'no data').

        Tightened to assert the Ride-tagged step specifically — a future
        Phase 4 could add `get_progression_analysis(sport='Run')` for runners
        and we don't want this test to silently rebroaden."""
        with (
            patch("bot.prompts.AthleteSettings.get_thresholds", return_value=self._thr(["run"])),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_weekly(user_id=1, language="ru")
        assert "get_polarization_index(sport='run')" in out
        assert "get_progression_analysis(sport='Ride')" not in out
        assert "**ML insights** (Ride)" not in out

    def test_weekly_runner_only_has_consecutive_numbering(self):
        """USER_SPORTS_SPEC code-review H2 (Phase 3): without the
        rebuild-by-branch trick, non-Ride users saw `1,2,3,4,6,7` (gap
        at 5) in the format-section list. Claude tends to renumber the
        rendered output and a "section 5 missing" jump confuses users
        in Telegram. Assert non-Ride athletes get 1-6 sequential."""
        with (
            patch("bot.prompts.AthleteSettings.get_thresholds", return_value=self._thr(["run"])),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_weekly(user_id=1, language="ru")
        # Six-section run: ML insights branch is dropped, Наблюдение
        # promotes to 5, План на неделю to 6.
        assert "5. 🔍 **Наблюдение**" in out
        assert "6. 📅 **План на неделю**" in out
        # No gap and no leftover 7-section numbering.
        assert "7. 📅" not in out
        assert "6. 🔍" not in out

    def test_weekly_cyclist_keeps_ride_progression_step(self):
        with (
            patch("bot.prompts.AthleteSettings.get_thresholds", return_value=self._thr(["ride"])),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_weekly(user_id=1, language="ru")
        assert "get_polarization_index(sport='ride')" in out
        assert "get_progression_analysis(sport='Ride')" in out
        assert "ML insights" in out

    def test_weekly_triathlete_unchanged_regression(self):
        """Triathlete keeps both Ride-progression call and ML insights —
        legacy hardcoded shape preserved."""
        with (
            patch(
                "bot.prompts.AthleteSettings.get_thresholds",
                return_value=self._thr(["swim", "ride", "run"]),
            ),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_weekly(user_id=1, language="ru")
        assert "get_polarization_index(sport='run')" in out
        assert "get_progression_analysis(sport='Ride')" in out
        assert "ML insights" in out

    def test_weekly_by_sport_breakdown_lists_user_sports(self):
        """Section 1 hint includes the athlete's actual sports list so
        Claude doesn't bother breaking down sports the athlete doesn't train."""
        with (
            patch("bot.prompts.AthleteSettings.get_thresholds", return_value=self._thr(["ride", "run"])),
            patch("bot.prompts.AthleteGoal.get_goals_for_prompt", return_value=[]),
        ):
            out = get_system_prompt_weekly(user_id=1, language="ru")
        assert "by-sport breakdown (Ride, Run)" in out
