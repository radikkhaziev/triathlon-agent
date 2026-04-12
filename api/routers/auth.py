from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import create_jwt, verify_code
from api.deps import get_current_user
from data.db import User, get_session

router = APIRouter()


@router.post("/api/auth/verify-code")
async def auth_verify_code(request: Request, body: dict) -> dict:
    """Verify a one-time code from /web bot command and return JWT."""
    code = str(body.get("code", "")).strip()
    if not code:
        raise HTTPException(status_code=400, detail="Code is required")

    chat_id = verify_code(code)
    if not chat_id:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    user = await User.get_by_chat_id(chat_id)
    role = user.role if user else "viewer"

    token = create_jwt(chat_id)
    return {"token": token, "role": role, "expires_in_days": 7}


@router.get("/api/auth/me")
async def auth_me(user: User | None = Depends(get_current_user)) -> dict:
    """Check current auth status."""
    if not user:
        return {"role": "anonymous", "authenticated": False}
    return {"role": user.role, "authenticated": True, "language": user.language}


@router.put("/api/auth/language")
async def set_language(body: dict, user: User | None = Depends(get_current_user)) -> dict:
    """Update user language preference."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    lang = body.get("language", "")
    if lang not in ("ru", "en"):
        raise HTTPException(status_code=400, detail="Language must be 'ru' or 'en'")

    async with get_session() as session:
        db_user = await session.get(User, user.id)
        db_user.language = lang
        await session.commit()

    return {"language": lang}
