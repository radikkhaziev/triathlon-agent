"""Intervals.icu OAuth flow — init + callback endpoints."""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
import sentry_sdk
from fastapi import Depends, HTTPException
from fastapi.responses import RedirectResponse

from api.auth import _get_jwt_secret
from api.deps import require_viewer
from api.dto import IntervalsAuthInitResponse
from config import settings
from data.db import User, UserDTO, get_session
from tasks.actors import actor_sync_athlete_settings, actor_user_wellness

from . import router

logger = logging.getLogger(__name__)

_OAUTH_AUTHORIZE_URL = "https://intervals.icu/oauth/authorize"
_OAUTH_TOKEN_URL = "https://intervals.icu/api/oauth/token"
_OAUTH_SCOPES = "ACTIVITY:WRITE,WELLNESS:READ,CALENDAR:WRITE,SETTINGS:WRITE"
_STATE_TTL_MINUTES = 15
_STATE_PURPOSE = "intervals_oauth"


def _generate_oauth_state(user_id: int) -> str:
    """Signed JWT binding the OAuth callback to its originating user."""
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


@router.post("/auth/init", response_model=IntervalsAuthInitResponse)
async def intervals_oauth_init(user: User = Depends(require_viewer)) -> IntervalsAuthInitResponse:
    """Initiate the Intervals.icu OAuth flow from an authenticated XHR.

    Demo users are blocked — they must not initiate OAuth for the owner's account.

    Returns ``{authorize_url}`` — the Intervals.icu /oauth/authorize URL with our
    ``client_id``, ``redirect_uri``, ``scope``, and a short-lived signed ``state`` JWT.
    """
    if user.role == "demo":
        raise HTTPException(status_code=403, detail="Read-only demo mode")

    if not settings.INTERVALS_OAUTH_CLIENT_ID:
        logger.error("OAuth init called but INTERVALS_OAUTH_CLIENT_ID is not set")
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

    Stores tokens, promotes viewer→athlete, generates mcp_token, and
    auto-dispatches initial sync actors for newly connected users.
    """
    settings_url = f"{settings.API_BASE_URL.rstrip('/')}/settings"

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
        was_new = not db_user.athlete_id
        if not db_user.athlete_id:
            db_user.athlete_id = intervals_athlete_id
        if db_user.role == "viewer":
            db_user.role = "athlete"
            logger.info("Promoted user %d to athlete via OAuth", user_id)
        if not db_user.mcp_token:
            db_user.generate_mcp_token()
            logger.info("Generated mcp_token for user %d", user_id)
        await session.commit()
        await session.refresh(db_user)
        user_dto = UserDTO.model_validate(db_user)

    # Auto-dispatch initial sync for newly connected athletes.
    if was_new:
        try:
            actor_sync_athlete_settings.send(user=user_dto)
            actor_user_wellness.send(user=user_dto)
            logger.info("Dispatched initial sync for new athlete user_id=%d", user_id)
        except Exception:
            logger.exception("Failed to dispatch initial sync for user_id=%d", user_id)

    return RedirectResponse(f"{settings_url}?connected=intervals", status_code=302)
