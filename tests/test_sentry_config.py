"""Tests for sentry_config — before_send scrubbing, traces_sampler, init guard."""

from unittest.mock import patch

from sentry_config import _before_send, _scrub_dict, _traces_sampler, init_sentry


class TestScrubDict:
    def test_redacts_sensitive_keys(self):
        d = {"api_key": "secret123", "name": "test"}
        _scrub_dict(d)
        assert d["api_key"] == "[REDACTED]"
        assert d["name"] == "test"

    def test_redacts_case_insensitive(self):
        d = {"MCP_TOKEN": "tok", "Authorization": "Bearer x"}
        _scrub_dict(d)
        assert d["MCP_TOKEN"] == "[REDACTED]"
        assert d["Authorization"] == "[REDACTED]"

    def test_redacts_nested(self):
        d = {"outer": {"api_key": "secret", "safe": "ok"}}
        _scrub_dict(d)
        assert d["outer"]["api_key"] == "[REDACTED]"
        assert d["outer"]["safe"] == "ok"

    def test_redacts_partial_match(self):
        d = {"intervals_api_key_encrypted": "val", "fernet_key": "val2"}
        _scrub_dict(d)
        assert d["intervals_api_key_encrypted"] == "[REDACTED]"
        assert d["fernet_key"] == "[REDACTED]"

    def test_redacts_list_of_dicts(self):
        d = {"users": [{"token": "secret", "name": "ok"}]}
        _scrub_dict(d)
        assert d["users"][0]["token"] == "[REDACTED]"
        assert d["users"][0]["name"] == "ok"

    def test_empty_dict(self):
        d = {}
        _scrub_dict(d)
        assert d == {}


class TestBeforeSend:
    def test_scrubs_extra(self):
        event = {"extra": {"api_key": "secret", "count": 5}}
        result = _before_send(event, {})
        assert result["extra"]["api_key"] == "[REDACTED]"
        assert result["extra"]["count"] == 5

    def test_scrubs_request_headers(self):
        event = {"request": {"headers": {"authorization": "Bearer tok123"}}}
        result = _before_send(event, {})
        assert result["request"]["headers"]["authorization"] == "[REDACTED]"

    def test_scrubs_request_data(self):
        event = {"request": {"data": {"password": "p4ss", "username": "user1"}}}
        result = _before_send(event, {})
        assert result["request"]["data"]["password"] == "[REDACTED]"
        assert result["request"]["data"]["username"] == "user1"

    def test_scrubs_breadcrumbs(self):
        event = {
            "breadcrumbs": {
                "values": [
                    {"data": {"mcp_token": "tok"}},
                    {"data": {"query": "SELECT 1"}},
                ]
            }
        }
        result = _before_send(event, {})
        assert result["breadcrumbs"]["values"][0]["data"]["mcp_token"] == "[REDACTED]"
        assert result["breadcrumbs"]["values"][1]["data"]["query"] == "SELECT 1"

    def test_scrubs_stackframe_vars(self):
        event = {
            "exception": {
                "values": [
                    {
                        "stacktrace": {
                            "frames": [
                                {"vars": {"api_key": "secret123", "count": 5}},
                                {"vars": {"mcp_token": "tok", "path": "/api"}},
                            ]
                        }
                    }
                ]
            }
        }
        result = _before_send(event, {})
        frames = result["exception"]["values"][0]["stacktrace"]["frames"]
        assert frames[0]["vars"]["api_key"] == "[REDACTED]"
        assert frames[0]["vars"]["count"] == 5
        assert frames[1]["vars"]["mcp_token"] == "[REDACTED]"
        assert frames[1]["vars"]["path"] == "/api"

    def test_returns_event(self):
        event = {"message": "test"}
        assert _before_send(event, {}) is event

    def test_handles_missing_sections(self):
        event = {}
        result = _before_send(event, {})
        assert result == {}


class TestTracesSampler:
    def test_skips_health_check(self):
        ctx = {"transaction_context": {"name": "GET /health"}}
        assert _traces_sampler(ctx) == 0.0

    def test_skips_health_check_trailing_slash(self):
        ctx = {"transaction_context": {"name": "GET /health/"}}
        assert _traces_sampler(ctx) == 0.0

    def test_samples_other_transactions(self):
        ctx = {"transaction_context": {"name": "GET /api/report"}}
        assert _traces_sampler(ctx) == 0.1  # default from settings

    def test_samples_empty_context(self):
        ctx = {}
        assert _traces_sampler(ctx) == 0.1


class TestInitSentry:
    def test_noop_when_dsn_empty(self):
        """init_sentry should not call sentry_sdk.init when DSN is empty."""
        with (
            patch("sentry_config.settings") as mock_settings,
            patch("sentry_config.sentry_sdk.init") as mock_init,
        ):
            mock_settings.SENTRY_DSN = ""
            init_sentry()
            mock_init.assert_not_called()

    def test_calls_init_when_dsn_set(self):
        """init_sentry should call sentry_sdk.init when DSN is configured."""
        with (
            patch("sentry_config.settings") as mock_settings,
            patch("sentry_config.sentry_sdk.init") as mock_init,
        ):
            mock_settings.SENTRY_DSN = "https://key@sentry.io/123"
            mock_settings.SENTRY_ENVIRONMENT = "test"
            mock_settings.SENTRY_TRACES_SAMPLE_RATE = 0.5
            mock_settings.SENTRY_RELEASE = "v1.0"
            init_sentry()
            mock_init.assert_called_once()
            call_kwargs = mock_init.call_args[1]
            assert call_kwargs["dsn"] == "https://key@sentry.io/123"
            assert call_kwargs["environment"] == "test"
            assert call_kwargs["release"] == "v1.0"
