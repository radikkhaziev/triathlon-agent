import hmac
import logging
import time
from datetime import datetime, timedelta, timezone

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import TypeAdapter

from api.auth import create_jwt, verify_code, verify_telegram_widget_auth
from api.deps import get_current_user, get_data_user_id
from api.dto import (
    BackfillStatusResponse,
    DemoAuthRequest,
    SetLanguageRequest,
    TelegramWidgetAuthRequest,
    VerifyCodeRequest,
)
from config import settings
from data.db import AthleteGoal, AthleteSettings, User, UserBackfillState, UserDTO, get_session
from tasks.actors import actor_bootstrap_step

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

# Retry-backfill anti-spam rate limit — one successful call per hour per user.
# Separate from the business cooldowns in `_backfill_retry_retry_after`:
# - business cooldown reflects "is there any point retrying right now?"
#   (7d after data completed, 1h after EMPTY_INTERVALS)
# - this rate limit is an endpoint-level guard against retry-button spamming
#   regardless of state, so a user who hits "Попробовать снова" repeatedly
#   after a `failed` state can't queue dozens of bootstrap chains.
#
# SINGLE-WORKER ASSUMPTION: this dict lives in one process's memory, so the
# guard holds only with a single uvicorn worker (current deployment). Adding
# `--workers N` silently partitions users across processes, letting a user
# bypass the hourly budget by round-robining workers. Before scaling out,
# move this to Redis INCR+EXPIRE keyed on user_id. Same caveat as
# `_mcp_config_last_access` above — keep both in sync when that migrates.
#
# Lazy cleanup in `_retry_backfill_check_and_record` keeps the dict bounded
# to users who retried within the last window.
_RETRY_BACKFILL_RATE_WINDOW_SEC = 3600.0
_retry_backfill_last_success: dict[int, float] = {}

# Business cooldowns — how long after finished_at before a retry is meaningful.
_COMPLETED_DATA_COOLDOWN = timedelta(days=7)
_EMPTY_INTERVALS_COOLDOWN = timedelta(hours=1)
_DEFAULT_BOOTSTRAP_PERIOD_DAYS = 365

_UserDTOAdapter = TypeAdapter(UserDTO)

# Allowlist of ``last_error`` values safe to return to the webapp. Everything
# else is collapsed into a generic sentinel before leaving the server —
# defensive against a future caller accidentally passing raw ``str(e)`` to
# ``UserBackfillState.mark_failed`` (httpx exceptions can embed request URLs
# with query params, see the docstring on ``mark_failed``). The UI renders
# these sentinels via i18n, so adding a new one here requires a webapp key too.
_LAST_ERROR_ALLOWLIST = frozenset(
    {
        "EMPTY_INTERVALS",
        "watchdog_exhausted",
        "OAuth revoked during backfill",
    }
)
_LAST_ERROR_WATCHDOG_PREFIX = "watchdog_kick_"
_LAST_ERROR_INTERNAL = "internal"


def _sanitize_last_error(raw: str | None) -> str | None:
    """Collapse anything outside the allowlist to ``"internal"``. Returns
    ``None`` for in-flight watchdog kicks (``watchdog_kick_N``) — they're
    bookkeeping, not a user-facing error state."""
    if raw is None:
        return None
    if raw.startswith(_LAST_ERROR_WATCHDOG_PREFIX):
        return None
    if raw in _LAST_ERROR_ALLOWLIST:
        return raw
    return _LAST_ERROR_INTERNAL


def _backfill_retry_retry_after(state: UserBackfillState, now: datetime) -> int | None:
    """Business cooldown: how many seconds the user must wait before a retry
    makes sense, or ``None`` if a retry is allowed right now.

    * ``completed`` + data + less than 7d ago → block (webhooks cover deltas)
    * ``completed`` + ``EMPTY_INTERVALS`` + less than 1h ago → block (Intervals
      hasn't ingested Garmin yet; no point hammering)
    * ``failed`` / ``completed`` older than the window / any other status → allow
    """
    if state.status != "completed" or state.finished_at is None:
        return None
    cooldown = _EMPTY_INTERVALS_COOLDOWN if state.is_empty_import() else _COMPLETED_DATA_COOLDOWN
    expires_at = state.finished_at + cooldown
    if now >= expires_at:
        return None
    return max(1, int((expires_at - now).total_seconds()))


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

    data_uid = get_data_user_id(user)
    t = await AthleteSettings.get_thresholds(data_uid)
    g = await AthleteGoal.get_goal_dto(data_uid)

    result = {
        "role": user.role,
        "authenticated": True,
        "language": user.language,
        # Frontend uses this to gate the "Connect Intervals.icu" CTA — Login
        # Widget signups land with ``false`` and must press /start in the bot
        # before OAuth (see issue #266 + /api/intervals/auth/init's 412).
        "bot_chat_initialized": user.bot_chat_initialized,
        "bot_username": settings.TELEGRAM_BOT_USERNAME,
        "intervals": {
            "method": user.intervals_auth_method,
            "athlete_id": user.athlete_id,
            "scope": user.intervals_oauth_scope,
        },
        "profile": {
            "age": t.age,
            "lthr_run": t.lthr_run,
            "lthr_bike": t.lthr_bike,
            "ftp": t.ftp,
            "css": t.css,
            "threshold_pace_run": t.threshold_pace_run,
        },
        "goal": (
            {
                "id": g.id,
                "event_name": g.event_name,
                "event_date": str(g.event_date),
                "ctl_target": g.ctl_target,
                "per_sport_targets": g.per_sport_targets,
            }
            if g
            else None
        ),
    }
    if user.role == "demo":
        result["language"] = "en"
        result["intervals"] = {"method": "oauth", "athlete_id": "demo", "scope": None}
        # Demo browses owner data read-only and never triggers Telegram I/O.
        # Pin to True so the frontend doesn't show a meaningless /start CTA.
        result["bot_chat_initialized"] = True
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


