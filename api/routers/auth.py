import hmac
import logging
import time

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import create_jwt, verify_code, verify_telegram_widget_auth
from api.deps import get_current_user
from api.dto import DemoAuthRequest, SetLanguageRequest, TelegramWidgetAuthRequest, VerifyCodeRequest
from config import settings
from data.db import User, get_session

logger = logging.getLogger(__name__)
router = APIRouter()

# Best-effort single-process rate limit for `/api/auth/mcp-config` —
# one disclosure per minute per user_id.
#
# LIMITATION: this dict lives in the process memory, so the guarantee holds
# only while the API runs with a single uvicorn worker (current deployment).
# Adding `--workers N` will silently partition clients across processes and
# break the limit. Move to Redis INCR+EXPIRE before scaling out.
#
# We use `time.monotonic()` for the window comparison to avoid NTP clock
# skew breaking the limiter.
_MCP_CONFIG_RATE_WINDOW_SEC = 60.0
_mcp_config_last_access: dict[int, float] = {}
_MCP_ALLOWED_ROLES = {"athlete", "owner"}

# Rate limit for demo login: max 5 attempts per IP per 5 minutes
_DEMO_RATE_WINDOW_SEC = 300.0
_DEMO_MAX_ATTEMPTS = 5
_demo_attempts: dict[str, list[float]] = {}


@router.post("/api/auth/demo")
async def auth_demo(request: Request, body: DemoAuthRequest) -> dict:
    """Authenticate with demo password for read-only access to owner's data."""
    demo_pw = settings.DEMO_PASSWORD.get_secret_value()
    if not demo_pw:
        raise HTTPException(status_code=404, detail="Demo mode is disabled")

    # Rate limit by IP
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = _demo_attempts.get(client_ip, [])
    attempts = [t for t in attempts if now - t < _DEMO_RATE_WINDOW_SEC]
    if len(attempts) >= _DEMO_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many attempts, try later")
    attempts.append(now)
    _demo_attempts[client_ip] = attempts

    password = body.password.strip()
    if not password or not hmac.compare_digest(password, demo_pw):
        logger.info("Demo login failed from ip=%s", client_ip)
        raise HTTPException(status_code=401, detail="Invalid demo password")

    owner = await User.get_owner()
    if not owner:
        raise HTTPException(status_code=503, detail="Demo not available")

    token = create_jwt(str(owner.chat_id), purpose="demo")
    logger.info("Demo login from ip=%s", client_ip)
    return {"token": token, "role": "demo", "expires_in_days": settings.JWT_EXPIRY_DAYS}


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
    """Check current auth status.

    The `intervals` block tells the frontend whether this user is connected
    to Intervals.icu and via which method (oauth / api_key / none). Settings
    page uses it to render the "Connect / Migrate / Connected" state of the
    Intervals.icu section.
    """
    if not user:
        return {"role": "anonymous", "authenticated": False}
    result = {
        "role": user.role,
        "authenticated": True,
        "language": user.language,
        "intervals": {
            "method": user.intervals_auth_method,  # "api_key" | "oauth" | "none"
            "athlete_id": user.athlete_id,
            "scope": user.intervals_oauth_scope,
        },
    }
    if user.role == "demo":
        result["language"] = "en"
        result["intervals"] = {"method": "oauth", "athlete_id": "demo", "scope": None}
    return result


@router.put("/api/auth/language")
async def set_language(body: SetLanguageRequest, user: User | None = Depends(get_current_user)) -> dict:
    """Update user language preference."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.role == "demo":
        raise HTTPException(status_code=403, detail="Read-only demo mode")

    async with get_session() as session:
        db_user = await session.get(User, user.id)
        db_user.language = body.language
        await session.commit()

    return {"language": body.language}


@router.get("/api/auth/mcp-config")
async def auth_mcp_config(request: Request, user: User | None = Depends(get_current_user)) -> dict:
    """Return the authenticated user's MCP connection config.

    Sensitive: `mcp_token` is a long-lived credential granting full MCP access.
    Layered defenses:

    - `get_current_user` — authentication (JWT or Telegram initData, freshness
      enforced in `_verify_and_parse_init_data` at 15-min window, see T11)
    - Role guard — only athletes and owners have mcp_tokens by design
    - Rate limit — one disclosure per minute per user_id, even the legitimate
      owner can't brute-scrape if their session is compromised. **Caveat:**
      this guard is in-process (see module-level `_mcp_config_last_access`),
      so it only works with a single uvicorn worker. Multi-worker deployment
      would require a shared store (Redis INCR+EXPIRE).
    - Audit log — every disclosure recorded to logs + Sentry breadcrumb with
      user_id + client IP, so operator can retrace leaks post-incident

    See `docs/MULTI_TENANT_SECURITY.md` §T4 (per-tenant MCP tokens) and §T11
    (initData replay window).
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.role not in _MCP_ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="MCP access not available for your role")
    if not user.mcp_token:
        raise HTTPException(status_code=404, detail="No MCP token configured for this user")

    now = time.monotonic()
    last = _mcp_config_last_access.get(user.id)
    if last is not None and now - last < _MCP_CONFIG_RATE_WINDOW_SEC:
        retry_in = int(_MCP_CONFIG_RATE_WINDOW_SEC - (now - last)) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: try again in {retry_in}s",
            headers={"Retry-After": str(retry_in)},
        )
    _mcp_config_last_access[user.id] = now

    # Audit trail — this is the most sensitive disclosure endpoint in the API.
    client_ip = request.client.host if request.client else "unknown"
    logger.warning(
        "mcp_token disclosed user_id=%s role=%s ip=%s user_agent=%s",
        user.id,
        user.role,
        client_ip,
        request.headers.get("user-agent", "-")[:200],
    )
    sentry_sdk.add_breadcrumb(
        category="auth.mcp_token",
        message=f"mcp_token disclosed to user_id={user.id}",
        level="warning",
        data={"user_id": user.id, "role": user.role, "ip": client_ip},
    )

    return {
        "url": f"{settings.API_BASE_URL.rstrip('/')}/mcp/",
        "token": user.mcp_token,
    }
