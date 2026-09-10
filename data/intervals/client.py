"""Intervals.icu API clients (async + sync).

Endpoint logic defined once in IntervalsClientBase via RequestSpec.
Subclasses add transport (_request + retry) and thin one-liner endpoints.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx
import sentry_sdk
from dramatiq import Retry
from pydantic import BaseModel

from data.db import User, UserDTO
from data.db.common import get_session, get_sync_session
from data.intervals.dto import ActivityDTO, EventExDTO, ScheduledWorkoutDTO, SportSettingsDTO, WellnessDTO

logger = logging.getLogger(__name__)

BASE_URL = "https://intervals.icu/api/v1"
MAX_RETRIES = 5
RETRY_MAX_DELAY = 60
RETRY_STATUSES = {429, 500, 502, 503, 504}
FIT_MAX_SIZE = 50 * 1024 * 1024  # 50 MB

# Intervals.icu quotas are per OAuth app, not per athlete: 100 requests/day
# per authorized athlete (min 5000, max 50000) and 1/8 of that per rolling
# 15 minutes (min 2500). Both arrive on every response as
# ``X-RateLimit-Limit: <15m>,<day>`` / ``X-RateLimit-Remaining: <15m>,<day>``.
# A 429 carries ``Retry-After`` — seconds until the window reopens, which for
# the daily quota is «until 00:00 UTC» (hours). Sleeping through that inside
# a worker thread is pointless, so anything beyond RETRY_MAX_DELAY raises
# ``IntervalsRateLimitError`` instead (see ``_raise_if_quota_exhausted``).
# https://forum.intervals.icu/t/api-access-to-intervals-icu/609
DAILY_QUOTA_WARN_THRESHOLD = 1500

# Jitter added to every quota deferral / pause so a backlog doesn't slam the
# 15-minute window the moment a quota resets. Shared by
# ``tasks.middleware.QuotaAwareRetries`` and the bootstrap pause stamp.
QUOTA_DEFER_JITTER_SEC = 120

# UTC date on which the low-quota warning already fired in this process —
# without it every request below the threshold would emit a WARNING line.
# Per process (N worker processes → N lines/day) and racy across threads
# (worst case: a duplicate line) — both acceptable for a once-a-day alarm.
_low_quota_warned_on: date | None = None


@dataclass(frozen=True)
class QuotaSnapshot:
    """Rate-limit headers from the most recent Intervals.icu response."""

    remaining_15m: int
    remaining_day: int
    limit_15m: int | None = None
    limit_day: int | None = None

    def __str__(self) -> str:
        limit_15m = "?" if self.limit_15m is None else str(self.limit_15m)
        limit_day = "?" if self.limit_day is None else str(self.limit_day)
        return f"{self.remaining_15m}/{limit_15m} per 15m, {self.remaining_day}/{limit_day} per day"


def _parse_pair(raw: str | None) -> tuple[int, int] | None:
    """``"2499,0"`` → ``(2499, 0)``; anything malformed → ``None``."""
    if not raw:
        return None
    parts = raw.split(",")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0].strip()), int(parts[1].strip())
    except ValueError:
        return None


def seconds_until_quota_reset(now: datetime | None = None) -> int:
    """Seconds until the next 00:00 UTC — when Intervals.icu resets the daily quota.

    A naive ``now`` is taken as UTC (mirrors ``parse_quota_pause``) rather than
    blowing up on aware-minus-naive subtraction.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    reset = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)
    return max(1, int((reset - now).total_seconds()))


def _fmt_duration(seconds: int) -> str:
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min"
    hours, rem = divmod(seconds, 3600)
    return f"{hours}h {rem // 60:02d}m"


