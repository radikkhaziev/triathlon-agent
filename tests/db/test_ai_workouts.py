"""Tests for Phase 1: Adaptive Training Plan — AI workout generation."""

from datetime import date

import pytest
from pydantic import ValidationError

from data.intervals.dto import PlannedWorkoutDTO, WorkoutStepDTO

# ---------------------------------------------------------------------------
# WorkoutStep
# ---------------------------------------------------------------------------


class TestWorkoutStep:
    def test_simple_step(self):
        step = WorkoutStepDTO(text="Warm-up", duration=600, hr={"units": "%lthr", "value": 65})
        assert step.text == "Warm-up"
        assert step.duration == 600
        assert step.hr == {"units": "%lthr", "value": 65}
        assert step.power is None

    def test_power_step(self):
        step = WorkoutStepDTO(text="Z2", duration=1800, power={"units": "%ftp", "value": 75})
        assert step.power["value"] == 75

    def test_repeat_group(self):
        step = WorkoutStepDTO(
            text="Tempo",
            reps=3,
            steps=[
                WorkoutStepDTO(duration=300, hr={"units": "%lthr", "value": 88}),
                WorkoutStepDTO(duration=120, hr={"units": "%lthr", "value": 65}),
            ],
        )
        assert step.reps == 3
        assert len(step.steps) == 2
        assert step.duration == 0

    def test_model_dump_excludes_none(self):
        step = WorkoutStepDTO(text="Warm-up", duration=600, hr={"units": "%lthr", "value": 65})
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
            steps=[WorkoutStepDTO(text="Main", duration=3600, power={"units": "%ftp", "value": 75})],
            duration_minutes=60,
            target_date=date(2026, 3, 29),
        )
        defaults.update(kwargs)
        return PlannedWorkoutDTO(**defaults)

    def test_external_id_format(self):
        w = self._make_workout(sport="Ride", target_date=date(2026, 3, 29), slot="morning")
        assert w.external_id == "tricoach:2026-03-29:ride:morning"

    def test_external_id_evening_slot(self):
        w = self._make_workout(slot="evening")
        assert w.external_id.endswith(":evening")

    def test_to_intervals_event_keys(self):
        w = self._make_workout()
        event = w.to_intervals_event()
        assert event.category == "WORKOUT"
        assert event.type == "Ride"
        assert event.start_date_local == "2026-03-29T00:00:00"
        assert event.moving_time == 3600
        assert event.external_id == "tricoach:2026-03-29:ride:morning"

    def test_generated_suffix(self):
        w = self._make_workout(suffix="generated")
        event = w.to_intervals_event()
        assert event.name == "AI: Z2 Endurance (generated)"

    def test_adapted_suffix(self):
        w = self._make_workout(suffix="adapted")
        event = w.to_intervals_event()
        assert event.name == "AI: Z2 Endurance (adapted)"

    def test_workout_doc_in_event(self):
        steps = [
            WorkoutStepDTO(text="Warm-up", duration=600, power={"units": "%ftp", "value": 60}),
            WorkoutStepDTO(text="Main", duration=1800, power={"units": "%ftp", "value": 75}),
        ]
        w = self._make_workout(steps=steps)
        event = w.to_intervals_event()
        doc = event.workout_doc
        assert "steps" in doc
        assert len(doc["steps"]) == 2
        assert doc["steps"][0]["text"] == "Warm-up"
        assert doc["steps"][1]["power"]["value"] == 75

    def test_workout_doc_repeat_group(self):
        steps = [
            WorkoutStepDTO(
                text="Intervals",
                reps=4,
                steps=[
                    WorkoutStepDTO(duration=300, hr={"units": "%lthr", "value": 90}),
                    WorkoutStepDTO(duration=120, hr={"units": "%lthr", "value": 60}),
                ],
            ),
        ]
        w = self._make_workout(steps=steps)
        event = w.to_intervals_event()
        interval = event.workout_doc["steps"][0]
        assert interval["reps"] == 4
        assert len(interval["steps"]) == 2

    def test_no_description_in_event(self):
        """workout_doc replaces description — no description key in event."""
        w = self._make_workout()
        event = w.to_intervals_event()
        assert event.description is None

    def test_rejects_step_without_target(self):
        """Targetless terminal steps leave watches unable to alert — must raise."""
        with pytest.raises(ValidationError, match="no intensity target"):
            PlannedWorkoutDTO(
                sport="Run",
                name="Test",
                steps=[WorkoutStepDTO(text="Z2", duration=1200)],
                duration_minutes=20,
            )

    def test_rejects_repeat_substep_without_target(self):
        """Sub-steps of a repeat group must also carry targets."""
        with pytest.raises(ValidationError, match="no intensity target"):
            PlannedWorkoutDTO(
                sport="Run",
                name="Test",
                steps=[
                    WorkoutStepDTO(
                        text="Intervals",
                        reps=3,
                        steps=[
                            WorkoutStepDTO(duration=300),  # missing target
                            WorkoutStepDTO(duration=120, hr={"units": "%lthr", "value": 60}),
                        ],
                    ),
                ],
                duration_minutes=20,
            )

    def test_accepts_pace_target(self):
        """Swim steps with pace target are valid."""
        PlannedWorkoutDTO(
            sport="Swim",
            name="Test",
            steps=[WorkoutStepDTO(text="Main", distance=400, pace={"units": "%pace", "value": 95})],
            duration_minutes=15,
        )

    def test_default_date_is_today(self):
        w = PlannedWorkoutDTO(
            sport="Run",
            name="Test",
            steps=[WorkoutStepDTO(text="Run", duration=1200, hr={"units": "%lthr", "value": 72, "end": 82})],
            duration_minutes=20,
        )
        assert w.target_date == date.today()


# ---------------------------------------------------------------------------
# Database CRUD (requires PostgreSQL via conftest)
# ---------------------------------------------------------------------------


class TestAiWorkoutsCRUD:
    async def test_save_and_get_by_external_id(self, _test_db):
        from data.db import AiWorkout

        row = await AiWorkout.save(
            user_id=1,
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

        fetched = await AiWorkout.get_by_external_id(1, "tricoach:2026-03-29:ride:morning")
        assert fetched is not None
        assert fetched.name == "Z2 Endurance"
        assert fetched.intervals_id == 12345

    async def test_upsert_updates_existing(self, _test_db):
        from data.db import AiWorkout

        await AiWorkout.save(
            user_id=1,
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

        await AiWorkout.save(
            user_id=1,
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

        fetched = await AiWorkout.get_by_external_id(1, "tricoach:2026-03-30:run:morning")
        assert fetched.name == "Easy Run v2"
        assert fetched.intervals_id == 222

    async def test_cancel_ai_workout(self, _test_db):
        from data.db import AiWorkout

        await AiWorkout.save(
            user_id=1,
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

        row = await AiWorkout.cancel(1, "tricoach:2026-03-31:swim:morning")
        assert row is not None
        assert row.status == "cancelled"

    async def test_get_upcoming(self, _test_db):
        from data.db import AiWorkout

        today = str(date.today())
        await AiWorkout.save(
            user_id=1,
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

        rows = await AiWorkout.get_upcoming(user_id=1, days_ahead=1)
        names = [r.name for r in rows]
        assert "Today Ride" in names

    async def test_get_for_date(self, _test_db):
        from data.db import AiWorkout

        await AiWorkout.save(
            user_id=1,
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

        rows = await AiWorkout.get_for_date(1, date(2026, 4, 1))
        assert len(rows) >= 1
        assert any(r.name == "April Run" for r in rows)
