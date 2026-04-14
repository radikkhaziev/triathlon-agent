"""UserDTO credential exclusion — regression for issue #147.

Dramatiq serializes actor args via `model_dump(mode='json')` / `repr()` into
messages that flow to Redis, exception strings, Sentry, and GitHub issues.
Plaintext credentials leaked that way in #146. The fix is to keep credentials
out of UserDTO entirely — actors re-fetch the ORM User when they need them.
"""

import pytest

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
        with pytest.raises(ValueError):
            UserDTO.model_validate(
                {"id": 1, "chat_id": "x", "api_key": "leaked"},
                strict=False,
                context=None,
            )

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
