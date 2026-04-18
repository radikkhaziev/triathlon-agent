"""Dramatiq actors package — re-exports public actors for discovery.

Usage: ``dramatiq tasks.actors`` discovers all actors via this __init__.
Private actors (prefixed with _) are internal to the package and should be
imported directly from their submodules when needed (e.g. in tests).
"""

import tasks.middleware  # noqa: F401 — ensure Pydantic encoder is active before any actor dispatch
from tasks.broker import broker  # noqa: F401 — ensure broker is configured before actor discovery

from .activities import actor_fetch_user_activities, actor_send_achievement_notification  # noqa: F401
from .athlets import actor_sync_athlete_goals, actor_sync_athlete_settings, actor_update_zones  # noqa: F401
from .reports import (  # noqa: F401
    actor_compose_user_evening_report,
    actor_compose_user_morning_report,
    actor_compose_weekly_report,
    actor_echo,
    actor_user_scheduled_workouts,
)
from .training_log import actor_fill_training_log, actor_fill_training_log_post  # noqa: F401
from .wellness import actor_user_wellness  # noqa: F401
from .workout import actor_push_workout  # noqa: F401
