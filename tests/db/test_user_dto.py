"""UserDTO credential exclusion — regression for issue #147.

Dramatiq serializes actor args via `model_dump(mode='json')` / `repr()` into
messages that flow to Redis, exception strings, Sentry, and GitHub issues.
Plaintext credentials leaked that way in #146. The fix is to keep credentials
out of UserDTO entirely — actors re-fetch the ORM User when they need them.
"""

import pytest
from pydantic import ValidationError

from data.db import UserDTO


class TestUserDTOExcludesCredentials:
    def test_no_api_key_field(self):
        """UserDTO must not define api_key — credentials stay on ORM only."""
        assert "api_key" not in UserDTO.model_fields

    def test_no_mcp_token_field(self):
        assert "mcp_token" not in UserDTO.model_fields

    def test_construction_rejects_api_key(self):
        """Passing api_key to constructor must fail — protects against regressions
        where someone re-adds the field without thinking about the leak path."""
        with pytest.raises(ValidationError) as exc_info:
            UserDTO(id=1, chat_id="x", api_key="leaked")
        # Assert the failure is specifically about the forbidden extra field,
        # not some other incidental validation error.
        errors = exc_info.value.errors()
        assert any(e["type"] == "extra_forbidden" and "api_key" in e["loc"] for e in errors)

    def test_construction_rejects_mcp_token(self):
        with pytest.raises(ValidationError) as exc_info:
            UserDTO(id=1, chat_id="x", mcp_token="leaked")
        errors = exc_info.value.errors()
        assert any(e["type"] == "extra_forbidden" and "mcp_token" in e["loc"] for e in errors)

    def test_repr_contains_no_credential_keys(self):
        u = UserDTO(id=1, chat_id="111", username="tester")
        r = repr(u)
        assert "api_key" not in r
        assert "mcp_token" not in r

    def test_model_dump_json_roundtrip_preserves_identity(self):
        """Critical path: dramatiq serializes via model_dump(mode='json') and
        re-parses via model_validate. The DTO must round-trip cleanly."""
        u = UserDTO(id=42, chat_id="555", username="athlete", language="en", is_silent=True)
        payload = u.model_dump(mode="json")
        u2 = UserDTO.model_validate(payload)
        assert u2.id == 42
        assert u2.chat_id == "555"
        assert u2.username == "athlete"
        assert u2.language == "en"
        assert u2.is_silent is True
        assert "api_key" not in payload
        assert "mcp_token" not in payload


class TestBotChatInitializedField:
    """Issue #266: ``bot_chat_initialized`` controls whether TelegramTool
    actually issues sendMessage. The DTO default is True (assume an
    onboarded user) so ad-hoc test/actor constructions don't accidentally
    suppress; the ORM-side default for newly created User rows is False
    (the migration drops the server_default after backfill, so new rows
    rely on the SQLAlchemy ``default=False`` until /start flips it)."""

    def test_default_true(self):
        u = UserDTO(id=1, chat_id="111")
        assert u.bot_chat_initialized is True

    def test_explicit_false_propagates(self):
        u = UserDTO(id=1, chat_id="111", bot_chat_initialized=False)
        assert u.bot_chat_initialized is False

    def test_roundtrip_preserves_false(self):
        """Dramatiq path: actor argument must serialize+restore the False sentinel."""
        u = UserDTO(id=1, chat_id="111", bot_chat_initialized=False)
        u2 = UserDTO.model_validate(u.model_dump(mode="json"))
        assert u2.bot_chat_initialized is False
