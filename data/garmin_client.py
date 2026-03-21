"""Wrapper around the garminconnect library for fetching athlete data."""

import logging
import time
from datetime import date, datetime
from pathlib import Path

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import settings
from data.models import (
    Activity,
    BodyBatteryData,
    BodyCompositionData,
    CyclingFTPData,
    DailyStats,
    EnduranceScoreData,
    HeartRateData,
    HRVData,
    LactateThresholdData,
    MaxMetricsData,
    RacePrediction,
    RespirationData,
    ScheduledWorkout,
    SleepData,
    SpO2Data,
    SportType,
    StressData,
    TrainingReadinessData,
    TrainingStatusData,
)

logger = logging.getLogger(__name__)


_RETRY_STRATEGY = Retry(
    total=5,
    connect=3,
    read=2,
    status=3,
    backoff_factor=1.0,
    status_forcelist=[500, 502, 503, 504],  # NOT 429 — retrying makes the ban longer
    respect_retry_after_header=True,
)

# Mapping from Garmin activity type names to our SportType enum.
# Garmin uses various strings like "running", "cycling", "lap_swimming", etc.
_GARMIN_SPORT_MAP: dict[str, SportType] = {
    "running": SportType.RUN,
    "trail_running": SportType.RUN,
    "treadmill_running": SportType.RUN,
    "track_running": SportType.RUN,
    "cycling": SportType.BIKE,
    "road_cycling": SportType.BIKE,
    "indoor_cycling": SportType.BIKE,
    "mountain_biking": SportType.BIKE,
    "gravel_cycling": SportType.BIKE,
    "virtual_ride": SportType.BIKE,
    "lap_swimming": SportType.SWIM,
    "open_water_swimming": SportType.SWIM,
    "swimming": SportType.SWIM,
    "pool_swimming": SportType.SWIM,
    "strength_training": SportType.STRENGTH,
    "cardio": SportType.STRENGTH,
}


def _map_sport(garmin_type: str) -> SportType:
    """Map a Garmin activity type string to our SportType enum."""
    if not garmin_type:
        return SportType.OTHER
    key = garmin_type.lower().replace(" ", "_")
    return _GARMIN_SPORT_MAP.get(key, SportType.OTHER)


def _minutes_to_hours(minutes: int | None) -> int | None:
    """Convert recovery time from minutes to hours, rounding up."""
    if minutes is None:
        return None
    return (minutes + 59) // 60


