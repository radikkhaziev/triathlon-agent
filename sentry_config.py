"""Sentry SDK initialization — single entry point for all components."""

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


def _before_send(event, hint):
    """Scrub sensitive data from Sentry events."""
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

    # Scrub local variables in exception stackframes
    for exc_info in event.get("exception", {}).get("values", []):
        for frame in exc_info.get("stacktrace", {}).get("frames", []):
            if frame.get("vars"):
                _scrub_dict(frame["vars"])

    return event


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
