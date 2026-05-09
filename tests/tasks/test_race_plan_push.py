"""Tests for the 24h pre-race plan push (PR2.6).

Coverage:
- ``actor_send_pre_race_plan_push`` — happy path + idempotency + missing
  plan + telegram-failure-leaves-unmarked.
- ``RacePlan.mark_pushed_for_race_date`` — writes the JSONB field, scoped to
  user_id, doesn't bump generated_at (would break the unique-index +
  regen-counter contracts).
- ``scheduler_pre_race_plan_push_job`` — picks goals where event_date is
  exactly today + 1, dispatches one actor message per (user, goal).
"""

import asyncio
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401  — pytest-asyncio collects via marker auto-discovery

from data.db import AthleteGoal, RacePlan, User, UserDTO, get_session

PAYLOAD = {
    "plan": {
        "headline": "Steady to km 16.",
        "warmup": "Easy 10 min.",
        "legs": [
            {
                "leg": "run",
                "distance": "21.1 km",
                "pacing": {"low": "5:30/km", "target": "5:10/km", "cap": "4:50/km"},
                "hr_ceiling_bpm": 175,
            }
        ],
        "fueling": {"carbs_g_per_hour": 70, "notes": "Gel every 25 min."},
        "transitions": [],
        "contingencies": [
            {"scenario": "heat", "plan": "Slow 5%."},
            {"scenario": "cramp", "plan": "Walk + salt."},
            {"scenario": "off-pace", "plan": "Drop to low."},
        ],
    },
    "race": {"id": 1, "name": "Drina Trail"},
    "confidence_tier": "final",
    "model_version": "v1",
    "regen_count_today": 0,
}


async def _seed_user_and_goal(*, user_id: int = 1, goal_id: int = 1, days_out: int = 1) -> tuple[int, int]:
    """Seed a user (if not already) + goal with event_date = today+days_out."""
    async with get_session() as session:
        existing = await session.get(User, user_id)
        if existing is None:
            session.add(User(id=user_id, chat_id=str(user_id), role="athlete"))
        session.add(
            AthleteGoal(
                id=goal_id,
                user_id=user_id,
                category="RACE_A",
                event_name="Drina Trail",
                event_date=date.today() + timedelta(days=days_out),
                sport_type="triathlon",
            )
        )
        await session.commit()
    return user_id, goal_id


# ---------------------------------------------------------------------------
# RacePlan.mark_pushed_for_race_date
# ---------------------------------------------------------------------------


class TestMarkPushedForRaceDate:
    async def test_writes_payload_field_without_bumping_generated_at(self):
        """generated_at MUST stay frozen — bumping it would either trip the
        partial unique index into a new day or interfere with the regen
        rate-limit counter (counter resets per UTC day of generated_at)."""
        await _seed_user_and_goal()
        row = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PAYLOAD)
        original_generated_at = row.generated_at

        ok = await RacePlan.mark_pushed_for_race_date(row.id, "2026-05-10", user_id=1)
        assert ok is True

        # Re-fetch and verify
        from sqlalchemy import select

        async with get_session() as session:
            updated = (await session.execute(select(RacePlan).where(RacePlan.id == row.id))).scalar_one()
        assert updated.payload["pushed_for_race_date"] == "2026-05-10"
        assert updated.generated_at == original_generated_at  # NOT bumped

    async def test_returns_false_for_cross_tenant_user_id(self):
        await _seed_user_and_goal()
        row = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PAYLOAD)
        # Seed second user
        async with get_session() as session:
            session.add(User(id=2, chat_id="2", role="athlete"))
            await session.commit()
        ok = await RacePlan.mark_pushed_for_race_date(row.id, "2026-05-10", user_id=2)
        assert ok is False
        # Original row untouched
        from sqlalchemy import select

        async with get_session() as session:
            unchanged = (await session.execute(select(RacePlan).where(RacePlan.id == row.id))).scalar_one()
        assert "pushed_for_race_date" not in (unchanged.payload or {})

    async def test_returns_false_when_row_does_not_exist(self):
        await _seed_user_and_goal()
        ok = await RacePlan.mark_pushed_for_race_date(99999, "2026-05-10", user_id=1)
        assert ok is False

    async def test_preserves_other_payload_fields(self):
        await _seed_user_and_goal()
        row = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PAYLOAD)
        await RacePlan.mark_pushed_for_race_date(row.id, "2026-05-10", user_id=1)
        from sqlalchemy import select

        async with get_session() as session:
            updated = (await session.execute(select(RacePlan).where(RacePlan.id == row.id))).scalar_one()
        # Original keys still present, just augmented with the push marker
        assert updated.payload["confidence_tier"] == "final"
        assert updated.payload["plan"]["warmup"] == "Easy 10 min."
        assert updated.payload["regen_count_today"] == 0


# ---------------------------------------------------------------------------
# actor_send_pre_race_plan_push
# ---------------------------------------------------------------------------


