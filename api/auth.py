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
_PENDING_CODES: dict[str, dict] = {}

CODE_TTL_SECONDS = 300  # 5 minutes

RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 300  # 5 minutes


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
    expired = [k for k, v in _PENDING_CODES.items() if now - v["created_at"] > CODE_TTL_SECONDS]
    for k in expired:
        del _PENDING_CODES[k]

    code = str(secrets.randbelow(900000) + 100000)
    _PENDING_CODES[code] = {
        "chat_id": chat_id,
        "created_at": now,
        "used": False,
    }
    return code


def verify_code(code: str) -> str | None:
    """Verify a one-time code. Returns chat_id if valid, None otherwise.

    Code is consumed (one-time use).
    """
    entry = _PENDING_CODES.get(code)
    if not entry:
        return None

    now = time.time()
    if now - entry["created_at"] > CODE_TTL_SECONDS:
        del _PENDING_CODES[code]
        return None

    if entry["used"]:
        return None

    entry["used"] = True
    del _PENDING_CODES[code]
    return entry["chat_id"]


def create_jwt(chat_id: str, *, purpose: str | None = None) -> str:
    """Create a JWT token for the given chat_id.

    Optional `purpose` claim: 'demo' for read-only demo access.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": chat_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.JWT_EXPIRY_DAYS * 86400,
    }
    if purpose:
        payload["purpose"] = purpose

    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    h = _b64(json.dumps(header, separators=(",", ":")).encode())
    p = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_get_jwt_secret(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64(sig)}"


TELEGRAM_AUTH_MAX_AGE_SECONDS = 24 * 3600  # Our replay window; Telegram docs show 24h as the recommended cap


def verify_telegram_widget_auth(data: dict, *, now: float | None = None) -> str | None:
    """Verify Telegram Login Widget callback payload.

    Per https://core.telegram.org/widgets/login#checking-authorization:
      1. Build a data-check-string: all fields except `hash`, sorted by key,
         joined as "key=value" with \\n separators.
      2. secret_key = SHA256(bot_token).
      3. Expected hash = HMAC-SHA256(secret_key, data-check-string).
      4. Reject if auth_date is older than our replay window (24h, see
         TELEGRAM_AUTH_MAX_AGE_SECONDS).

    Returns the Telegram user id (as string — maps to User.chat_id) on success,
    None on any failure. Never raises.

    `now` is an override for tests; defaults to time.time().
    """
    try:
        received_hash = data.get("hash")
        if not received_hash or not isinstance(received_hash, str):
            return None

        auth_date_raw = data.get("auth_date")
        if auth_date_raw is None:
            return None
        try:
            auth_date = int(auth_date_raw)
        except (TypeError, ValueError):
            return None

        user_id = data.get("id")
        if user_id is None:
            return None

        current_time = now if now is not None else time.time()
        if current_time - auth_date > TELEGRAM_AUTH_MAX_AGE_SECONDS:
            return None
        if auth_date - current_time > 60:  # future-dated, clock skew tolerance
            return None

        # Telegram never emits `null` for optional fields — it omits them.
        # Drop `None` values here so that a payload carrying a stray JSON
        # `null` (e.g. from a client library that serializes `undefined` as
        # `null`) still matches Telegram's signed data-check-string.
        fields = {k: v for k, v in data.items() if k != "hash" and v is not None}
        data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))

        bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
        if not bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN is not configured, cannot verify widget auth")
            return None

        secret_key = hashlib.sha256(bot_token.encode()).digest()
        expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, received_hash):
            return None

        return str(user_id)
    except Exception:
        logger.debug("Telegram widget verification failed", exc_info=True)
        return None


def verify_jwt(token: str) -> tuple[str | None, str | None]:
    """Verify JWT and return (chat_id, purpose) or (None, None) if invalid."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None, None

        h, p, s = parts

        # Verify signature
        expected_sig = hmac.new(_get_jwt_secret(), f"{h}.{p}".encode(), hashlib.sha256).digest()
        # Decode received signature (add padding)
        sig_bytes = base64.urlsafe_b64decode(s + "==")
        if not hmac.compare_digest(expected_sig, sig_bytes):
            return None, None

        # Decode payload (add padding)
        payload = json.loads(base64.urlsafe_b64decode(p + "=="))

        # Check expiry
        if payload.get("exp", 0) < time.time():
            return None, None

        return payload.get("sub"), payload.get("purpose")
    except Exception:
        logger.debug("JWT verification failed", exc_info=True)
        return None, None
