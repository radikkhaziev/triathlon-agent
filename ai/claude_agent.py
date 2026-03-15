import logging

import anthropic

from ai.prompts import MORNING_REPORT_PROMPT, get_system_prompt
from config import settings
from data.models import DailyMetrics, GoalProgress, ScheduledWorkout

logger = logging.getLogger(__name__)


class ClaudeAgent:
    def __init__(self) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY.get_secret_value())
        self.model = "claude-sonnet-4-6"

    async def get_morning_recommendation(
        self,
        metrics: DailyMetrics,
        hrv_last: float,
        hrv_baseline: float,
        sleep_duration_str: str,
        stress_score: int,
        resting_hr_baseline: float,
        workout: ScheduledWorkout | None,
        goal: GoalProgress,
    ) -> str:
        workout_text = "Rest day / no workout scheduled"
        if workout:
            workout_text = f"{workout.sport.value}: {workout.workout_name}"
            if workout.description:
                workout_text += f"\n  {workout.description}"

        prompt = MORNING_REPORT_PROMPT.format(
            date=metrics.date.strftime("%B %d, %Y"),
            sleep_score=metrics.sleep_score,
            sleep_duration=sleep_duration_str,
            hrv_last=hrv_last,
            hrv_baseline=hrv_baseline,
            hrv_delta=metrics.hrv_delta_pct,
            resting_hr=metrics.resting_hr,
            resting_hr_baseline=resting_hr_baseline,
            body_battery=metrics.body_battery_morning,
            stress_score=stress_score,
            ctl=metrics.ctl,
            atl=metrics.atl,
            tsb=metrics.tsb,
            ctl_swim=metrics.ctl_swim,
            ctl_bike=metrics.ctl_bike,
            ctl_run=metrics.ctl_run,
            workout_today=workout_text,
            goal_event=goal.event_name,
            weeks_remaining=goal.weeks_remaining,
            goal_pct=goal.overall_pct,
            swim_pct=goal.swim_pct,
            bike_pct=goal.bike_pct,
            run_pct=goal.run_pct,
        )

        try:
            message = await self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=get_system_prompt(),
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception:
            logger.exception("Claude API call failed")
            return "AI recommendation unavailable. Check logs for details."

    async def analyze_week(
        self,
        activities_summary: str,
        metrics_summary: str,
    ) -> str:
        prompt = f"""Provide a brief weekly training summary and recommendations.

WEEKLY ACTIVITIES:
{activities_summary}

WEEKLY METRICS TREND:
{metrics_summary}

Please provide:
1. Brief summary of the training week (volume, intensity balance)
2. Key observation about recovery trends
3. One recommendation for next week
"""
        try:
            message = await self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=get_system_prompt(),
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception:
            logger.exception("Claude API call failed")
            return "AI analysis unavailable."
