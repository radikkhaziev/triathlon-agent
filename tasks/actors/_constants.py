"""Shared constants for actors — neutral module to break import cycles.

Live in their own file so both ``reports.py`` and ``wellness.py`` can import
them at module top without dragging the actor cycle.
"""

# Wellness-cron defers the compose by this much so Intervals.icu can settle
# its late CTL/ATL recompute (late activities, late HRV). Also the freshness
# threshold for the ``__scheduled__`` / ``__generating__`` sentinels — a
# sentinel older than 2× this delay (~20 min) is treated as stale and the
# slot reopens for a retry. See ``docs/MULTI_TENANT_SECURITY_SPEC.md`` plus
# the docstrings on ``_is_free_for_morning_report`` / ``_claim_slot``.
MORNING_REPORT_DELAY_SEC = 10 * 60