def _user_dto(*, user_id: int = 1) -> UserDTO:
    """Fresh UserDTO matching the seeded User row.

    Field set is restricted by ``UserDTO.model_config = extra='forbid'`` —
    credentials are deliberately excluded (issue #147). Stick to the public
    UserDTO surface (id / chat_id / language / is_silent / etc.)."""
    return UserDTO(
        id=user_id,
        chat_id=str(user_id),
        language="en",
    )


class TestActorSendPreRacePlanPush:
    def _patch_telegram(self, *, send_returns: object | None = {"ok": True}) -> MagicMock:
        """Patch TelegramTool so we don't hit the real API."""
        instance = MagicMock()
        instance.send_message = MagicMock(return_value=send_returns)
        return MagicMock(return_value=instance)

    async def test_skips_when_no_plan_exists(self):
        """No plan generated → log+skip, no Telegram call."""
        from tasks.actors.race_plan import actor_send_pre_race_plan_push

        await _seed_user_and_goal()
        tg_factory = self._patch_telegram()

        with patch("tasks.actors.race_plan.TelegramTool", tg_factory):
            # Run the sync actor body in a thread so @dual ORM dispatches to
            # SYNC (otherwise the test's running event loop makes @dual return
            # awaitable coroutines that the actor doesn't await).
            await asyncio.to_thread(
                actor_send_pre_race_plan_push.fn,
                user=_user_dto(),
                goal_id=1,
                race_date="2026-05-10",
            )

        tg_factory.assert_not_called()

    async def test_happy_path_sends_and_marks_pushed(self):
        """Plan exists → render → send → mark payload."""
        from tasks.actors.race_plan import actor_send_pre_race_plan_push

        await _seed_user_and_goal()
        plan_row = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PAYLOAD)
        tg_factory = self._patch_telegram()

        with patch("tasks.actors.race_plan.TelegramTool", tg_factory):
            # Run the sync actor body in a thread so @dual ORM dispatches to
            # SYNC (otherwise the test's running event loop makes @dual return
            # awaitable coroutines that the actor doesn't await).
            await asyncio.to_thread(
                actor_send_pre_race_plan_push.fn,
                user=_user_dto(),
                goal_id=1,
                race_date="2026-05-10",
            )

        # Telegram was called once with markdown=True + inline_keyboard
        tg_factory.assert_called_once()
        instance = tg_factory.return_value
        instance.send_message.assert_called_once()
        kwargs = instance.send_message.call_args.kwargs
        assert kwargs.get("markdown") is True
        assert "inline_keyboard" in kwargs["reply_markup"]
        assert "Drina Trail" in kwargs["text"]

        # Row marked
        from sqlalchemy import select

        async with get_session() as session:
            updated = (await session.execute(select(RacePlan).where(RacePlan.id == plan_row.id))).scalar_one()
        assert updated.payload["pushed_for_race_date"] == "2026-05-10"

    async def test_idempotent_on_second_invocation(self):
        """Second call for the same race_date short-circuits — no Telegram."""
        from tasks.actors.race_plan import actor_send_pre_race_plan_push

        await _seed_user_and_goal()
        await RacePlan.save(
            user_id=1,
            goal_id=1,
            model_version="v1",
            payload={**PAYLOAD, "pushed_for_race_date": "2026-05-10"},  # already-marked
        )
        tg_factory = self._patch_telegram()

        with patch("tasks.actors.race_plan.TelegramTool", tg_factory):
            # Run the sync actor body in a thread so @dual ORM dispatches to
            # SYNC (otherwise the test's running event loop makes @dual return
            # awaitable coroutines that the actor doesn't await).
            await asyncio.to_thread(
                actor_send_pre_race_plan_push.fn,
                user=_user_dto(),
                goal_id=1,
                race_date="2026-05-10",
            )

        tg_factory.assert_not_called()

    async def test_does_not_mark_when_telegram_returns_none(self):
        """Telegram unreachable → don't stamp pushed_for_race_date so a future
        retry COULD try again (in practice the next cron tick is past race day,
        but the contract is correct: only mark on successful delivery)."""
        from tasks.actors.race_plan import actor_send_pre_race_plan_push

        await _seed_user_and_goal()
        plan_row = await RacePlan.save(user_id=1, goal_id=1, model_version="v1", payload=PAYLOAD)
        tg_factory = self._patch_telegram(send_returns=None)  # blocked / unreachable

        with patch("tasks.actors.race_plan.TelegramTool", tg_factory):
            # Run the sync actor body in a thread so @dual ORM dispatches to
            # SYNC (otherwise the test's running event loop makes @dual return
            # awaitable coroutines that the actor doesn't await).
            await asyncio.to_thread(
                actor_send_pre_race_plan_push.fn,
                user=_user_dto(),
                goal_id=1,
                race_date="2026-05-10",
            )

        # Send was attempted
        tg_factory.return_value.send_message.assert_called_once()
        # But row NOT marked
        from sqlalchemy import select

        async with get_session() as session:
            unchanged = (await session.execute(select(RacePlan).where(RacePlan.id == plan_row.id))).scalar_one()
        assert "pushed_for_race_date" not in (unchanged.payload or {})

    async def test_uses_inline_race_block_for_event_name(self):
        """Goal-snapshot in payload.race.name is used (not a fresh AthleteGoal
        query) so a deleted goal doesn't break the rendering — see spec §11.3."""
        from tasks.actors.race_plan import actor_send_pre_race_plan_push

        await _seed_user_and_goal()
        await RacePlan.save(
            user_id=1,
            goal_id=1,
            model_version="v1",
            payload={**PAYLOAD, "race": {"id": 1, "name": "Custom Race Name From Snapshot"}},
        )
        tg_factory = self._patch_telegram()

        with patch("tasks.actors.race_plan.TelegramTool", tg_factory):
            # Run the sync actor body in a thread so @dual ORM dispatches to
            # SYNC (otherwise the test's running event loop makes @dual return
            # awaitable coroutines that the actor doesn't await).
            await asyncio.to_thread(
                actor_send_pre_race_plan_push.fn,
                user=_user_dto(),
                goal_id=1,
                race_date="2026-05-10",
            )

        text = tg_factory.return_value.send_message.call_args.kwargs["text"]
        assert "Custom Race Name From Snapshot" in text


