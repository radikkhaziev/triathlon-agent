"""Tests for actor_save_fitness_projection (FITNESS_UPDATED handler)."""

from datetime import date
from unittest.mock import patch

from data.db.user import UserDTO
from tasks.actors.fitness import actor_save_fitness_projection


def _user() -> UserDTO:
    return UserDTO(id=42, chat_id="111", username="tester", athlete_id="i317960")


# Sample shape mirrors docs/INTERVALS_WEBHOOKS_RESEARCH.md §A.3 — records[0] is
# the recomputed anchor (today), the rest are the projected future curve.
_TODAY = date(2026, 4, 16)
_TODAY_ISO = _TODAY.isoformat()
_RECORDS = [
    {
        "id": _TODAY_ISO,
        "ctl": 18.94,
        "atl": 38.27,
        "rampRate": 4.23,
        "ctlLoad": 41.0,
        "atlLoad": 44.0,
        "sportInfo": [{"type": "Ride", "eftp": 207.82}],
    },
    {"id": "2026-05-21", "ctl": 8.23, "atl": 0.26, "rampRate": -1.49, "ctlLoad": 0.0, "atlLoad": 0.0},
    {"id": "2026-09-15", "ctl": 0.51, "atl": 0.0, "rampRate": -0.09, "ctlLoad": 0.0, "atlLoad": 0.0},
]


def _patch_today(monkeypatch_target=_TODAY):
    return patch("tasks.actors.fitness.local_today", return_value=monkeypatch_target)


class TestActorSaveFitnessProjection:
    def test_empty_records_is_noop(self):
        with (
            patch("tasks.actors.fitness.FitnessProjection.save_bulk") as save,
            patch("tasks.actors.fitness.Wellness.update_loads") as upd,
        ):
            actor_save_fitness_projection(user=_user(), records=[])
            save.assert_not_called()
            upd.assert_not_called()

    def test_records_without_id_are_skipped(self):
        with (
            patch("tasks.actors.fitness.FitnessProjection.save_bulk") as save,
            patch("tasks.actors.fitness.Wellness.update_loads") as upd,
        ):
            actor_save_fitness_projection(user=_user(), records=[{"ctl": 1.0}, {}])
            save.assert_not_called()
            upd.assert_not_called()

    def test_saves_projection_with_today_first(self):
        """save_bulk receives the records sorted by date asc (today first)."""
        with (
            _patch_today(),
            patch("tasks.actors.fitness.FitnessProjection.save_bulk") as save,
            patch("tasks.actors.fitness.Wellness.update_loads", return_value=True),
        ):
            actor_save_fitness_projection(user=_user(), records=list(reversed(_RECORDS)))
            save.assert_called_once()
            kwargs = save.call_args.kwargs
            assert kwargs["user_id"] == 42
            assert [r["id"] for r in kwargs["records"]] == [_TODAY_ISO, "2026-05-21", "2026-09-15"]

    def test_updates_wellness_for_today_from_payload(self):
        """Wellness.update_loads is called with today's record values — no API roundtrip."""
        with (
            _patch_today(),
            patch("tasks.actors.fitness.FitnessProjection.save_bulk"),
            patch("tasks.actors.fitness.Wellness.update_loads", return_value=True) as upd,
        ):
            actor_save_fitness_projection(user=_user(), records=_RECORDS)
            upd.assert_called_once_with(
                user_id=42,
                dt=_TODAY_ISO,
                ctl=18.94,
                atl=38.27,
                ramp_rate=4.23,
                ctl_load=41.0,
                atl_load=44.0,
            )

    def test_no_wellness_update_when_today_absent(self):
        """If today isn't in records (edge case), skip the wellness update."""
        future_only = [r for r in _RECORDS if r["id"] != _TODAY_ISO]
        with (
            _patch_today(),
            patch("tasks.actors.fitness.FitnessProjection.save_bulk") as save,
            patch("tasks.actors.fitness.Wellness.update_loads") as upd,
        ):
            actor_save_fitness_projection(user=_user(), records=future_only)
            save.assert_called_once()
            upd.assert_not_called()
