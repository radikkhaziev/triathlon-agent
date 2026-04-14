"""Sentry SDK initialization — single entry point for all components."""

import re

import sentry_sdk
from sentry_sdk.integrations.dramatiq import DramatiqIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from config import settings

SENSITIVE_KEYS = {
    "api_key",
    "token",
    "secret",
    "password",
    "mcp_token",
    "encryption_key",
    "fernet",
    "jwt",
    "authorization",
    "cookie",
}

# Defence-in-depth for issue #147: scrub credential-shaped substrings inside
# free-form text fields (exception values, log messages, breadcrumb messages).
# These fire even if the structured-dict scrubber misses — e.g. when dramatiq
# serializes actor args via repr() into an exception string.
_REDACT = "[REDACTED]"
# Group 1 = key name + separator (preserved), group 2 = value (redacted).
# Matches quoted values with spaces via `'[^']*'` / `"[^"]*"`, and bare values
# via `[^'"\s,}\)\]]+`. Key alternation covers the credential shapes used in
# this repo — extend when adding new ones.
_SENSITIVE_KV = re.compile(
    r"""(['"]?(?:api[_-]?key|mcp[_-]?token|access[_-]?token|refresh[_-]?token|"""
    r"""bearer[_-]?token|auth[_-]?token|secret|password|passwd|"""
    r"""encryption[_-]?key|fernet[_-]?key|jwt)['"]?\s*[:=]\s*)"""
    r"""(?:'[^']*'|"[^"]*"|[^'"\s,}\)\]]+)""",
    re.IGNORECASE,
)
_SENSITIVE_BEARER = re.compile(
    r"(Authorization\s*:\s*Bearer\s+)[\w\-\.=+/]+",
    re.IGNORECASE,
)
# Pydantic's SecretStr already masks via repr(), but catch the rare case of a
# custom repr or manual unwrap leaking a SecretStr('realvalue') literal.
_SENSITIVE_SECRETSTR = re.compile(r"SecretStr\(\s*['\"][^'\"]+['\"]\s*\)")


def _scrub_text(text):
    """Redact credential-shaped substrings. Passes through non-strings untouched."""
    if not isinstance(text, str) or not text:
        return text
    text = _SENSITIVE_KV.sub(rf"\1{_REDACT}", text)
    text = _SENSITIVE_BEARER.sub(rf"\1{_REDACT}", text)
    text = _SENSITIVE_SECRETSTR.sub(f"SecretStr({_REDACT})", text)
    return text


def _before_send(event, hint):
    """Scrub sensitive data from Sentry events.

    Two complementary passes:
      1. Key-based dict scrubber (`_scrub_dict`) — redacts by key name.
      2. Regex string scrubber (`_walk_strings` + `_scrub_text`) — redacts by
         value shape (e.g. `api_key='real'`). Catches secrets embedded inside
         exception messages, log strings, and free-form text fields where
         the key-based pass can't see.
    """
    _walk_strings(event)

    for section in ("extra", "contexts"):
        data = event.get(section, {})
        if isinstance(data, dict):
            _scrub_dict(data)

    request = event.get("request", {})
    _scrub_dict(request.get("headers", {}))
    if isinstance(request.get("data"), dict):
        _scrub_dict(request["data"])

    for crumb in event.get("breadcrumbs", {}).get("values", []):
        _scrub_dict(crumb.get("data", {}))

    # Stackframe vars — keyed dict scrub (plus the string walk above already
    # rewrote any sensitive values that appeared as raw strings).
    for exc_info in event.get("exception", {}).get("values", []):
        for frame in exc_info.get("stacktrace", {}).get("frames", []):
            if frame.get("vars"):
                _scrub_dict(frame["vars"])
    for thread in event.get("threads", {}).get("values", []):
        for frame in thread.get("stacktrace", {}).get("frames", []):
            if frame.get("vars"):
                _scrub_dict(frame["vars"])

    return event


def _walk_strings(node) -> None:
    """Recursively rewrite every string leaf in the event tree through `_scrub_text`.

    Covers exception.values[*].value, event.message, logentry.message,
    breadcrumb messages, extra dict values, threads frames — every leak path
    at once, instead of hand-listing known keys that decay as Sentry evolves.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str):
                node[k] = _scrub_text(v)
            elif isinstance(v, (dict, list)):
                _walk_strings(v)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                node[i] = _scrub_text(v)
            elif isinstance(v, (dict, list)):
                _walk_strings(v)


def _scrub_dict(d: dict):
    """Redact values whose keys match sensitive patterns."""
    for key in list(d.keys()):
        if any(s in key.lower() for s in SENSITIVE_KEYS):
            d[key] = "[REDACTED]"
        elif isinstance(d[key], dict):
            _scrub_dict(d[key])
        elif isinstance(d[key], list):
            for item in d[key]:
                if isinstance(item, dict):
                    _scrub_dict(item)


def _traces_sampler(sampling_context):
    """Custom sampler: skip health checks, sample everything else."""
    tx_name = sampling_context.get("transaction_context", {}).get("name", "")
    if tx_name in ("GET /health", "GET /health/"):
        return 0.0
    return settings.SENTRY_TRACES_SAMPLE_RATE


def init_sentry():
    """Initialize Sentry SDK. No-op if SENTRY_DSN is empty."""
    if not settings.SENTRY_DSN:
        return

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        release=settings.SENTRY_RELEASE or None,
        traces_sampler=_traces_sampler,
        before_send=_before_send,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(transaction_style="endpoint"),
            DramatiqIntegration(),
            LoggingIntegration(
                level=None,
                event_level="ERROR",
            ),
        ],
        send_default_pii=False,
    )
