"""Tests for ``ActivityAchievement.save_bulk`` — webhook persistence path.

The webhook receiver in ``api/routers/intervals/webhook.py`` calls
``save_bulk`` with the raw ``event.activity`` dict for ACTIVITY_ACHIEVEMENTS
events. Tests lock down:

- power PRs from ``icu_achievements[]`` are persisted with full context
- FTP_CHANGE synthesised when ``icu_rolling_ftp_delta != 0``
- idempotency under webhook redelivery (UNIQUE on user+activity+achievement)
- malformed achievements are dropped, not raised
"""

from __future__ import annotations

from datetime import date

from data.db import Activity, ActivityAchievement
from data.intervals.dto import ActivityDTO


async def _seed_activity(activity_id: str = "i12345") -> None:
    """The achievements table FKs to activities; seed a minimal row first."""
    dto = ActivityDTO(id=activity_id, start_date_local=date.today(), type="Ride")
    await Activity.save_bulk(user=1, activities=[dto])


def _power_pr(secs: int = 5, watts: int = 500) -> dict:
    return {
        "id": f"ps0_{secs}",
        "type": "BEST_POWER",
        "watts": watts,
        "secs": secs,
        "point": {"start_index": 392, "end_index": 392 + secs, "secs": secs, "value": watts},
    }


class TestSaveBulkPowerPRs:
    async def test_persists_single_power_pr(self, _test_db):
        await _seed_activity("i1")
        activity = {
            "id": "i1",
            "icu_achievements": [_power_pr(5, 500)],
            "icu_rolling_ftp": 208,
            "icu_ctl": 18.94,
        }
        inserted = await ActivityAchievement.save_bulk(
            user_id=1,
            activity_id="i1",
            activity=activity,
        )
        assert inserted == 1

        rows = await ActivityAchievement.get_for_activity(user_id=1, activity_id="i1")
        assert len(rows) == 1
        row = rows[0]
        assert row.achievement_id == "ps0_5"
        assert row.type == "BEST_POWER"
        assert row.value == 500.0
        assert row.secs == 5
        assert row.ftp_at_time == 208
        assert row.ctl_at_time == 18.94
        assert row.point_data == {"start_index": 392, "end_index": 397, "secs": 5, "value": 500}

    async def test_persists_multiple_power_prs(self, _test_db):
        await _seed_activity("i2")
        activity = {
            "id": "i2",
            "icu_achievements": [_power_pr(5, 500), _power_pr(60, 320), _power_pr(300, 250)],
            "icu_rolling_ftp": 208,
        }
        inserted = await ActivityAchievement.save_bulk(
            user_id=1,
            activity_id="i2",
            activity=activity,
        )
        assert inserted == 3

        rows = await ActivityAchievement.get_for_activity(user_id=1, activity_id="i2")
        assert len(rows) == 3
        assert {r.secs for r in rows} == {5, 60, 300}

    async def test_no_achievements_returns_zero(self, _test_db):
        await _seed_activity("i3")
        inserted = await ActivityAchievement.save_bulk(
            user_id=1,
            activity_id="i3",
            activity={"id": "i3"},
        )
        assert inserted == 0


class TestSaveBulkIdempotency:
    """``ON CONFLICT DO NOTHING`` on (user_id, activity_id, achievement_id).
    Intervals.icu retries webhooks; we must not duplicate rows."""

    async def test_redelivery_does_not_duplicate(self, _test_db):
        await _seed_activity("i4")
        activity = {
            "id": "i4",
            "icu_achievements": [_power_pr(5, 500)],
        }
        first = await ActivityAchievement.save_bulk(
            user_id=1,
            activity_id="i4",
            activity=activity,
        )
        assert first == 1

        second = await ActivityAchievement.save_bulk(
            user_id=1,
            activity_id="i4",
            activity=activity,
        )
        assert second == 0  # nothing new

        rows = await ActivityAchievement.get_for_activity(user_id=1, activity_id="i4")
        assert len(rows) == 1


class TestSaveBulkFTPChange:
    """Non-zero ``icu_rolling_ftp_delta`` → synthetic FTP_CHANGE row.

    Surfaces FTP PRs in the same query as power PRs (unified social-share list).
    """

    async def test_ftp_change_creates_synthetic_row(self, _test_db):
        await _seed_activity("i5")
        activity = {
            "id": "i5",
            "icu_achievements": [],
            "icu_rolling_ftp": 215,
            "icu_rolling_ftp_delta": 7,
            "icu_ctl": 22.5,
        }
        inserted = await ActivityAchievement.save_bulk(
            user_id=1,
            activity_id="i5",
            activity=activity,
        )
        assert inserted == 1

        rows = await ActivityAchievement.get_for_activity(user_id=1, activity_id="i5")
        assert len(rows) == 1
        row = rows[0]
        assert row.type == "FTP_CHANGE"
        assert row.achievement_id == "ftp_change"
        assert row.value == 215.0
        assert row.secs is None
        assert row.ftp_at_time == 215
        assert row.ctl_at_time == 22.5
        assert row.extra == {"delta": 7}

    async def test_zero_delta_does_not_create_row(self, _test_db):
        """``icu_rolling_ftp_delta == 0`` — FTP didn't change, skip."""
        await _seed_activity("i6")
        activity = {
            "id": "i6",
            "icu_rolling_ftp": 208,
            "icu_rolling_ftp_delta": 0,
        }
        inserted = await ActivityAchievement.save_bulk(
            user_id=1,
            activity_id="i6",
            activity=activity,
        )
        assert inserted == 0

    async def test_ftp_change_alongside_power_prs(self, _test_db):
        """Both FTP_CHANGE and BEST_POWER in the same payload — saved together."""
        await _seed_activity("i7")
        activity = {
            "id": "i7",
            "icu_achievements": [_power_pr(5, 500)],
            "icu_rolling_ftp": 215,
            "icu_rolling_ftp_delta": 7,
        }
        inserted = await ActivityAchievement.save_bulk(
            user_id=1,
            activity_id="i7",
            activity=activity,
        )
        assert inserted == 2

        rows = await ActivityAchievement.get_for_activity(user_id=1, activity_id="i7")
        types = {r.type for r in rows}
        assert types == {"BEST_POWER", "FTP_CHANGE"}


class TestSaveBulkMalformed:
    """Forward-compat: malformed Intervals.icu payloads must not crash the
    receiver (returning 4xx/5xx makes Intervals.icu disable the webhook)."""

    async def test_drops_achievement_without_id(self, _test_db):
        await _seed_activity("i8")
        activity = {
            "id": "i8",
            "icu_achievements": [{"type": "BEST_POWER", "watts": 500}],  # no id
        }
        inserted = await ActivityAchievement.save_bulk(
            user_id=1,
            activity_id="i8",
            activity=activity,
        )
        assert inserted == 0

    async def test_drops_achievement_without_type(self, _test_db):
        await _seed_activity("i9")
        activity = {
            "id": "i9",
            "icu_achievements": [{"id": "ps0_5", "watts": 500}],  # no type
        }
        inserted = await ActivityAchievement.save_bulk(
            user_id=1,
            activity_id="i9",
            activity=activity,
        )
        assert inserted == 0

    async def test_drops_non_dict_achievement(self, _test_db):
        await _seed_activity("i10")
        activity = {
            "id": "i10",
            "icu_achievements": ["not a dict", 42, _power_pr(5, 500)],
        }
        inserted = await ActivityAchievement.save_bulk(
            user_id=1,
            activity_id="i10",
            activity=activity,
        )
        # only the valid one survives
        assert inserted == 1
