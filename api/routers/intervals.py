"""Intervals.icu integration endpoints.

Two responsibilities:

1. **OAuth flow** (`POST /auth/init`, `GET /auth/callback`) — Phase 1 of the OAuth
   migration. See `docs/INTERVALS_OAUTH_SPEC.md` for the full plan. Current
   scope is intentionally narrow: we want to observe the real token-exchange
   response shape before wiring the tokens into `IntervalsClient` (Phase 2).

2. **Webhook receiver** (`/hook/{external_id}`) — still a logging-only stub.
   Intervals.icu push events (ACTIVITY_UPLOADED, CALENDAR_UPDATED, etc.) come
   here once webhooks are configured in the OAuth app settings. Real dispatch
   is deferred until we see a live payload.
"""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
import sentry_sdk
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from api.auth import _get_jwt_secret
from api.deps import require_viewer
from config import settings
from data.db import User, get_session

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
# report. See INTERVALS_OAUTH_SPEC §2.4.
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


@router.post("/auth/init")
async def intervals_oauth_init(user: User = Depends(require_viewer)) -> dict:
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
    return {"authorize_url": url}


@router.get("/auth/callback")
async def intervals_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle Intervals.icu OAuth callback — exchange code for access_token.

    Phase 1 scope (DONT change without checking `docs/INTERVALS_OAUTH_SPEC.md`
    §3): the callback stores tokens in the DB but **does not** promote
    viewer→athlete, does not generate mcp_token, and does not dispatch sync
    actors. Those side-effects land in Phase 2 after we verify the handshake.

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
# Webhook receiver (still a logging stub — see module docstring)
# ---------------------------------------------------------------------------


@router.post("/hook/{external_id}")
async def intervals_hook(external_id: str, request: Request) -> dict:
    """Stub receiver — logs method, headers, query params, and JSON body.

    Responds 200 unconditionally so Intervals.icu does not retry while we
    are still figuring out the contract. Once we know the payload shape,
    this will dispatch a dramatiq actor per event type.
    """
    try:
        body = await request.json()
    except Exception:
        body = None
        raw = await request.body()
        logger.info("Intervals hook [%s] non-JSON body: %r", external_id, raw[:2000])

    logger.info(
        "Intervals hook [%s] headers=%s query=%s body=%s",
        external_id,
        dict(request.headers),
        dict(request.query_params),
        body,
    )

    return {"status": "ok", "external_id": external_id}
