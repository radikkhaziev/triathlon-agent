from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import check_rate_limit, create_jwt, verify_code
from api.deps import get_current_role
from config import settings

router = APIRouter()


@router.post("/api/auth/verify-code")
async def auth_verify_code(request: Request, body: dict) -> dict:
    """Verify a one-time code from /web bot command and return JWT."""
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in 5 minutes.")

    code = str(body.get("code", "")).strip()
    if not code:
        raise HTTPException(status_code=400, detail="Code is required")

    chat_id = verify_code(code)
    if not chat_id:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    token = create_jwt(chat_id)
    role = "owner" if chat_id == str(settings.TELEGRAM_CHAT_ID) else "viewer"
    return {"token": token, "role": role, "expires_in_days": settings.JWT_EXPIRY_DAYS}


@router.get("/api/auth/me")
async def auth_me(role: str = Depends(get_current_role)) -> dict:
    """Check current auth status."""
    return {"role": role, "authenticated": role != "anonymous"}
