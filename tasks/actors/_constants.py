"""Shared constants for actors — neutral module to break import cycles.

Live in their own file so both ``reports.py`` and ``wellness.py`` can import
them at module top without dragging the actor cycle.
"""

from datetime import timedelta

# Wellness-cron defers the compose by this much so Intervals.icu can settle
# its late CTL/ATL recompute (late activities, late HRV). Also the freshness
# threshold for the ``__scheduled__`` / ``__generating__`` sentinels — a
# sentinel older than 2× this delay (~20 min) is treated as stale and the
# slot reopens for a retry. See ``docs/MULTI_TENANT_SECURITY_SPEC.md`` plus
# the docstrings on ``_is_free_for_morning_report`` / ``_claim_slot``.
MORNING_REPORT_DELAY_SEC = 10 * 60

# ``actor_user_wellness`` re-fetches sport settings from the Intervals.icu API
# only when the newest ``athlete_settings.synced_at`` is older than this.
# Changes normally arrive via the SPORT_SETTINGS_UPDATED webhook (full payload
# inline, no API call); the fetch is a safety net for a missed webhook, capped
# at ~1 request/user/day to protect the app-wide daily quota.
SETTINGS_SYNC_MAX_AGE = timedelta(hours=24)

# Daily-quota reserve for the OAuth bootstrap backfill. ``actor_bootstrap_step``
# pauses a chunk (re-enqueues itself for after the 00:00 UTC reset) when the
# app-wide daily remainder would drop below this after fanning out the
# chunk's per-activity work — keeping headroom for webhooks, morning reports
# and MCP calls of every other user. ~3 requests per activity: detail,
# intervals, FIT download.
BOOTSTRAP_DAILY_RESERVE = 1500
BOOTSTRAP_REQUESTS_PER_ACTIVITY = 3
# Wider than the middleware's 120 s: nothing about a daily-reset pause is
# time-critical, and every parked bootstrap plus every deferred message wakes
# at the same 00:00 UTC — spread them over the first quarter hour.
BOOTSTRAP_PAUSE_JITTER_SEC = 15 * 60
