"""Tests for ``data.db.user.normalize_telegram_language`` — BCP-47 → supported
language set mapping. Used by ``User.get_or_create_from_telegram`` to seed
``users.language`` from Telegram's client locale on first ``/start``.
"""

import pytest


class TestNormalizeTelegramLanguage:
    """``normalize_telegram_language`` maps Telegram's BCP-47 ``language_code``
    to the project's supported set (``ru``/``en``). Used on first ``/start`` to
    seed ``users.language``. Unsupported / missing → ``en`` (international
    fallback, not the owner's native ``ru``)."""

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("ru", "ru"),
            ("en", "en"),
            ("RU", "ru"),  # case insensitive
            ("EN", "en"),
            ("ru-RU", "ru"),  # region stripped
            ("en-US", "en"),
            ("en-GB", "en"),
            ("pt-br", "en"),  # unsupported lang → en fallback
            ("de", "en"),
            ("zh-CN", "en"),
            ("", "en"),  # empty string → en
            (None, "en"),  # None (Telegram client omits) → en
        ],
    )
    def test_normalization(self, code, expected):
        from data.db.user import normalize_telegram_language

        assert normalize_telegram_language(code) == expected
