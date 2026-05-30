"""MCP tool for Polarization Index — zone time distribution analysis."""

from datetime import timedelta

from data.db import Activity, ActivityDetail, AthleteGoal
from data.metrics import compute_polarization, compute_polarization_trends, delta_vs_target, target_distribution
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id
from tasks.dto import local_today

_WINDOWS = (7, 14, 28, 56)
_SPORT_MAP = {"run": "Run", "ride": "Ride"}
# ≤14 days to the nearest race → polarized peak/taper; otherwise pyramidal base.
# Deliberate local mirror of tasks.utils.PEAK_TAPER_DAYS: importing that module would
# pull tasks.actors.workout (Dramatiq actor registration as an import side-effect) into
# the MCP server. Same race-week semantics, so keep the two in sync if either changes.
_PEAK_TAPER_DAYS = 14


def _phase_from_upcoming(goals: list, today) -> str:
    """Pure phase rule: ≤14d to the nearest active future race → "peak", else "base".

    Picks the NEAREST upcoming active goal (not category-first), so «RACE_A in 200d +
    RACE_B in 7d» correctly enters peak for the close B-race — mirrors tasks/utils.py.
    """
    upcoming = [g for g in goals if g.is_active and g.event_date and g.event_date >= today]
    if not upcoming:
        return "base"
    nearest = min(upcoming, key=lambda g: g.event_date)
    return "peak" if (nearest.event_date - today).days <= _PEAK_TAPER_DAYS else "base"


async def _resolve_training_phase(user_id: int) -> str:
    """Derive TID phase from the nearest upcoming active race (DB wrapper over the
    pure :func:`_phase_from_upcoming`). Phase 3 may refine this further."""
    goals = await AthleteGoal.get_all(user_id=user_id)
    return _phase_from_upcoming(goals, local_today())


async def get_polarization_multi_window(
    user_id: int,
    sport: str,
    phase: str | None = None,
) -> tuple[dict[int, dict], list[str]]:
    """Fetch zone times once (56d) and compute polarization for all windows.

    Each populated window also carries the sport/phase `target` band and `delta` gaps
    vs that target. `phase=None` auto-resolves from the nearest race (peak/base);
    pass an explicit phase to override. Returns (windows_dict, signals).
    """
    # No-data paths share one `empty` dict across windows — safe because they return
    # BEFORE _attach_targets (which would otherwise mutate the aliased object 4×).
    target = _SPORT_MAP.get(sport.lower())
    if not target:
        empty = compute_polarization([])
        return {w: empty for w in _WINDOWS}, []

    since = local_today() - timedelta(days=max(_WINDOWS))
    activities, _ = await Activity.get_range(user_id, since, local_today())
    filtered = [(a.id, a.start_date_local) for a in activities if a.type == target]

    if not filtered:
        empty = compute_polarization([])
        return {w: empty for w in _WINDOWS}, []

    details = await ActivityDetail.get_bulk([aid for aid, _ in filtered])

    # Build (date_str, hr_zone_times) pairs
    dated_zt = []
    for aid, dt in filtered:
        d = details.get(aid)
        if d and d.hr_zone_times:
            dated_zt.append((str(dt)[:10], d.hr_zone_times))

    windows: dict[int, dict] = {}
    for w in _WINDOWS:
        cutoff = str(local_today() - timedelta(days=w))
        zt_window = [zt for dt_str, zt in dated_zt if dt_str >= cutoff]
        windows[w] = compute_polarization(zt_window)

    if phase is None:
        phase = await _resolve_training_phase(user_id)
    _attach_targets(windows, sport, phase)
    signals = compute_polarization_trends(windows)
    return windows, signals


def _attach_targets(windows: dict[int, dict], sport: str, phase: str) -> None:
    """Annotate each populated window with the resolved target band + delta-vs-target."""
    target = target_distribution(sport, phase)
    for res in windows.values():
        res["target"] = target
        if res["pattern"] == "insufficient_data":
            continue
        res["delta"] = delta_vs_target(res["low_pct"], res["mid_pct"], res["high_pct"], target)


@mcp.tool()
async def get_polarization_index(
    sport: str = "run",
    days: int = 28,
) -> dict:
    """Returns Polarization Index across multiple windows (7d, 14d, 28d, 56d).

    Each window shows Low/Mid/High zone distribution and pattern classification.
    Trend signals detect gray-zone drift, taper, deload, and overtraining risk.

    Each window also carries `polarization_index` (Treff 2019, PI>2 = polarized),
    a `target` band (easy/Z2/hard goal, auto-calibrated to sport + race-phase), and
    `delta` (gaps vs target with a `verdict`: on_target / too_much_z2 / too_little_easy
    / too_much_hard). Use target+delta for a PROACTIVE nudge, signals for reactive drift.

    Args:
        sport: "run" or "ride" (swim excluded — zone mapping unreliable without power)
        days: primary window to highlight (7, 14, 28, or 56). All windows are returned.

    Patterns: polarized (optimal), pyramidal (acceptable), threshold (gray zone risk),
    too_easy (not enough stimulus), too_hard (overtraining risk).
    """
    user_id = get_current_user_id()
    windows, signals = await get_polarization_multi_window(user_id, sport)

    primary = windows.get(days, windows[28])

    return {
        "sport": sport,
        "primary_window": days,
        **primary,
        "windows": {str(d): w for d, w in windows.items()},
        "signals": signals,
    }
