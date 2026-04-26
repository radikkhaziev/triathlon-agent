"""Tests for sentry_config — before_send scrubbing, traces_sampler, init guard."""

from unittest.mock import patch

from sentry_config import _before_send, _scrub_dict, _scrub_text, _traces_sampler, init_sentry


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


class TestScrubText:
    """Defence-in-depth regex scrubbing for issue #147.

    Dramatiq serializes actor args via repr() into exception messages. The
    structured-dict scrubber doesn't see those strings, so regex catches them.
    """

    def test_redacts_api_key_dict_repr(self):
        text = "_actor_foo(user={'id': 1, 'api_key': '1h545g8e229f27ewxdv23z8h'})"
        scrubbed = _scrub_text(text)
        assert "1h545g8e229f27ewxdv23z8h" not in scrubbed
        assert "[REDACTED]" in scrubbed
        assert "'id': 1" in scrubbed

    def test_redacts_mcp_token_dict_repr(self):
        text = "args: {'mcp_token': 'WGcaMeXA35bfNvSrg4v7sWl-1jebW2Ne'}"
        assert "WGcaMeXA35bfNvSrg4v7sWl-1jebW2Ne" not in _scrub_text(text)

    def test_redacts_equals_form(self):
        text = "foo(api_key=abc123, count=5)"
        scrubbed = _scrub_text(text)
        assert "abc123" not in scrubbed
        assert "count=5" in scrubbed

    def test_redacts_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.xyz"
        scrubbed = _scrub_text(text)
        assert "eyJhbGciOiJIUzI1NiJ9.xyz" not in scrubbed
        assert "Authorization" in scrubbed

    def test_redacts_secretstr_literal(self):
        text = "SecretStr('leaked-value')"
        assert "leaked-value" not in _scrub_text(text)

    def test_preserves_non_sensitive_text(self):
        text = "Normal error message with no secrets"
        assert _scrub_text(text) == text

    def test_handles_none(self):
        assert _scrub_text(None) is None

    def test_handles_empty(self):
        assert _scrub_text("") == ""


class TestBeforeSendTextScrubbing:
    """Issue #147: the actual leak in #146 was the exception.value string."""

    def test_scrubs_exception_value(self):
        event = {
            "exception": {
                "values": [
                    {
                        "value": "Failed to process message _actor_foo("
                        "user={'id': 1, 'api_key': 'real-secret-key-123'})",
                    }
                ]
            }
        }
        result = _before_send(event, {})
        assert "real-secret-key-123" not in result["exception"]["values"][0]["value"]

    def test_scrubs_event_message(self):
        event = {"message": "Login with mcp_token='tok-abcdef'"}
        result = _before_send(event, {})
        assert "tok-abcdef" not in result["message"]

    def test_scrubs_breadcrumb_message(self):
        event = {"breadcrumbs": {"values": [{"message": "Calling API with api_key='xyz999'"}]}}
        result = _before_send(event, {})
        assert "xyz999" not in result["breadcrumbs"]["values"][0]["message"]

    def test_scrubs_extra_string_values(self):
        """String values under non-sensitive keys are still scrubbed by the tree walker."""
        event = {"extra": {"context": "called foo(api_key='leaked-123')"}}
        result = _before_send(event, {})
        assert "leaked-123" not in result["extra"]["context"]

    def test_scrubs_threads_frames(self):
        """Non-exception threads stacktraces also get their vars scrubbed."""
        event = {
            "threads": {
                "values": [
                    {
                        "stacktrace": {
                            "frames": [
                                {"vars": {"mcp_token": "real-thread-tok"}},
                            ]
                        }
                    }
                ]
            }
        }
        result = _before_send(event, {})
        assert result["threads"]["values"][0]["stacktrace"]["frames"][0]["vars"]["mcp_token"] == "[REDACTED]"

    def test_scrubs_telegram_bot_token_in_exception_value(self):
        """Issue #266/#267/#268: httpx HTTPStatusError leaks bot token via URL in exception.value."""
        leaked = "AAFmjQKLJzQWII3eRcPyzJUai4Bi38DhTuI"
        event = {
            "exception": {
                "values": [
                    {
                        "value": (
                            f"Client error '400 Bad Request' for url "
                            f"'https://api.telegram.org/bot8598544740:{leaked}/sendMessage'"
                        ),
                    }
                ]
            }
        }
        result = _before_send(event, {})
        scrubbed = result["exception"]["values"][0]["value"]
        assert leaked not in scrubbed
        assert "[REDACTED]" in scrubbed

    def test_scrubs_quoted_value_with_spaces(self):
        """Quoted value containing spaces must be fully redacted, not truncated at first space."""
        text = "password='hunter 2 with spaces'"
        event = {"exception": {"values": [{"value": text}]}}
        result = _before_send(event, {})
        scrubbed = result["exception"]["values"][0]["value"]
        assert "hunter" not in scrubbed
        assert "spaces" not in scrubbed


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
