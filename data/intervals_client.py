"""Client for Intervals.icu API (https://intervals.icu/api/v1/docs)."""

import asyncio
import logging
from datetime import date, datetime

import httpx

from config import settings
from data.models import ScheduledWorkout, Wellness

logger = logging.getLogger(__name__)

_BASE_URL = "https://intervals.icu/api/v1"
_MAX_RETRIES = 3
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _to_snake(name: str) -> str:
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


class IntervalsClient:
    """Thin wrapper around Intervals.icu REST API using httpx (async).

    Singleton: IntervalsClient() reuses the same instance and httpx session.
    """

    _instance: "IntervalsClient | None" = None

    def __new__(cls) -> "IntervalsClient":
        if cls._instance is not None:
            return cls._instance
        inst = super().__new__(cls)
        inst._initialized = False
        cls._instance = inst
        return inst

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            auth=("API_KEY", settings.INTERVALS_API_KEY.get_secret_value()),
            headers={"Accept": "application/json"},
            timeout=30.0,
        )
        self._athlete_id = settings.INTERVALS_ATHLETE_ID

    async def close(self) -> None:
        """Close the underlying httpx session."""
        await self._client.aclose()
        IntervalsClient._instance = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Retry
    # ------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Execute an HTTP request with retry and exponential backoff."""
        for attempt in range(_MAX_RETRIES):
            resp = await self._client.request(method, path, **kwargs)
            if resp.status_code not in _RETRY_STATUSES:
                resp.raise_for_status()
                return resp

            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 2**attempt
            logger.warning(
                "Intervals.icu %s %s → %d, retry %d/%d in %.0fs",
                method,
                path,
                resp.status_code,
                attempt + 1,
                _MAX_RETRIES,
                delay,
            )
            await asyncio.sleep(delay)

        resp.raise_for_status()
        return resp  # unreachable, raise_for_status throws

    # ------------------------------------------------------------------
    # Wellness
    # ------------------------------------------------------------------

    async def get_wellness(self, dt: date | datetime | None = None) -> Wellness:
        """Fetch wellness data for a single date (YYYY-MM-DD)."""
        date_str = (dt or date.today()).strftime("%Y-%m-%d")
        resp = await self._request("GET", f"/athlete/{self._athlete_id}/wellness/{date_str}")
        data = {_to_snake(k): v for k, v in resp.json().items()}
        return Wellness.model_validate(data)

    # ------------------------------------------------------------------
    # Scheduled Workouts (Events)
    # ------------------------------------------------------------------

    async def get_events(
        self,
        oldest: date | None = None,
        newest: date | None = None,
        category: str = "WORKOUT",
    ) -> list[ScheduledWorkout]:
        """Fetch planned workouts/events for a date range.

        Args:
            oldest: Start date (default: today).
            newest: End date inclusive (default: oldest + 6 days).
            category: Comma-separated filter, e.g. "WORKOUT", "WORKOUT,RACE_A".
        """
        params: dict[str, str] = {"category": category}
        if oldest:
            params["oldest"] = oldest.strftime("%Y-%m-%d")
        if newest:
            params["newest"] = newest.strftime("%Y-%m-%d")

        resp = await self._request(
            "GET",
            f"/athlete/{self._athlete_id}/events",
            params=params,
        )
        events = []
        for raw in resp.json():
            data = {_to_snake(k): v for k, v in raw.items()}
            events.append(ScheduledWorkout.model_validate(data))
        return events
