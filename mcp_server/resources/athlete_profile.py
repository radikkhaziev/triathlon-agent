"""MCP resources — read-only athlete profile, goal, and thresholds."""

from mcp.server.fastmcp import FastMCP

from data.db import AthleteGoal, AthleteSettings
from data.db.dto import AthleteGoalDTO, AthleteThresholdsDTO
from mcp_server.context import get_current_user_id
from tasks.dto import local_today


def render_race_goal_resource(goals: list[AthleteGoalDTO]) -> str:
    """Render the body of the ``athlete://goal`` MCP resource.

    Module-level helper (extracted from a closure) so the rendering rules can
    be exercised from unit tests — specifically the ``CTL=0`` not-skipped
    invariant flagged in PR #325 by Copilot.

    Output shapes:
      * **0 goals** — single line «No active goal set.»
      * **1 goal**  — block with no label.
      * **2 goals** — RACE_A then Nearest, separated by blank line.
    """
    if not goals:
        return "No active goal set."

    lines: list[str] = []

    def _emit(g: AthleteGoalDTO, label: str) -> None:
        prefix = f"{label}: " if label else ""
        lines.append(f"{prefix}{g.event_name}")
        lines.append(f"  Date: {g.event_date}")
        lines.append(f"  Sport: {g.sport_type}")
        # Overall ``ctl_target=0``: legit «no load» target during full taper /
        # injury recovery — render it (``is not None`` not truthy, Copilot #325).
        if g.ctl_target is not None:
            lines.append(f"  CTL Target (total): {g.ctl_target}")
        # Per-sport ``target=0``: by project convention means «not part of this
        # race plan» (e.g. swim=0 on a run-only goal), not full taper. Drop to
        # match the Dashboard endpoint's per-sport rule (api/routers/dashboard.py).
        # Keeping the two consumers aligned avoids the «Claude sees swim=0,
        # webapp doesn't» divergence flagged in code review.
        if g.per_sport_targets:
            for sport, target in g.per_sport_targets.items():
                if target is None or target <= 0:
                    continue
                lines.append(f"  CTL Target ({sport}): {target}")

    if len(goals) == 1:
        _emit(goals[0], label="")
    else:
        _emit(goals[0], label="RACE_A")
        lines.append("")
        _emit(goals[1], label="Nearest")

    return "\n".join(lines)


def register_resources(mcp: FastMCP) -> None:
    """Register all static resources on the MCP server."""

    @mcp.resource("athlete://profile")
    async def athlete_profile() -> str:
        """Athlete profile: age, heart rate thresholds, power, swim speed, HR zones."""
        user_id = get_current_user_id()
        t: AthleteThresholdsDTO = await AthleteSettings.get_thresholds(user_id)

        lines = [f"Age: {t.age or '—'}"]

        if t.lthr_run:
            lines.append(f"LTHR Run: {t.lthr_run} bpm")
        if t.lthr_bike:
            lines.append(f"LTHR Bike: {t.lthr_bike} bpm")
        if t.max_hr:
            lines.append(f"Max HR: {t.max_hr} bpm")
        lines.append("Resting HR: dynamic (from daily wellness sync)")
        if t.ftp:
            lines.append(f"FTP: {t.ftp} W")
        if t.css:
            lines.append(f"CSS: {t.css} s/100m")

        if t.lthr_run:
            lr = t.lthr_run
            lines.append(f"\nHR Zones (Run, % of LTHR {lr}):")
            lines.append(f"  Z1: 0-{int(lr * 0.72)} (0-72%)")
            lines.append(f"  Z2: {int(lr * 0.72)}-{int(lr * 0.82)} (72-82%)")
            lines.append(f"  Z3: {int(lr * 0.82)}-{int(lr * 0.87)} (82-87%)")
            lines.append(f"  Z4: {int(lr * 0.87)}-{int(lr * 0.92)} (87-92%)")
            lines.append(f"  Z5: {int(lr * 0.92)}-{lr} (92-100%)")

        if t.lthr_bike:
            lb = t.lthr_bike
            lines.append(f"\nHR Zones (Bike, % of LTHR {lb}):")
            lines.append(f"  Z1: 0-{int(lb * 0.68)} (0-68%)")
            lines.append(f"  Z2: {int(lb * 0.68)}-{int(lb * 0.83)} (68-83%)")
            lines.append(f"  Z3: {int(lb * 0.83)}-{int(lb * 0.94)} (83-94%)")
            lines.append(f"  Z4: {int(lb * 0.94)}-{int(lb * 1.05)} (94-105%)")
            lines.append(f"  Z5: {int(lb * 1.05)}-{int(lb * 1.20)} (105-120%)")

        return "\n".join(lines)

    @mcp.resource("athlete://goal")
    async def race_goal() -> str:
        """Current race goal(s) — RACE_A always (if set) plus the nearest race
        if it differs from RACE_A (typically a B/C tune-up). See #323 Strand D
        for the «focus on RACE_A; nearest is tactical context» framing.
        """
        user_id = get_current_user_id()
        goals: list[AthleteGoalDTO] = await AthleteGoal.get_goals_for_prompt(user_id, local_today())
        return render_race_goal_resource(goals)

    @mcp.resource("athlete://thresholds")
    def thresholds() -> str:
        """Business rules and thresholds for training decisions.

        All CTL/ATL/TSB values come from Intervals.icu. Thresholds are calibrated
        for Intervals.icu impulse-response model, NOT TrainingPeaks PMC.
        """
        return (
            "TSB Zones (Intervals.icu calibrated):\n"
            "  > +10: under-training\n"
            "  -10 to +10: optimal\n"
            "  -10 to -25: productive overreach\n"
            "  < -25: overtraining risk\n"
            "\n"
            "Ramp Rate:\n"
            "  <= 7 TSS/week: safe\n"
            "  > 7 TSS/week: injury risk, flag and reduce\n"
            "\n"
            "HRV Recovery Status:\n"
            "  green (above upper bound): full load\n"
            "  yellow (between bounds): train as planned, monitor\n"
            "  red (below lower bound): reduce intensity or rest\n"
            "\n"
            "Recovery Score (0-100):\n"
            "  Weights: RMSSD 35%, Banister 25%, RHR 20%, Sleep 20%\n"
            "  excellent >85: any intensity\n"
            "  good 70-85: Z2 full volume\n"
            "  moderate 40-70: Z1-Z2 only, 45-60 min\n"
            "  low <40: rest or Z1 <=30 min\n"
            "\n"
            "HRV Algorithm: Flatt & Esco — today vs 7d mean, asymmetric bounds (-1/+0.5 SD)\n"
            "\n"
            "RHR: inverted — elevated RHR = red, low RHR = green\n"
            "  Bounds: ±0.5 SD of 30-day mean\n"
            "\n"
            "SWC (Smallest Worthwhile Change): 0.5 × SD_60d\n"
            "CV: <5% very stable, 5-10% normal, >10% unreliable\n"
        )
