from data.models import DailyMetrics, GoalProgress, ScheduledWorkout


def format_morning_message(
    metrics: DailyMetrics,
    workout: ScheduledWorkout | None,
    goal: GoalProgress,
    ai_text: str,
) -> str:
    level_emoji = {"green": "\U0001f7e2", "yellow": "\U0001f7e1", "red": "\U0001f534"}
    level = level_emoji[metrics.readiness_level.value]

    hrv_arrow = (
        "\u2193" if metrics.hrv_delta_pct < -5
        else "\u2191" if metrics.hrv_delta_pct > 5
        else "\u2192"
    )

    sport_emoji = {
        "swimming": "\U0001f3ca",
        "cycling": "\U0001f6b4",
        "running": "\U0001f3c3",
        "strength_training": "\U0001f4aa",
        "other": "\U0001f3cb",
    }

    workout_text = "Rest day / no workout scheduled"
    if workout:
        emoji = sport_emoji.get(workout.sport.value, "\U0001f3cb")
        workout_text = f"{emoji} {workout.workout_name}"

    def progress_bar(pct: float, width: int = 8) -> str:
        filled = int((pct / 100) * width)
        return "\u2588" * filled + "\u2591" * (width - filled)

    return f"""\
\U0001f305 *Good morning! Report for {metrics.date.strftime('%B %d, %Y')}*

\u2501\u2501\u2501 READINESS \u2501\u2501\u2501
{level} *{metrics.readiness_score}/100*

HRV `{metrics.hrv_delta_pct:+.0f}%` {hrv_arrow}  Sleep `{metrics.sleep_score}/100`
Battery `{metrics.body_battery_morning}/100`  RHR `{metrics.resting_hr:.0f} bpm`

\u2501\u2501\u2501 TODAY'S PLAN \u2501\u2501\u2501
{workout_text}

\u2501\u2501\u2501 TRAINING LOAD \u2501\u2501\u2501
CTL `{metrics.ctl:.0f}` \u00b7 ATL `{metrics.atl:.0f}` \u00b7 TSB `{metrics.tsb:+.0f}`

\u2501\u2501\u2501 GOAL: {goal.event_name} ({goal.weeks_remaining} weeks) \u2501\u2501\u2501
\U0001f3ca `{progress_bar(goal.swim_pct)}` {goal.swim_pct:.0f}%
\U0001f6b4 `{progress_bar(goal.bike_pct)}` {goal.bike_pct:.0f}%
\U0001f3c3 `{progress_bar(goal.run_pct)}` {goal.run_pct:.0f}%

\u2501\u2501\u2501 AI RECOMMENDATION \u2501\u2501\u2501
{ai_text}
"""


def format_status_message(metrics: DailyMetrics) -> str:
    level_emoji = {"green": "\U0001f7e2", "yellow": "\U0001f7e1", "red": "\U0001f534"}
    level = level_emoji[metrics.readiness_level.value]

    return f"""\
{level} *Readiness: {metrics.readiness_score}/100*

HRV `{metrics.hrv_delta_pct:+.0f}%` | Sleep `{metrics.sleep_score}/100` | Battery `{metrics.body_battery_morning}/100`
RHR `{metrics.resting_hr:.0f} bpm`
CTL `{metrics.ctl:.0f}` | ATL `{metrics.atl:.0f}` | TSB `{metrics.tsb:+.0f}`
"""


def format_goal_message(goal: GoalProgress) -> str:
    def progress_bar(pct: float, width: int = 10) -> str:
        filled = int((pct / 100) * width)
        return "\u2588" * filled + "\u2591" * (width - filled)

    status = "\u2705 On track" if goal.on_track else "\u26a0\ufe0f Behind schedule"

    return f"""\
\U0001f3c1 *{goal.event_name}* \u2014 {goal.weeks_remaining} weeks remaining

{status}

\U0001f3ca Swim  `{progress_bar(goal.swim_pct)}` {goal.swim_pct:.0f}%
\U0001f6b4 Bike  `{progress_bar(goal.bike_pct)}` {goal.bike_pct:.0f}%
\U0001f3c3 Run   `{progress_bar(goal.run_pct)}` {goal.run_pct:.0f}%

Overall: *{goal.overall_pct:.0f}%*
"""