class GarminClient:
    """High-level wrapper around the garminconnect library.

    Handles authentication with token caching, rate limiting,
    and parsing raw API responses into Pydantic models.

    Singleton: GarminClient() reads credentials from settings.
    """

    _instance: "GarminClient | None" = None
    _login_cooldown_until: float = 0.0
    _LOGIN_COOLDOWN_SEC = 2 * 60 * 60  # 2 hours after a 429
    _DEFAULT_TIMEOUT_SEC = 180

    def __new__(cls) -> "GarminClient":
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
        self.email = settings.GARMIN_EMAIL
        self.password = settings.GARMIN_PASSWORD.get_secret_value()
        self.profile = None
        self.client = Garmin(self.email, self.password)
        self._last_request_time: float = 0.0
        self._login(soft=True)
        self._mount_retry_adapter()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _login(self, soft: bool = False) -> None:
        self.profile = None

        tokenstore_path = Path(settings.GARMIN_TOKENS).expanduser().resolve()
        normalized_path = str(tokenstore_path)

        try:
            self.client.garth.load(normalized_path)
            if self.client.garth.oauth1_token and self.client.garth.oauth2_token:
                logger.info("Garmin tokens loaded from %s", normalized_path)
                if not soft:
                    self.client.garth.refresh_oauth2()

                _g_settings = self.client.garth.connectapi(self.client.garmin_connect_user_settings_url)
                if _g_settings and isinstance(_g_settings, dict):
                    self.client.garth.dump(normalized_path)
                    self.profile = self.client.garth.profile
                    return
        except Exception as exc:
            logger.warning("Failed to load/refresh Garmin tokens: %s", exc)

        # Tokens missing or invalid — fall back to credential login.
        # Do NOT pass tokenstore here: garminconnect re-raises
        # FileNotFoundError if token files are absent, and wraps
        # pydantic ValidationError into GarminConnectConnectionError
        # if they are corrupted.
        try:
            self.client.login()
        except GarminConnectTooManyRequestsError as exc:
            self._set_cooldown(self._LOGIN_COOLDOWN_SEC)
            logger.warning("Failed to login to Garmin: %s", exc)
            return
        except GarminConnectConnectionError as exc:
            self._set_cooldown(self._DEFAULT_TIMEOUT_SEC)
            logger.error("Authentication failed for Garmin: %s", exc)
            return

        self.client.garth.dump(normalized_path)
        self.profile = self.client.garth.profile

    def _mount_retry_adapter(self) -> None:
        """Mount an HTTPAdapter with retry/backoff on the garth session."""
        adapter = HTTPAdapter(max_retries=_RETRY_STRATEGY)
        sess = self.client.garth.sess
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        """Ensure at least 1 second between consecutive API requests."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_request_time = time.monotonic()

    def _check_cooldown(self) -> bool:
        """Return True if cooldown is active (skip the request)."""
        now = time.monotonic()
        if now < self._login_cooldown_until:
            remaining = int(self._login_cooldown_until - now)
            logger.warning("Garmin API on cooldown, %ds remaining — skipping", remaining)
            return True
        return False

    def _set_cooldown(self, duration: int) -> None:
        """Activate login cooldown after a 429 error."""
        type(self)._login_cooldown_until = time.monotonic() + duration
        logger.warning("Garmin 429 — cooldown for %ds", duration)

    def _call_api(self, fn, *args, **kwargs):
        """Call a Garmin API method with rate limiting and session recovery."""
        if self._check_cooldown():
            return
        if not self.profile:
            logger.warning("No OAuth tokens — attempting login before API call")
            self._login()

        # failed to login
        if not self.profile:
            logger.error("Garmin API call failed: no valid authentication")
            return

        self._rate_limit()
        try:
            return fn(*args, **kwargs)
        except GarminConnectAuthenticationError:
            self.profile = None
        except GarminConnectTooManyRequestsError:
            self.profile = None
            self._set_cooldown(self._LOGIN_COOLDOWN_SEC)
        except GarminConnectConnectionError:
            self._set_cooldown(self._DEFAULT_TIMEOUT_SEC)
        except Exception as exc:
            logger.error("Garmin API call error: %s", exc)

    # ------------------------------------------------------------------
    # Data fetching methods
    # ------------------------------------------------------------------

    def get_sleep(self, date_str: str) -> SleepData:
        """Fetch sleep data for a given date (YYYY-MM-DD).

        Parses the dailySleepDTO from the Garmin response.
        """
        raw = self._call_api(self.client.get_sleep_data, date_str)
        if not raw:
            return SleepData(date=date.fromisoformat(date_str))

        logger.debug("Raw sleep data keys: %s", list(raw.keys()))

        dto = raw.get("dailySleepDTO", {})
        sleep_scores = dto.get("sleepScores", {})
        sleep_score = sleep_scores.get("overall", {}).get("value", 0)

        return SleepData(
            date=date.fromisoformat(date_str),
            score=int(sleep_score or 0),
            duration=int(dto.get("sleepTimeSeconds") or 0),
            start=int(dto.get("sleepStartTimestampLocal") or 0),
            end=int(dto.get("sleepEndTimestampLocal") or 0),
            stress_avg=int(dto.get("avgSleepStress") or 0),
            hrv_avg=int(raw.get("avgOvernightHrv") or 0),
            heart_rate_avg=int(dto.get("avgHeartRate") or 0),
        )

    def get_hrv(self, date_str: str) -> HRVData:
        """Fetch HRV summary for a given date (YYYY-MM-DD)."""
        raw = self._call_api(self.client.get_hrv_data, date_str)
        if not raw:
            return HRVData(date=date.fromisoformat(date_str))

        logger.debug("Raw HRV data keys: %s", list(raw.keys()))
        summary = raw.get("hrvSummary", {})

        return HRVData(
            date=date.fromisoformat(date_str),
            hrv_weekly_avg=float(summary.get("weeklyAvg", 0) or 0),
            hrv_last_night=float(summary.get("lastNight", 0) or summary.get("lastNightAvg", 0) or 0),
            hrv_5min_high=summary.get("lastNight5MinHigh"),
            status=str(summary.get("status", "Unknown") or "Unknown"),
        )

    def get_body_battery(self, start: str, end: str) -> list[BodyBatteryData]:
        """Fetch body battery data for a date range (YYYY-MM-DD)."""
        raw = self._call_api(self.client.get_body_battery, start, end)
        logger.debug("Body battery entries: %d", len(raw) if raw else 0)

        results: list[BodyBatteryData] = []
        if not raw:
            return results

        # The API may return a list of daily summaries or detailed readings.
        # We group by date and extract min/max for start/end values.
        for entry in raw:
            try:
                entry_date_str = entry.get("calendarDate") or entry.get("date")
                if not entry_date_str:
                    continue
                results.append(
                    BodyBatteryData(
                        date=date.fromisoformat(entry_date_str),
                        start_value=int(entry.get("startValue", 0) or entry.get("bodyBatteryStartOfDay", 0) or 0),
                        end_value=int(entry.get("endValue", 0) or entry.get("bodyBatteryEndOfDay", 0) or 0),
                        charged=int(entry.get("charged", 0) or entry.get("bodyBatteryCharged", 0) or 0),
                        drained=int(entry.get("drained", 0) or entry.get("bodyBatteryDrained", 0) or 0),
                    )
                )
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping body battery entry: %s", exc)
                continue

        return results

    def get_stress(self, date_str: str) -> StressData:
        """Fetch stress data for a given date (YYYY-MM-DD)."""
        raw = self._call_api(self.client.get_stress_data, date_str)
        logger.debug("Raw stress data keys: %s", list(raw.keys()) if raw else None)

        if not raw:
            return StressData(
                date=date.fromisoformat(date_str),
                avg_stress=0.0,
                max_stress=0.0,
                stress_duration_seconds=0,
                rest_duration_seconds=0,
            )

        return StressData(
            date=date.fromisoformat(date_str),
            avg_stress=float(raw.get("overallStressLevel", 0) or raw.get("avgStressLevel", 0) or 0),
            max_stress=float(raw.get("maxStressLevel", 0) or 0),
            stress_duration_seconds=int(raw.get("highStressDuration", 0) or raw.get("stressDuration", 0) or 0),
            rest_duration_seconds=int(raw.get("restStressDuration", 0) or raw.get("lowStressDuration", 0) or 0),
        )

    def get_resting_hr(self, date_str: str) -> float:
        """Fetch resting heart rate for a given date (YYYY-MM-DD)."""
        raw = self._call_api(self.client.get_rhr_day, date_str)
        logger.debug("Raw resting HR data: %s", raw)

        if not raw:
            return 0.0

        # The response may nest the value in different structures
        value = raw.get("restingHeartRate")
        if value is None:
            stats = raw.get("statisticsDTO", {})
            value = stats.get("restingHeartRate")
        if value is None:
            value = raw.get("value")

        return float(value or 0)

    def get_scheduled_workouts(self, start: str, end: str) -> list[ScheduledWorkout]:
        """Fetch scheduled/planned workouts for a date range (YYYY-MM-DD)."""
        raw = self._call_api(self.client.get_training_plan_list)

        # Try calendar-based approach if training plan list is unavailable
        if not raw:
            raw = self._call_api(self.client.get_calendar, start, end)

        results: list[ScheduledWorkout] = []
        if not raw:
            return results

        # The calendar response may have workouts in different structures
        items = raw if isinstance(raw, list) else raw.get("calendarItems", [])

        for item in items:
            try:
                sched_date_str = item.get("date") or item.get("calendarDate") or item.get("scheduledDate")
                if not sched_date_str:
                    continue

                # Filter to requested date range
                sched_date = date.fromisoformat(sched_date_str[:10])
                start_date = date.fromisoformat(start)
                end_date = date.fromisoformat(end)
                if sched_date < start_date or sched_date > end_date:
                    continue

                sport_str = (
                    item.get("activityType", {}).get("typeKey", "")
                    if isinstance(item.get("activityType"), dict)
                    else item.get("sportType", item.get("activityTypeKey", ""))
                )

                results.append(
                    ScheduledWorkout(
                        scheduled_date=sched_date,
                        workout_name=str(item.get("title", "") or item.get("workoutName", "") or "Workout"),
                        sport=_map_sport(str(sport_str)),
                        description=item.get("description") or item.get("notes"),
                        planned_duration_seconds=item.get("duration") or item.get("plannedDuration"),
                        planned_tss=item.get("plannedTSS") or item.get("estimatedTSS"),
                    )
                )
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping scheduled workout entry: %s", exc)
                continue

        return results

    def get_activities(self, start: int = 0, limit: int = 20) -> list[Activity]:
        """Fetch recent activities.

        Args:
            start: Offset index (0-based).
            limit: Maximum number of activities to return.
        """
        raw = self._call_api(self.client.get_activities, start, limit)
        logger.debug("Fetched %d activities", len(raw) if raw else 0)

        results: list[Activity] = []
        if not raw:
            return results

        for act in raw:
            try:
                # Parse activity type
                activity_type = act.get("activityType", {})
                type_key = activity_type.get("typeKey", "") if isinstance(activity_type, dict) else str(activity_type)

                # Parse start time
                start_time_str = act.get("startTimeLocal") or act.get("startTimeGMT", "")
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    start_time = datetime.now()

                results.append(
                    Activity(
                        activity_id=int(act.get("activityId", 0)),
                        sport=_map_sport(type_key),
                        start_time=start_time,
                        duration_seconds=int(act.get("duration", 0) or 0),
                        distance_meters=act.get("distance"),
                        avg_hr=act.get("averageHR"),
                        max_hr=act.get("maxHR"),
                        avg_power=act.get("averagePower"),
                        normalized_power=act.get("normPower") or act.get("normalizedPower"),
                        tss=act.get("trainingStressScore"),
                    )
                )
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping activity entry: %s", exc)
                continue

        return results

    def get_training_readiness(self, date_str: str) -> TrainingReadinessData:
        """Fetch training readiness score for a given date (YYYY-MM-DD)."""
        raw = self._call_api(self.client.get_training_readiness, date_str)
        logger.debug("Raw training readiness data: %s", raw)

        if not raw:
            return TrainingReadinessData(
                date=date.fromisoformat(date_str),
                score=0,
                level="UNKNOWN",
            )

        # The response may be a list with one entry or a dict
        entry = raw[0] if isinstance(raw, list) and raw else raw if isinstance(raw, dict) else {}

        return TrainingReadinessData(
            date=date.fromisoformat(date_str),
            score=int(entry.get("score", 0) or entry.get("readinessScore", 0) or 0),
            level=str(entry.get("level") or entry.get("readinessLevel") or "UNKNOWN"),
            hrv_status=entry.get("hrvStatus") or entry.get("hrvFeedback"),
            sleep_score=entry.get("sleepScore") or entry.get("sleepQualityScore"),
            recovery_time_hours=_minutes_to_hours(entry.get("recoveryTimeInMinutes")),
        )

    def get_training_status(self, date_str: str) -> TrainingStatusData:
        """Fetch training status for a given date (YYYY-MM-DD)."""
        raw = self._call_api(self.client.get_training_status, date_str)
        logger.debug("Raw training status data: %s", raw)

        if not raw:
            return TrainingStatusData(
                date=date.fromisoformat(date_str),
                training_status="UNKNOWN",
            )

        # May be a list or dict
        entry = raw[0] if isinstance(raw, list) and raw else raw if isinstance(raw, dict) else {}

        return TrainingStatusData(
            date=date.fromisoformat(date_str),
            training_status=str(entry.get("trainingStatus") or entry.get("currentDayTrainingStatus") or "UNKNOWN"),
            vo2_max_run=entry.get("vo2MaxPreciseValue") or entry.get("vo2MaxRun"),
            vo2_max_bike=entry.get("vo2MaxCyclingPreciseValue") or entry.get("vo2MaxBike"),
            load_focus=entry.get("loadFocus") or entry.get("trainingLoadFocus"),
        )

    # ------------------------------------------------------------------
    # Additional data methods
    # ------------------------------------------------------------------

    def get_activities_by_date(self, start: str, end: str) -> list[Activity]:
        """Fetch activities within a date range (YYYY-MM-DD).

        More precise than get_activities() for syncing specific periods.
        """
        raw = self._call_api(self.client.get_activities_by_date, start, end)
        logger.debug("Fetched %d activities by date", len(raw) if raw else 0)

        results: list[Activity] = []
        if not raw:
            return results

        for act in raw:
            try:
                activity_type = act.get("activityType", {})
                type_key = activity_type.get("typeKey", "") if isinstance(activity_type, dict) else str(activity_type)

                start_time_str = act.get("startTimeLocal") or act.get("startTimeGMT", "")
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    start_time = datetime.now()

                results.append(
                    Activity(
                        activity_id=int(act.get("activityId", 0)),
                        sport=_map_sport(type_key),
                        start_time=start_time,
                        duration_seconds=int(act.get("duration", 0) or 0),
                        distance_meters=act.get("distance"),
                        avg_hr=act.get("averageHR"),
                        max_hr=act.get("maxHR"),
                        avg_power=act.get("averagePower"),
                        normalized_power=act.get("normPower") or act.get("normalizedPower"),
                        tss=act.get("trainingStressScore"),
                    )
                )
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping activity entry: %s", exc)
                continue

        return results

    def get_heart_rates(self, date_str: str) -> HeartRateData:
        """Fetch daily heart rate summary for a given date (YYYY-MM-DD)."""
        raw = self._call_api(self.client.get_heart_rates, date_str)
        logger.debug("Raw heart rates data keys: %s", list(raw.keys()) if raw else None)

        if not raw:
            return HeartRateData(
                date=date.fromisoformat(date_str),
                resting_hr=0.0,
                max_hr=0.0,
                min_hr=0.0,
            )

        return HeartRateData(
            date=date.fromisoformat(date_str),
            resting_hr=float(raw.get("restingHeartRate", 0) or 0),
            max_hr=float(raw.get("maxHeartRate", 0) or 0),
            min_hr=float(raw.get("minHeartRate", 0) or 0),
            avg_hr=raw.get("averageHeartRate"),
        )

    def get_stats(self, date_str: str) -> DailyStats:
        """Fetch daily summary stats (steps, calories, distance, etc.)."""
        raw = self._call_api(self.client.get_stats, date_str)
        logger.debug("Raw stats data keys: %s", list(raw.keys()) if raw else None)

        if not raw:
            return DailyStats(
                date=date.fromisoformat(date_str),
                total_steps=0,
                total_distance_meters=0.0,
                active_calories=0,
                total_calories=0,
                intensity_minutes=0,
                floors_climbed=0,
            )

        return DailyStats(
            date=date.fromisoformat(date_str),
            total_steps=int(raw.get("totalSteps", 0) or 0),
            total_distance_meters=float(raw.get("totalDistanceMeters", 0) or 0),
            active_calories=int(raw.get("activeKilocalories", 0) or 0),
            total_calories=int(raw.get("totalKilocalories", 0) or 0),
            intensity_minutes=int(
                (raw.get("moderateIntensityMinutes", 0) or 0) + (raw.get("vigorousIntensityMinutes", 0) or 0)
            ),
            floors_climbed=int(raw.get("floorsAscended", 0) or 0),
        )

    def get_body_composition(self, start: str, end: str) -> list[BodyCompositionData]:
        """Fetch body composition / weigh-in data for a date range."""
        raw = self._call_api(self.client.get_body_composition, start, end)
        logger.debug("Raw body composition data: %s", type(raw))

        results: list[BodyCompositionData] = []
        if not raw:
            return results

        entries = raw.get("dateWeightList", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []

        for entry in entries:
            try:
                entry_date_str = entry.get("calendarDate") or entry.get("date")
                if not entry_date_str:
                    continue

                weight_grams = entry.get("weight")
                weight_kg = round(weight_grams / 1000.0, 2) if weight_grams else None

                results.append(
                    BodyCompositionData(
                        date=date.fromisoformat(entry_date_str[:10]),
                        weight_kg=weight_kg,
                        bmi=entry.get("bmi"),
                        body_fat_pct=entry.get("bodyFat"),
                        muscle_mass_kg=entry.get("muscleMass"),
                        bone_mass_kg=entry.get("boneMass"),
                        body_water_pct=entry.get("bodyWater"),
                    )
                )
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping body composition entry: %s", exc)
                continue

        return results

    def get_respiration(self, date_str: str) -> RespirationData:
        """Fetch respiration / breathing rate data for a given date."""
        raw = self._call_api(self.client.get_respiration_data, date_str)
        logger.debug("Raw respiration data: %s", raw)

        if not raw:
            return RespirationData(
                date=date.fromisoformat(date_str),
                avg_breathing_rate=0.0,
            )

        return RespirationData(
            date=date.fromisoformat(date_str),
            avg_breathing_rate=float(
                raw.get("avgWakingRespirationValue", 0) or raw.get("avgSleepRespirationValue", 0) or 0
            ),
            lowest_breathing_rate=raw.get("lowestRespirationValue"),
            highest_breathing_rate=raw.get("highestRespirationValue"),
        )

    def get_spo2(self, date_str: str) -> SpO2Data:
        """Fetch SpO2 (blood oxygen) data for a given date."""
        raw = self._call_api(self.client.get_spo2_data, date_str)
        logger.debug("Raw SpO2 data: %s", raw)

        if not raw:
            return SpO2Data(date=date.fromisoformat(date_str), avg_spo2=0.0)

        return SpO2Data(
            date=date.fromisoformat(date_str),
            avg_spo2=float(raw.get("averageSpO2", 0) or raw.get("averageSPO2", 0) or 0),
            lowest_spo2=raw.get("lowestSpO2") or raw.get("lowestSPO2"),
        )

    def get_max_metrics(self, date_str: str) -> MaxMetricsData:
        """Fetch VO2max and other max metrics for a given date."""
        raw = self._call_api(self.client.get_max_metrics, date_str)
        logger.debug("Raw max metrics data: %s", raw)

        if not raw:
            return MaxMetricsData(date=date.fromisoformat(date_str))

        # Response can be a list of metric groups or a dict
        entries = raw if isinstance(raw, list) else raw.get("maxMetricsEntries", [raw])

        vo2_run: float | None = None
        vo2_bike: float | None = None

        for entry in entries:
            sport = entry.get("sport", "").lower() if isinstance(entry, dict) else ""
            vo2 = (
                entry.get("vo2MaxPreciseValue") or entry.get("generic", {}).get("vo2MaxPreciseValue")
                if isinstance(entry, dict)
                else None
            )
            if "run" in sport and vo2:
                vo2_run = float(vo2)
            elif "cycl" in sport and vo2:
                vo2_bike = float(vo2)
            elif vo2 and vo2_run is None:
                vo2_run = float(vo2)

        return MaxMetricsData(
            date=date.fromisoformat(date_str),
            vo2_max_run=vo2_run,
            vo2_max_bike=vo2_bike,
        )

    def get_race_predictions(self) -> list[RacePrediction]:
        """Fetch predicted race times."""
        raw = self._call_api(self.client.get_race_predictions)
        logger.debug("Raw race predictions data: %s", raw)

        results: list[RacePrediction] = []
        if not raw:
            return results

        entries = raw if isinstance(raw, list) else raw.get("racePredictions", [])

        for entry in entries:
            try:
                name = entry.get("raceName") or entry.get("distanceName") or entry.get("name", "Unknown")
                time_sec = entry.get("predictedTime") or entry.get("predictedTimeInSeconds")
                if time_sec is not None:
                    results.append(
                        RacePrediction(
                            distance_name=str(name),
                            predicted_time_seconds=float(time_sec),
                        )
                    )
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping race prediction: %s", exc)
                continue

        return results

    def get_endurance_score(self, date_str: str) -> EnduranceScoreData:
        """Fetch endurance score for a given date."""
        raw = self._call_api(self.client.get_endurance_score, date_str)
        logger.debug("Raw endurance score data: %s", raw)

        if not raw:
            return EnduranceScoreData(date=date.fromisoformat(date_str), overall_score=0)

        return EnduranceScoreData(
            date=date.fromisoformat(date_str),
            overall_score=int(raw.get("overallScore", 0) or raw.get("enduranceScore", 0) or 0),
            rating=raw.get("enduranceScoreLevel") or raw.get("rating"),
        )

    def get_lactate_threshold(self) -> LactateThresholdData:
        """Fetch the athlete's current lactate threshold values."""
        raw = self._call_api(self.client.get_lactate_threshold)
        logger.debug("Raw lactate threshold data: %s", raw)

        if not raw:
            return LactateThresholdData()

        return LactateThresholdData(
            heart_rate=raw.get("lactateThresholdHeartRate") or raw.get("heartRate"),
            speed=raw.get("lactateThresholdSpeed") or raw.get("speed"),
        )

    def get_cycling_ftp(self) -> CyclingFTPData:
        """Fetch the athlete's current cycling FTP."""
        raw = self._call_api(self.client.get_cycling_ftp)
        logger.debug("Raw cycling FTP data: %s", raw)

        if not raw:
            return CyclingFTPData()

        ftp_date_str = raw.get("ftpDate") or raw.get("testDate")
        ftp_date_val = None
        if ftp_date_str:
            try:
                ftp_date_val = date.fromisoformat(ftp_date_str[:10])
            except (ValueError, TypeError):
                pass

        return CyclingFTPData(
            ftp=raw.get("ftpValue") or raw.get("ftp"),
            ftp_date=ftp_date_val,
        )
