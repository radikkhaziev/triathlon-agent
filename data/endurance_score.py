"""Endurance Score — composite endurance state across all sports.

Pure formulas, no DB IO. Caller passes a list of activities + wellness
snapshots + thresholds + reference date; gets back the composite score,
per-component breakdown, per-sport decomposition, current zone, and a
milestone badge (if any rule triggered).

Spec: ``docs/ENDURANCE_SCORE_SPEC.md``.

The metric answers «как я сейчас в тренировочном цикле, по всем спортам сразу»
— a single 0..8000 number with a state-band label (Растренирован → На пике).
Mirrors Garmin Endurance Score structure (VO2max-anchored + training-history
bonuses), with our own weights since Firstbeat's formula is closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean, pstdev
from typing import Sequence

# ─── VO2max formulas ────────────────────────────────────────────────


# Storer (Storer TW et al. 1990) — bike VO2max from FTP, weight, age.
def vo2max_bike_storer(ftp_w: float, weight_kg: float, age: int) -> float:
    return (10.51 * ftp_w + 6.35 * weight_kg - 10.49 * age + 519.3) / weight_kg


# Daniels VDOT — run VO2max from threshold pace (sec/km).
# Threshold ≈ 86% vVO2max (Daniels' Running Formula, 3rd ed.), and we apply
# ACSM running equation `VO2 = 0.2 * v_m_per_min + 3.5`.
def vo2max_run_daniels(threshold_pace_sec_per_km: float) -> float:
    vlt_kmh = 3600.0 / threshold_pace_sec_per_km
    vvo2max_kmh = vlt_kmh / 0.86
    vvo2max_m_per_min = vvo2max_kmh * 1000.0 / 60.0
    return 0.2 * vvo2max_m_per_min + 3.5


# Default for AG-male 40-44 when no thresholds known (rough Cooper median).
DEFAULT_VO2MAX = 40.0

# ─── Bonuses configuration ─────────────────────────────────────────

LONG_TERM_TARGET_CTL = 80.0  # CTL above which LongTerm caps at 1000
LONG_TERM_MAX = 1000.0

RECENT_RAMP_CAP_TSS_PER_WEEK = 8.0  # ramp at which RecentBonus saturates
RECENT_MAX = 200.0

DURATION_SHARE_CAP = 0.5  # share of TSS from long sessions at which bonus saturates
DURATION_MAX = 800.0
# Effective max bonus = DURATION_SHARE_CAP * DURATION_MAX = 0.5 * 800 = 400 (spec §3).

LONG_SESSION_THRESHOLDS_SEC: dict[str, int] = {
    "Run": 90 * 60,
    "Ride": 120 * 60,
    "Swim": 60 * 60,
}
Z2PLUS_MIN_SHARE = 0.70  # ≥70% time in Z2+ to count as long-quality session

CONSISTENCY_WINDOW_WEEKS = 8
CONSISTENCY_MIN_WEEKS_WITH_DATA = 4
CONSISTENCY_MAX = 200.0

RECOVERY_DFA_GREEN_THRESHOLD = 0.75
RECOVERY_MIN_VALID_SESSIONS = 3
RECOVERY_MAX = 200.0
# Same per-sport duration floors as Duration — but lighter (Ride ≥60min vs ≥120min,
# Run ≥45min vs ≥90min). DFA-α1 mean stabilises around the 40-50min mark, so we
# accept moderate sessions for recovery-state classification.
RECOVERY_VALID_THRESHOLDS_SEC: dict[str, int] = {
    "Ride": 60 * 60,
    "Run": 45 * 60,
}

# ─── Zones (5-band, training-state framework) ───────────────────────

ENDURANCE_MAX = 8000


@dataclass(frozen=True)
class EnduranceZone:
    id: str
    label_ru: str
    label_en: str
    min_score: int
    color: str


# Zone thresholds + colors — single source of truth on the backend, but
# duplicated for FE rendering at `webapp/src/components/halo/EnduranceScore.tsx`
# (`ENDURANCE_ZONES`) and in the spec `docs/ENDURANCE_SCORE_SPEC.md` §3.8. When
# tuning thresholds or colors, sync all three so the FE gauge zone matches the
# `current.zone` field the API returns. FE labels live in
# `webapp/src/i18n/{en,ru}.json:load.endurance.zone.*` — don't add another copy.
ENDURANCE_ZONES: tuple[EnduranceZone, ...] = (
    EnduranceZone("detrained", "Растренирован", "Detrained", 0, "#ef4444"),
    EnduranceZone("recovering", "Восстанавливаюсь", "Recovering", 3000, "#f97316"),
    EnduranceZone("maintaining", "Поддерживаю", "Maintaining", 4500, "#eab308"),
    EnduranceZone("productive", "Развиваюсь", "Productive", 5500, "#22c55e"),
    EnduranceZone("peaking", "На пике", "Peaking", 6500, "#3b82f6"),
)


def classify_zone(score: int) -> EnduranceZone:
    """Return the last zone whose ``min_score`` is ≤ ``score``."""
    current = ENDURANCE_ZONES[0]
    for z in ENDURANCE_ZONES:
        if score >= z.min_score:
            current = z
    return current


# ─── Inputs ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EnduranceActivity:
    """Minimal activity projection for ES calculation."""

    dt: date
    type: str  # "Run" | "Ride" | "Swim" | "Other"
    moving_time_sec: int
    training_load: float | None
    z2plus_time_pct: float | None  # fraction 0..1, None if no zone-time data
    dfa_a1_mean: float | None


@dataclass(frozen=True)
class WellnessSnapshot:
    dt: date
    ctl: float | None
    ramp_rate: float | None
    sport_ctl: dict[str, float] = field(default_factory=dict)  # keys: "Ride"/"Run"/"Swim"


@dataclass(frozen=True)
class AthleteProfile:
    age: int | None
    weight_kg: float | None
    ftp_w: float | None  # bike FTP from athlete_settings.power_zones_bike.ftp
    threshold_pace_sec_per_km: float | None  # run threshold from pace_zones_run


# ─── Results ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EnduranceComponents:
    base: int
    long_term: int
    recent: int
    duration: int
    consistency: int
    recovery: int


@dataclass(frozen=True)
class PerSport:
    name: str  # "Bike" | "Run" | "Swim" | "Other"
    pct: float  # share of total CTL, 0..100
    sub_score: int | None  # per-sport ES sub-score, None if insufficient data


@dataclass(frozen=True)
class Badge:
    id: str  # "new_zone" | "best_90d" | "top_10_percentile" | "in_form_3m"
    label: str
    icon: str


@dataclass(frozen=True)
class EnduranceScoreResult:
    score: int  # composite ES
    zone_id: str  # zone.id, lookup via classify_zone(score)
    vo2max_composite: float
    components: EnduranceComponents
    per_sport: list[PerSport]
    badge: Badge | None
    # Diagnostic — included in components_json for post-hoc debugging.
    insufficient_data: bool = False
    insufficient_reason: str | None = None


# ─── Component formulas ─────────────────────────────────────────────


def vo2max_composite(
    athlete: AthleteProfile,
    sport_ctl: dict[str, float],
) -> float:
    """Compute composite VO2max weighted by sport-CTL share.

    Per-sport VO2max:
      · Bike — Storer (needs ftp + weight + age)
      · Run  — Daniels (needs threshold_pace)
      · Swim — proxy = run (no industry-standard formula from swim pace)

    Fallbacks:
      · No FTP → bike = run (if available) else DEFAULT_VO2MAX
      · No threshold_pace → run = bike (if available) else DEFAULT_VO2MAX
      · Neither — composite returns DEFAULT_VO2MAX (caller may flag insufficient).
    """
    age = athlete.age or 40
    weight_kg = athlete.weight_kg or 75.0

    vo2_bike = vo2max_bike_storer(athlete.ftp_w, weight_kg, age) if athlete.ftp_w is not None else None
    vo2_run = (
        vo2max_run_daniels(athlete.threshold_pace_sec_per_km) if athlete.threshold_pace_sec_per_km is not None else None
    )

    if vo2_bike is None and vo2_run is None:
        return DEFAULT_VO2MAX
    if vo2_bike is None:
        vo2_bike = vo2_run
    if vo2_run is None:
        vo2_run = vo2_bike
    vo2_swim = vo2_run  # proxy

    total_ctl = sum(v for v in sport_ctl.values() if v is not None) or 0.0
    if total_ctl <= 0:
        # No sport-CTL data — average the three with equal weight.
        return (vo2_bike + vo2_run + vo2_swim) / 3.0

    share_ride = (sport_ctl.get("Ride") or 0.0) / total_ctl
    share_run = (sport_ctl.get("Run") or 0.0) / total_ctl
    share_swim = (sport_ctl.get("Swim") or 0.0) / total_ctl
    return share_ride * vo2_bike + share_run * vo2_run + share_swim * vo2_swim


def long_term_bonus(ctl_avg_8w: float) -> float:
    return min(max(ctl_avg_8w, 0.0) / LONG_TERM_TARGET_CTL, 1.0) * LONG_TERM_MAX


def recent_bonus(ramp_rate: float | None) -> float:
    if ramp_rate is None:
        return 0.0
    return min(max(ramp_rate, 0.0) / RECENT_RAMP_CAP_TSS_PER_WEEK, 1.0) * RECENT_MAX


def duration_bonus(activities_28d: Sequence[EnduranceActivity]) -> float:
    """Bonus from long quality sessions in last 28 days.

    Long = type-specific duration floor (Run ≥90min, Ride ≥120min, Swim ≥60min).
    Quality = ≥70% time in Z2+ (filters trash-rides). If z2plus_time_pct is
    None (no zone-time data on activity), session still counts — we don't have
    a basis to penalise, but in practice activity_details fills this in.
    """
    long_tss = 0.0
    total_tss = 0.0
    for a in activities_28d:
        tl = a.training_load or 0.0
        total_tss += tl
        threshold = LONG_SESSION_THRESHOLDS_SEC.get(a.type)
        if threshold is None:
            continue
        if a.moving_time_sec < threshold:
            continue
        if a.z2plus_time_pct is not None and a.z2plus_time_pct < Z2PLUS_MIN_SHARE:
            continue
        long_tss += tl
    if total_tss <= 0:
        return 0.0
    share = long_tss / total_tss
    return min(share, DURATION_SHARE_CAP) * DURATION_MAX


def consistency_bonus(weekly_tss: Sequence[float]) -> float:
    """Bonus from weekly-TSS consistency over last 8 weeks.

    ``weekly_tss`` is expected to be 0..8 floats (one per week, may include
    zeros for empty weeks — those are skipped). CV (coefficient of variation)
    of non-empty weeks determines the bonus: low CV = consistent = high bonus.
    """
    non_empty = [w for w in weekly_tss if w > 0]
    if len(non_empty) < CONSISTENCY_MIN_WEEKS_WITH_DATA:
        return 0.0
    m = mean(non_empty)
    if m <= 0:
        return 0.0
    # Use population stdev (pstdev) — these are observed values, not a sample.
    cv = pstdev(non_empty) / m
    return min(max(1.0 - cv, 0.0), 1.0) * CONSISTENCY_MAX


def recovery_bonus(activities_28d: Sequence[EnduranceActivity]) -> float:
    """Bonus from DFA-α1 stability across long-enough sessions in last 28 days.

    Validity per spec §3.6: Ride ≥60min OR Run ≥45min, dfa_a1_mean recorded.
    Green = mean ≥0.75 (aerobic state). If <3 valid sessions — bonus = 0
    (insufficient data clamp).
    """
    valid: list[float] = []
    for a in activities_28d:
        if a.dfa_a1_mean is None:
            continue
        threshold = RECOVERY_VALID_THRESHOLDS_SEC.get(a.type)
        if threshold is None:
            continue
        if a.moving_time_sec < threshold:
            continue
        valid.append(a.dfa_a1_mean)
    if len(valid) < RECOVERY_MIN_VALID_SESSIONS:
        return 0.0
    green = sum(1 for v in valid if v >= RECOVERY_DFA_GREEN_THRESHOLD)
    share_green = green / len(valid)
    return share_green * RECOVERY_MAX


# ─── Per-sport decomposition ────────────────────────────────────────


def per_sport_breakdown(sport_ctl: dict[str, float]) -> list[PerSport]:
    """Render the 4-row breakdown for the card (Bike / Run / Swim / Other).

    Percentages are share of total CTL. ``Other`` is the gap (e.g., strength,
    walks) — we expose it but don't compute its sub-score.

    Sub-scores are intentionally None in Phase 1 — the card doesn't render
    them, and computing them properly requires per-sport activity/wellness
    queries that aren't worth the cost for the Phase 1 endpoint.
    """
    total = sum(v for v in sport_ctl.values() if v) or 0.0
    if total <= 0:
        return []
    parts: list[PerSport] = []
    accounted = 0.0
    for key, display in (("Ride", "Bike"), ("Run", "Run"), ("Swim", "Swim")):
        v = sport_ctl.get(key) or 0.0
        pct = (v / total) * 100.0
        accounted += pct
        parts.append(PerSport(name=display, pct=round(pct, 1), sub_score=None))
    other_pct = max(0.0, 100.0 - accounted)
    if other_pct > 0.05:
        parts.append(PerSport(name="Other", pct=round(other_pct, 1), sub_score=None))
    return parts


# ─── Badge rule engine ──────────────────────────────────────────────


def _percentile(values: Sequence[float], q: float) -> float:
    """``q``-percentile (0..100) of a list, linear interpolation. Safe for empty list."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (q / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def compute_badge(
    score_today: int,
    zone_today_id: str,
    *,
    zone_yesterday_id: str | None,
    scores_last_90d: Sequence[int],
    scores_last_365d: Sequence[int],
    zones_last_84d: Sequence[str],
    recent_badge_ids: Sequence[str] = (),
) -> Badge | None:
    """Badge rule engine — first matching rule wins.

    Priority order matches §3.9:
      1. Zone breakthrough  (most emotional moment, lowest cooldown)
      2. Best in 90 days
      3. Top 10% of weeks (last 365d history)
      4. 3 months in form (84 days in productive/peaking)

    Cooldown (spec §3.9): a rule is skipped if its badge id appears in
    ``recent_badge_ids`` — the caller supplies the previous N days' fired
    badges (1 day window for #1, 7 days for #2/#3/#4). Pass an empty tuple
    to disable cooldown (Phase-1 fallback path when there's no history).
    """
    zone_order = {z.id: i for i, z in enumerate(ENDURANCE_ZONES)}
    today_idx = zone_order.get(zone_today_id, 0)

    # #1 — Zone breakthrough (zone_yesterday → zone_today moved up)
    if zone_yesterday_id is not None and "new_zone" not in recent_badge_ids:
        yesterday_idx = zone_order.get(zone_yesterday_id, 0)
        if today_idx > yesterday_idx:
            label_ru = next(z.label_ru for z in ENDURANCE_ZONES if z.id == zone_today_id)
            return Badge(id="new_zone", label=f"Новая зона: {label_ru}", icon="✨")

    # #2 — Best in 90 days
    if (
        scores_last_90d
        and "best_90d" not in recent_badge_ids
        and score_today >= max(scores_last_90d)
        # Require at least 30 days of history (so a brand-new user with 5 days
        # of data doesn't trigger "best in 90 days" trivially).
        and len(scores_last_90d) >= 30
    ):
        return Badge(id="best_90d", label="Лучший за 3 месяца", icon="🏆")

    # #3 — Top 10% of last-year history. Originally required 365 consecutive
    # daily rows but a single missed cron-day blocks the badge forever.
    # Relaxed to «≥90 datapoints in the 365d window» — gives the same
    # statistical power (p90 from 90 samples is reliable) without the
    # all-or-nothing fragility.
    if scores_last_365d and "top_10_percentile" not in recent_badge_ids and len(scores_last_365d) >= 90:
        p90 = _percentile(list(scores_last_365d), 90.0)
        if score_today >= p90:
            return Badge(id="top_10_percentile", label="Топ 10% твоих дней", icon="🔥")

    # #4 — 3 months in productive/peaking
    if zones_last_84d and "in_form_3m" not in recent_badge_ids and len(zones_last_84d) >= 84:
        in_form = {"productive", "peaking"}
        if all(z in in_form for z in zones_last_84d):
            return Badge(id="in_form_3m", label="3 месяца в форме", icon="💪")

    return None


