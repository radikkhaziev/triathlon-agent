from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import create_jwt, verify_code
from api.deps import get_current_user
from data.db import User

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
    return {"role": user.role, "authenticated": True}
