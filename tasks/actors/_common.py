"""Shared constants for actor modules."""

import zoneinfo

from config import settings
from tasks.broker import broker  # noqa: F401 — ensures broker is configured before @dramatiq.actor

TZ = zoneinfo.ZoneInfo(settings.TIMEZONE)

_CATEGORY_TO_READINESS = {"excellent": "green", "good": "green", "moderate": "yellow", "low": "red"}
