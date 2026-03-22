"""Tests for Garmin Connect authentication and login logic."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

from data.garmin_client import GarminClient

load_dotenv()

_email = os.getenv("GARMIN_EMAIL")
_password = os.getenv("GARMIN_PASSWORD")

pytestmark = pytest.mark.skipif(
    not _email or not _password,
    reason="GARMIN_EMAIL / GARMIN_PASSWORD not set in .env",
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset GarminClient singleton so each test starts fresh."""
    yield
    GarminClient._instance = None
    GarminClient._login_cooldown_until = 0.0


def _patch_settings(token_dir: str):
    """Patch settings in the garmin_client module to use a custom token dir."""
    mock_settings = MagicMock()
    mock_settings.GARMIN_EMAIL = _email
    mock_settings.GARMIN_PASSWORD = MagicMock()
    mock_settings.GARMIN_PASSWORD.get_secret_value.return_value = _password
    mock_settings.GARMIN_TOKENS = token_dir
    return patch("data.garmin_client.settings", mock_settings)


class TestLoginWithExistingTokens:
    """Login with already saved valid tokens — should skip credential login."""

    def test_login_with_valid_tokens(self):
        """When valid tokens exist, should load them and not call client.login()."""
        gc = GarminClient()
        assert gc.profile is not None, "First login should succeed"

        # Reset singleton, but tokens remain on disk
        GarminClient._instance = None
        GarminClient._login_cooldown_until = 0.0

        gc2 = GarminClient()
        assert gc2.profile is not None, "Second login should succeed using saved tokens"
        assert isinstance(gc2.profile, dict)


class TestLoginWithInvalidTokens:
    """Login with corrupted/expired tokens — should fall back to credentials."""

    def test_login_with_bad_tokens_falls_back(self, tmp_path):
        """When token files exist but are invalid, should fall back to credential login."""
        token_dir = tmp_path / "garmin_tokens"
        token_dir.mkdir()

        # Write garbage token files to simulate corrupted tokens
        (token_dir / "oauth1_token.json").write_text('{"invalid": "data"}')
        (token_dir / "oauth2_token.json").write_text('{"invalid": "data"}')

        with _patch_settings(str(token_dir)):
            gc = GarminClient()

            # Should still succeed by falling back to credential login
            assert gc.profile is not None, "Should fall back to credential login when tokens are invalid"
            assert isinstance(gc.profile, dict)
