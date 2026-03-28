"""Tests for Phase 1: Adaptive Training Plan — AI workout generation."""

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from data.models import PlannedWorkout, WorkoutStep

# ---------------------------------------------------------------------------
# WorkoutStep
# ---------------------------------------------------------------------------


class TestWorkoutStep:
    def test_simple_step(self):
        step = WorkoutStep(text="Warm-up", duration=600, hr={"units": "%lthr", "value": 65})
        assert step.text == "Warm-up"
        assert step.duration == 600
        assert step.hr == {"units": "%lthr", "value": 65}
        assert step.power is None

    def test_power_step(self):
        step = WorkoutStep(text="Z2", duration=1800, power={"units": "%ftp", "value": 75})
        assert step.power["value"] == 75

    def test_repeat_group(self):
        step = WorkoutStep(
            text="Tempo",
            reps=3,
            steps=[
                WorkoutStep(duration=300, hr={"units": "%lthr", "value": 88}),
                WorkoutStep(duration=120, hr={"units": "%lthr", "value": 65}),
            ],
        )
        assert step.reps == 3
        assert len(step.steps) == 2
        assert step.duration == 0

    def test_model_dump_excludes_none(self):
        step = WorkoutStep(text="Warm-up", duration=600, hr={"units": "%lthr", "value": 65})
        d = step.model_dump(exclude_none=True)
        assert "power" not in d
        assert "pace" not in d
        assert "cadence" not in d
        assert "reps" not in d
        assert "steps" not in d
        assert d["text"] == "Warm-up"
        assert d["duration"] == 600


# ---------------------------------------------------------------------------
# PlannedWorkout
# ---------------------------------------------------------------------------


class TestPlannedWorkout:
    def _make_workout(self, **kwargs):
        defaults = dict(
            sport="Ride",
            name="Z2 Endurance",
            steps=[WorkoutStep(text="Main", duration=3600, power={"units": "%ftp", "value": 75})],
            duration_minutes=60,
            target_date=date(2026, 3, 29),
        )
        defaults.update(kwargs)
        return PlannedWorkout(**defaults)

    def test_external_id_format(self):
        w = self._make_workout(sport="Ride", target_date=date(2026, 3, 29), slot="morning")
        assert w.external_id == "tricoach:2026-03-29:ride:morning"

    def test_external_id_evening_slot(self):
        w = self._make_workout(slot="evening")
        assert w.external_id.endswith(":evening")

    def test_to_intervals_event_keys(self):
        w = self._make_workout()
        event = w.to_intervals_event()
        assert event["category"] == "WORKOUT"
        assert event["type"] == "Ride"
        assert event["start_date_local"] == "2026-03-29T00:00:00"
        assert event["moving_time"] == 3600
        assert event["external_id"] == "tricoach:2026-03-29:ride:morning"

    def test_generated_suffix(self):
        w = self._make_workout(suffix="generated")
        event = w.to_intervals_event()
        assert event["name"] == "AI: Z2 Endurance (generated)"

    def test_adapted_suffix(self):
        w = self._make_workout(suffix="adapted")
        event = w.to_intervals_event()
        assert event["name"] == "AI: Z2 Endurance (adapted)"

    def test_workout_doc_in_event(self):
        steps = [
            WorkoutStep(text="Warm-up", duration=600, power={"units": "%ftp", "value": 60}),
            WorkoutStep(text="Main", duration=1800, power={"units": "%ftp", "value": 75}),
        ]
        w = self._make_workout(steps=steps)
        event = w.to_intervals_event()
        doc = event["workout_doc"]
        assert "steps" in doc
        assert len(doc["steps"]) == 2
        assert doc["steps"][0]["text"] == "Warm-up"
        assert doc["steps"][1]["power"]["value"] == 75

    def test_workout_doc_repeat_group(self):
        steps = [
            WorkoutStep(
                text="Intervals",
                reps=4,
                steps=[
                    WorkoutStep(duration=300, hr={"units": "%lthr", "value": 90}),
                    WorkoutStep(duration=120, hr={"units": "%lthr", "value": 60}),
                ],
            ),
        ]
        w = self._make_workout(steps=steps)
        event = w.to_intervals_event()
        interval = event["workout_doc"]["steps"][0]
        assert interval["reps"] == 4
        assert len(interval["steps"]) == 2

    def test_no_description_in_event(self):
        """workout_doc replaces description — no description key in event."""
        w = self._make_workout()
        event = w.to_intervals_event()
        assert "description" not in event

    def test_default_date_is_today(self):
        w = PlannedWorkout(
            sport="Run",
            name="Test",
            steps=[WorkoutStep(text="Run", duration=1200)],
            duration_minutes=20,
        )
        assert w.target_date == date.today()


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


