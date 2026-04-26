"""Tests for ApiUsageDaily upsert and get_range."""

from datetime import date

from data.db import ApiUsageDaily


class TestApiUsageDaily:
    async def test_increment_creates_new_row(self, _test_db):
        row = await ApiUsageDaily.increment(user_id=1, input_tokens=100, output_tokens=50)
        assert row.input_tokens == 100
        assert row.output_tokens == 50
        assert row.request_count == 1
        assert row.date == str(date.today())

    async def test_increment_upserts_existing_row(self, _test_db):
        await ApiUsageDaily.increment(user_id=1, input_tokens=100, output_tokens=50)
        row = await ApiUsageDaily.increment(user_id=1, input_tokens=200, output_tokens=80)
        assert row.input_tokens == 300
        assert row.output_tokens == 130
        assert row.request_count == 2

    async def test_increment_with_cache_tokens(self, _test_db):
        row = await ApiUsageDaily.increment(
            user_id=1, input_tokens=100, output_tokens=50, cache_read_tokens=500, cache_creation_tokens=200
        )
        assert row.cache_read_tokens == 500
        assert row.cache_creation_tokens == 200

    async def test_get_range(self, _test_db):
        for i in range(3):
            await ApiUsageDaily.increment(user_id=1, input_tokens=100 * (i + 1), output_tokens=50)

        # All 3 increments go to today (same date), so get_range returns 1 row
        rows = await ApiUsageDaily.get_range(user_id=1, days_back=7)
        assert len(rows) >= 1
        assert rows[0].input_tokens == 600  # 100 + 200 + 300
        assert rows[0].request_count == 3

    async def test_get_range_returns_only_requested_user(self, _test_db):
        await ApiUsageDaily.increment(user_id=1, input_tokens=100, output_tokens=50)

        rows = await ApiUsageDaily.get_range(user_id=1, days_back=7)
        assert len(rows) == 1
        assert rows[0].user_id == 1


class TestGetTodayRequestCount:
    """``get_today_request_count`` powers the chat daily-cap gate
    (``CHAT_DAILY_LIMIT``). Every miss must return 0 (not raise) so the
    first message of the day passes the gate cleanly."""

    async def test_returns_zero_for_user_with_no_row(self, _test_db):
        assert await ApiUsageDaily.get_today_request_count(user_id=999) == 0

    async def test_returns_request_count_for_today(self, _test_db):
        await ApiUsageDaily.increment(user_id=1, input_tokens=10, output_tokens=5)
        await ApiUsageDaily.increment(user_id=1, input_tokens=10, output_tokens=5)
        assert await ApiUsageDaily.get_today_request_count(user_id=1) == 2

    async def test_isolated_per_user(self, _test_db):
        await ApiUsageDaily.increment(user_id=1, input_tokens=10, output_tokens=5)
        assert await ApiUsageDaily.get_today_request_count(user_id=1) == 1
        assert await ApiUsageDaily.get_today_request_count(user_id=2) == 0
