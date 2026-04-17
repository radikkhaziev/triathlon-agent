"""Intervals.icu webhook receiver — parses push events, verifies secret,
resolves tenant, dispatches actors for supported event types.
"""

import hmac
import logging

import sentry_sdk
from fastapi import Request
from pydantic import BaseModel, TypeAdapter, ValidationError

from api.dto import IntervalsWebhookEvent, IntervalsWebhookPayload
from config import settings
from data.db import User, UserDTO
from data.intervals.dto import ActivityDTO, SportSettingsDTO, WellnessDTO
from tasks.actors import actor_user_wellness

from . import router

logger = logging.getLogger(__name__)

# Map each known event type to the DTO used to parse items in `records`.
_EVENT_RECORD_MODELS: dict[str, type[BaseModel] | None] = {
    "WELLNESS_UPDATED": WellnessDTO,
    "SPORT_SETTINGS_UPDATED": SportSettingsDTO,
    "ACTIVITY_UPLOADED": ActivityDTO,
    "ACTIVITY_ANALYZED": ActivityDTO,
    "ACTIVITY_UPDATED": ActivityDTO,
    # Unknown / unsampled:
    "FITNESS_UPDATED": None,
    "CALENDAR_UPDATED": None,
    "ACTIVITY_DELETED": None,
    "ACTIVITY_ACHIEVEMENTS": None,
    "APP_SCOPE_CHANGED": None,
}

_KNOWN_EVENT_TYPES = frozenset(_EVENT_RECORD_MODELS.keys())


# One-shot guard so the "SECRET is not set" warning emits at most once per process.
_webhook_secret_missing_warned = False


def _format_validation_errors(exc: ValidationError) -> list[str]:
    """Turn a Pydantic ``ValidationError`` into PII-safe metadata strings."""
    sanitized: list[str] = []
    for err in exc.errors(include_input=False):
        loc = ".".join(str(part) for part in err.get("loc", ())) or "<root>"
        error_type = err.get("type", "unknown")
        sanitized.append(f"{loc}:{error_type}")
    return sanitized


def _verify_webhook_secret(payload: IntervalsWebhookPayload, client_ip: str) -> bool:
    """Compare ``payload.secret`` against ``INTERVALS_WEBHOOK_SECRET`` in constant time."""
    global _webhook_secret_missing_warned
    expected = settings.INTERVALS_WEBHOOK_SECRET.get_secret_value()
    if not expected:
        if not _webhook_secret_missing_warned:
            logger.warning(
                "Intervals webhook received but INTERVALS_WEBHOOK_SECRET is not set — "
                "accepting all requests (debug mode). Set the env var to enforce "
                "verification. This warning will emit only once per process."
            )
            _webhook_secret_missing_warned = True
        return True
    if not payload.secret or not hmac.compare_digest(payload.secret, expected):
        logger.warning(
            "Intervals webhook secret mismatch, dropping payload ip=%s events=%d",
            client_ip,
            len(payload.events),
        )
        return False
    return True


def _classify_parse_status(
    records_count: int,
    parsed_count: int,
    has_model: bool,
) -> tuple[str, str, str]:
    """Decide how to categorize a webhook event for Sentry monitoring."""
    if records_count == 0:
        return "empty", "info", "EMPTY"
    if not has_model:
        return "no_dto", "info", "NO DTO"
    if parsed_count == records_count:
        return "ok", "info", "OK"
    if parsed_count == 0:
        return "failed", "warning", "PARSE FAILED"
    return "partial", "warning", "PARTIAL"


def _sentry_monitor_event(
    event: IntervalsWebhookEvent,
    normalized_type: str,
    user: User,
    parsed_count: int,
    record_field_names: set[str],
    parse_errors: list[str],
    has_model: bool,
) -> None:
    """Send a classified Sentry message with event **metadata only** (no PII)."""
    status, level, prefix = _classify_parse_status(
        records_count=len(event.records),
        parsed_count=parsed_count,
        has_model=has_model,
    )
    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("source", "intervals_webhook")
            scope.set_tag("intervals_event_type", normalized_type)
            scope.set_tag("parse_status", status)
            scope.set_tag("intervals_athlete_id", event.athlete_id)
            scope.set_tag("user_id", str(user.id))
            if event.type != normalized_type:
                scope.set_extra("original_event_type", event.type)
            scope.set_extra("records_count", len(event.records))
            scope.set_extra("parsed_count", parsed_count)
            scope.set_extra("record_field_names", sorted(record_field_names))
            scope.set_extra("event_timestamp", event.timestamp)
            if parse_errors:
                scope.set_extra("parse_errors", parse_errors[:10])
            sentry_sdk.capture_message(
                f"Intervals webhook {prefix}: {normalized_type}",
                level=level,
            )
    except Exception:
        logger.warning("Failed to forward intervals webhook event to Sentry", exc_info=True)


