"""Intervals.icu integration endpoints.

Two responsibilities:

1. **OAuth flow** (`POST /auth/init`, `GET /auth/callback`) — Phase 1 of the
   OAuth migration. Scope is intentionally narrow: we observe the real
   token-exchange response shape before wiring the tokens into
   `IntervalsClient` (Phase 2).

2. **Webhook receiver** (`POST /webhook`) — receives Intervals.icu push
   events, verifies the shared `secret`, resolves the tenant by
   `athlete_id`, parses records against reused `data/intervals/dto` models
   (drift detection), and forwards metadata-only samples to Sentry during
   the observability phase. Real per-event dispatch (writing to DB, firing
   sync actors) lands in a later phase once parser coverage is confirmed.
"""

import hmac
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
import sentry_sdk
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ValidationError

from api.auth import _get_jwt_secret
from api.deps import require_viewer
from api.dto import IntervalsAuthInitResponse, IntervalsWebhookEvent, IntervalsWebhookPayload
from config import settings
from data.db import User, get_session
from data.intervals.dto import ActivityDTO, SportSettingsDTO, WellnessDTO

# Map each known event type to the DTO used to parse items in `records`.
# Intervals.icu webhook records have the same shape as the corresponding
# REST API responses, so we reuse the same Pydantic models that
# `data/intervals/client.py` already validates for polling sync. Keeps the
# webhook parser and the REST parser on a single source of truth.
#
# Only types whose record shape is already verified against a production
# webhook are mapped here. Types with unknown or guessed shape map to None
# so they fall through the monitoring path unparsed — their samples reach
# the observability pipeline and tell us what DTO to write next, instead of
# silently parsing as a too-permissive superset and hiding the real shape.
_EVENT_RECORD_MODELS: dict[str, type[BaseModel] | None] = {
    "WELLNESS_UPDATED": WellnessDTO,  # verified 2026-04-15 on real i317960 payload
    "SPORT_SETTINGS_UPDATED": SportSettingsDTO,
    "ACTIVITY_UPLOADED": ActivityDTO,
    "ACTIVITY_ANALYZED": ActivityDTO,
    "ACTIVITY_UPDATED": ActivityDTO,
    # Unknown / unsampled:
    "FITNESS_UPDATED": None,  # close to Wellness but shape unconfirmed — don't guess
    "CALENDAR_UPDATED": None,  # read shape ≠ EventExDTO (write-only DTO)
    "ACTIVITY_DELETED": None,  # records likely empty or id-only
    "ACTIVITY_ACHIEVEMENTS": None,  # shape unknown
    "APP_SCOPE_CHANGED": None,  # no records expected
}

# Event types we handle (from Intervals.icu webhook config). Keep in sync
# with the checkboxes in the OAuth app Manage App settings. Unknown types
# are logged at INFO but not dispatched — future types can be added here
# without touching the payload parser.
_KNOWN_EVENT_TYPES = frozenset(
    {
        "APP_SCOPE_CHANGED",
        "CALENDAR_UPDATED",
        "ACTIVITY_UPLOADED",
        "ACTIVITY_ANALYZED",
        "ACTIVITY_UPDATED",
        "ACTIVITY_DELETED",
        "ACTIVITY_ACHIEVEMENTS",
        "WELLNESS_UPDATED",
        "FITNESS_UPDATED",
        "SPORT_SETTINGS_UPDATED",
    }
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intervals", tags=["intervals"])

_OAUTH_AUTHORIZE_URL = "https://intervals.icu/oauth/authorize"
_OAUTH_TOKEN_URL = "https://intervals.icu/api/oauth/token"
_OAUTH_SCOPES = "ACTIVITY:READ,WELLNESS:READ,CALENDAR:WRITE,SETTINGS:WRITE"
# Per Intervals.icu docs: "For each scope specify READ or WRITE (to update,
# implies READ access) and use commas to separate multiple scopes." So
# :WRITE gives us both write AND read — listing the same area twice produces
# "Duplicate scope" error because their parser keys by area name.
#
# Why SETTINGS:WRITE (not READ): `actor_update_zones` pushes new LTHR values
# to Intervals.icu via client.update_sport_settings() after ramp-test drift
# detection. Read-only would break the "Обновить зоны" button in morning
# report.
_STATE_TTL_MINUTES = 15
_STATE_PURPOSE = "intervals_oauth"


def _generate_oauth_state(user_id: int) -> str:
    """Signed JWT binding the OAuth callback to its originating user.

    `purpose` claim prevents a valid session JWT from being replayed as an
    OAuth state. 15-min TTL is the consent-screen-fill budget — longer gives
    CSRF more room, shorter risks user timeouts after 2FA flows.
    """
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=_STATE_TTL_MINUTES),
        "purpose": _STATE_PURPOSE,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm="HS256")


