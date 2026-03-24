"""Shared utilities for sport type mapping and CTL extraction."""

# Canonical mapping: Intervals.icu activity/sport type → swim/bike/run
SPORT_MAP: dict[str, str] = {
    "swim": "swim",
    "swimming": "swim",
    "openwaterswim": "swim",
    "ride": "bike",
    "bike": "bike",
    "cycling": "bike",
    "virtualride": "bike",
    "mountainbikeride": "bike",
    "gravelride": "bike",
    "ebikeride": "bike",
    "emountainbikeride": "bike",
    "trackride": "bike",
    "run": "run",
    "running": "run",
    "virtualrun": "run",
    "trailrun": "run",
}


def extract_sport_ctl(sport_info: list[dict] | None) -> dict[str, float | None]:
    """Extract per-sport CTL from sport_info JSON stored in wellness.

    Looks for 'ctl' field inside each sport entry. Returns dict with
    swim/bike/run CTL values, or None if not available.

    Works with both the original Intervals.icu format (type + eftp/wPrime/pMax)
    enriched with 'ctl' field by our pipeline, and any legacy formats.
    """
    result: dict[str, float | None] = {"swim": None, "bike": None, "run": None}
    if not sport_info:
        return result
    if not isinstance(sport_info, list):
        return result
    for entry in sport_info:
        raw_type = (entry.get("type") or entry.get("sport") or "").lower()
        sport = SPORT_MAP.get(raw_type)
        if not sport:
            continue
        ctl_val = entry.get("ctl")
        if ctl_val is None:
            ctl_val = entry.get("ctlLoad")
        if ctl_val is None:
            continue
        result[sport] = round(float(ctl_val), 1)
    return result


def extract_sport_ctl_tuple(sport_info: list[dict] | None) -> tuple[float, float, float]:
    """Same as extract_sport_ctl but returns (swim, bike, run) tuple.

    Returns 0.0 instead of None for missing values — used in AI prompt formatting.
    """
    d = extract_sport_ctl(sport_info)
    return (d["swim"] or 0.0, d["bike"] or 0.0, d["run"] or 0.0)
