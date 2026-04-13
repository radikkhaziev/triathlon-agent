import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.auth import create_jwt, verify_code, verify_telegram_widget_auth
from api.deps import get_current_user
from config import settings
from data.db import User, get_session

logger = logging.getLogger(__name__)
router = APIRouter()


class VerifyCodeRequest(BaseModel):
    code: str = Field(..., min_length=1)


class TelegramWidgetAuthRequest(BaseModel):
    """Telegram Login Widget callback payload.

    Extra fields are allowed for forward-compat — they're included in the
    HMAC-SHA256 data-check-string per Telegram's spec.
    """

    model_config = ConfigDict(extra="allow")

    id: int
    auth_date: int
    hash: str
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None


class SetLanguageRequest(BaseModel):
    language: Literal["ru", "en"]


@router.post("/api/auth/verify-code")
async def auth_verify_code(request: Request, body: VerifyCodeRequest) -> dict:
    """Verify a one-time code from /web bot command and return JWT."""
    code = body.code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Code is required")

    chat_id = verify_code(code)
    if not chat_id:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    user = await User.get_by_chat_id(chat_id)
    role = user.role if user else "viewer"

    token = create_jwt(chat_id)
    return {"token": token, "role": role, "expires_in_days": 7}


@router.post("/api/auth/telegram-widget")
async def auth_telegram_widget(body: TelegramWidgetAuthRequest) -> dict:
    """Verify Telegram Login Widget callback and return JWT.

    Body: the raw payload from Telegram Login Widget (id, first_name, username,
    photo_url, auth_date, hash). Signature is verified via HMAC-SHA256;
    auth_date must be fresh (<24h).

    If the user does not yet exist, we auto-create a `viewer` row — same
    behaviour as `/start` and Mini App initData flow. Upgrade to `athlete`
    role is still manual via `cli shell`.
    """
    payload = body.model_dump(exclude_none=True)
    chat_id = verify_telegram_widget_auth(payload)
    if not chat_id:
        raise HTTPException(status_code=401, detail="Invalid Telegram login data")

    display_name = f"{body.first_name or ''} {body.last_name or ''}".strip() or None

    user = await User.get_or_create_from_telegram(
        chat_id=chat_id,
        username=body.username,
        display_name=display_name,
    )
    logger.info("User resolved via Telegram Widget: id=%s chat_id=%s", user.id, chat_id)

    token = create_jwt(chat_id)
    return {"token": token, "role": user.role, "expires_in_days": settings.JWT_EXPIRY_DAYS}


@router.get("/api/auth/telegram-widget-config")
async def auth_telegram_widget_config() -> dict:
    """Return Telegram Login Widget config for the frontend (bot username)."""
    return {"bot_username": settings.TELEGRAM_BOT_USERNAME}


@router.get("/api/auth/me")
async def auth_me(user: User | None = Depends(get_current_user)) -> dict:
    """Check current auth status."""
    if not user:
        return {"role": "anonymous", "authenticated": False}
    return {"role": user.role, "authenticated": True, "language": user.language}


@router.put("/api/auth/language")
async def set_language(body: SetLanguageRequest, user: User | None = Depends(get_current_user)) -> dict:
    """Update user language preference."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async with get_session() as session:
        db_user = await session.get(User, user.id)
        db_user.language = body.language
        await session.commit()

    return {"language": body.language}