class IntervalsRateLimitError(Retry):
    """429 whose ``Retry-After`` exceeds ``RETRY_MAX_DELAY`` — the rolling-15m
    or daily app-wide quota is exhausted, so in-process sleep-and-retry
    would only burn a worker thread (and more 429s).

    Subclasses ``dramatiq.Retry`` on purpose — a quota hit is a deferral,
    not a defect. The Dramatiq worker logs ``Retry`` subclasses at debug
    instead of «Failed to process message … unhandled exception» at ERROR,
    and since Sentry sees worker errors only through ``LoggingIntegration
    (event_level=ERROR)`` (``tasks/broker.py`` replaces the middleware list,
    so ``DramatiqIntegration``'s ``SentryMiddleware`` is not installed),
    no Sentry event is produced. ``tasks.middleware.QuotaAwareRetries``
    re-enqueues the message after ``retry_after`` without consuming a retry
    slot. Async callers (MCP tools) surface ``str(e)`` to the user; the
    ``data/`` → ``dramatiq`` import is a deliberate, documented exception.
    """

    def __init__(
        self,
        retry_after: int,
        *,
        method: str,
        path: str,
        quota: QuotaSnapshot | None = None,
    ) -> None:
        self.retry_after = retry_after
        self.method = method
        self.path = path
        self.quota = quota
        super().__init__(
            f"Intervals.icu API quota exhausted on {method} {path}, retry after {_fmt_duration(retry_after)}",
            delay=retry_after * 1000,
        )


class IntervalsAccessError(Exception):
    """Base for permanent Intervals.icu access failures.

    Catching this in a Dramatiq actor means: «this user can't currently talk to
    Intervals.icu — log, skip, don't retry». Concrete subclasses distinguish
    the cause (token revoked, scope revoked, no creds configured at all).
    """


class IntervalsAuthError(IntervalsAccessError):
    """Raised when Intervals.icu returns 401 for an OAuth user.

    The token has been revoked or expired. On 401, ``_execute()``
    clears the stored tokens in the DB before raising this error.
    """

    def __init__(self, user_id: int):
        self.user_id = user_id
        super().__init__(f"Intervals.icu OAuth token revoked for user {user_id}")


class IntervalsScopeError(IntervalsAccessError):
    """Raised when Intervals.icu returns 403 (scope revoked / insufficient).

    The token is still valid for other scopes — we do NOT clear it.
    """

    def __init__(self, user_id: int | None, method: str, path: str):
        self.user_id = user_id
        self.method = method
        self.path = path
        suffix = f" for user {user_id}" if user_id is not None else ""
        super().__init__(f"Intervals.icu 403 on {method} {path}{suffix}")


class IntervalsCredsMissingError(IntervalsAccessError):
    """Raised by ``_resolve_credentials`` when the user has no usable Intervals.icu
    credentials — either no ``athlete_id``, or no OAuth access_token (after a
    revoke or before initial connect). The actor must skip; there's nothing to
    authenticate with.
    """

    def __init__(self, user_id: int, reason: str):
        self.user_id = user_id
        self.reason = reason
        super().__init__(f"User {user_id} has no Intervals.icu credentials: {reason}")


def to_snake(name: str) -> str:
    """Convert camelCase to snake_case: 'restingHR' → 'resting_hr'."""
    result: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper():
            if i and not name[i - 1].isupper():
                result.append("_")
            elif i and i + 1 < len(name) and name[i - 1].isupper() and not name[i + 1].isupper():
                result.append("_")
            result.append(ch.lower())
        else:
            result.append(ch)
    return "".join(result)


@dataclass(frozen=True)
class RequestSpec:
    """Declarative endpoint description: HTTP method, path, kwargs, parser."""

    method: str
    path: str
    kwargs: dict = field(default_factory=dict)
    parser: type[BaseModel] | Callable[[httpx.Response], Any] | None = None  # Model, callable, or None → resp.json()
    handle_404: bool = False
    void: bool = False  # True for DELETE-like ops with no response body