# ---------------------------------------------------------------------------
# scheduler_pre_race_plan_push_job
# ---------------------------------------------------------------------------


class TestSchedulerPreRacePlanPushJob:
    async def test_dispatches_only_for_goals_with_event_date_tomorrow(self):
        """Goal-side query filters by event_date == today + 1; goals further
        out (e.g. day after tomorrow) or in the past don't fire."""
        from bot.scheduler import scheduler_pre_race_plan_push_job

        # Seed 3 goals: yesterday, tomorrow, day-after-tomorrow.
        async with get_session() as session:
            existing = await session.get(User, 1)
            if existing is None:
                session.add(User(id=1, chat_id="1", role="athlete"))
            session.add_all(
                [
                    AthleteGoal(
                        id=10,
                        user_id=1,
                        category="RACE_A",
                        event_name="Yesterday",
                        event_date=date.today() - timedelta(days=1),
                        sport_type="triathlon",
                    ),
                    AthleteGoal(
                        id=11,
                        user_id=1,
                        category="RACE_A",
                        event_name="Tomorrow",
                        event_date=date.today() + timedelta(days=1),
                        sport_type="triathlon",
                    ),
                    AthleteGoal(
                        id=12,
                        user_id=1,
                        category="RACE_A",
                        event_name="Day after tomorrow",
                        event_date=date.today() + timedelta(days=2),
                        sport_type="triathlon",
                    ),
                ]
            )
            await session.commit()

        # Patch local_today and the dramatiq actor's send.
        send_mock = MagicMock()
        with (
            patch("bot.scheduler.local_today", return_value=date.today()),
            patch("bot.scheduler.actor_send_pre_race_plan_push.send", send_mock),
        ):
            await scheduler_pre_race_plan_push_job()

        # Exactly one dispatch: the "Tomorrow" goal.
        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        assert kwargs["goal_id"] == 11
        assert kwargs["race_date"] == (date.today() + timedelta(days=1)).isoformat()

    async def test_skips_inactive_goals(self):
        """is_active=False goals never fire push (athlete cancelled the race)."""
        from bot.scheduler import scheduler_pre_race_plan_push_job

        async with get_session() as session:
            existing = await session.get(User, 1)
            if existing is None:
                session.add(User(id=1, chat_id="1", role="athlete"))
            session.add(
                AthleteGoal(
                    id=20,
                    user_id=1,
                    category="RACE_A",
                    event_name="Cancelled tomorrow",
                    event_date=date.today() + timedelta(days=1),
                    sport_type="triathlon",
                    is_active=False,  # cancelled
                )
            )
            await session.commit()

        send_mock = MagicMock()
        with (
            patch("bot.scheduler.local_today", return_value=date.today()),
            patch("bot.scheduler.actor_send_pre_race_plan_push.send", send_mock),
        ):
            await scheduler_pre_race_plan_push_job()

        send_mock.assert_not_called()

    async def test_skips_inactive_users(self):
        """is_active=False users never get pushes (deactivated account)."""
        from bot.scheduler import scheduler_pre_race_plan_push_job

        # Insert User first + flush so the FK from AthleteGoal can resolve.
        # SQLAlchemy's default order-by-dependency isn't reliable across
        # asyncpg + ORM combinations seen in this codebase.
        async with get_session() as session:
            session.add(User(id=99, chat_id="user-99", role="athlete", is_active=False))
            await session.flush()
            session.add(
                AthleteGoal(
                    id=30,
                    user_id=99,
                    category="RACE_A",
                    event_name="Tomorrow but user inactive",
                    event_date=date.today() + timedelta(days=1),
                    sport_type="triathlon",
                )
            )
            await session.commit()

        send_mock = MagicMock()
        with (
            patch("bot.scheduler.local_today", return_value=date.today()),
            patch("bot.scheduler.actor_send_pre_race_plan_push.send", send_mock),
        ):
            await scheduler_pre_race_plan_push_job()

        send_mock.assert_not_called()