class TestWorkoutPrompt:
    def test_prompt_formats_without_error(self):
        from ai.prompts import WORKOUT_GENERATION_PROMPT

        result = WORKOUT_GENERATION_PROMPT.format(
            athlete_age=43,
            lthr_run=153,
            lthr_bike=153,
            ftp=233,
            css=141,
            goal_event="Ironman 70.3",
            goal_date="2026-09-15",
            weeks_remaining=25,
            recovery_score=78,
            recovery_category="good",
            hrv_delta=5.2,
            hrv_status="green",
            rhr_today="46",
            rhr_30d="48",
            sleep_score=82,
            ctl=62.5,
            atl=71.2,
            tsb=-8.7,
            ramp_rate=1.2,
            ctl_swim=9.0,
            ctl_bike=25.2,
            ctl_run=13.8,
            ctl_swim_target=15,
            ctl_bike_target=35,
            ctl_run_target=25,
            yesterday_summary="Run 40m Z2",
        )
        assert "Ironman 70.3" in result
        assert "78/100" in result
        assert "steps" in result.lower()


# ---------------------------------------------------------------------------
# _parse_step
# ---------------------------------------------------------------------------


class TestParseStep:
    def test_simple_step(self):
        from ai.claude_agent import _parse_step

        step = _parse_step({"text": "Warm-up", "duration": 600, "hr": {"units": "%lthr", "value": 65}})
        assert isinstance(step, WorkoutStep)
        assert step.text == "Warm-up"
        assert step.duration == 600
        assert step.hr["value"] == 65

    def test_repeat_group(self):
        from ai.claude_agent import _parse_step

        step = _parse_step(
            {
                "text": "Tempo",
                "reps": 3,
                "steps": [
                    {"duration": 300, "power": {"units": "%ftp", "value": 88}},
                    {"duration": 120, "power": {"units": "%ftp", "value": 60}},
                ],
            }
        )
        assert step.reps == 3
        assert len(step.steps) == 2
        assert step.steps[0].power["value"] == 88

    def test_all_targets(self):
        from ai.claude_agent import _parse_step

        step = _parse_step(
            {
                "text": "Full",
                "duration": 300,
                "hr": {"units": "%lthr", "value": 75},
                "power": {"units": "%ftp", "value": 80},
                "pace": {"units": "%pace", "value": 90},
                "cadence": {"units": "rpm", "value": 90},
            }
        )
        assert step.hr is not None
        assert step.power is not None
        assert step.pace is not None
        assert step.cadence is not None


# ---------------------------------------------------------------------------
# generate_workout (mocked Claude API)
# ---------------------------------------------------------------------------


