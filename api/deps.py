"""Shared FastAPI dependencies for auth and role checks."""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs

import sentry_sdk
from fastapi import Depends, Header, HTTPException

from api.auth import verify_jwt
from config import settings
from data.db import User


async def get_current_user(authorization: str | None = Header(default=None)) -> User | None:
    """Resolve current user from Telegram initData or JWT Bearer token.

    Returns User object or None (anonymous).
    """
    if not authorization:
        return None

    chat_id: str | None = None

    if authorization.startswith("Bearer "):
        jwt_token = authorization[7:]
        chat_id = verify_jwt(jwt_token)
    else:
        bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
        if bot_token:
            parsed = _verify_and_parse_init_data(authorization, bot_token)
            if parsed:
                user_json = parsed.get("user", [None])[0]
                if user_json:
                    try:
                        user_data = json.loads(user_json)
                        chat_id = str(user_data.get("id", ""))
                    except (json.JSONDecodeError, TypeError):
                        pass

    if not chat_id:
        return None

    user = await User.get_by_chat_id(chat_id)
    if user:
        sentry_sdk.set_user(
            {
                "id": str(user.id),
                "username": f"athlete_{user.athlete_id}" if user.athlete_id else f"user_{user.id}",
                "role": user.role,
            }
        )
    return user


def _verify_and_parse_init_data(init_data: str, bot_token: str) -> dict | None:
    """Verify Telegram initData HMAC + auth_date freshness, return parsed fields or None."""
    parsed = parse_qs(init_data)
    received_hash = parsed.pop("hash", [None])[0]
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    # Reject stale initData (>15 min) to prevent replay attacks
    auth_date_str = parsed.get("auth_date", [None])[0]
    if auth_date_str:
        try:
            if time.time() - int(auth_date_str) > 900:
                return None
        except (ValueError, TypeError):
            return None

    return parsed


async def require_viewer(user: User | None = Depends(get_current_user)) -> User:
    """Require authenticated user. Returns User object.

    Active athletes see their own data.
    Viewers without athlete_id see owner data (read-only).
    """
    if not user:
        raise HTTPException(status_code=401, detail="Telegram authorization required")
    return user


def get_data_user_id(user: User) -> int:
    """Resolve which user_id to query data for.

    Active athletes with athlete_id → own data.
    Everyone else (viewers) → owner data.
    """
    if user.is_active and user.athlete_id:
        return user.id
    return 1


async def require_athlete(user: User | None = Depends(get_current_user)) -> User:
    """Require active athlete with Intervals.icu credentials configured."""
    if not user:
        raise HTTPException(status_code=401, detail="Telegram authorization required")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    if not user.athlete_id:
        raise HTTPException(status_code=403, detail="Athlete profile not configured")
    return user


async def require_owner(user: User | None = Depends(get_current_user)) -> User:
    """Require owner role."""
    if not user:
        raise HTTPException(status_code=401, detail="Telegram authorization required")
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user
