"""Tests for tasks/dto.py — DateDTO coercion + local_today helper."""

from datetime import date, datetime
from unittest.mock import patch

from pydantic import TypeAdapter

from tasks.dto import DateDTO, local_today


class TestDateDTOCoerce:
    def test_str_coerces_to_date(self):
        assert TypeAdapter(DateDTO).validate_python("2026-04-03") == date(2026, 4, 3)

    def test_datetime_strips_time_component(self):
        assert TypeAdapter(DateDTO).validate_python(datetime(2026, 4, 3, 17, 30)) == date(2026, 4, 3)

    def test_date_passes_through(self):
        d = date(2026, 4, 3)
        assert TypeAdapter(DateDTO).validate_python(d) is d


class TestLocalToday:
    """The whole point of `local_today` is that it ignores container `TZ` —
    if the host is UTC at 23:30 but the user is in Belgrade at 01:30 the next
    day, actors must agree with the user's calendar. We pin a fake `now()`
    that's UTC-yesterday / Belgrade-today and check it returns the Belgrade
    date."""

    def test_uses_settings_timezone_not_naive_today(self):
        # 2026-05-04 22:30 UTC = 2026-05-05 00:30 Europe/Belgrade (DST: UTC+2).
        # `date.today()` here would return 2026-05-04 (UTC); `local_today()`
        # must return 2026-05-05.
        from datetime import timezone

        utc_evening = datetime(2026, 5, 4, 22, 30, tzinfo=timezone.utc)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: D401, ARG003 — match stdlib signature
                return utc_evening.astimezone(tz) if tz else utc_evening

        with patch("tasks.dto._dt.datetime", _FixedDatetime):
            assert local_today() == date(2026, 5, 5)