@router.get("/api/auth/backfill-status", response_model=BackfillStatusResponse)
async def auth_backfill_status(user: User | None = Depends(get_current_user)) -> BackfillStatusResponse:
    """Progress of the OAuth bootstrap backfill for the current user.

    Tenant-scoped: reads by ``current_user.id``, never by query parameter.
    Returns ``status='none'`` when the user has never triggered a backfill.
    Demo-safe: demo users resolve to the owner via ``get_data_user_id``, but
    we report the owner's real state here — same behavior as other read-only
    endpoints exposed to demo.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data_uid = get_data_user_id(user)
    state = await UserBackfillState.get(data_uid)
    if state is None:
        return BackfillStatusResponse(status="none")

    return BackfillStatusResponse(
        status=state.status,
        cursor_dt=state.cursor_dt.isoformat(),
        oldest_dt=state.oldest_dt.isoformat(),
        newest_dt=state.newest_dt.isoformat(),
        progress_pct=round(state.progress_pct(), 1),
        chunks_done=state.chunks_done,
        period_days=state.period_days,
        started_at=state.started_at.isoformat() if state.started_at else None,
        finished_at=state.finished_at.isoformat() if state.finished_at else None,
        last_error=_sanitize_last_error(state.last_error),
    )


@router.post("/api/auth/retry-backfill")
async def auth_retry_backfill(user: User | None = Depends(get_current_user)) -> dict:
    """Manually re-run the OAuth bootstrap for the authenticated athlete.

    Two independent guards:

    1. **Business cooldown** (``_backfill_retry_retry_after``) — "is a retry
       meaningful right now?" 7d after a successful backfill, 1h after an
       EMPTY_INTERVALS result (Intervals.icu still catching up on Garmin).
    2. **Anti-spam rate limit** — 1 successful call per hour per user_id,
       regardless of state. A user stuck in ``failed`` could otherwise queue
       dozens of bootstrap chains by mashing the button.

    On success: ``UserBackfillState.start`` resets the row to
    ``status='running'``, ``cursor_dt=oldest``, then dispatches the chunk-
    recursive actor. The OAuth callback's fast-path sync of today/settings/
    goals is *not* repeated — those rows are already fresh from ongoing
    webhooks/scheduler.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Demo reject MUST come before any rate-limit lookup keyed by `user.id`.
    # Demo requests resolve to the owner's `user.id` via `get_data_user_id`,
    # so letting them touch the rate-limit dict would let a demo session
    # starve (or share the budget of) the actual owner.
    if user.role == "demo":
        raise HTTPException(status_code=403, detail="Read-only demo mode")
    if not user.athlete_id:
        raise HTTPException(status_code=400, detail="No Intervals.icu account connected")
    if user.intervals_auth_method == "none":
        raise HTTPException(status_code=400, detail="Intervals.icu not connected")

    now_mono = time.monotonic()
    # Lazy prune — drop entries older than the rate window so the dict stays
    # bounded to "users who retried within the last hour". O(n) on cleanup,
    # triggered sparsely when the dict exceeds a watermark.
    if len(_retry_backfill_last_success) > 512:
        cutoff = now_mono - _RETRY_BACKFILL_RATE_WINDOW_SEC
        stale = [uid for uid, ts in _retry_backfill_last_success.items() if ts <= cutoff]
        for uid in stale:
            _retry_backfill_last_success.pop(uid, None)

    last_success = _retry_backfill_last_success.get(user.id)
    if last_success is not None and now_mono - last_success < _RETRY_BACKFILL_RATE_WINDOW_SEC:
        retry_in = int(_RETRY_BACKFILL_RATE_WINDOW_SEC - (now_mono - last_success)) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: try again in {retry_in}s",
            headers={"Retry-After": str(retry_in)},
        )

    state = await UserBackfillState.get(user.id)
    now_utc = datetime.now(timezone.utc)

    if state is not None and state.status == "running":
        raise HTTPException(status_code=409, detail="already_running")

    if state is not None:
        retry_after = _backfill_retry_retry_after(state, now_utc)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="cooldown",
                headers={"Retry-After": str(retry_after)},
            )

    today = now_utc.date()
    oldest = today - timedelta(days=_DEFAULT_BOOTSTRAP_PERIOD_DAYS)
    newest = today - timedelta(days=1)

    async with get_session() as session:
        db_user = await session.get(User, user.id)
        if db_user is None:
            raise HTTPException(status_code=404, detail="User not found")
        user_dto = _UserDTOAdapter.validate_python(db_user)

    await UserBackfillState.start(
        user_id=user.id,
        period_days=_DEFAULT_BOOTSTRAP_PERIOD_DAYS,
        oldest_dt=oldest,
        newest_dt=newest,
    )
    actor_bootstrap_step.send(
        user=user_dto,
        cursor_dt=oldest.isoformat(),
        period_days=_DEFAULT_BOOTSTRAP_PERIOD_DAYS,
    )

    _retry_backfill_last_success[user.id] = now_mono
    logger.info("retry-backfill dispatched user_id=%d period=%dd", user.id, _DEFAULT_BOOTSTRAP_PERIOD_DAYS)

    return {"status": "running", "started_at": now_utc.isoformat()}
