from datetime import date

from data.db import (
    ActivityDetail,
    AiWorkout,
    AthleteSettings,
    ThresholdFreshnessDTO,
    User,
    UserDTO,
    WellnessPostDTO,
    get_sync_session,
)
from data.intervals.dto import PlannedWorkoutDTO
from data.ramp_tests import create_ramp_test
from tasks.dto import local_today

from .actors.workout import actor_push_workout


class RampTrainingSuggestion:

    def __init__(
        self,
        user: UserDTO,
        wellness: WellnessPostDTO | None,
        sports: list[str] | None = None,
    ):
        self.user = user
        self.wellness = wellness
        if sports is None:
            sports = ["Run", "Ride"]
        self.sports = sports
        self.suggested_sport = None
        self.days_since = None

    @property
    def _has_data(self) -> bool:
        return bool(self.wellness and self.wellness.ctl is not None and self.wellness.atl is not None)

    @property
    def tsb(self) -> float:
        if not self._has_data:
            return 0
        return self.wellness.ctl - self.wellness.atl

    @property
    def is_test_needed(self) -> bool:
        # Need wellness with valid CTL/ATL — without it tsb is meaningless.
        if not self._has_data:
            return False
        # Deep fatigue distorts DFA a1 — HRVT2 detection becomes unreliable.
        if self.tsb <= -10:
            return False
        # Ramp test stresses the system; low recovery noises the HRV signal
        # and the linear fit collapses (R² drops). Need a clean baseline.
        recovery = self.wellness.recovery_score
        if recovery is None or recovery < 70:
            return False

        upcoming = AiWorkout.get_upcoming(user_id=self.user.id, days_ahead=14)
        if any("Ramp Test" in (w.name or "") for w in upcoming):
            return False  # already planned

        for sport in self.sports:
            data: ThresholdFreshnessDTO = User.get_threshold_freshness(user_id=self.user.id, sport=sport)
            if data.status == "no_data":
                self.suggested_sport = sport  # never tested → suggest
                return True
            if data.days_since and data.days_since > 30:
                self.suggested_sport = sport  # stale → suggest
                self.days_since = data.days_since
                return True
        return False

    def plan_ramp(self, sport: str | None = None, dt: date | None = None) -> str:
        """Create and push a ramp test workout. Returns status message.

        Calls @dual ORM methods (User.get_threshold_freshness, AiWorkout.get_upcoming) —
        from an async context wrap with ``asyncio.to_thread`` so @dual dispatches to
        the sync branch (issue #277).
        """
        if sport is None:
            sport = self.suggested_sport or "Run"

        if dt is None:
            dt = local_today()

        upcoming = AiWorkout.get_upcoming(user_id=self.user.id, days_ahead=14)
        if any("Ramp Test" in (w.name or "") for w in upcoming):
            return f"Ramp Test ({sport}) уже запланирован"

        freshness: ThresholdFreshnessDTO = User.get_threshold_freshness(user_id=self.user.id, sport=sport)

        threshold_pace: float | None = None
        if sport == "Run":
            run_settings = AthleteSettings.get(self.user.id, sport)
            threshold_pace = run_settings.threshold_pace if run_settings else None

        workout: PlannedWorkoutDTO = create_ramp_test(sport, dt, freshness.days_since, threshold_pace=threshold_pace)

        actor_push_workout.send(
            user=self.user,
            workout=workout,
            dt=dt,
        )
        return f"Ramp Test ({sport}) поставлен в очередь на {dt.strftime('%d.%m')}"


def detect_compliance(log, activity) -> str:
    """Detect which plan variant the athlete followed.

    Returns: "followed_original" | "followed_adapted" | "followed_ai" | "modified" | "unplanned"
    """
    if log.source == "none":
        return "unplanned"

    actual_dur = activity.moving_time or 0

    # Check adapted match
    if log.adapted_duration_sec:
        adapted_ratio = actual_dur / log.adapted_duration_sec if log.adapted_duration_sec else 0
        if 0.7 <= adapted_ratio <= 1.3:
            return "followed_adapted"

    # Check original match
    if log.original_duration_sec:
        original_ratio = actual_dur / log.original_duration_sec if log.original_duration_sec else 0
        if 0.7 <= original_ratio <= 1.3:
            if log.source == "ai":
                return "followed_ai"
            return "followed_original"

    return "modified"


def compute_max_zone_sync(activity_id: int | str, sport: str | None = None) -> str | None:
    """Sync version: determine the zone where the athlete spent the most time."""
    with get_sync_session() as session:
        detail = session.get(ActivityDetail, activity_id)
    if not detail:
        return None

    zones = None
    if sport == "Ride" and detail.power_zone_times:
        zones = detail.power_zone_times
    elif sport == "Swim" and detail.pace_zone_times:
        zones = detail.pace_zone_times
    if not zones and detail.hr_zone_times:
        zones = detail.hr_zone_times
    if not zones:
        return None

    if len(zones) >= 6:
        zone_values = zones[1:6]
    elif len(zones) == 5:
        zone_values = zones[:5]
    else:
        return None

    if all(v == 0 for v in zone_values):
        return None

    max_idx = min(range(len(zone_values)), key=lambda i: (-zone_values[i], i))
    return f"Z{max_idx + 1}"
