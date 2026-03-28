import json
import logging
import zoneinfo
from datetime import date, datetime, timedelta

import anthropic

from ai.prompts import (
    MORNING_REPORT_PROMPT,
    WORKOUT_GENERATION_PROMPT,
    get_system_prompt,
    get_system_prompt_chat,
    get_system_prompt_v2,
)
from ai.tool_definitions import CHAT_TOOLS, MORNING_TOOLS, TOOL_HANDLERS
from bot.formatter import format_duration, sport_emoji
from config import settings
from data.models import PlannedWorkout, WorkoutStep
from data.utils import extract_sport_ctl_tuple

logger = logging.getLogger(__name__)


async def build_morning_prompt(
    wellness_row,
    hrv_flatt,
    hrv_aie,
    rhr_row,
    scheduled_workouts: list | None = None,
    *,
    template: str = MORNING_REPORT_PROMPT,
) -> str:
    """Build the fully formatted morning report prompt.

    Args:
        template: Prompt template string. Defaults to MORNING_REPORT_PROMPT (Claude).
                  Pass MORNING_REPORT_PROMPT_GEMINI for Gemini.
    """
    hrv_today = float(wellness_row.hrv) if wellness_row.hrv else 0
    hrv_7d = hrv_flatt.rmssd_7d if hrv_flatt else 0
    hrv_delta = 0.0
    if hrv_today and hrv_7d and hrv_7d > 0:
        hrv_delta = (hrv_today - hrv_7d) / hrv_7d * 100

    sleep_secs = wellness_row.sleep_secs or 0
    h, m = divmod(sleep_secs // 60, 60)
    sleep_duration = f"{h}ч {m}м" if h else f"{m}м"

    tsb = (wellness_row.ctl - wellness_row.atl) if wellness_row.ctl and wellness_row.atl else 0

    # Goal progress
    event_date = settings.GOAL_EVENT_DATE
    weeks_remaining = max(0, (event_date - date.today()).days // 7)
    goal_pct = (
        min(100, (wellness_row.ctl / settings.GOAL_CTL_TARGET * 100))
        if wellness_row.ctl and settings.GOAL_CTL_TARGET
        else 0
    )

    # Per-sport CTL from sport_info JSON (enriched with calculated CTL)
    ctl_swim, ctl_bike, ctl_run = extract_sport_ctl_tuple(wellness_row.sport_info)
    swim_pct = (
        min(100, ctl_swim / settings.GOAL_SWIM_CTL_TARGET * 100) if settings.GOAL_SWIM_CTL_TARGET and ctl_swim else 0
    )
    bike_pct = (
        min(100, ctl_bike / settings.GOAL_BIKE_CTL_TARGET * 100) if settings.GOAL_BIKE_CTL_TARGET and ctl_bike else 0
    )
    run_pct = min(100, ctl_run / settings.GOAL_RUN_CTL_TARGET * 100) if settings.GOAL_RUN_CTL_TARGET and ctl_run else 0

    planned_text = _format_planned_workouts(scheduled_workouts)
    yesterday_dfa_text = await _format_yesterday_dfa()

    return template.format(
        date=date.today().strftime("%d.%m.%Y"),
        recovery_score=wellness_row.recovery_score or 0,
        recovery_category=wellness_row.recovery_category or "unknown",
        recovery_recommendation=wellness_row.recovery_recommendation or "—",
        ess_today=wellness_row.ess_today or 0,
        banister_recovery=(wellness_row.banister_recovery if wellness_row.banister_recovery is not None else 50.0),
        sleep_score=wellness_row.sleep_score or 0,
        sleep_duration=sleep_duration,
        hrv_today=f"{hrv_today:.0f}" if hrv_today else "—",
        hrv_7d=f"{hrv_7d:.0f}" if hrv_7d else "—",
        hrv_delta=hrv_delta,
        hrv_status_flatt=hrv_flatt.status if hrv_flatt else "insufficient_data",
        hrv_status_aie=hrv_aie.status if hrv_aie else "insufficient_data",
        hrv_cv=f"{hrv_flatt.cv_7d:.1f}" if hrv_flatt and hrv_flatt.cv_7d else "—",
        hrv_swc_verdict=_swc_verdict(hrv_today, hrv_flatt),
        rhr_today=f"{rhr_row.rhr_today:.0f}" if rhr_row and rhr_row.rhr_today else "—",
        rhr_30d=f"{rhr_row.rhr_30d:.0f}" if rhr_row and rhr_row.rhr_30d else "—",
        rhr_delta=(rhr_row.rhr_today - rhr_row.rhr_30d) if rhr_row and rhr_row.rhr_today and rhr_row.rhr_30d else 0,
        rhr_status=rhr_row.status if rhr_row else "insufficient_data",
        ctl=wellness_row.ctl or 0,
        atl=wellness_row.atl or 0,
        tsb=tsb,
        ramp_rate=wellness_row.ramp_rate or 0,
        ctl_swim=ctl_swim,
        ctl_bike=ctl_bike,
        ctl_run=ctl_run,
        ctl_swim_target=settings.GOAL_SWIM_CTL_TARGET,
        ctl_bike_target=settings.GOAL_BIKE_CTL_TARGET,
        ctl_run_target=settings.GOAL_RUN_CTL_TARGET,
        goal_event=settings.GOAL_EVENT_NAME,
        weeks_remaining=weeks_remaining,
        goal_pct=goal_pct,
        swim_pct=swim_pct,
        bike_pct=bike_pct,
        run_pct=run_pct,
        planned_workouts=planned_text,
        yesterday_dfa_summary=yesterday_dfa_text,
    )


class ClaudeAgent:
    def __init__(self) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY.get_secret_value())
        self.model = "claude-sonnet-4-6"

    async def get_morning_recommendation(
        self,
        wellness_row,
        hrv_flatt,
        hrv_aie,
        rhr_row,
        scheduled_workouts: list | None = None,
        *,
        prompt: str | None = None,
    ) -> str:
        """Generate morning AI recommendation from current wellness data.

        Args:
            wellness_row: WellnessRow for today
            hrv_flatt: HrvAnalysisRow for flatt_esco (or None)
            hrv_aie: HrvAnalysisRow for ai_endurance (or None)
            rhr_row: RhrAnalysisRow for today (or None)
            scheduled_workouts: list of ScheduledWorkoutRow for today (or None)
            prompt: Pre-built prompt (if None, builds it from wellness data)
        """
        if prompt is None:
            prompt = await build_morning_prompt(
                wellness_row=wellness_row,
                hrv_flatt=hrv_flatt,
                hrv_aie=hrv_aie,
                rhr_row=rhr_row,
                scheduled_workouts=scheduled_workouts,
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
            raise

    async def _run_tool_use_loop(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 4096,
        max_iterations: int = 10,
    ) -> str:
        """Run Claude API with tool-use loop. Returns final text response.

        Shared between morning analysis (V2) and free-form chat.
        """
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )

        iterations = 0
        while response.stop_reason == "tool_use" and iterations < max_iterations:
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await self._execute_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        }
                    )

            # response.content — list of ContentBlock; SDK accepts as-is
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            response = await self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
            )
            iterations += 1

        text_blocks = [b.text for b in response.content if b.type == "text"]
        return "\n".join(text_blocks)

    async def get_morning_recommendation_v2(self, target_date: date) -> str:
        """Generate morning AI recommendation using tool-use."""
        system = get_system_prompt_v2()
        messages: list[dict] = [
            {"role": "user", "content": f"Сгенерируй утренний отчёт за {target_date.strftime('%Y-%m-%d')}"},
        ]
        result = await self._run_tool_use_loop(system, messages, MORNING_TOOLS, max_tokens=4096)
        return result or "Не удалось сгенерировать отчёт"

    async def chat(self, user_message: str) -> str:
        """Handle a free-form chat message. Stateless: no conversation history."""
        system = get_system_prompt_chat()
        messages: list[dict] = [{"role": "user", "content": user_message}]
        result = await self._run_tool_use_loop(system, messages, CHAT_TOOLS, max_tokens=2048)
        return result or "Не удалось обработать запрос."

    async def _execute_tool(self, name: str, input_data: dict) -> dict:
        """Execute a tool call and return the result."""
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return {"error": f"Unknown tool: {name}"}
        try:
            return await handler(**input_data)
        except Exception as e:
            logger.warning("Tool %s failed: %s", name, e)
            return {"error": str(e)}

    async def generate_workout(
        self,
        wellness_row,
        hrv_flatt,
        hrv_aie,
        rhr_row,
    ) -> PlannedWorkout | None:
        """Generate a structured workout based on current athlete state.

        Returns PlannedWorkout, or None if rest day is recommended.
        """
        hrv_today = float(wellness_row.hrv) if wellness_row.hrv else 0
        hrv_7d = hrv_flatt.rmssd_7d if hrv_flatt else 0
        hrv_delta = (hrv_today - hrv_7d) / hrv_7d * 100 if hrv_today and hrv_7d else 0.0
        hrv_status = hrv_flatt.status if hrv_flatt else "insufficient_data"

        tsb = (wellness_row.ctl - wellness_row.atl) if wellness_row.ctl and wellness_row.atl else 0
        ctl_swim, ctl_bike, ctl_run = extract_sport_ctl_tuple(wellness_row.sport_info)

        event_date = settings.GOAL_EVENT_DATE
        weeks_remaining = max(0, (event_date - date.today()).days // 7)

        yesterday_summary = await _format_yesterday_dfa()

        prompt = WORKOUT_GENERATION_PROMPT.format(
            athlete_age=settings.ATHLETE_AGE,
            lthr_run=settings.ATHLETE_LTHR_RUN,
            lthr_bike=settings.ATHLETE_LTHR_BIKE,
            ftp=settings.ATHLETE_FTP,
            css=settings.ATHLETE_CSS,
            goal_event=settings.GOAL_EVENT_NAME,
            goal_date=settings.GOAL_EVENT_DATE,
            weeks_remaining=weeks_remaining,
            recovery_score=wellness_row.recovery_score or 0,
            recovery_category=wellness_row.recovery_category or "unknown",
            hrv_delta=hrv_delta,
            hrv_status=hrv_status,
            rhr_today=f"{rhr_row.rhr_today:.0f}" if rhr_row and rhr_row.rhr_today else "—",
            rhr_30d=f"{rhr_row.rhr_30d:.0f}" if rhr_row and rhr_row.rhr_30d else "—",
            sleep_score=wellness_row.sleep_score or 0,
            ctl=wellness_row.ctl or 0,
            atl=wellness_row.atl or 0,
            tsb=tsb,
            ramp_rate=wellness_row.ramp_rate or 0,
            ctl_swim=ctl_swim,
            ctl_bike=ctl_bike,
            ctl_run=ctl_run,
            ctl_swim_target=settings.GOAL_SWIM_CTL_TARGET,
            ctl_bike_target=settings.GOAL_BIKE_CTL_TARGET,
            ctl_run_target=settings.GOAL_RUN_CTL_TARGET,
            yesterday_summary=yesterday_summary,
        )

        try:
            message = await self.client.messages.create(
                model=self.model,
                max_tokens=512,
                system=get_system_prompt(),
                messages=[{"role": "user", "content": prompt}],
            )
            text = message.content[0].text.strip()

            # Parse JSON response (strip markdown fences if present)
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            data = json.loads(text)

            # Rest day
            if data.get("sport") == "Rest":
                logger.info("AI recommended rest day: %s", data.get("rationale", ""))
                return None

            # Validate sport type
            valid_sports = {"Ride", "Run", "Swim", "WeightTraining"}
            if data.get("sport") not in valid_sports:
                logger.warning("AI returned invalid sport: %s", data.get("sport"))
                return None

            # Parse steps into WorkoutStep models
            raw_steps = data.get("steps", [])
            steps = [_parse_step(s) for s in raw_steps]

            return PlannedWorkout(
                sport=data["sport"],
                name=data["name"],
                steps=steps,
                duration_minutes=data["duration_minutes"],
                target_tss=data.get("target_tss"),
                rationale=data.get("rationale", ""),
                target_date=date.today(),
            )
        except json.JSONDecodeError:
            logger.exception("Failed to parse workout JSON from Claude: %s", text[:200])
            return None
        except Exception:
            logger.exception("Workout generation failed")
            return None

    async def analyze_week(
        self,
        activities_summary: str,
        metrics_summary: str,
    ) -> str:
        prompt = f"""Дай краткий итог тренировочной недели и рекомендации.

АКТИВНОСТИ ЗА НЕДЕЛЮ:
{activities_summary}

ТРЕНД МЕТРИК:
{metrics_summary}

Ответь:
1. Краткий итог недели (объём, баланс интенсивности)
2. Наблюдение о трендах восстановления
3. Одна рекомендация на следующую неделю
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
            raise


async def _format_yesterday_dfa() -> str:
    """Format yesterday's DFA data for the morning AI prompt."""
    from data.database import get_activities_for_date, get_activity_hrv_for_date

    tz = zoneinfo.ZoneInfo(settings.TIMEZONE)
    yesterday = datetime.now(tz).date() - timedelta(days=1)
    activities = await get_activities_for_date(yesterday)
    if not activities:
        return "Нет данных DFA за вчера"

    hrv_analyses = await get_activity_hrv_for_date(yesterday)
    hrv_map = {h.activity_id: h for h in hrv_analyses}

    lines = []
    for a in activities:
        emoji = sport_emoji(a.type)
        dur_str = format_duration(a.moving_time)

        hrv = hrv_map.get(a.id)
        if hrv and hrv.processing_status == "processed":
            detail_parts: list[str] = []
            if hrv.ra_pct is not None:
                detail_parts.append(f"Ra {hrv.ra_pct:+.1f}%")
            if hrv.da_pct is not None:
                detail_parts.append(f"Da {hrv.da_pct:+.1f}%")
            if hrv.hrvt1_hr is not None:
                hrvt1 = f"HRVT1 {hrv.hrvt1_hr:.0f}bpm"
                if hrv.hrvt1_power is not None:
                    hrvt1 += f"/{hrv.hrvt1_power:.0f}W"
                if hrv.hrvt1_pace is not None:
                    hrvt1 += f"/{hrv.hrvt1_pace}"
                detail_parts.append(hrvt1)
            if hrv.hrv_quality:
                detail_parts.append(f"quality: {hrv.hrv_quality}")
            details = ", ".join(detail_parts)
            lines.append(f"- {emoji} {a.type or '?'} {dur_str}: {details}")
        elif hrv:
            lines.append(f"- {emoji} {a.type or '?'} {dur_str}: {hrv.processing_status}")
        # Activities without HRV row (not eligible) — skip

    return "\n".join(lines) if lines else "Нет данных DFA за вчера"


def _format_planned_workouts(workouts: list | None) -> str:
    """Format scheduled workouts for the AI prompt."""
    if not workouts:
        return "Нет запланированных тренировок"
    lines = []
    for w in workouts:
        dur = f"{w.moving_time // 60} мин" if w.moving_time else "—"
        parts = [f"- {w.type or '?'}: {w.name or '—'} ({dur})"]
        if w.description:
            parts.append(f"  Детали: {w.description}")
        lines.append("\n".join(parts))
    return "\n".join(lines)


def _parse_step(raw: dict) -> WorkoutStep:
    """Parse a raw step dict from Claude JSON into a WorkoutStep model."""
    sub_steps = None
    if "steps" in raw and raw["steps"]:
        sub_steps = [_parse_step(s) for s in raw["steps"]]
    return WorkoutStep(
        text=raw.get("text", ""),
        duration=raw.get("duration", 0),
        reps=raw.get("reps"),
        hr=raw.get("hr"),
        power=raw.get("power"),
        pace=raw.get("pace"),
        cadence=raw.get("cadence"),
        steps=sub_steps,
    )


def _swc_verdict(hrv_today: float, hrv_row) -> str:
    if not hrv_today or not hrv_row or not hrv_row.swc or not hrv_row.rmssd_60d:
        return "недостаточно данных"
    delta = hrv_today - hrv_row.rmssd_60d
    if abs(delta) < hrv_row.swc:
        return "в пределах шума"
    if delta > 0:
        return "значимое улучшение"
    return "значимое снижение"
