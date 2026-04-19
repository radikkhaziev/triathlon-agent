"""Intervals.icu API clients (async + sync).

Endpoint logic defined once in IntervalsClientBase via RequestSpec.
Subclasses add transport (_request + retry) and thin one-liner endpoints.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

import httpx
import sentry_sdk
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


class IntervalsAuthError(Exception):
    """Raised when Intervals.icu returns 401 for an OAuth user.

    The token has been revoked or expired. On 401, ``_execute()``
    clears the stored tokens in the DB before raising this error.
    """

    def __init__(self, user_id: int):
        self.user_id = user_id
        super().__init__(f"Intervals.icu OAuth token revoked for user {user_id}")


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

    Supports two authentication methods (see `api/routers/intervals/oauth.py`):
    - **api_key** (legacy): HTTP Basic Auth ``("API_KEY", key)``
    - **access_token** (OAuth): ``Authorization: Bearer {token}``

    Callers should use the ``for_user()`` factory instead of ``__init__``
    directly — it reads ``User.intervals_auth_method`` and picks the right
    credential automatically.
    """

    def __init__(
        self,
        *,
        athlete_id: str,
        api_key: str | None = None,
        access_token: str | None = None,
        user_id: int | None = None,
    ) -> None:
        if not athlete_id:
            raise ValueError("IntervalsClient requires a non-empty athlete_id")
        if not api_key and not access_token:
            raise ValueError("IntervalsClient requires either api_key or access_token")
        self._api_key = api_key
        self._access_token = access_token
        self._athlete_id = athlete_id
        self._user_id = user_id

    def _http_client_kwargs(self) -> dict:
        headers: dict[str, str] = {"Accept": "application/json"}
        kwargs: dict[str, Any] = {
            "base_url": BASE_URL,
            "headers": headers,
            "timeout": 30.0,
        }
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        else:
            kwargs["auth"] = ("API_KEY", self._api_key)
        return kwargs

    def _compute_retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), RETRY_MAX_DELAY)
            except (ValueError, OverflowError):
                pass
        return min(2**attempt * 10, RETRY_MAX_DELAY)

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

    def _parse_events(self, resp: httpx.Response) -> list[ScheduledWorkoutDTO]:
        events = []
        for raw in resp.json():
            data = {to_snake(k): v for k, v in raw.items()}
            events.append(ScheduledWorkoutDTO.model_validate(data))
        return events

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

    def _spec_get_activities(self, oldest: date | None = None, newest: date | None = None) -> RequestSpec:
        if oldest is None:
            oldest = date.today() - timedelta(days=90)
        if newest is None:
            newest = date.today()
        params = {
            "oldest": oldest.strftime("%Y-%m-%d"),
            "newest": newest.strftime("%Y-%m-%d"),
            "fields": (
                "id,start_date_local,type,icu_training_load,moving_time,"
                "average_heartrate,race,sub_type,source,icu_rpe"
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

    def _spec_get_activity_streams(self, activity_id: str, types: list[str] | None = None) -> RequestSpec:
        params: dict = {}
        if types:
            params["types"] = ",".join(types)
        return RequestSpec(
            "GET",
            f"/activity/{activity_id}/streams",
            kwargs={"params": params} if params else {},
            handle_404=True,
        )

    def _spec_get_activity_intervals(self, activity_id: str) -> RequestSpec:
        return RequestSpec("GET", f"/activity/{activity_id}/intervals", handle_404=True)

    def _spec_update_activity(self, activity_id: str, data: dict) -> RequestSpec:
        return RequestSpec("PUT", f"/activity/{activity_id}", kwargs={"json": data})

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
    """Pick the right auth kwargs for IntervalsClient based on User's auth method.

    Returns a dict suitable for unpacking into ``cls(athlete_id=..., **creds)``.
    Raises ``LookupError`` if no usable credentials are configured.
    """
    if not user.athlete_id:
        raise LookupError(f"User {user.id} has no athlete_id — cannot build Intervals.icu API URLs")
    if user.intervals_auth_method == "oauth" and user.intervals_access_token:
        return {"athlete_id": user.athlete_id, "access_token": user.intervals_access_token}
    if user.api_key:
        return {"athlete_id": user.athlete_id, "api_key": user.api_key}
    raise LookupError(
        f"User {user.id} has no Intervals.icu credentials "
        f"(method={user.intervals_auth_method}, api_key={'set' if user.api_key_encrypted else 'empty'}, "
        f"oauth_token={'set' if user.intervals_access_token_encrypted else 'empty'})"
    )


# ======================================================================
# Async client
# ======================================================================


class IntervalsAsyncClient(IntervalsClientBase):
    """Async Intervals.icu client using httpx.AsyncClient."""

    def __init__(
        self,
        *,
        athlete_id: str,
        api_key: str | None = None,
        access_token: str | None = None,
        user_id: int | None = None,
    ) -> None:
        super().__init__(athlete_id=athlete_id, api_key=api_key, access_token=access_token, user_id=user_id)
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
            for attempt in range(MAX_RETRIES):
                resp = await self._client.request(method, path, **kwargs)
                if resp.status_code not in RETRY_STATUSES:
                    resp.raise_for_status()
                    span.set_data("http.status_code", resp.status_code)
                    return resp
                delay = self._compute_retry_delay(resp, attempt)
                self._log_retry(method, path, resp.status_code, attempt, delay)
                await asyncio.sleep(delay)
            resp.raise_for_status()
            return resp  # unreachable

    async def _execute(self, spec: RequestSpec) -> Any:
        try:
            resp = await self._request(spec.method, spec.path, **spec.kwargs)
            return self._parse_response(resp, spec)
        except httpx.HTTPStatusError as e:
            if spec.handle_404 and e.response.status_code == 404:
                return None
            if e.response.status_code == 401 and self._access_token and self._user_id:
                logger.warning("Intervals.icu 401 for OAuth user %d — clearing tokens", self._user_id)
                async with get_session() as session:
                    db_user = await session.get(User, self._user_id)
                    if db_user:
                        db_user.clear_oauth_tokens()
                        await session.commit()
                raise IntervalsAuthError(self._user_id) from e
            raise

    # -- Endpoints (one-liners) ----------------------------------------

    async def get_wellness(self, dt: date | datetime | None = None) -> WellnessDTO:
        return await self._execute(self._spec_get_wellness(dt))

    async def get_activities(self, oldest: date | None = None, newest: date | None = None) -> list[ActivityDTO]:
        return await self._execute(self._spec_get_activities(oldest, newest))

    async def download_fit(self, activity_id: str) -> bytes | None:
        return await self._execute(self._spec_download_fit(activity_id))

    async def get_activity_detail(self, activity_id: str) -> dict | None:
        return await self._execute(self._spec_get_activity_detail(activity_id))

    async def get_activity_streams(self, activity_id: str, types: list[str] | None = None) -> list[dict] | None:
        return await self._execute(self._spec_get_activity_streams(activity_id, types))

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


# ======================================================================
# Sync client
# ======================================================================


class IntervalsSyncClient(IntervalsClientBase):
    """Sync Intervals.icu client using httpx.Client."""

    def __init__(
        self,
        *,
        athlete_id: str,
        api_key: str | None = None,
        access_token: str | None = None,
        user_id: int | None = None,
    ) -> None:
        super().__init__(athlete_id=athlete_id, api_key=api_key, access_token=access_token, user_id=user_id)
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
            for attempt in range(MAX_RETRIES):
                resp = self._client.request(method, path, **kwargs)
                if resp.status_code not in RETRY_STATUSES:
                    resp.raise_for_status()
                    span.set_data("http.status_code", resp.status_code)
                    return resp
                delay = self._compute_retry_delay(resp, attempt)
                self._log_retry(method, path, resp.status_code, attempt, delay)
                time.sleep(delay)
            resp.raise_for_status()
            return resp  # unreachable

    def _execute(self, spec: RequestSpec) -> Any:
        try:
            resp = self._request(spec.method, spec.path, **spec.kwargs)
            return self._parse_response(resp, spec)
        except httpx.HTTPStatusError as e:
            if spec.handle_404 and e.response.status_code == 404:
                return None
            if e.response.status_code == 401 and self._access_token and self._user_id:
                logger.warning("Intervals.icu 401 for OAuth user %d — clearing tokens", self._user_id)
                with get_sync_session() as session:
                    db_user = session.get(User, self._user_id)
                    if db_user:
                        db_user.clear_oauth_tokens()
                        session.commit()
                raise IntervalsAuthError(self._user_id) from e
            raise

    # -- Endpoints (one-liners) ----------------------------------------

    def get_wellness(self, dt: date | datetime | None = None) -> WellnessDTO:
        return self._execute(self._spec_get_wellness(dt))

    def get_activities(self, oldest: date | None = None, newest: date | None = None) -> list[ActivityDTO]:
        return self._execute(self._spec_get_activities(oldest, newest))

    def download_fit(self, activity_id: str) -> bytes | None:
        return self._execute(self._spec_download_fit(activity_id))

    def get_activity_detail(self, activity_id: str) -> dict | None:
        return self._execute(self._spec_get_activity_detail(activity_id))

    def get_activity_streams(self, activity_id: str, types: list[str] | None = None) -> list[dict] | None:
        return self._execute(self._spec_get_activity_streams(activity_id, types))

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
