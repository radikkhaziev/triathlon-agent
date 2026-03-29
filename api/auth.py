"""Desktop web authentication via one-time codes and JWT tokens.

Flow:
1. User sends /web to Telegram bot → gets a 6-digit code (valid 5 min)
2. User enters code on /login page → POST /api/auth/verify-code
3. Server validates code → returns JWT (valid JWT_EXPIRY_DAYS)
4. Frontend stores JWT in localStorage, sends as Authorization: Bearer <jwt>
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time

from config import settings

logger = logging.getLogger(__name__)

# In-memory store for pending codes: {code_str: {chat_id, created_at, used}}
_pending_codes: dict[str, dict] = {}

CODE_TTL_SECONDS = 300  # 5 minutes

# Rate limiting for verify-code: {ip: [timestamp, ...]}
_verify_attempts: dict[str, list[float]] = {}
RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 300  # 5 minutes


def check_rate_limit(ip: str) -> bool:
    """Return True if the IP is within rate limits, False if exceeded."""
    now = time.time()
    attempts = _verify_attempts.get(ip, [])
    # Remove expired attempts
    attempts = [t for t in attempts if now - t < RATE_LIMIT_WINDOW_SECONDS]
    _verify_attempts[ip] = attempts
    if len(attempts) >= RATE_LIMIT_MAX_ATTEMPTS:
        return False
    attempts.append(now)
    return True


def _get_jwt_secret() -> bytes:
    """Get JWT signing secret — JWT_SECRET if set, else TELEGRAM_BOT_TOKEN."""
    secret = settings.JWT_SECRET.get_secret_value()
    if not secret:
        secret = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    return secret.encode()


def generate_code(chat_id: str) -> str:
    """Generate a 6-digit one-time code for the given chat_id."""
    # Clean up expired codes first
    now = time.time()
    expired = [k for k, v in _pending_codes.items() if now - v["created_at"] > CODE_TTL_SECONDS]
    for k in expired:
        del _pending_codes[k]

    code = str(secrets.randbelow(900000) + 100000)
    _pending_codes[code] = {
        "chat_id": chat_id,
        "created_at": now,
        "used": False,
    }
    return code


def verify_code(code: str) -> str | None:
    """Verify a one-time code. Returns chat_id if valid, None otherwise.

    Code is consumed (one-time use).
    """
    entry = _pending_codes.get(code)
    if not entry:
        return None

    now = time.time()
    if now - entry["created_at"] > CODE_TTL_SECONDS:
        del _pending_codes[code]
        return None

    if entry["used"]:
        return None

    entry["used"] = True
    del _pending_codes[code]
    return entry["chat_id"]


def create_jwt(chat_id: str) -> str:
    """Create a JWT token for the given chat_id."""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": chat_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.JWT_EXPIRY_DAYS * 86400,
    }

    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    h = _b64(json.dumps(header, separators=(",", ":")).encode())
    p = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_get_jwt_secret(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64(sig)}"


def verify_jwt(token: str) -> str | None:
    """Verify JWT and return chat_id (sub claim), or None if invalid/expired."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        h, p, s = parts

        # Verify signature
        expected_sig = hmac.new(_get_jwt_secret(), f"{h}.{p}".encode(), hashlib.sha256).digest()
        # Decode received signature (add padding)
        sig_bytes = base64.urlsafe_b64decode(s + "==")
        if not hmac.compare_digest(expected_sig, sig_bytes):
            return None

        # Decode payload (add padding)
        payload = json.loads(base64.urlsafe_b64decode(p + "=="))

        # Check expiry
        if payload.get("exp", 0) < time.time():
            return None

        return payload.get("sub")
    except Exception:
        logger.debug("JWT verification failed", exc_info=True)
        return None