class TestGenerateWorkout:
    def _make_wellness_row(self):
        return SimpleNamespace(
            hrv=52.0,
            recovery_score=78,
            recovery_category="good",
            recovery_recommendation="zone2_ok",
            sleep_score=82,
            ctl=62.5,
            atl=71.2,
            ramp_rate=1.2,
            sport_info=[],
        )

    def _make_hrv_row(self, status="green"):
        return SimpleNamespace(rmssd_7d=48.0, status=status)

    def _make_rhr_row(self):
        return SimpleNamespace(rhr_today=46.0, rhr_30d=48.0)

    @patch("ai.claude_agent._format_yesterday_dfa", new_callable=AsyncMock, return_value="Run 40m Z2")
    @patch("ai.claude_agent.extract_sport_ctl_tuple", return_value=(9.0, 25.0, 14.0))
    async def test_valid_json_returns_planned_workout(self, mock_ctl, mock_dfa):
        from ai.claude_agent import ClaudeAgent

        response_json = json.dumps(
            {
                "sport": "Ride",
                "name": "Z2 Endurance",
                "steps": [
                    {"text": "Warm-up", "duration": 600, "power": {"units": "%ftp", "value": 60}},
                    {"text": "Main", "duration": 2400, "power": {"units": "%ftp", "value": 75}},
                ],
                "duration_minutes": 50,
                "target_tss": 55,
                "rationale": "Bike CTL needs building",
            }
        )

        agent = ClaudeAgent()
        mock_message = SimpleNamespace(content=[SimpleNamespace(text=response_json)])
        agent.client = AsyncMock()
        agent.client.messages.create = AsyncMock(return_value=mock_message)

        result = await agent.generate_workout(
            self._make_wellness_row(), self._make_hrv_row(), self._make_hrv_row(), self._make_rhr_row()
        )

        assert result is not None
        assert isinstance(result, PlannedWorkout)
        assert result.sport == "Ride"
        assert result.name == "Z2 Endurance"
        assert len(result.steps) == 2
        assert result.duration_minutes == 50

    @patch("ai.claude_agent._format_yesterday_dfa", new_callable=AsyncMock, return_value="")
    @patch("ai.claude_agent.extract_sport_ctl_tuple", return_value=(9.0, 25.0, 14.0))
    async def test_markdown_fenced_json_still_parses(self, mock_ctl, mock_dfa):
        from ai.claude_agent import ClaudeAgent

        inner = json.dumps(
            {
                "sport": "Run",
                "name": "Easy",
                "steps": [{"text": "Run", "duration": 2400, "hr": {"units": "%lthr", "value": 70}}],
                "duration_minutes": 40,
                "target_tss": 30,
                "rationale": "Recovery",
            }
        )
        response_text = f"```json\n{inner}\n```"

        agent = ClaudeAgent()
        mock_message = SimpleNamespace(content=[SimpleNamespace(text=response_text)])
        agent.client = AsyncMock()
        agent.client.messages.create = AsyncMock(return_value=mock_message)

        result = await agent.generate_workout(
            self._make_wellness_row(), self._make_hrv_row(), self._make_hrv_row(), self._make_rhr_row()
        )

        assert result is not None
        assert result.sport == "Run"

    @patch("ai.claude_agent._format_yesterday_dfa", new_callable=AsyncMock, return_value="")
    @patch("ai.claude_agent.extract_sport_ctl_tuple", return_value=(9.0, 25.0, 14.0))
    async def test_rest_day_returns_none(self, mock_ctl, mock_dfa):
        from ai.claude_agent import ClaudeAgent

        response_json = json.dumps(
            {
                "sport": "Rest",
                "name": "Rest Day",
                "steps": [],
                "duration_minutes": 0,
                "target_tss": None,
                "rationale": "Recovery low, HRV red",
            }
        )

        agent = ClaudeAgent()
        mock_message = SimpleNamespace(content=[SimpleNamespace(text=response_json)])
        agent.client = AsyncMock()
        agent.client.messages.create = AsyncMock(return_value=mock_message)

        result = await agent.generate_workout(
            self._make_wellness_row(), self._make_hrv_row(), self._make_hrv_row(), self._make_rhr_row()
        )

        assert result is None

    @patch("ai.claude_agent._format_yesterday_dfa", new_callable=AsyncMock, return_value="")
    @patch("ai.claude_agent.extract_sport_ctl_tuple", return_value=(9.0, 25.0, 14.0))
    async def test_invalid_json_returns_none(self, mock_ctl, mock_dfa):
        from ai.claude_agent import ClaudeAgent

        agent = ClaudeAgent()
        mock_message = SimpleNamespace(content=[SimpleNamespace(text="not json at all")])
        agent.client = AsyncMock()
        agent.client.messages.create = AsyncMock(return_value=mock_message)

        result = await agent.generate_workout(
            self._make_wellness_row(), self._make_hrv_row(), self._make_hrv_row(), self._make_rhr_row()
        )

        assert result is None


