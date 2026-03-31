from datetime import date

from data.db import AiWorkout, ThresholdFreshnessDTO, User, UserDTO, WellnessPostDTO
from data.intervals.dto import PlannedWorkoutDTO
from data.ramp_tests import create_ramp_test

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
            sports = ["Run"]
        # ("Ride", "Run")
        self.sports = sports
        self.suggested_sport = None
        self.days_since = None

    @property
    def tsb(self) -> float:
        if not self.wellness:
            return 0
        if self.wellness.ctl is None or self.wellness.atl is None:
            return 0
        return self.wellness.ctl - self.wellness.atl

    @property
    def is_test_needed(self) -> bool:
        if self.tsb == 0:
            return False  # no data, don't suggest

        upcoming = AiWorkout.get_upcoming(user_id=self.user.id, days_ahead=14)
        if any("Ramp Test" in (w.name or "") for w in upcoming):
            return False  # already planned

        for sport in self.sports:
            data: ThresholdFreshnessDTO = User.get_threshold_freshness(user_id=self.user.id, sport=sport)
            if data.status == "no_data":
                self.suggested_sport = sport  # never tested → suggest
                return True  # never tested → suggest
            if data.days_since and data.days_since > 30:
                self.suggested_sport = sport  # stale → suggest
                self.days_since = data.days_since

                return True  # stale → suggest
        return False

    def plan_ramp(self, sport: str | None = None, dt: date | None = None) -> str:
        """Create and push a ramp test workout. Returns status message."""
        if sport is None:
            sport = self.suggested_sport or "Run"

        if dt is None:
            dt = date.today()

        upcoming = AiWorkout.get_upcoming(user_id=self.user.id, days_ahead=14)
        if any("Ramp Test" in (w.name or "") for w in upcoming):
            return f"Ramp Test ({sport}) уже запланирован"

        freshness: ThresholdFreshnessDTO = User.get_threshold_freshness(user_id=self.user.id, sport=sport)
        workout: PlannedWorkoutDTO = create_ramp_test(sport, dt, freshness.days_since)

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
