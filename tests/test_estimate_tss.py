"""AC-2 — estimate_tss (docs/PLANNED_LOAD_SPEC.md §4).

Deterministic: TSS = Σ dur_h · IF² · 100, IF = mid(start,end)/100, repeat
groups expanded, open (duration:0 + target) steps absorb the
`moving_time − Σ timed` residual. Anchors: the spec's 2026-05-17 run (≈48)
and the 2026-05-24 residual run (≈103).
"""

from data.intervals.dto import WorkoutStepDTO, estimate_tss


def _steps(raw):
    return WorkoutStepDTO.from_raw_list(raw)


def test_flat_single_step_midpoint_if_squared():
    # 1 h at pace 70-80% → IF 0.75 → 1·0.75²·100 = 56.25
    steps = _steps([{"text": "Z", "duration": 3600, "pace": {"start": 70, "end": 80, "units": "%pace"}}])
    assert estimate_tss(steps, 3600) == 56


def test_repeat_group_expansion():
    # 4× [60s @95% + 60s @45%] → 4·(0.016667·90.25 + 0.016667·20.25) ≈ 7
    steps = _steps(
        [
            {
                "text": "4x",
                "reps": 4,
                "duration": 480,
                "steps": [
                    {"text": "Hard", "duration": 60, "power": {"start": 90, "end": 100, "units": "%ftp"}},
                    {"text": "Easy", "duration": 60, "power": {"start": 40, "end": 50, "units": "%ftp"}},
                ],
            }
        ]
    )
    assert estimate_tss(steps, 480) == 7


def test_open_step_absorbs_residual():
    # WU 300s + open Interval (dur 0) + CD 300s, moving_time 3600 →
    # residual 3000s @75%. 3+3+46.875 ≈ 53
    steps = _steps(
        [
            {"text": "WU", "duration": 300, "pace": {"start": 50, "end": 70, "units": "%pace"}},
            {"text": "Interval", "duration": 0, "pace": {"start": 70, "end": 80, "units": "%pace"}},
            {"text": "CD", "duration": 300, "pace": {"start": 50, "end": 70, "units": "%pace"}},
        ]
    )
    assert estimate_tss(steps, 3600) == 53


def test_multiple_open_steps_split_residual_evenly():
    # 2 open steps, no timed, moving_time 3600 → 1800s each @75% → 28.125·2 ≈ 56
    steps = _steps(
        [
            {"text": "A", "duration": 0, "pace": {"start": 70, "end": 80, "units": "%pace"}},
            {"text": "B", "duration": 0, "pace": {"start": 70, "end": 80, "units": "%pace"}},
        ]
    )
    assert estimate_tss(steps, 3600) == 56


def test_repeat_group_reps_zero_expands_zero_times():
    """PR#398: `reps=0` must expand 0×, not 1× (was `s.reps or 1`).
    Only the trailing 600s @85% (≈12) counts; the reps=0 block contributes 0.
    """
    steps = _steps(
        [
            {
                "text": "0x",
                "reps": 0,
                "duration": 0,
                "steps": [{"text": "X", "duration": 600, "power": {"start": 90, "end": 100, "units": "%ftp"}}],
            },
            {"text": "Real", "duration": 600, "power": {"start": 80, "end": 90, "units": "%ftp"}},
        ]
    )
    assert estimate_tss(steps, 1200) == 12


def test_target_less_rest_step_contributes_zero():
    # 600s @85% + 120s rest (no target) → only the interval counts ≈ 12
    steps = _steps(
        [
            {"text": "Interval", "duration": 600, "power": {"start": 80, "end": 90, "units": "%ftp"}},
            {"text": "Rest", "duration": 120},
        ]
    )
    assert estimate_tss(steps, 720) == 12


def test_stepless_plan_returns_none():
    assert estimate_tss([], 3600) is None
    assert estimate_tss(None, 3600) is None


def test_open_step_without_moving_time_returns_none():
    # duration:0 target step, nothing bounds it → None (don't fabricate load)
    steps = _steps([{"text": "Run", "duration": 0, "pace": {"start": 70, "end": 80, "units": "%pace"}}])
    assert estimate_tss(steps, None) is None


def test_all_rest_returns_none():
    # steps exist but zero intensity everywhere → 0 → None (don't push 0)
    steps = _steps([{"text": "Rest", "duration": 600}])
    assert estimate_tss(steps, 600) is None


def test_spec_anchor_2026_05_17_run_about_48():
    """Spec §4 worked example: 50-min %pace run ≈ 48 TSS."""
    steps = _steps(
        [
            {"text": "Warm-up", "duration": 300, "pace": {"start": 50, "end": 83, "units": "%pace"}},
            {"text": "Interval", "duration": 2400, "pace": {"start": 73, "end": 83, "units": "%pace"}},
            {"text": "Cool-down", "duration": 300, "pace": {"start": 58, "end": 73, "units": "%pace"}},
        ]
    )
    assert estimate_tss(steps, 3000) == 48


def test_spec_anchor_2026_05_24_residual_run_about_103():
    """Open «steady» block: timed 900s, moving_time 6300s → residual 5400s ≈ 103."""
    steps = _steps(
        [
            {"text": "Warm-up", "duration": 300, "pace": {"start": 58, "end": 73, "units": "%pace"}},
            {"text": "Warm-up", "duration": 300, "pace": {"start": 73, "end": 83, "units": "%pace"}},
            {"text": "Interval", "duration": 0, "pace": {"start": 73, "end": 83, "units": "%pace"}},
            {"text": "Cool-down", "duration": 300, "pace": {"start": 58, "end": 73, "units": "%pace"}},
        ]
    )
    assert estimate_tss(steps, 6300) == 103


def test_distance_only_targeted_step_classified_open():
    """Pins review M1 / spec §8: a duration:0 step with `distance` + a target
    is treated as OPEN (predicate ignores distance) and absorbs the residual
    evenly. WU 300s @60% (3.0) + dist step @75% taking residual 3300s
    (0.91667·56.25 ≈ 51.56) → 55. If a future change makes the predicate
    distance-aware, this fails — exactly the regression guard intended.
    """
    steps = _steps(
        [
            {"text": "WU", "duration": 300, "pace": {"start": 50, "end": 70, "units": "%pace"}},
            {"text": "Set", "duration": 0, "distance": 200, "pace": {"start": 70, "end": 80, "units": "%pace"}},
        ]
    )
    assert estimate_tss(steps, 3600) == 55
