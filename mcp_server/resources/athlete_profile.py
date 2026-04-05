"""MCP resources — read-only athlete profile, goal, and thresholds."""

from config import settings
from data.db import AthleteConfig
from mcp_server.context import get_current_user_id


def register_resources(mcp):
    """Register all static resources on the MCP server."""

    @mcp.resource("athlete://profile")
    def athlete_profile() -> str:
        """Athlete profile: age, heart rate thresholds, power, swim speed, HR zones."""
        user_id = get_current_user_id()
        t = AthleteConfig.get_thresholds(user_id)

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
    def race_goal() -> str:
        """Current race goal: event name, date, CTL targets (total + per-sport)."""
        user_id = get_current_user_id()
        g = AthleteConfig.get_goal(user_id)
        if not g:
            return "No active goal set."

        lines = [
            f"Event: {g.event_name}",
            f"Date: {g.event_date}",
            f"Sport: {g.sport_type}",
        ]
        if g.ctl_target:
            lines.append(f"CTL Target (total): {g.ctl_target}")
        if g.per_sport_targets:
            for sport, target in g.per_sport_targets.items():
                lines.append(f"CTL Target ({sport}): {target}")
        return "\n".join(lines)

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
            "HRV Algorithm: " + settings.HRV_ALGORITHM + " (primary)\n"
            "  Flatt & Esco: today vs 7d mean, asymmetric bounds (-1/+0.5 SD)\n"
            "  AIEndurance: 7d mean vs 60d mean, symmetric bounds (±0.5 SD)\n"
            "\n"
            "RHR: inverted — elevated RHR = red, low RHR = green\n"
            "  Bounds: ±0.5 SD of 30-day mean\n"
            "\n"
            "SWC (Smallest Worthwhile Change): 0.5 × SD_60d\n"
            "CV: <5% very stable, 5-10% normal, >10% unreliable\n"
        )