class IntervalsClientBase:
    """Shared config, URL building, response parsing, and endpoint specs.

    Subclasses implement _request() and _execute() for sync/async transport.

    OAuth-only since the api_key auth path was retired — callers must use the
    ``for_user()`` factory which reads ``User.intervals_access_token`` from
    the DB. Direct construction with ``access_token=`` is for tests.
    """

    def __init__(
        self,
        *,
        athlete_id: str,
        access_token: str,
        user_id: int | None = None,
    ) -> None:
        if not athlete_id:
            raise ValueError("IntervalsClient requires a non-empty athlete_id")
        if not access_token:
            raise ValueError("IntervalsClient requires a non-empty access_token")
        self._access_token = access_token
        self._athlete_id = athlete_id
        self._user_id = user_id
        # Rate-limit headers from the last response. Attached to
        # ``IntervalsRateLimitError`` for diagnostics; the bootstrap budget
        # reserve (spec «Intervals.icu rate limits», Phase 3) will read it.
        self.quota: QuotaSnapshot | None = None

    def _http_client_kwargs(self) -> dict:
        return {
            "base_url": BASE_URL,
            "headers": {
                "Accept": "application/json",
                "Authorization": f"Bearer {self._access_token}",
            },
            "timeout": 30.0,
        }

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> int | None:
        """Seconds from the ``Retry-After`` header, falling back to the
        ``retry_after_seconds`` field Intervals.icu puts in the 429 body.
        HTTP-date form of the header is not used by Intervals — treated as absent."""
        raw = resp.headers.get("Retry-After")
        if raw:
            try:
                return max(1, int(float(raw)))  # floor at 1 s — «0» must never mean «hammer»
            except (ValueError, OverflowError):
                pass
        try:
            body = resp.json()
        except ValueError:
            return None
        if isinstance(body, dict) and isinstance(body.get("retry_after_seconds"), (int, float)):
            return max(1, int(body["retry_after_seconds"]))
        return None

    def _compute_retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = self._parse_retry_after(resp)
        if retry_after is not None:
            return float(min(retry_after, RETRY_MAX_DELAY))
        return min(2**attempt * 10, RETRY_MAX_DELAY)

    def _record_quota(self, resp: httpx.Response) -> None:
        """Capture ``X-RateLimit-*`` headers; warn once per UTC day per process
        when the daily remainder drops below ``DAILY_QUOTA_WARN_THRESHOLD``.

        ``self.quota`` always mirrors the *latest* response: a response
        without (or with malformed) headers — e.g. the per-second per-IP 429 —
        resets it to ``None`` rather than letting a stale snapshot from an
        earlier call masquerade as current in logs / ``IntervalsRateLimitError``.
        """
        global _low_quota_warned_on
        remaining = _parse_pair(resp.headers.get("X-RateLimit-Remaining"))
        if remaining is None:
            self.quota = None
            return
        limit = _parse_pair(resp.headers.get("X-RateLimit-Limit"))
        self.quota = QuotaSnapshot(
            remaining_15m=remaining[0],
            remaining_day=remaining[1],
            limit_15m=limit[0] if limit else None,
            limit_day=limit[1] if limit else None,
        )
        today = datetime.now(timezone.utc).date()
        if self.quota.remaining_day < DAILY_QUOTA_WARN_THRESHOLD and _low_quota_warned_on != today:
            _low_quota_warned_on = today
            logger.warning("Intervals.icu daily quota low: %s", self.quota)

    def _raise_if_quota_exhausted(self, method: str, path: str, resp: httpx.Response) -> None:
        """On 429: a ``Retry-After`` beyond ``RETRY_MAX_DELAY`` means the 15-minute
        window or the daily quota is gone. Raise ``IntervalsRateLimitError`` so
        the caller defers the whole unit of work instead of sleeping here.
        Short/absent ``Retry-After`` (the per-second per-IP limit sends no
        headers) falls through to the normal sleep-and-retry loop."""
        retry_after = self._parse_retry_after(resp)
        if retry_after is None or retry_after <= RETRY_MAX_DELAY:
            return
        logger.warning(
            "Intervals.icu %s %s → 429, quota exhausted (%s), retry after %ds",
            method,
            path,
            self.quota or "no X-RateLimit headers",
            retry_after,
        )
        sentry_sdk.add_breadcrumb(
            category="intervals_icu",
            message=f"Quota exhausted on {path}: retry after {retry_after}s",
            level="warning",
        )
        raise IntervalsRateLimitError(retry_after, method=method, path=path, quota=self.quota)

    def _log_retry(self, method: str, path: str, status: int, attempt: int, delay: float) -> None:
        logger.warning(
            "Intervals.icu %s %s → %d, retry %d/%d in %.0fs",
            method,
            path,
            status,
            attempt + 1,
            MAX_RETRIES,
            delay,
        )
        sentry_sdk.add_breadcrumb(
            category="intervals_icu",
            message=f"Retry {attempt + 1}/{MAX_RETRIES} for {path}: {status}",
            level="warning",
        )

    def _log_transport_retry(
        self, method: str, path: str, exc: httpx.TransportError, attempt: int, delay: float
    ) -> None:
        name = exc.__class__.__name__
        logger.warning(
            "Intervals.icu %s %s → %s, retry %d/%d in %.0fs",
            method,
            path,
            name,
            attempt + 1,
            MAX_RETRIES,
            delay,
        )
        sentry_sdk.add_breadcrumb(
            category="intervals_icu",
            message=f"Retry {attempt + 1}/{MAX_RETRIES} for {path}: {name}",
            level="warning",
        )

    @staticmethod
    def _start_span(method: str, path: str):
        return sentry_sdk.start_span(op="http.client", description=f"{method} intervals.icu{path}")

    # ------------------------------------------------------------------
    # Response parsers
    # ------------------------------------------------------------------

    def _parse_activities(self, resp: httpx.Response) -> list[ActivityDTO]:
        activities = []
        for raw in resp.json():
            data = {to_snake(k): v for k, v in raw.items()}
            if "average_heartrate" in data:
                data["average_hr"] = data.pop("average_heartrate")
            activities.append(ActivityDTO.model_validate(data))
        return activities

    def _parse_event(self, resp: httpx.Response) -> ScheduledWorkoutDTO:
        data = {to_snake(k): v for k, v in resp.json().items()}
        return ScheduledWorkoutDTO.model_validate(data)

    @staticmethod
    def _parse_response(resp: httpx.Response, spec: "RequestSpec") -> Any:
        if spec.void:
            return None
        if spec.parser is None:
            return resp.json()
        if isinstance(spec.parser, type) and issubclass(spec.parser, BaseModel):
            data = resp.json()
            if isinstance(data, list):
                return [spec.parser.model_validate(item) for item in data]
            return spec.parser.model_validate(data)
        return spec.parser(resp)

    def _parse_fit(self, resp: httpx.Response, activity_id: str) -> bytes | None:
        content_length = resp.headers.get("content-length")
        if content_length and int(content_length) > FIT_MAX_SIZE:
            logger.warning("FIT file too large (%s bytes), skipping %s", content_length, activity_id)
            return None
        if len(resp.content) > FIT_MAX_SIZE:
            logger.warning("FIT file too large (%d bytes), skipping %s", len(resp.content), activity_id)
            return None
        return resp.content

    # ------------------------------------------------------------------
    # Endpoint specs — defined once, used by both clients
    # ------------------------------------------------------------------

    def _spec_get_wellness(self, dt: date | datetime | None = None) -> RequestSpec:
        date_str = (dt or date.today()).strftime("%Y-%m-%d")
        return RequestSpec("GET", f"/athlete/{self._athlete_id}/wellness/{date_str}", parser=WellnessDTO)

    def _spec_get_wellness_range(self, oldest: date, newest: date) -> RequestSpec:
        """List wellness records over a date range (Intervals.icu listWellnessRecords).

        Maps to ``GET /athlete/{id}/wellness?oldest=...&newest=...``. The OpenAPI
        path template is ``/wellness{ext}`` but the bare path serves JSON by default
        (mirrors how ``/activities`` behaves without an extension).
        """
        params = {
            "oldest": oldest.strftime("%Y-%m-%d"),
            "newest": newest.strftime("%Y-%m-%d"),
        }
        return RequestSpec(
            "GET",
            f"/athlete/{self._athlete_id}/wellness",
            kwargs={"params": params},
            parser=WellnessDTO,
        )

    def _spec_get_activities(self, oldest: date | None = None, newest: date | None = None) -> RequestSpec:
        if oldest is None:
            oldest = date.today() - timedelta(days=90)
        if newest is None:
            newest = date.today()
        params = {
            "oldest": oldest.strftime("%Y-%m-%d"),
            "newest": newest.strftime("%Y-%m-%d"),
            # Intervals.icu's /activities list endpoint requires explicit
            # `fields=` selection — any field NOT listed here is omitted from
            # the JSON response (verified empirically 2026-05-13 for the
            # `compliance` column). When you add a new field to ActivityDTO
            # and want it captured via list-sync (not just webhook-direct),
            # add it here too.
            "fields": (
                "id,start_date_local,type,icu_training_load,moving_time,"
                "average_heartrate,race,sub_type,source,icu_rpe,compliance"
            ),
        }
        return RequestSpec(
            "GET",
            f"/athlete/{self._athlete_id}/activities",
            kwargs={"params": params},
            parser=self._parse_activities,
        )

    def _spec_download_fit(self, activity_id: str) -> RequestSpec:
        return RequestSpec(
            "GET",
            f"/activity/{activity_id}/file",
            kwargs={"headers": {"Accept": "application/octet-stream"}, "timeout": 60.0},
            parser=lambda r: self._parse_fit(r, activity_id),
            handle_404=True,
        )

    def _spec_get_activity_detail(self, activity_id: str) -> RequestSpec:
        return RequestSpec("GET", f"/activity/{activity_id}", handle_404=True)

    def _spec_get_activity_intervals(self, activity_id: str) -> RequestSpec:
        return RequestSpec("GET", f"/activity/{activity_id}/intervals", handle_404=True)

    def _spec_update_activity(self, activity_id: str, data: dict) -> RequestSpec:
        return RequestSpec("PUT", f"/activity/{activity_id}", kwargs={"json": data})

    def _spec_get_event(self, event_id: int) -> RequestSpec:
        return RequestSpec(
            "GET",
            f"/athlete/{self._athlete_id}/events/{event_id}",
            parser=self._parse_event,
            handle_404=True,
        )

    def _spec_create_event(self, event: EventExDTO) -> RequestSpec:
        return RequestSpec(
            "POST",
            f"/athlete/{self._athlete_id}/events",
            kwargs={"json": event.model_dump(exclude_none=True)},
            parser=self._parse_event,
        )

    def _spec_update_event(self, event_id: int, event: EventExDTO) -> RequestSpec:
        return RequestSpec(
            "PUT",
            f"/athlete/{self._athlete_id}/events/{event_id}",
            kwargs={"json": event.model_dump(exclude_none=True)},
            parser=self._parse_event,
        )

    def _spec_delete_event(self, event_id: int) -> RequestSpec:
        return RequestSpec("DELETE", f"/athlete/{self._athlete_id}/events/{event_id}", void=True)

    def _spec_get_sport_settings(self, sport: str) -> RequestSpec:
        return RequestSpec("GET", f"/athlete/{self._athlete_id}/sport-settings/{sport}", parser=SportSettingsDTO)

    def _spec_list_sport_settings(self) -> RequestSpec:
        return RequestSpec("GET", f"/athlete/{self._athlete_id}/sport-settings", parser=SportSettingsDTO)

    def _spec_update_sport_settings(self, sport: str, sport_settings: dict) -> RequestSpec:
        return RequestSpec(
            "PUT",
            f"/athlete/{self._athlete_id}/sport-settings/{sport}",
            kwargs={"json": sport_settings, "params": {"recalcHrZones": "true"}},
        )

    def _spec_get_events(
        self,
        oldest: date | None = None,
        newest: date | None = None,
        category: str = "WORKOUT",
    ) -> RequestSpec:
        params: dict[str, str] = {"category": category}
        if oldest:
            params["oldest"] = oldest.strftime("%Y-%m-%d")
        if newest:
            params["newest"] = newest.strftime("%Y-%m-%d")
        return RequestSpec(
            "GET",
            f"/athlete/{self._athlete_id}/events",
            kwargs={"params": params},
            parser=ScheduledWorkoutDTO,
        )