# ---------------------------------------------------------------------------
# Dispatchers — one function per supported event type.
# ---------------------------------------------------------------------------


def _dispatch_wellness(user: UserDTO, event: IntervalsWebhookEvent) -> None:
    if not event.records:
        return

    wellness_dtos = TypeAdapter(list[WellnessDTO]).validate_python(event.records)

    # Sort by updated ascending — process oldest records first
    wellness_dtos.sort(key=lambda w: w.updated)

    for wellness_dto in wellness_dtos:
        actor_user_wellness.send(
            user=user,
            dt=wellness_dto.id,
            wellnessDTO=wellness_dto,
        )


# ---------------------------------------------------------------------------
# Main webhook handler
# ---------------------------------------------------------------------------


async def _handle_webhook_event(event: IntervalsWebhookEvent) -> None:
    """Resolve tenant, parse records, monitor, and dispatch."""
    normalized_type = event.type.strip().upper()

    user = await User.get_by_athlete_id(event.athlete_id)
    if user is None:
        logger.warning(
            "Intervals webhook for unknown athlete_id=%s event_type=%s — skipping",
            event.athlete_id,
            event.type,
        )
        return

    if normalized_type not in _KNOWN_EVENT_TYPES:
        logger.info(
            "Intervals webhook unknown event type=%s normalized=%s user_id=%s",
            event.type,
            normalized_type,
            user.id,
        )
        return

    # Parse records into typed DTOs — drift detection.
    parsed_count = 0
    parse_errors: list[str] = []
    model_cls = _EVENT_RECORD_MODELS.get(normalized_type)
    if model_cls is not None:
        for i, record in enumerate(event.records):
            try:
                model_cls.model_validate(record)
                parsed_count += 1
            except ValidationError as e:
                sanitized = _format_validation_errors(e)
                parse_errors.append(f"record[{i}]: {','.join(sanitized)}")

    record_field_names: set[str] = set()
    for record in event.records:
        if isinstance(record, dict):
            record_field_names.update(record.keys())

    logger.info(
        "Intervals webhook event type=%s normalized=%s user_id=%s athlete_id=%s "
        "records=%d parsed=%d errors=%d timestamp=%s",
        event.type,
        normalized_type,
        user.id,
        event.athlete_id,
        len(event.records),
        parsed_count,
        len(parse_errors),
        event.timestamp,
    )
    if parse_errors:
        logger.warning(
            "Intervals webhook DTO parse errors event_type=%s user_id=%s: %s",
            normalized_type,
            user.id,
            "; ".join(parse_errors[:5]),
        )

    if settings.INTERVALS_WEBHOOK_MONITORING:
        _sentry_monitor_event(
            event,
            normalized_type,
            user,
            parsed_count,
            record_field_names,
            parse_errors,
            has_model=model_cls is not None,
        )

    # Dispatch actors for supported event types.
    user_dto = UserDTO.model_validate(user)
    if normalized_type == "WELLNESS_UPDATED":
        _dispatch_wellness(user_dto, event)


@router.post("/webhook")
async def intervals_webhook(request: Request) -> dict:
    """Receive push webhooks from Intervals.icu.

    Always returns 200 — Intervals.icu retries/disables the webhook on 4xx/5xx.
    Supported dispatchers: WELLNESS_UPDATED → ``actor_user_wellness``.
    """
    client_ip = request.client.host if request.client else "unknown"

    interesting_headers = {
        k: v for k, v in request.headers.items() if k.startswith("x-") or k in ("user-agent", "content-type")
    }
    logger.info("Intervals webhook delivery ip=%s headers=%s", client_ip, interesting_headers)

    try:
        raw_body = await request.json()
    except Exception:
        raw = await request.body()
        logger.warning("Intervals webhook non-JSON body from ip=%s size=%d", client_ip, len(raw))
        return {"status": "ok"}
    logger.info("Intervals webhook raw body from ip=%s body=%s", client_ip, raw_body)

    try:
        payload = IntervalsWebhookPayload.model_validate(raw_body)
    except ValidationError as e:
        sanitized = _format_validation_errors(e)
        logger.warning(
            "Intervals webhook invalid payload from ip=%s error_count=%d fields=%s",
            client_ip,
            len(sanitized),
            sanitized[:10],
        )
        return {"status": "ok"}

    if not _verify_webhook_secret(payload, client_ip):
        return {"status": "ok"}

    for event in payload.events:
        try:
            await _handle_webhook_event(event)
        except Exception:
            logger.exception(
                "Intervals webhook handler failed for event type=%s athlete_id=%s",
                event.type,
                event.athlete_id,
            )
            with sentry_sdk.new_scope() as scope:
                scope.set_tag("source", "intervals_webhook")
                scope.set_tag("intervals_event_type", event.type)
                scope.set_tag("intervals_athlete_id", event.athlete_id)
                sentry_sdk.capture_exception()

    return {"status": "ok"}
