"""Client for Intervals.icu API (https://intervals.icu/api/v1/docs)."""

import asyncio
import logging
from datetime import date, datetime, timedelta

import httpx

from config import settings
from data.models import Activity, ScheduledWorkout, Wellness

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

    @property
    def is_active(self) -> bool:
        """Whether this client has been initialized and not yet closed."""
        return self._initialized

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

    async def get_activities(
        self,
        oldest: date | None = None,
        newest: date | None = None,
    ) -> list[Activity]:
        """Fetch completed activities with training load and sport type.

        Args:
            oldest: Start date (default: 90 days ago).
            newest: End date (default: today).
        """
        if oldest is None:
            oldest = date.today() - timedelta(days=90)
        if newest is None:
            newest = date.today()

        params: dict[str, str] = {
            "oldest": oldest.strftime("%Y-%m-%d"),
            "newest": newest.strftime("%Y-%m-%d"),
            "fields": "id,start_date_local,type,icu_training_load,moving_time,average_heartrate",
        }
        resp = await self._request(
            "GET",
            f"/athlete/{self._athlete_id}/activities",
            params=params,
        )
        activities = []
        for raw in resp.json():
            data = {_to_snake(k): v for k, v in raw.items()}
            # Intervals.icu returns averageHeartrate → average_heartrate, model uses average_hr
            if "average_heartrate" in data:
                data["average_hr"] = data.pop("average_heartrate")
            activities.append(Activity.model_validate(data))
        return activities

    # ------------------------------------------------------------------
    # FIT file download (Level 2: DFA alpha 1)
    # ------------------------------------------------------------------

    async def download_fit(self, activity_id: str) -> bytes | None:
        """Download original FIT file for an activity.

        Returns raw bytes or None if not available (404).
        Uses _request() for retry on 429/5xx.
        """
        max_size = 50 * 1024 * 1024  # 50 MB
        try:
            resp = await self._request(
                "GET",
                f"/activity/{activity_id}/file",
                headers={"Accept": "application/octet-stream"},
                timeout=60.0,
            )
            content_length = resp.headers.get("content-length")
            if content_length and int(content_length) > max_size:
                logger.warning("FIT file too large (%s bytes), skipping %s", content_length, activity_id)
                return None
            if len(resp.content) > max_size:
                logger.warning("FIT file too large (%d bytes), skipping %s", len(resp.content), activity_id)
                return None
            return resp.content
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_activity_detail(self, activity_id: str) -> dict | None:
        """Fetch full activity detail from Intervals.icu.

        GET /api/v1/activity/{activity_id}
        Returns raw JSON dict with all computed metrics (NP, IF, EF, zones, etc.),
        or None if the activity is not found (404).
        """
        try:
            resp = await self._request("GET", f"/activity/{activity_id}")
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_activity_intervals(self, activity_id: str) -> list[dict] | None:
        """Fetch per-interval breakdown for an activity.

        GET /api/v1/activity/{activity_id}/intervals
        Returns list of interval dicts with power, HR, speed, cadence, etc.,
        or None if the activity is not found (404).
        """
        try:
            resp = await self._request("GET", f"/activity/{activity_id}/intervals")
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # ------------------------------------------------------------------
    # Write Events (Adaptive Training Plan — Phase 1)
    # ------------------------------------------------------------------

    async def create_event(self, event: dict) -> dict:
        """Create a workout event on the athlete's Intervals.icu calendar.

        POST /athlete/{id}/events
        Returns the created event dict with server-generated ID.
        """
        resp = await self._request(
            "POST",
            f"/athlete/{self._athlete_id}/events",
            json=event,
        )
        return resp.json()

    async def update_event(self, event_id: int, event: dict) -> dict:
        """Update an existing event on the athlete's calendar.

        PUT /athlete/{id}/events/{event_id}
        Returns the updated event dict.
        """
        resp = await self._request(
            "PUT",
            f"/athlete/{self._athlete_id}/events/{event_id}",
            json=event,
        )
        return resp.json()

    async def delete_event(self, event_id: int) -> None:
        """Delete an event from the athlete's calendar.

        DELETE /athlete/{id}/events/{event_id}
        """
        await self._request(
            "DELETE",
            f"/athlete/{self._athlete_id}/events/{event_id}",
        )

    # ------------------------------------------------------------------
    # Sport Settings
    # ------------------------------------------------------------------

    async def update_sport_settings(self, sport: str, settings: dict) -> dict:
        """Update sport-specific settings (LTHR, FTP, pace, zones).

        PUT /athlete/{id}/sport-settings/{sport}?recalcHrZones=true
        """
        resp = await self._request(
            "PUT",
            f"/athlete/{self._athlete_id}/sport-settings/{sport}",
            json=settings,
            params={"recalcHrZones": "true"},
        )
        return resp.json()

    # ------------------------------------------------------------------
    # Read Events
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


async def sync_athlete_settings() -> None:
    """Push athlete thresholds from config to Intervals.icu sport settings.

    Called on startup to keep Intervals.icu zones in sync with .env values.
    Logs results, never raises — startup must not fail due to sync errors.
    """
    client = IntervalsClient()
    sport_settings = {
        "Ride": {
            "ftp": int(settings.ATHLETE_FTP),
            "lthr": settings.ATHLETE_LTHR_BIKE,
            "max_hr": settings.ATHLETE_MAX_HR,
        },
        "Run": {
            "lthr": settings.ATHLETE_LTHR_RUN,
            "max_hr": settings.ATHLETE_MAX_HR,
            "threshold_pace": round(1000.0 / settings.ATHLETE_THRESHOLD_PACE_RUN, 4),  # sec/km → m/s
        },
        "Swim": {
            "threshold_pace": round(100.0 / settings.ATHLETE_CSS, 4),  # sec/100m → m/s (API expects speed)
        },
    }

    for sport, payload in sport_settings.items():
        try:
            await client.update_sport_settings(sport, payload)
            logger.info("Synced %s settings to Intervals.icu: %s", sport, payload)
        except Exception as exc:
            detail = getattr(getattr(exc, "response", None), "text", "")
            logger.warning("Failed to sync %s settings: %s %s", sport, exc, detail)
