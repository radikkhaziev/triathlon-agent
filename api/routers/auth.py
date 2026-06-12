import logging
import os
import time
from datetime import datetime, timedelta, timezone

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import TypeAdapter

from api.auth import create_jwt, verify_code, verify_telegram_widget_auth
from api.deps import get_current_user, get_data_user_id, is_demo
from api.dto import (
    BackfillStatusResponse,
    SetLanguageRequest,
    SportsUpdateRequest,
    TelegramWidgetAuthRequest,
    VerifyCodeRequest,
)
from config import settings
from data.avatar_storage import avatar_path
from data.db import AthleteGoal, AthleteSettings, User, UserBackfillState, UserDTO, Wellness, get_session
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

# Rate limit for demo token mint: max 5 per IP per 5 minutes
_DEMO_RATE_WINDOW_SEC = 300.0
_DEMO_MAX_ATTEMPTS = 5
_demo_attempts: dict[str, list[float]] = {}

# Demo tokens are short-lived as a hard ceiling / hygiene measure. The actual
# kill switch is INSTANT: `get_current_user` rejects `purpose="demo"` tokens
# whenever DEMO_ENABLED is off (api/deps.py) — do not treat this TTL as the
# revocation mechanism or assume extending it is safe.
# See docs/DEMO_PUBLIC_ACCESS_SPEC.md Phase 3.
_DEMO_TOKEN_TTL_SEC = 24 * 3600

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
async def auth_demo(request: Request) -> dict:
    """Issue a read-only demo token — public, passwordless.

    Gated by ``DEMO_ENABLED`` — checked here at mint AND in ``get_current_user``
    at every verification, so flipping the flag off revokes existing tokens
    instantly. Per-IP rate limit guards the mint endpoint (each token is just
    a DB-read amplifier, but no point handing them out in bulk). 24h TTL
    (``_DEMO_TOKEN_TTL_SEC``) is a hygiene ceiling, not the revocation path.
    """
    if not settings.DEMO_ENABLED:
        raise HTTPException(status_code=404, detail="Demo mode is disabled")

    # Rate limit by IP
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    # Lazy prune — the key space is attacker-controlled on a public endpoint
    # (IPv6), so drop IPs whose newest attempt fell out of the window. Same
    # watermark pattern as `_retry_backfill_last_success`.
    if len(_demo_attempts) > 512:
        cutoff = now - _DEMO_RATE_WINDOW_SEC
        stale = [ip for ip, ts in _demo_attempts.items() if not ts or ts[-1] <= cutoff]
        for ip in stale:
            _demo_attempts.pop(ip, None)
    attempts = _demo_attempts.get(client_ip, [])
    attempts = [t for t in attempts if now - t < _DEMO_RATE_WINDOW_SEC]
    if len(attempts) >= _DEMO_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many attempts, try later")
    attempts.append(now)
    _demo_attempts[client_ip] = attempts

    owner = await User.get_owner()
    if not owner:
        raise HTTPException(status_code=503, detail="Demo not available")

    token = create_jwt(str(owner.chat_id), purpose="demo", ttl_seconds=_DEMO_TOKEN_TTL_SEC)
    logger.info("Demo login from ip=%s", client_ip)
    return {"token": token, "role": "demo", "expires_in_hours": _DEMO_TOKEN_TTL_SEC // 3600}


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


# Stable URL — `GET /api/auth/avatar` resolves the file to the SESSION'S user
# (no chat_id in the URL). Direct /static/avatar/* access is blocked at the
# server layer (see `api/server.py`) so we don't leak Telegram photos to
# anyone who can guess a chat_id.
_AVATAR_ENDPOINT_URL = "/api/auth/avatar"


def _avatar_url_if_exists(chat_id: str | None) -> str | None:
    """Returns the avatar endpoint URL when the cached file is present, else None.

    Gating on file existence here saves the frontend a request for users who
    have no avatar — the UI can render initials immediately without a 404
    roundtrip. URL itself is constant; the served bytes come from the session.
    """
    if not chat_id:
        return None
    if not os.path.isfile(avatar_path(chat_id)):
        return None
    return _AVATAR_ENDPOINT_URL


@router.get("/api/auth/me")
async def auth_me(user: User | None = Depends(get_current_user)) -> dict:
    """Check current auth status.

    The `intervals` block tells the frontend whether this user is connected
    to Intervals.icu (``athlete_id`` non-null) and what OAuth scope they
    granted. Settings page uses it to render the "Connect / Connected" state
    of the Intervals.icu section.
    """
    if not user:
        return {"role": "anonymous", "authenticated": False}

    data_uid = get_data_user_id(user)
    t = await AthleteSettings.get_thresholds(data_uid)
    g = await AthleteGoal.get_goal_dto(data_uid)

    # G1=B: Personal card shows per-sport HR-max (read-only, Intervals-synced)
    # + latest body weight. ``get_thresholds`` only keeps one aggregate
    # ``max_hr``; the prototype wants Swim/Bike/Run separately.
    all_settings = await AthleteSettings.get_all(data_uid)
    hr_max = {"run": None, "bike": None, "swim": None}
    for s in all_settings:
        if s.sport == "Run":
            hr_max["run"] = s.max_hr
        elif s.sport == "Ride":
            hr_max["bike"] = s.max_hr
        elif s.sport == "Swim":
            hr_max["swim"] = s.max_hr
    weight = await Wellness.get_latest_weight(data_uid)
    vo2max = await Wellness.get_latest_vo2max(data_uid)

    result = {
        "role": user.role,
        "authenticated": True,
        "language": user.language,
        # Identity is the *authenticated* user (NOT data_uid) — a viewer/demo
        # browsing the owner's data still sees their own name in the header.
        # Telegram first+last is stored as `display_name` on create
        # (bot /start → `tg_user.full_name`; Login Widget → "first last").
        "display_name": user.display_name,
        "username": user.username,
        "avatar_url": _avatar_url_if_exists(user.chat_id),
        # Frontend uses this to gate the "Connect Intervals.icu" CTA — Login
        # Widget signups land with ``false`` and must press /start in the bot
        # before OAuth (see issue #266 + /api/intervals/auth/init's 412).
        "bot_chat_initialized": user.bot_chat_initialized,
        "bot_username": settings.TELEGRAM_BOT_USERNAME,
        "intervals": {
            "athlete_id": user.athlete_id,
            "scope": user.intervals_oauth_scope,
            # True iff an OAuth access_token is currently stored. False after
            # disconnect/401-revoke even when `athlete_id` lingers — frontend
            # uses this to decide between "Connected" vs "Reconnect" UI.
            "connected": user.intervals_access_token_encrypted is not None,
        },
        "sports": user.sports,
        "profile": {
            "age": t.age,
            "lthr_run": t.lthr_run,
            "lthr_bike": t.lthr_bike,
            "ftp": t.ftp,
            "css": t.css,
            "threshold_pace_run": t.threshold_pace_run,
            "weight": weight,
            "vo2max": vo2max,
            "hr_max": hr_max,
        },
        "goal": (
            {
                "id": g.id,
                "event_name": g.event_name,
                "event_date": str(g.event_date),
                "sport_type": g.sport_type,
                "ctl_target": g.ctl_target,
                "per_sport_targets": g.per_sport_targets,
            }
            if g
            else None
        ),
    }
    if is_demo(user):
        result["language"] = "en"
        result["intervals"] = {"athlete_id": "demo", "scope": None, "connected": True}
        # Demo browses owner data read-only and never triggers Telegram I/O.
        # Pin to True so the frontend doesn't show a meaningless /start CTA.
        result["bot_chat_initialized"] = True
        # Demo JWT mints with the owner's chat_id (auth_demo:147), so
        # `get_current_user` returns the OWNER User row and its real Telegram
        # identity. Without this scrub, every demo session would render the
        # owner's first+last name and @username in Settings / sidebar / the
        # PersonalCard header — a direct PII leak triggered by handing out the
        # demo password. Pin to None (frontend falls back to "Profile" / no
        # @handle), matching how `intervals.athlete_id` is set to "demo".
        result["display_name"] = None
        result["username"] = None
        # Demo browses the owner's data; we already scrub display_name +
        # @username for PII reasons (line above). Apply the same scrub to
        # the avatar URL — otherwise demo sessions would show the owner's
        # Telegram profile photo.
        result["avatar_url"] = None
        # Pin sports for demo so the gate never blocks the read-only tour.
        # PUT /sports rejects demo separately so no actual write reaches DB.
        #
        # CAVEAT: this pin only affects the API response shape (drives the
        # SportsPicker gate). The morning-report / chat prompt path reads
        # ``User.sports`` directly via ``AthleteSettings.get_thresholds`` →
        # ``render_athlete_block`` and is NOT pinned. If the demo row's DB
        # value is NULL the prompt falls back to "render all sections"
        # (legacy behaviour) and stays consistent with this response. But
        # if anyone ever sets demo ``User.sports`` to a non-NULL subset
        # (e.g. ``["run"]``), the API will still return all-three here
        # while the prompt narrows — silent drift. Keep demo's DB row
        # NULL or document the override before enabling the demo path.
        result["sports"] = ["ride", "run", "swim"]
    return result


@router.get("/api/auth/avatar")
async def auth_avatar(user: User | None = Depends(get_current_user)) -> FileResponse:
    """Serve the cached Telegram avatar for the authenticated session's user.

    Demo session → 404: the demo password mints a JWT against the owner's
    chat_id (see `auth_me`), so serving from chat_id would leak the owner's
    photo. Same scrub as `display_name` / `username` / `avatar_url` in
    `/auth/me`.

    `private` cache (browser only, never CDN) for 5 min — avatar changes
    are daily at most (morning report sync); a stale 5-min cache is fine
    and saves dashboard polls from re-reading the file.
    """
    if not user or is_demo(user):
        raise HTTPException(status_code=404, detail="Not found")
    path = avatar_path(user.chat_id)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.put("/api/auth/language")
async def set_language(body: SetLanguageRequest, user: User | None = Depends(get_current_user)) -> dict:
    """Update user language preference."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if is_demo(user):
        raise HTTPException(status_code=403, detail="Read-only demo mode")

    async with get_session() as session:
        db_user = await session.get(User, user.id)
        db_user.language = body.language
        await session.commit()

    return {"language": body.language}


@router.put("/api/auth/sports")
async def set_sports(body: SportsUpdateRequest, user: User | None = Depends(get_current_user)) -> dict:
    """Persist the athlete's sport selection (`swim`/`ride`/`run`).

    Releases the SportsPicker gate on next webapp load. Pydantic enforces
    enum membership / 1≤len≤3; we additionally canonicalise (dedupe + sort)
    so the on-disk JSON stays stable and downstream comparisons can use
    plain `==`.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if is_demo(user):
        raise HTTPException(status_code=403, detail="Read-only demo mode")

    canonical = body.canonical()
    await User.update_sports(user.id, canonical)
    return {"sports": canonical}


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

    See `docs/MULTI_TENANT_SECURITY_SPEC.md` §T4 (per-tenant MCP tokens) and §T11
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
    if is_demo(user):
        raise HTTPException(status_code=403, detail="Read-only demo mode")
    if not user.athlete_id:
        raise HTTPException(status_code=400, detail="No Intervals.icu account connected")
    # Presence check via the encrypted column avoids a wasted Fernet decrypt
    # on every retry-backfill request (the property would decrypt just to
    # compute truthiness). Same pattern as /api/auth/me's `connected` field.
    if user.intervals_access_token_encrypted is None:
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