def _resolve_credentials(user: User) -> dict:
    """Build IntervalsClient kwargs from a User row.

    Returns a dict suitable for unpacking into ``cls(**creds)`` — contains
    ``athlete_id`` + ``access_token``. Callers should NOT pass ``athlete_id``
    separately (duplicate-keyword TypeError).

    Raises ``IntervalsCredsMissingError`` if athlete_id or OAuth access_token
    is missing — Dramatiq actors catch ``IntervalsAccessError`` and skip so
    Dramatiq doesn't retry-loop on a user with revoked Intervals.icu access.
    """
    if not user.athlete_id:
        raise IntervalsCredsMissingError(user.id, "no athlete_id — cannot build Intervals.icu API URLs")
    if not user.intervals_access_token:
        raise IntervalsCredsMissingError(user.id, "no OAuth access_token — user must reconnect Intervals.icu")
    return {"athlete_id": user.athlete_id, "access_token": user.intervals_access_token}


# ======================================================================
# Async client
# ======================================================================


class IntervalsAsyncClient(IntervalsClientBase):
    """Async Intervals.icu client using httpx.AsyncClient."""

    def __init__(
        self,
        *,
        athlete_id: str,
        access_token: str,
        user_id: int | None = None,
    ) -> None:
        super().__init__(athlete_id=athlete_id, access_token=access_token, user_id=user_id)
        self._client = httpx.AsyncClient(**self._http_client_kwargs())

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "IntervalsAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    @classmethod
    @asynccontextmanager
    async def for_user(cls, user: int | User | UserDTO):
        """Create a session with per-user credentials from the DB.

        On 401 with OAuth — ``_execute`` clears tokens and raises
        ``IntervalsAuthError``.
        """
        if isinstance(user, (int, UserDTO)):
            user_id = user if isinstance(user, int) else user.id
            async with get_session() as session:
                user = await session.get(User, user_id)
            if user is None:
                raise LookupError(f"User {user_id} not found")
        creds = _resolve_credentials(user)
        async with cls(**creds, user_id=user.id) as session:
            yield session

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        with self._start_span(method, path) as span:
            span.set_data("http.method", method)
            last_exc: httpx.TransportError | None = None
            for attempt in range(MAX_RETRIES):
                try:
                    resp = await self._client.request(method, path, **kwargs)
                except httpx.TransportError as e:
                    last_exc = e
                    delay = min(2**attempt * 10, RETRY_MAX_DELAY)
                    self._log_transport_retry(method, path, e, attempt, delay)
                    await asyncio.sleep(delay)
                    continue
                self._record_quota(resp)
                if resp.status_code not in RETRY_STATUSES:
                    resp.raise_for_status()
                    span.set_data("http.status_code", resp.status_code)
                    return resp
                if resp.status_code == 429:
                    self._raise_if_quota_exhausted(method, path, resp)
                delay = self._compute_retry_delay(resp, attempt)
                self._log_retry(method, path, resp.status_code, attempt, delay)
                await asyncio.sleep(delay)
            if last_exc is not None:
                raise last_exc
            resp.raise_for_status()
            return resp  # unreachable

    async def _execute(self, spec: RequestSpec) -> Any:
        try:
            resp = await self._request(spec.method, spec.path, **spec.kwargs)
            return self._parse_response(resp, spec)
        except httpx.HTTPStatusError as e:
            if spec.handle_404 and e.response.status_code == 404:
                return None
            if e.response.status_code == 401 and self._user_id:
                logger.warning("Intervals.icu 401 for OAuth user %d — clearing tokens", self._user_id)
                async with get_session() as session:
                    db_user = await session.get(User, self._user_id)
                    if db_user:
                        db_user.clear_oauth_tokens()
                        await session.commit()
                raise IntervalsAuthError(self._user_id) from e
            if e.response.status_code == 403:
                logger.info(
                    "Intervals.icu 403 on %s %s for user %s — scope revoked, skipping",
                    spec.method,
                    spec.path,
                    self._user_id,
                )
                raise IntervalsScopeError(self._user_id, spec.method, spec.path) from e
            raise

    # -- Endpoints (one-liners) ----------------------------------------

    async def get_wellness(self, dt: date | datetime | None = None) -> WellnessDTO:
        return await self._execute(self._spec_get_wellness(dt))

    async def get_wellness_range(self, oldest: date, newest: date) -> list[WellnessDTO]:
        return await self._execute(self._spec_get_wellness_range(oldest, newest))

    async def get_activities(self, oldest: date | None = None, newest: date | None = None) -> list[ActivityDTO]:
        return await self._execute(self._spec_get_activities(oldest, newest))

    async def download_fit(self, activity_id: str) -> bytes | None:
        return await self._execute(self._spec_download_fit(activity_id))

    async def get_activity_detail(self, activity_id: str) -> dict | None:
        return await self._execute(self._spec_get_activity_detail(activity_id))

    async def get_activity_intervals(self, activity_id: str) -> list[dict] | None:
        return await self._execute(self._spec_get_activity_intervals(activity_id))

    async def update_activity(self, activity_id: str, data: dict) -> dict:
        return await self._execute(self._spec_update_activity(activity_id, data))

    async def create_event(self, event: EventExDTO) -> ScheduledWorkoutDTO:
        return await self._execute(self._spec_create_event(event))

    async def update_event(self, event_id: int, event: EventExDTO) -> ScheduledWorkoutDTO:
        return await self._execute(self._spec_update_event(event_id, event))

    async def delete_event(self, event_id: int) -> None:
        await self._execute(self._spec_delete_event(event_id))

    async def get_sport_settings(self, sport: str) -> SportSettingsDTO:
        return await self._execute(self._spec_get_sport_settings(sport))

    async def list_sport_settings(self) -> list[SportSettingsDTO]:
        return await self._execute(self._spec_list_sport_settings())

    async def update_sport_settings(self, sport: str, sport_settings: dict) -> dict:
        return await self._execute(self._spec_update_sport_settings(sport, sport_settings))

    async def get_events(
        self,
        oldest: date | None = None,
        newest: date | None = None,
        category: str = "WORKOUT",
    ) -> list[ScheduledWorkoutDTO]:
        return await self._execute(self._spec_get_events(oldest, newest, category))

    async def get_event(self, event_id: int) -> ScheduledWorkoutDTO | None:
        return await self._execute(self._spec_get_event(event_id))