def _validate_oauth_state(state: str) -> int | None:
    """Return `user_id` or `None` if state is invalid/expired/wrong purpose."""
    try:
        payload = jwt.decode(state, _get_jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != _STATE_PURPOSE:
        return None
    try:
        return int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        return None


def _format_validation_errors(exc: ValidationError) -> list[str]:
    """Turn a Pydantic `ValidationError` into PII-safe metadata strings.

    `str(ValidationError)` embeds `input_value=...` in the default message,
    which leaks record contents (weight, HRV, restingHR, ...) into logs and
    Sentry. Instead we format from `exc.errors(include_input=False)` and emit
    only the field path and error type — enough to diagnose schema drift,
    nothing from the payload itself. `msg` is intentionally dropped because
    Pydantic sometimes inlines the value there too.
    """
    sanitized: list[str] = []
    for err in exc.errors(include_input=False):
        loc = ".".join(str(part) for part in err.get("loc", ())) or "<root>"
        error_type = err.get("type", "unknown")
        sanitized.append(f"{loc}:{error_type}")
    return sanitized


# Module-level guard so the "SECRET is not set" warning emits at most once
# per process, instead of once per delivery. First real webhook still leaves
# a marker in the logs; subsequent ones stay silent.
_webhook_secret_missing_warned = False


def _verify_webhook_secret(payload: IntervalsWebhookPayload, client_ip: str) -> bool:
    """Compare `payload.secret` against `INTERVALS_WEBHOOK_SECRET` in constant
    time. Returns True if verified or if verification is disabled (empty
    secret in settings — Phase 1 debug mode).

    On mismatch we log + return False so the caller can silently drop the
    payload without 4xx-ing Intervals.icu (which would retry or disable the
    webhook).
    """
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
    """Decide how to categorize a webhook event for Sentry monitoring.

    Returns `(status, level, prefix)`:
    - `status` — value for the `parse_status` tag: `ok` / `partial` /
      `failed` / `no_dto` / `empty`. Filterable in Sentry UI.
    - `level` — Sentry severity (`info` for OK/empty/no_dto, `warning` for
      partial/failed so they show up in warning filters and alerts).
    - `prefix` — human-readable tag embedded in the Sentry message so each
      category becomes its own Sentry Issue (distinct grouping fingerprint).

    Decision tree:
    - No records at all → empty (rare: APP_SCOPE_CHANGED)
    - No DTO mapped → no_dto (shape needs sampling)
    - All records parsed cleanly → ok
    - Zero records parsed (100% errors) → failed
    - Otherwise → partial (some records parsed, some errored)
    """
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
    """Send a classified Sentry message with event **metadata only**.

    **Does NOT forward record contents.** Wellness webhooks contain health
    data (weight, HRV, resting HR, sleep, body fat, blood glucose, ...)
    which is GDPR Art. 9 special-category PII; we deliberately keep it out
    of Sentry. What we send instead is enough to verify DTO coverage:

    - message encodes parse status (`OK` / `PARTIAL` / `PARSE FAILED` /
      `NO DTO` / `EMPTY`) so Sentry groups them into separate Issues —
      distinguishable in the Issues list without opening each event
    - level is `warning` for `partial` / `failed`, `info` for the rest,
      so you can filter/alert on `level:warning tag:source:intervals_webhook`
    - tags: `source`, `intervals_event_type` (normalized), `parse_status`,
      `intervals_athlete_id`, `user_id`
    - extras: `original_event_type` (only if differs from normalized),
      `records_count`, `parsed_count`, `record_field_names` (sorted set of
      top-level keys — for schema drift detection), `parse_errors` (first
      10, PII-sanitized via `_format_validation_errors`)

    Errors during Sentry send are swallowed — monitoring must never affect
    webhook 200 responses. Disable via `INTERVALS_WEBHOOK_MONITORING=false`
    (opt-in by default — see `config.py`).
    """
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


async def _handle_webhook_event(event: IntervalsWebhookEvent) -> None:
    """Resolve tenant by athlete_id, parse records into known DTOs, log and
    (during monitoring phase) forward metadata to Sentry. Real per-type
    dispatch (writing to wellness table, firing sync actors, etc.) is
    deferred until the sampled payloads confirm our DTOs parse them
    without drift.
    """
    # Normalize event type — tolerance for case/whitespace drift in the
    # upstream API. Original `event.type` is kept as-received so auditing /
    # Sentry / logs can still see what Intervals.icu actually sent if it
    # ever differs from the normalized form.
    normalized_type = event.type.strip().upper()

    user = await User.get_by_athlete_id(event.athlete_id)
    if user is None:
        logger.warning(
            "Intervals webhook for unknown athlete_id=%s event_type=%s — skipping (no matching row in users table)",
            event.athlete_id,
            event.type,
        )
        return

    if normalized_type not in _KNOWN_EVENT_TYPES:
        logger.info(
            "Intervals webhook unknown event type=%s normalized=%s user_id=%s athlete_id=%s",
            event.type,
            normalized_type,
            user.id,
            event.athlete_id,
        )
        return

    # Parse records into typed DTOs for known event types — **drift detection
    # only**. Successful parses are counted and discarded; the goal during
    # the observability phase is to learn whether Intervals.icu's real shape
    # still matches our DTOs. Real dispatch (writing to wellness table, etc.)
    # starts in a later phase when we trust the parser.
    #
    # Only catch Pydantic `ValidationError` here — any other exception
    # (TypeError, AttributeError, ...) is a code defect and should propagate
    # to the outer try/except in `intervals_webhook`, which captures it in
    # Sentry with stack trace.
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

    # Collect top-level keys across the batch — safe to forward to Sentry
    # (no PII values, only field names), lets us spot schema drift.
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


@router.post("/auth/init", response_model=IntervalsAuthInitResponse)
async def intervals_oauth_init(user: User = Depends(require_viewer)) -> IntervalsAuthInitResponse:
    """Initiate the Intervals.icu OAuth flow from an authenticated XHR.

    Why POST+JSON instead of a GET redirect: the frontend carries auth via the
    `Authorization` header (Telegram initData or Bearer JWT from localStorage).
    A full-page `<a href>` navigation would NOT send that header, so a GET
    endpoint with `require_viewer` would 401. Instead the frontend calls this
    over `apiFetch` (which attaches the header), receives the signed authorize
    URL, and navigates the browser to it via `window.location.assign(...)`.

    Returns `{authorize_url}` — the Intervals.icu /oauth/authorize URL with our
    `client_id`, `redirect_uri`, `scope`, and a short-lived signed `state` JWT
    that binds the callback to this user.

    Returns 503 if `INTERVALS_OAUTH_CLIENT_ID` is not configured.
    """
    if not settings.INTERVALS_OAUTH_CLIENT_ID:
        logger.error("OAuth init called but INTERVALS_OAUTH_CLIENT_ID is not set")
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Intervals.icu OAuth is not configured on this server")

    state = _generate_oauth_state(user.id)
    params = {
        "client_id": settings.INTERVALS_OAUTH_CLIENT_ID,
        "redirect_uri": settings.INTERVALS_OAUTH_REDIRECT_URI,
        "scope": _OAUTH_SCOPES,
        "state": state,
    }
    url = f"{_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
    logger.info("Intervals OAuth init user_id=%s redirect_uri=%s", user.id, settings.INTERVALS_OAUTH_REDIRECT_URI)
    return IntervalsAuthInitResponse(authorize_url=url)


@router.get("/auth/callback")
async def intervals_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle Intervals.icu OAuth callback — exchange code for access_token.

    Phase 1 scope: the callback stores tokens in the DB but **does not**
    promote viewer→athlete, does not generate mcp_token, and does not
    dispatch sync actors. Those side-effects land in Phase 2 after we
    verify the handshake.

    Logs the response structure (keys, athlete_id, scope) — never the raw
    `access_token`. This is intentional for Phase 1 observability.

    Always returns a 302 to `/settings?connected=intervals` on success, or
    `/settings?error=oauth_<reason>` on any failure path. The frontend reads
    the query param on mount and shows a toast.
    """
    settings_url = f"{settings.API_BASE_URL.rstrip('/')}/settings"

    # User declined on Intervals.icu consent screen
    if error:
        logger.info("Intervals OAuth user declined: error=%s", error)
        return RedirectResponse(f"{settings_url}?error=oauth_cancelled", status_code=302)

    if not code or not state:
        logger.warning("Intervals OAuth callback missing code or state (code=%s state=%s)", bool(code), bool(state))
        return RedirectResponse(f"{settings_url}?error=oauth_invalid_callback", status_code=302)

    user_id = _validate_oauth_state(state)
    if user_id is None:
        logger.warning("Intervals OAuth callback with invalid/expired state")
        return RedirectResponse(f"{settings_url}?error=oauth_invalid_state", status_code=302)

    # Server-to-server token exchange. cookbook form:
    #   curl -X POST https://intervals.icu/api/oauth/token
    #     -d client_id=... -d client_secret=... -d code=...
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                _OAUTH_TOKEN_URL,
                data={
                    "client_id": settings.INTERVALS_OAUTH_CLIENT_ID,
                    "client_secret": settings.INTERVALS_OAUTH_CLIENT_SECRET.get_secret_value(),
                    "code": code,
                },
            )
    except httpx.RequestError as e:
        logger.error("Intervals OAuth token exchange network error: %s", e)
        sentry_sdk.capture_exception(e)
        return RedirectResponse(f"{settings_url}?error=oauth_network", status_code=302)

    if resp.status_code != 200:
        # Log structure but not full body — may contain partial token data.
        logger.error(
            "Intervals OAuth token exchange failed: status=%s body_len=%d",
            resp.status_code,
            len(resp.text),
        )
        return RedirectResponse(f"{settings_url}?error=oauth_exchange_failed", status_code=302)

    try:
        data = resp.json()
    except ValueError:
        logger.error("Intervals OAuth response is not valid JSON")
        return RedirectResponse(f"{settings_url}?error=oauth_bad_response", status_code=302)

    # Phase 1 observability: log the response shape (keys, athlete, scope) so
    # we can confirm the cookbook assumptions on the first real callback.
    # NEVER log `access_token` itself.
    athlete_obj = data.get("athlete") or {}
    logger.info(
        "Intervals OAuth callback success user_id=%s keys=%s athlete_id=%s athlete_name=%s scope=%s token_type=%s",
        user_id,
        sorted(data.keys()),
        athlete_obj.get("id"),
        athlete_obj.get("name"),
        data.get("scope"),
        data.get("token_type"),
    )

    access_token = data.get("access_token")
    intervals_athlete_id = str(athlete_obj.get("id", "")) or None
    scope = data.get("scope", "")

    if not access_token or not intervals_athlete_id:
        logger.error("Intervals OAuth response missing required fields, keys=%s", sorted(data.keys()))
        return RedirectResponse(f"{settings_url}?error=oauth_bad_response", status_code=302)

    # Athlete_id mismatch guard: if this User row is already linked to a
    # different Intervals.icu athlete, refuse to silently overwrite. Protects
    # against a user accidentally authorizing a second account.
    async with get_session() as session:
        db_user = await session.get(User, user_id)
        if db_user is None:
            logger.error("Intervals OAuth callback user_id=%s not found in DB", user_id)
            return RedirectResponse(f"{settings_url}?error=oauth_user_not_found", status_code=302)

        if db_user.athlete_id and db_user.athlete_id != intervals_athlete_id:
            logger.warning(
                "Intervals OAuth athlete_id mismatch user_id=%s existing=%s incoming=%s",
                user_id,
                db_user.athlete_id,
                intervals_athlete_id,
            )
            return RedirectResponse(f"{settings_url}?error=oauth_account_mismatch", status_code=302)

        db_user.set_oauth_tokens(access_token=access_token, scope=scope)
        if not db_user.athlete_id:
            db_user.athlete_id = intervals_athlete_id
        # Phase 1 intentional omissions (see spec §3):
        # - no role promotion viewer→athlete
        # - no user.generate_mcp_token() for new users
        # - no sync actor dispatch
        await session.commit()

    return RedirectResponse(f"{settings_url}?connected=intervals", status_code=302)


# ---------------------------------------------------------------------------
# Webhook receiver — parses Intervals.icu push events, verifies shared secret,
# resolves tenant by athlete_id, dispatches by event type.
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def intervals_webhook(request: Request) -> dict:
    """Receive push webhooks from Intervals.icu.

    Register this URL in Intervals.icu → Manage App → Webhook URLs:
        https://bot.endurai.me/api/intervals/webhook

    Shape (observed in production):
        {
          "secret": "<shared secret from Webhook Secret field>",
          "events": [
            {
              "athlete_id": "i317960",
              "type": "WELLNESS_UPDATED",
              "timestamp": "2026-04-15T16:29:56.819+00:00",
              "records": [ ... full wellness record with ctl/atl/hrv/sleep/... ]
            },
            ...
          ]
        }

    The endpoint is **public** — Intervals.icu servers POST here without any
    bearer token we can issue. Security relies entirely on `body.secret`
    verification against `INTERVALS_WEBHOOK_SECRET` env var.

    Current behaviour: parse, verify secret, resolve tenant by
    `event.athlete_id`, validate records against typed DTOs (drift
    detection), forward metadata samples to Sentry if
    `INTERVALS_WEBHOOK_MONITORING=true`. Real dispatching (writing records
    to DB, firing sync actors) is deferred to subsequent phases.

    Always returns 200 — Intervals.icu retries/disables the webhook on 4xx
    / 5xx responses, and we can't afford to lose events over a transient
    bug. Drops are logged instead.
    """
    client_ip = request.client.host if request.client else "unknown"

    # Observability: sample interesting request headers once per delivery.
    # User-Agent + any `x-*` custom headers are useful signals during the
    # monitoring phase (signature header? retry count? webhook id?). We
    # avoid dumping the entire headers dict to keep log noise down.
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

    try:
        payload = IntervalsWebhookPayload.model_validate(raw_body)
    except ValidationError as e:
        # Use sanitized error list instead of `str(e)` — Pydantic embeds
        # input values (including `secret` and record fields) in the default
        # message, which we never want in logs or Sentry.
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

    # Process each event independently — a single bad event shouldn't drop
    # its siblings in the same batch. Exceptions are tagged `source=intervals_webhook`
    # in Sentry so the Issue grouping stays separate from unrelated API errors.
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
            # Continue with the next event.

    return {"status": "ok"}