# ─── Top-level compute ──────────────────────────────────────────────


def _weekly_ctl_avg(snapshots: Sequence[WellnessSnapshot], ref_date: date, weeks: int) -> float:
    """Mean CTL over the last ``weeks`` weeks (ending at ref_date)."""
    cutoff = ref_date - timedelta(weeks=weeks)
    vals = [s.ctl for s in snapshots if s.ctl is not None and cutoff <= s.dt <= ref_date]
    return mean(vals) if vals else 0.0


def _weekly_tss_from_activities(
    activities: Sequence[EnduranceActivity],
    ref_date: date,
    weeks: int,
) -> list[float]:
    """Sum TSS per week (Mon-Sun) for the last ``weeks`` weeks ending at ref_date.

    The week containing ``ref_date`` is the last bucket. Activities outside
    the window are ignored.
    """
    buckets = [0.0] * weeks
    window_start = ref_date - timedelta(weeks=weeks - 1, days=ref_date.weekday())
    for a in activities:
        if a.dt < window_start or a.dt > ref_date:
            continue
        delta_days = (a.dt - window_start).days
        idx = delta_days // 7
        if 0 <= idx < weeks:
            buckets[idx] += a.training_load or 0.0
    return buckets


def compute_endurance_score(
    *,
    ref_date: date,
    athlete: AthleteProfile,
    latest_wellness: WellnessSnapshot,
    wellness_56d: Sequence[WellnessSnapshot],
    activities_28d: Sequence[EnduranceActivity],
    activities_8w: Sequence[EnduranceActivity],
    # Optional inputs for badge engine. If not provided — badge is None.
    zone_yesterday_id: str | None = None,
    scores_last_90d: Sequence[int] = (),
    scores_last_365d: Sequence[int] = (),
    zones_last_84d: Sequence[str] = (),
    # Badge cooldown (spec §3.9): caller passes already-fired badge ids in
    # the recent window — 1d for #1 (new_zone), 7d for #2/#3/#4. Empty =
    # disable cooldown (fallback path without history).
    recent_badge_ids: Sequence[str] = (),
) -> EnduranceScoreResult:
    """Compute composite Endurance Score for ``ref_date``.

    Caller is responsible for fetching the per-sport-tagged data slices:
      · ``athlete``: age, weight, FTP, threshold_pace
      · ``latest_wellness``: today's CTL/ramp/sport_ctl
      · ``wellness_56d``: 8 weeks of CTL for LongTermBonus avg
      · ``activities_28d``: for Duration + Recovery bonuses
      · ``activities_8w``: for Consistency (weekly-TSS CV)

    Pure function — no IO, no clock. Returns ``EnduranceScoreResult`` with
    components, per-sport decomposition, zone classification, and badge.
    """
    sport_ctl = dict(latest_wellness.sport_ctl)
    vo2 = vo2max_composite(athlete, sport_ctl)

    base = round(100.0 * vo2)
    long_term = round(long_term_bonus(_weekly_ctl_avg(wellness_56d, ref_date, weeks=8)))
    recent = round(recent_bonus(latest_wellness.ramp_rate))
    duration = round(duration_bonus(activities_28d))
    weekly_tss = _weekly_tss_from_activities(activities_8w, ref_date, weeks=CONSISTENCY_WINDOW_WEEKS)
    consistency = round(consistency_bonus(weekly_tss))
    recovery = round(recovery_bonus(activities_28d))

    score = base + long_term + recent + duration + consistency + recovery
    score = max(0, min(score, ENDURANCE_MAX))

    zone = classify_zone(score)
    per_sport = per_sport_breakdown(sport_ctl)
    badge = compute_badge(
        score,
        zone.id,
        zone_yesterday_id=zone_yesterday_id,
        scores_last_90d=scores_last_90d,
        scores_last_365d=scores_last_365d,
        zones_last_84d=zones_last_84d,
        recent_badge_ids=recent_badge_ids,
    )

    insufficient = athlete.ftp_w is None and athlete.threshold_pace_sec_per_km is None
    return EnduranceScoreResult(
        score=score,
        zone_id=zone.id,
        vo2max_composite=round(vo2, 1),
        components=EnduranceComponents(
            base=base,
            long_term=long_term,
            recent=recent,
            duration=duration,
            consistency=consistency,
            recovery=recovery,
        ),
        per_sport=per_sport,
        badge=badge,
        insufficient_data=insufficient,
        insufficient_reason="no_thresholds" if insufficient else None,
    )


# Re-export for callers that want to import a single name.
__all__ = [
    "AthleteProfile",
    "Badge",
    "compute_badge",
    "compute_endurance_score",
    "DEFAULT_VO2MAX",
    "ENDURANCE_MAX",
    "ENDURANCE_ZONES",
    "EnduranceActivity",
    "EnduranceComponents",
    "EnduranceScoreResult",
    "EnduranceZone",
    "PerSport",
    "WellnessSnapshot",
    "classify_zone",
    "consistency_bonus",
    "duration_bonus",
    "long_term_bonus",
    "per_sport_breakdown",
    "recent_bonus",
    "recovery_bonus",
    "vo2max_bike_storer",
    "vo2max_composite",
    "vo2max_run_daniels",
]
