"""MCP resources — read-only athlete profile, goal, and thresholds."""

from config import settings


def register_resources(mcp):
    """Register all static resources on the MCP server."""

    @mcp.resource("athlete://profile")
    def athlete_profile() -> str:
        """Static athlete profile: age, heart rate thresholds, power, swim speed."""
        lthr_run = settings.ATHLETE_LTHR_RUN
        lthr_bike = settings.ATHLETE_LTHR_BIKE
        return (
            f"Age: {settings.ATHLETE_AGE}\n"
            f"LTHR Run: {lthr_run} bpm\n"
            f"LTHR Bike: {lthr_bike} bpm\n"
            f"Max HR: {settings.ATHLETE_MAX_HR} bpm\n"
            f"Resting HR: {settings.ATHLETE_RESTING_HR} bpm\n"
            f"FTP: {settings.ATHLETE_FTP} W\n"
            f"CSS: {settings.ATHLETE_CSS} s/100m\n"
            f"\n"
            f"HR Zones (Run, % of LTHR {lthr_run}):\n"
            f"  Z1: 0-{int(lthr_run * 0.72)} ({0}-72%)\n"
            f"  Z2: {int(lthr_run * 0.72)}-{int(lthr_run * 0.82)} (72-82%)\n"
            f"  Z3: {int(lthr_run * 0.82)}-{int(lthr_run * 0.87)} (82-87%)\n"
            f"  Z4: {int(lthr_run * 0.87)}-{int(lthr_run * 0.92)} (87-92%)\n"
            f"  Z5: {int(lthr_run * 0.92)}-{lthr_run} (92-100%)\n"
            f"\n"
            f"HR Zones (Bike, % of LTHR {lthr_bike}):\n"
            f"  Z1: 0-{int(lthr_bike * 0.68)} (0-68%)\n"
            f"  Z2: {int(lthr_bike * 0.68)}-{int(lthr_bike * 0.83)} (68-83%)\n"
            f"  Z3: {int(lthr_bike * 0.83)}-{int(lthr_bike * 0.94)} (83-94%)\n"
            f"  Z4: {int(lthr_bike * 0.94)}-{int(lthr_bike * 1.05)} (94-105%)\n"
            f"  Z5: {int(lthr_bike * 1.05)}-{int(lthr_bike * 1.20)} (105-120%)\n"
        )

    @mcp.resource("athlete://goal")
    def race_goal() -> str:
        """Current race goal: event name, date, CTL targets (total + per-sport)."""
        return (
            f"Event: {settings.GOAL_EVENT_NAME}\n"
            f"Date: {settings.GOAL_EVENT_DATE}\n"
            f"CTL Target (total): {settings.GOAL_CTL_TARGET}\n"
            f"CTL Target (swim): {settings.GOAL_SWIM_CTL_TARGET}\n"
            f"CTL Target (bike): {settings.GOAL_BIKE_CTL_TARGET}\n"
            f"CTL Target (run): {settings.GOAL_RUN_CTL_TARGET}\n"
        )

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
