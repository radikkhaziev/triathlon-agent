"""MCP tool for Polarization Index — zone time distribution analysis."""

from datetime import date, timedelta

from data.db import Activity, ActivityDetail
from data.metrics import compute_polarization, compute_polarization_trends
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id

_WINDOWS = (7, 14, 28, 56)
_SPORT_MAP = {"run": "Run", "ride": "Ride"}


async def get_polarization_multi_window(
    user_id: int,
    sport: str,
) -> tuple[dict[int, dict], list[str]]:
    """Fetch zone times once (56d) and compute polarization for all windows.

    Returns (windows_dict, signals).
    """
    target = _SPORT_MAP.get(sport.lower())
    if not target:
        empty = compute_polarization([])
        return {w: empty for w in _WINDOWS}, []

    since = date.today() - timedelta(days=max(_WINDOWS))
    activities, _ = await Activity.get_range(user_id, since, date.today())
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
        cutoff = str(date.today() - timedelta(days=w))
        zt_window = [zt for dt_str, zt in dated_zt if dt_str >= cutoff]
        windows[w] = compute_polarization(zt_window)

    signals = compute_polarization_trends(windows)
    return windows, signals


@mcp.tool()
async def get_polarization_index(
    sport: str = "run",
    days: int = 28,
) -> dict:
    """Returns Polarization Index across multiple windows (7d, 14d, 28d, 56d).

    Each window shows Low/Mid/High zone distribution and pattern classification.
    Trend signals detect gray-zone drift, taper, deload, and overtraining risk.

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