# ---------------------------------------------------------------------------
# Database CRUD (requires PostgreSQL via conftest)
# ---------------------------------------------------------------------------


class TestAiWorkoutsCRUD:
    async def test_save_and_get_by_external_id(self, _test_db):
        from data.database import get_ai_workout_by_external_id, save_ai_workout

        row = await save_ai_workout(
            date_str="2026-03-29",
            sport="Ride",
            slot="morning",
            external_id="tricoach:2026-03-29:ride:morning",
            intervals_id=12345,
            name="Z2 Endurance",
            description="Warm-up; Main; Cool-down",
            duration_minutes=60,
            target_tss=65,
            rationale="Bike CTL needs building",
        )
        assert row.external_id == "tricoach:2026-03-29:ride:morning"
        assert row.status == "active"

        fetched = await get_ai_workout_by_external_id("tricoach:2026-03-29:ride:morning")
        assert fetched is not None
        assert fetched.name == "Z2 Endurance"
        assert fetched.intervals_id == 12345

    async def test_upsert_updates_existing(self, _test_db):
        from data.database import get_ai_workout_by_external_id, save_ai_workout

        await save_ai_workout(
            date_str="2026-03-30",
            sport="Run",
            slot="morning",
            external_id="tricoach:2026-03-30:run:morning",
            intervals_id=111,
            name="Easy Run v1",
            description="Z2",
            duration_minutes=40,
            target_tss=30,
            rationale="v1",
        )

        await save_ai_workout(
            date_str="2026-03-30",
            sport="Run",
            slot="morning",
            external_id="tricoach:2026-03-30:run:morning",
            intervals_id=222,
            name="Easy Run v2",
            description="Z2 updated",
            duration_minutes=45,
            target_tss=35,
            rationale="v2",
        )

        fetched = await get_ai_workout_by_external_id("tricoach:2026-03-30:run:morning")
        assert fetched.name == "Easy Run v2"
        assert fetched.intervals_id == 222

    async def test_cancel_ai_workout(self, _test_db):
        from data.database import cancel_ai_workout, save_ai_workout

        await save_ai_workout(
            date_str="2026-03-31",
            sport="Swim",
            slot="morning",
            external_id="tricoach:2026-03-31:swim:morning",
            intervals_id=333,
            name="CSS Intervals",
            description="Swim",
            duration_minutes=50,
            target_tss=45,
            rationale="test",
        )

        row = await cancel_ai_workout("tricoach:2026-03-31:swim:morning")
        assert row is not None
        assert row.status == "cancelled"

    async def test_get_upcoming(self, _test_db):
        from data.database import get_ai_workouts_upcoming, save_ai_workout

        today = str(date.today())
        await save_ai_workout(
            date_str=today,
            sport="Ride",
            slot="morning",
            external_id=f"tricoach:{today}:ride:upcoming-test",
            intervals_id=444,
            name="Today Ride",
            description="test",
            duration_minutes=60,
            target_tss=60,
            rationale="test",
        )

        rows = await get_ai_workouts_upcoming(days_ahead=1)
        names = [r.name for r in rows]
        assert "Today Ride" in names

    async def test_get_for_date(self, _test_db):
        from data.database import get_ai_workouts_for_date, save_ai_workout

        await save_ai_workout(
            date_str="2026-04-01",
            sport="Run",
            slot="morning",
            external_id="tricoach:2026-04-01:run:date-test",
            intervals_id=555,
            name="April Run",
            description="test",
            duration_minutes=30,
            target_tss=25,
            rationale="test",
        )

        rows = await get_ai_workouts_for_date(date(2026, 4, 1))
        assert len(rows) >= 1
        assert any(r.name == "April Run" for r in rows)
