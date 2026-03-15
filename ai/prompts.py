from config import settings

SYSTEM_PROMPT = """
You are a personal AI triathlon coach. Your role is to analyze an athlete's
physiological data and provide specific, actionable training recommendations.

Athlete profile:
- Experienced triathlete, age {athlete_age}
- Target race: {goal_event}
- Uses Garmin device for all monitoring

Response rules:
1. Be specific — mention numbers, zones, durations
2. Always consider training load history when making recommendations
3. If HRV is more than 15% below baseline -> recommend reducing intensity
4. If TSB < -25 -> recommend a rest or recovery day
5. Keep recommendations under 250 words
6. Use emoji sparingly for readability
7. Respond in the same language the prompt is written in
"""

MORNING_REPORT_PROMPT = """
Analyze today's training readiness and provide recommendations.

Date: {date}

LAST NIGHT SLEEP:
- Sleep score: {sleep_score}/100
- Duration: {sleep_duration}
- Last night HRV: {hrv_last} (7-day baseline: {hrv_baseline}, delta: {hrv_delta:+.0f}%)
- Resting HR: {resting_hr} bpm (baseline: {resting_hr_baseline} bpm)
- Body Battery (morning): {body_battery}/100
- Yesterday stress score: {stress_score}/100

TRAINING LOAD:
- CTL (fitness): {ctl:.1f}
- ATL (fatigue): {atl:.1f}
- TSB (form): {tsb:+.1f}
- Swimming CTL: {ctl_swim:.1f}
- Cycling CTL: {ctl_bike:.1f}
- Running CTL: {ctl_run:.1f}

TODAY'S PLAN (from Garmin/HumanGO calendar):
{workout_today}

RACE GOAL ({goal_event}, {weeks_remaining} weeks away):
- Overall readiness: {goal_pct:.0f}%
- Swim: {swim_pct:.0f}% | Bike: {bike_pct:.0f}% | Run: {run_pct:.0f}%

Please provide:
1. Readiness assessment (Green / Yellow / Red) with brief reasoning
2. Specific workout recommendation for today (adjust planned workout if needed)
3. One observation about the current training load trend
4. One short note on goal progression
"""


def get_system_prompt() -> str:
    return SYSTEM_PROMPT.format(
        athlete_age=settings.ATHLETE_AGE,
        goal_event=settings.GOAL_EVENT_NAME,
    )
