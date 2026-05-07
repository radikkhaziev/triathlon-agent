"""Personal recovery/compliance patterns derived from training_log.

ATP Phase 3 finishing work — see `docs/ADAPTIVE_TRAINING_PLAN.md` §3.

Two consumers read these patterns:
  1. Weekly report — Claude calls the `get_personal_patterns` MCP tool, which
     wraps `compute_personal_patterns` (`mcp_server/tools/training_log.py`).
  2. Free-form chat — `render_athlete_block` injects a rendered block into
     the per-user system prompt (`bot/prompts.py:_render_personal_patterns`).

Always returns a dict with ``entries_total`` + ``entries_complete``; aggregate
fields are present only when ``entries_complete >= MIN_COMPLETE_ENTRIES``.
Both consumers branch on that count locally — one query covers both the
diagnostic ("you have N/M, keep training") and the rendered block.

No cron, no persistence: the aggregation reads ≤365 rows and is cheap enough
to recompute on every call. Add a cache only if profiling proves a hot path.
"""

from data.db import TrainingLog

MIN_COMPLETE_ENTRIES = 30


async def compute_personal_patterns(user_id: int, days_back: int = 90) -> dict:
    """Aggregate recovery/compliance patterns from `training_log`.

    Always returns a dict with ``entries_total`` and ``entries_complete``.
    Aggregate fields (``recovery_response_by_category``, etc.) are populated
    only when ``entries_complete >= MIN_COMPLETE_ENTRIES`` — below that
    threshold the dict is the bare counts and Claude has nothing to learn.
    """
    rows = await TrainingLog.get_range(user_id=user_id, days_back=days_back)

    complete = [
        r for r in rows if r.compliance and r.post_recovery_score is not None and r.pre_recovery_score is not None
    ]

    counts = {"entries_total": len(rows), "entries_complete": len(complete)}

    if len(complete) < MIN_COMPLETE_ENTRIES:
        return counts

    buckets: dict[str, list[float]] = {"low": [], "moderate": [], "good": [], "excellent": []}
    for r in complete:
        cat = r.pre_recovery_category or "moderate"
        if cat in buckets:
            buckets[cat].append(r.recovery_delta or 0)

    recovery_response: dict[str, dict] = {}
    for cat, deltas in buckets.items():
        if deltas:
            recovery_response[cat] = {
                "count": len(deltas),
                "avg_delta": round(sum(deltas) / len(deltas), 1),
                "min_delta": round(min(deltas), 1),
                "max_delta": round(max(deltas), 1),
            }

    compliance_counts: dict[str, int] = {}
    for r in complete:
        c = r.compliance or "unknown"
        compliance_counts[c] = compliance_counts.get(c, 0) + 1

    total_compliance = sum(compliance_counts.values())
    compliance_rates = {
        k: {"count": v, "pct": round(v / total_compliance * 100, 1)} for k, v in compliance_counts.items()
    }

    hrv_groups: dict[str, list[float]] = {}
    for r in complete:
        status = r.pre_hrv_status or "unknown"
        hrv_groups.setdefault(status, []).append(r.recovery_delta or 0)

    hrv_sensitivity: dict[str, dict] = {}
    for status, deltas in hrv_groups.items():
        if deltas:
            hrv_sensitivity[status] = {
                "count": len(deltas),
                "avg_delta": round(sum(deltas) / len(deltas), 1),
            }

    matrix: dict[str, dict[str, list[float]]] = {}
    for r in complete:
        cat = r.pre_recovery_category or "moderate"
        zone = r.actual_max_zone_time or "unknown"
        matrix.setdefault(cat, {}).setdefault(zone, [])
        matrix[cat][zone].append(r.recovery_delta or 0)

    recovery_intensity_matrix: dict[str, dict] = {}
    for cat, zones_map in matrix.items():
        recovery_intensity_matrix[cat] = {}
        for zone, deltas in sorted(zones_map.items()):
            recovery_intensity_matrix[cat][zone] = {
                "count": len(deltas),
                "avg_delta": round(sum(deltas) / len(deltas), 1),
            }

    skipped_deltas = [r.recovery_delta or 0 for r in complete if r.compliance == "skipped"]
    trained_deltas = [r.recovery_delta or 0 for r in complete if r.compliance != "skipped"]

    return {
        **counts,
        "recovery_response_by_category": recovery_response,
        "recovery_intensity_matrix": recovery_intensity_matrix,
        "compliance_rates": compliance_rates,
        "hrv_sensitivity": hrv_sensitivity,
        "skipped_avg_delta": round(sum(skipped_deltas) / max(len(skipped_deltas), 1), 1),
        "trained_avg_delta": round(sum(trained_deltas) / max(len(trained_deltas), 1), 1),
    }