# ======================================================================
# Sync client
# ======================================================================


class IntervalsSyncClient(IntervalsClientBase):
    """Sync Intervals.icu client using httpx.Client."""

    def __init__(
        self,
        *,
        athlete_id: str,
        access_token: str,
        user_id: int | None = None,
    ) -> None:
        super().__init__(athlete_id=athlete_id, access_token=access_token, user_id=user_id)
        self._client = httpx.Client(**self._http_client_kwargs())

    @classmethod
    @contextmanager
    def for_user(cls, user: int | User | UserDTO):
        """Create a session with per-user credentials from the DB.

        On 401 with OAuth — ``_execute`` clears tokens and raises
        ``IntervalsAuthError``.
        """
        if isinstance(user, (int, UserDTO)):
            user_id = user if isinstance(user, int) else user.id
            with get_sync_session() as session:
                user = session.get(User, user_id)
            if user is None:
                raise LookupError(f"User {user_id} not found")
        creds = _resolve_credentials(user)
        with cls(**creds, user_id=user.id) as session:
            yield session

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "IntervalsSyncClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        with self._start_span(method, path) as span:
            span.set_data("http.method", method)
            last_exc: httpx.TransportError | None = None
            for attempt in range(MAX_RETRIES):
                try:
                    resp = self._client.request(method, path, **kwargs)
                except httpx.TransportError as e:
                    last_exc = e
                    delay = min(2**attempt * 10, RETRY_MAX_DELAY)
                    self._log_transport_retry(method, path, e, attempt, delay)
                    time.sleep(delay)
                    continue
                self._record_quota(resp)
                if resp.status_code not in RETRY_STATUSES:
                    resp.raise_for_status()
                    span.set_data("http.status_code", resp.status_code)
                    return resp
                if resp.status_code == 429:
                    self._raise_if_quota_exhausted(method, path, resp)
                delay = self._compute_retry_delay(resp, attempt)
                self._log_retry(method, path, resp.status_code, attempt, delay)
                time.sleep(delay)
            if last_exc is not None:
                raise last_exc
            resp.raise_for_status()
            return resp  # unreachable

    def _execute(self, spec: RequestSpec) -> Any:
        try:
            resp = self._request(spec.method, spec.path, **spec.kwargs)
            return self._parse_response(resp, spec)
        except httpx.HTTPStatusError as e:
            if spec.handle_404 and e.response.status_code == 404:
                return None
            if e.response.status_code == 401 and self._user_id:
                logger.warning("Intervals.icu 401 for OAuth user %d — clearing tokens", self._user_id)
                with get_sync_session() as session:
                    db_user = session.get(User, self._user_id)
                    if db_user:
                        db_user.clear_oauth_tokens()
                        session.commit()
                raise IntervalsAuthError(self._user_id) from e
            if e.response.status_code == 403:
                logger.info(
                    "Intervals.icu 403 on %s %s for user %s — scope revoked, skipping",
                    spec.method,
                    spec.path,
                    self._user_id,
                )
                raise IntervalsScopeError(self._user_id, spec.method, spec.path) from e
            raise

    # -- Endpoints (one-liners) ----------------------------------------

    def get_wellness(self, dt: date | datetime | None = None) -> WellnessDTO:
        return self._execute(self._spec_get_wellness(dt))

    def get_wellness_range(self, oldest: date, newest: date) -> list[WellnessDTO]:
        return self._execute(self._spec_get_wellness_range(oldest, newest))

    def get_activities(self, oldest: date | None = None, newest: date | None = None) -> list[ActivityDTO]:
        return self._execute(self._spec_get_activities(oldest, newest))

    def download_fit(self, activity_id: str) -> bytes | None:
        return self._execute(self._spec_download_fit(activity_id))

    def get_activity_detail(self, activity_id: str) -> dict | None:
        return self._execute(self._spec_get_activity_detail(activity_id))

    def get_activity_intervals(self, activity_id: str) -> list[dict] | None:
        return self._execute(self._spec_get_activity_intervals(activity_id))

    def update_activity(self, activity_id: str, data: dict) -> dict:
        return self._execute(self._spec_update_activity(activity_id, data))

    def create_event(self, event: EventExDTO) -> ScheduledWorkoutDTO:
        return self._execute(self._spec_create_event(event))

    def update_event(self, event_id: int, event: EventExDTO) -> ScheduledWorkoutDTO:
        return self._execute(self._spec_update_event(event_id, event))

    def delete_event(self, event_id: int) -> None:
        self._execute(self._spec_delete_event(event_id))

    def get_sport_settings(self, sport: str) -> SportSettingsDTO:
        return self._execute(self._spec_get_sport_settings(sport))

    def list_sport_settings(self) -> list[SportSettingsDTO]:
        return self._execute(self._spec_list_sport_settings())

    def update_sport_settings(self, sport: str, sport_settings: dict) -> dict:
        return self._execute(self._spec_update_sport_settings(sport, sport_settings))

    def get_events(
        self,
        oldest: date | None = None,
        newest: date | None = None,
        category: str = "WORKOUT",
    ) -> list[ScheduledWorkoutDTO]:
        return self._execute(self._spec_get_events(oldest, newest, category))

    def get_event(self, event_id: int) -> ScheduledWorkoutDTO | None:
        return self._execute(self._spec_get_event(event_id))
