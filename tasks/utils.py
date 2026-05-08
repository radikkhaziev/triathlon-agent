from datetime import date

from data.db import (
    ActivityDetail,
    AiWorkout,
    AthleteGoal,
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

# Test cadence by training phase (RAMP_TEST_BIKE_SPEC §9):
#   - Peak/taper (≤14 days to A-race): NO testing — high stress + tapering
#     volume produces unreliable α1 fits and disrupts race-week routine.
#   - Base phase (≤56 days, 8 weeks): test every 8 weeks. Recovery/aerobic
#     emphasis means slower threshold drift; less frequent re-calibration.
#   - Build phase (>56 days from A-race, or no A-race): test every 6 weeks.
#     Active progression — thresholds shift faster, tighter cadence keeps
#     zones honest.
#   - No active goal: 30-day default (current behavior, less informed).
PEAK_TAPER_DAYS = 14
BASE_PHASE_CADENCE_DAYS = 56
BUILD_PHASE_CADENCE_DAYS = 42
DEFAULT_CADENCE_DAYS = 30


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

    def _staleness_threshold_days(self) -> int | None:
        """Days-since-last-ramp threshold derived from training phase.

        Returns ``None`` to suppress all ramp suggestions (peak/taper week).
        See module-level constants for the cadence schedule.

        Picks the **nearest upcoming** active goal — not just RACE_A — so an
        athlete with «RACE_A in 200d + RACE_B in 7d» correctly enters
        peak/taper for the close-by B-race rather than build phase for the
        far-future A. ``AthleteGoal.get_active`` returns RACE_A first by
        category which would miss this.
        """
        goals = AthleteGoal.get_all(user_id=self.user.id)
        today = local_today()
        upcoming = [g for g in goals if g.is_active and g.event_date and g.event_date >= today]
        if not upcoming:
            return DEFAULT_CADENCE_DAYS
        nearest = min(upcoming, key=lambda g: g.event_date)
        days_to_race = (nearest.event_date - today).days
        if days_to_race <= PEAK_TAPER_DAYS:
            return None  # suppress
        if days_to_race <= BASE_PHASE_CADENCE_DAYS:
            return BASE_PHASE_CADENCE_DAYS
        return BUILD_PHASE_CADENCE_DAYS

    @property
    def is_test_needed(self) -> bool:
        # Need wellness with valid CTL/ATL — without it tsb is meaningless.
        if not self._has_data:
            return False
        # Deep fatigue distorts DFA a1 — HRVT2 detection becomes unreliable
        # regardless of whether this is a first test or a re-test.
        if self.tsb <= -10:
            return False

        upcoming = AiWorkout.get_upcoming(user_id=self.user.id, days_ahead=14)
        if any("Ramp Test" in (w.name or "") for w in upcoming):
            return False  # already planned

        cadence_days = self._staleness_threshold_days()
        if cadence_days is None:
            return False  # peak/taper — never suggest

        # Pick candidate sport. Two-pass priority:
        #   1. Bootstrap (`no_data`) — first-time test wins. Without a baseline
        #      drift detection is moot; the bootstrap path also relaxes the
        #      recovery gate (newcomer has no prior fit to «protect»).
        #   2. Stale (`days_since > cadence_days`) — among stale sports, pick
        #      the one whose threshold is furthest out of date. Cadence varies
        #      by training phase (build/base) — see _staleness_threshold_days.
        # Tie-break: declared sport-list order (Run preferred when equal).
        freshness: dict[str, ThresholdFreshnessDTO] = {
            sport: User.get_threshold_freshness(user_id=self.user.id, sport=sport) for sport in self.sports
        }

        bootstrap_sports = [s for s in self.sports if freshness[s].status == "no_data"]
        is_bootstrap = bool(bootstrap_sports)

        candidate_sport: str | None = None
        candidate_days_since: int | None = None

        if is_bootstrap:
            candidate_sport = bootstrap_sports[0]
        else:
            stale = [(s, freshness[s].days_since) for s in self.sports if (freshness[s].days_since or 0) > cadence_days]
            if not stale:
                return False
            # max() picks largest days_since; ties broken by smaller sports-list index
            # via negative-index secondary key (higher key wins → smaller index wins).
            candidate_sport, candidate_days_since = max(stale, key=lambda x: (x[1], -self.sports.index(x[0])))

        if not is_bootstrap:
            # Ramp test stresses the system; low recovery noises the HRV signal
            # and the linear fit collapses (R² drops). Need a clean baseline.
            recovery = self.wellness.recovery_score
            if recovery is None or recovery < 70:
                return False

        self.suggested_sport = candidate_sport
        self.days_since = candidate_days_since
        return True

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
        bike_ftp: float | None = None
        if sport == "Run":
            run_settings = AthleteSettings.get(self.user.id, sport)
            threshold_pace = run_settings.threshold_pace if run_settings else None
        elif sport == "Ride":
            ride_settings = AthleteSettings.get(self.user.id, sport)
            bike_ftp = float(ride_settings.ftp) if ride_settings and ride_settings.ftp else None

        workout: PlannedWorkoutDTO = create_ramp_test(
            sport, dt, freshness.days_since, threshold_pace=threshold_pace, bike_ftp=bike_ftp
        )

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
