"""Shared types for the tasks package.

DateDTO — Annotated date type with auto-coercion from str/datetime.
Used as a type hint in Pydantic models; not callable directly.

To validate outside a model, use TypeAdapter::
    >>> from tasks.dto import DateDTO
    >>> from datetime import datetime
    >>> from pydantic import TypeAdapter

    >>> TypeAdapter(DateDTO).validate_python("2026-04-03")
    datetime.date(2026, 4, 3)

    >>> TypeAdapter(DateDTO).validate_python(datetime.now())
    datetime.date(2026, 4, 3)

    >>> datetime.now().date().isoformat()
    '2026-04-03'
"""

import datetime as _dt
import zoneinfo
from datetime import date
from typing import Annotated

from pydantic import BaseModel, BeforeValidator

from config import settings


def _coerce_date(v: date | str | _dt.datetime) -> date:
    if isinstance(v, _dt.datetime):
        return v.date()
    return date.fromisoformat(v) if isinstance(v, str) else v


DateDTO = Annotated[date, BeforeValidator(_coerce_date)]


# Resolved once at import — ``zoneinfo.ZoneInfo`` caches by name, but going
# through the cache on every actor tick still costs a hash + lookup. Settings
# are immutable for the process lifetime so a module-level cache is safe.
_LOCAL_TZ = zoneinfo.ZoneInfo(settings.TIMEZONE)


def local_today() -> date:
    return _dt.datetime.now(_LOCAL_TZ).date()


class ORMDTO(BaseModel):
    is_new: bool = False  # True if created, False if updated
    is_changed: bool = False  # True if any fields changed (for updates)
    row: object | None = None  # The original ORM row (for updates)


class ThresholdsDTO(BaseModel):
    """DFA a1 threshold detection result."""

    hrvt1_hr: float
    hrvt2_hr: float | None = None
    r_squared: float
    confidence: str  # high | moderate | low
    hrvt1_power: int | None = None  # watts (bike) — pow at HRVT1 (aerobic threshold)
    hrvt1_pace: str | None = None  # "M:SS" (run)
    hrvt2_pace: str | None = None  # "M:SS" (run) — pace at HRVT2 (anaerobic threshold)
    hrvt2_power: int | None = None  # watts (bike) — pow at HRVT2 (anaerobic threshold ≈ FTP)


class ReadinessDTO(BaseModel):
    """Readiness (Ra) — warmup performance vs baseline."""

    ra_pct: float  # % change vs baseline
    pa_today: float
    status: str  # excellent | normal | under_recovered


class DurabilityDTO(BaseModel):
    """Durability (Da) — first vs second half performance."""

    da_pct: float  # % change
    status: str  # excellent | normal | fatigued | overreached


class PaBaselineDTO(BaseModel):
    """Pa baseline data for saving."""

    pa_value: float
    dfa_a1_ref: float | None = None
    quality: str | None = None


class FitProcessingResultDTO(BaseModel):
    """Result of FIT file DFA a1 processing."""

    status: str  # processed | too_short | no_rr_data | low_quality
    hrv_quality: str | None = None
    artifact_pct: float | None = None
    rr_count: int = 0
    dfa_a1_mean: float | None = None
    dfa_a1_warmup: float | None = None
    dfa_timeseries: list[dict] | None = None
    thresholds: ThresholdsDTO | None = None
    ra_result: ReadinessDTO | None = None
    pa_today: float | None = None
    pa_baseline_data: PaBaselineDTO | None = None
    da_result: DurabilityDTO | None = None
