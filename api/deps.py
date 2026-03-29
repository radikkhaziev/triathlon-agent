"""Shared FastAPI dependencies for auth and role checks."""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import parse_qs

from fastapi import Depends, Header, HTTPException

from api.auth import verify_jwt
from config import settings


def _verify_and_parse_init_data(init_data: str, bot_token: str) -> dict | None:
    """Verify Telegram initData HMAC and return parsed fields, or None if invalid."""
    parsed = parse_qs(init_data)
    received_hash = parsed.pop("hash", [None])[0]
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None
    return parsed


def get_current_role(authorization: str | None = Header(default=None)) -> str:
    """Resolve current role from Telegram initData or JWT Bearer token."""
    if not authorization:
        return "anonymous"

    if authorization.startswith("Bearer "):
        jwt_token = authorization[7:]
        chat_id = verify_jwt(jwt_token)
        if chat_id and chat_id == str(settings.TELEGRAM_CHAT_ID):
            return "owner"
        if chat_id:
            return "viewer"
        return "anonymous"

    bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    if not bot_token:
        return "anonymous"

    parsed = _verify_and_parse_init_data(authorization, bot_token)
    if parsed is None:
        return "anonymous"

    user_json = parsed.get("user", [None])[0]
    if not user_json:
        return "anonymous"

    try:
        user = json.loads(user_json)
    except (json.JSONDecodeError, TypeError):
        return "anonymous"

    user_id = str(user.get("id", ""))
    if user_id == str(settings.TELEGRAM_CHAT_ID):
        return "owner"
    return "viewer"


def require_viewer(role: str = Depends(get_current_role)) -> str:
    """Require at least viewer role and return resolved role."""
    if role == "anonymous":
        raise HTTPException(status_code=401, detail="Telegram authorization required")
    return role


def require_owner(role: str = Depends(get_current_role)) -> None:
    """Require owner role."""
    if role == "anonymous":
        raise HTTPException(status_code=401, detail="Telegram authorization required")
    if role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
