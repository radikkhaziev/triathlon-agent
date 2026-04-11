"""MCP tools for Training Log (ATP Phase 3)."""

from data.db import TrainingLog
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_training_log(target_date: str = "", days_back: int = 14) -> dict:
    """Get training log: planned vs actual, pre-workout state, compliance, and recovery outcome."""
    user_id = get_current_user_id()
    rows = await TrainingLog.get_range(user_id=user_id, days_back=days_back)

    entries = []
    for r in rows:
        entry = {
            "date": r.date,
            "sport": r.sport,
            "source": r.source,
            "original_name": r.original_name,
            "original_duration_min": (r.original_duration_sec // 60) if r.original_duration_sec else None,
            "adapted_name": r.adapted_name,
            "adaptation_reason": r.adaptation_reason,
            "pre": {
                "recovery": r.pre_recovery_score,
                "category": r.pre_recovery_category,
                "hrv_status": r.pre_hrv_status,
                "hrv_delta": r.pre_hrv_delta_pct,
                "tsb": r.pre_tsb,
                "sleep": r.pre_sleep_score,
                "ra": r.pre_ra_pct,
            },
            "actual": (
                {
                    "activity_id": r.actual_activity_id,
                    "sport": r.actual_sport,
                    "duration_min": (r.actual_duration_sec // 60) if r.actual_duration_sec else None,
                    "avg_hr": r.actual_avg_hr,
                    "tss": r.actual_tss,
                    "max_zone": r.actual_max_zone_time,
                }
                if r.compliance
                else None
            ),
            "compliance": r.compliance,
            "post": (
                {
                    "recovery": r.post_recovery_score,
                    "hrv_delta": r.post_hrv_delta_pct,
                    "sleep": r.post_sleep_score,
                    "ra": r.post_ra_pct,
                    "recovery_delta": r.recovery_delta,
                }
                if r.post_recovery_score is not None
                else None
            ),
        }
        entries.append(entry)

    return {"count": len(entries), "entries": entries}


@mcp.tool()
async def get_personal_patterns(days_back: int = 90) -> dict:
    """Compute personal recovery and compliance patterns. Requires 30+ training log entries."""
    user_id = get_current_user_id()
    rows = await TrainingLog.get_range(user_id=user_id, days_back=days_back)

    # Filter to entries with complete data (pre + actual + post)
    complete = [
        r for r in rows if r.compliance and r.post_recovery_score is not None and r.pre_recovery_score is not None
    ]

    if len(complete) < 30:
        return {
            "status": "insufficient_data",
            "entries_total": len(rows),
            "entries_complete": len(complete),
            "message": f"Need 30+ complete entries, have {len(complete)}. Keep training!",
        }

    # Recovery response: group by pre_recovery bucket + compliance
    buckets = {"low": [], "moderate": [], "good": [], "excellent": []}
    for r in complete:
        cat = r.pre_recovery_category or "moderate"
        if cat in buckets:
            buckets[cat].append(r.recovery_delta or 0)

    recovery_response = {}
    for cat, deltas in buckets.items():
        if deltas:
            recovery_response[cat] = {
                "count": len(deltas),
                "avg_delta": round(sum(deltas) / len(deltas), 1),
                "min_delta": round(min(deltas), 1),
                "max_delta": round(max(deltas), 1),
            }

    # Compliance rates
    compliance_counts: dict[str, int] = {}
    for r in complete:
        c = r.compliance or "unknown"
        compliance_counts[c] = compliance_counts.get(c, 0) + 1

    total_compliance = sum(compliance_counts.values())
    compliance_rates = {
        k: {"count": v, "pct": round(v / total_compliance * 100, 1)} for k, v in compliance_counts.items()
    }

    # HRV sensitivity: avg recovery_delta when HRV green vs yellow vs red
    hrv_groups: dict[str, list[float]] = {}
    for r in complete:
        status = r.pre_hrv_status or "unknown"
        hrv_groups.setdefault(status, []).append(r.recovery_delta or 0)

    hrv_sensitivity = {}
    for status, deltas in hrv_groups.items():
        if deltas:
            hrv_sensitivity[status] = {
                "count": len(deltas),
                "avg_delta": round(sum(deltas) / len(deltas), 1),
            }

    # Recovery response by zone (recovery × intensity matrix)
    zone_groups: dict[str, list[float]] = {}
    for r in complete:
        zone = r.actual_max_zone_time or "unknown"
        zone_groups.setdefault(zone, []).append(r.recovery_delta or 0)

    recovery_by_zone = {}
    for zone, deltas in sorted(zone_groups.items()):
        if deltas:
            recovery_by_zone[zone] = {
                "count": len(deltas),
                "avg_delta": round(sum(deltas) / len(deltas), 1),
                "min_delta": round(min(deltas), 1),
                "max_delta": round(max(deltas), 1),
            }

    # Recovery × intensity matrix: category + zone → avg delta
    matrix: dict[str, dict[str, list[float]]] = {}
    for r in complete:
        cat = r.pre_recovery_category or "moderate"
        zone = r.actual_max_zone_time or "unknown"
        matrix.setdefault(cat, {}).setdefault(zone, [])
        matrix[cat][zone].append(r.recovery_delta or 0)

    recovery_intensity_matrix = {}
    for cat, zones_map in matrix.items():
        recovery_intensity_matrix[cat] = {}
        for zone, deltas in sorted(zones_map.items()):
            recovery_intensity_matrix[cat][zone] = {
                "count": len(deltas),
                "avg_delta": round(sum(deltas) / len(deltas), 1),
            }

    # Skipped vs trained recovery comparison
    skipped_deltas = [r.recovery_delta or 0 for r in complete if r.compliance == "skipped"]
    trained_deltas = [r.recovery_delta or 0 for r in complete if r.compliance != "skipped"]

    return {
        "status": "ok",
        "entries_total": len(rows),
        "entries_complete": len(complete),
        "recovery_response_by_category": recovery_response,
        "recovery_response_by_zone": recovery_by_zone,
        "recovery_intensity_matrix": recovery_intensity_matrix,
        "compliance_rates": compliance_rates,
        "hrv_sensitivity": hrv_sensitivity,
        "skipped_avg_delta": round(sum(skipped_deltas) / max(len(skipped_deltas), 1), 1),
        "trained_avg_delta": round(sum(trained_deltas) / max(len(trained_deltas), 1), 1),
    }
