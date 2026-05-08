"""Single source of truth for sport-name mapping.

Two namespaces co-exist in the codebase:

- **Intervals.icu casing** (``"Run"`` / ``"Ride"`` / ``"Swim"``) — used by
  ``AthleteSettings.sport``, ``Activity.type``, ``ScheduledWorkout.type``,
  the ramp-test pipeline, and any field that mirrors the Intervals.icu API
  shape directly.
- **Lowercase enum** (``"run"`` / ``"ride"`` / ``"swim"``) — used by
  ``User.sports``, the ``SportsUpdateRequest`` API DTO, and frontend code.
  Lowercase is the modern choice for user-facing config because it matches
  REST/JSON convention and the SportTag TypeScript enum.

Keeping two divergent dicts in ``api/routers/auth.py`` and ``tasks/utils.py``
was a code-review finding — they would silently desync. Centralising lets a
new sport be added in one place.
"""

from __future__ import annotations

# Intervals.icu casing → user-facing lowercase enum.
INTERVALS_TO_LOWER: dict[str, str] = {"Run": "run", "Ride": "ride", "Swim": "swim"}

# Reverse map for the morning-report ramp pipeline.
LOWER_TO_INTERVALS: dict[str, str] = {v: k for k, v in INTERVALS_TO_LOWER.items()}

# Sports that ``data/ramp_tests.create_ramp_test`` actually supports today.
# Swim is on the roadmap (see ``docs/RAMP_TEST_SWIM_SPEC.md``); add ``"Swim"``
# here once the builder lands so swim-only athletes start getting suggestions.
RAMP_SUPPORTED_INTERVALS: set[str] = {"Run", "Ride"}

# Priority order for ramp-test routing. Used as a tie-break when multiple
# sports are stale by the same number of days, AND as the canonical output
# order of ``user_ramp_sports`` so the API's alphabetical sort on
# ``users.sports`` doesn't accidentally bias suggestions toward Ride
# (alphabetically before Run) for triathletes. Run-first matches the
# legacy expectation and is the most common discipline.
RAMP_PRIORITY: tuple[str, ...] = ("Run", "Ride", "Swim")
