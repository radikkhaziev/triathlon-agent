"""Garmin GDPR data importer — bulk upsert to database."""

from __future__ import annotations

import logging

from sqlalchemy.dialects.postgresql import insert

from data.db import (
    GarminAbnormalHrEvents,
    GarminBioMetrics,
    GarminDailySummary,
    GarminFitnessMetrics,
    GarminHealthStatus,
    GarminRacePredictions,
    GarminSleep,
    GarminTrainingLoad,
    GarminTrainingReadiness,
)
from data.db.common import get_sync_session

from .dto import (
    GarminAbnormalHrEventDTO,
    GarminBioMetricsDTO,
    GarminDailySummaryDTO,
    GarminFitnessMetricsDTO,
    GarminHealthStatusDTO,
    GarminRacePredictionsDTO,
    GarminSleepDTO,
    GarminTrainingLoadDTO,
    GarminTrainingReadinessDTO,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


def _bulk_upsert(model, constraint: str, rows: list[dict], force: bool) -> int:
    """Bulk insert with ON CONFLICT handling. Returns number of rows affected."""
    if not rows:
        return 0

    inserted = 0
    with get_sync_session() as session:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            stmt = insert(model).values(batch)
            if force:
                # Update all columns except id and created_at
                update_cols = {
                    c.name: stmt.excluded[c.name] for c in model.__table__.columns if c.name not in ("id", "created_at")
                }
                stmt = stmt.on_conflict_do_update(constraint=constraint, set_=update_cols)
            else:
                stmt = stmt.on_conflict_do_nothing(constraint=constraint)
            result = session.execute(stmt)
            inserted += result.rowcount
        session.commit()

    return inserted


def import_sleep(user_id: int, data: list[GarminSleepDTO], force: bool = False) -> int:
    rows = [{"user_id": user_id, **d.model_dump()} for d in data]
    return _bulk_upsert(GarminSleep, "uq_garmin_sleep_user_date", rows, force)


def import_daily_summary(user_id: int, data: list[GarminDailySummaryDTO], force: bool = False) -> int:
    rows = [{"user_id": user_id, **d.model_dump()} for d in data]
    return _bulk_upsert(GarminDailySummary, "uq_garmin_daily_user_date", rows, force)


def import_training_readiness(user_id: int, data: list[GarminTrainingReadinessDTO], force: bool = False) -> int:
    rows = [{"user_id": user_id, **d.model_dump()} for d in data]
    return _bulk_upsert(GarminTrainingReadiness, "uq_garmin_readiness_user_date_ctx", rows, force)


def import_health_status(user_id: int, data: list[GarminHealthStatusDTO], force: bool = False) -> int:
    rows = [{"user_id": user_id, **d.model_dump()} for d in data]
    return _bulk_upsert(GarminHealthStatus, "uq_garmin_health_user_date", rows, force)


def import_training_load(user_id: int, data: list[GarminTrainingLoadDTO], force: bool = False) -> int:
    rows = [{"user_id": user_id, **d.model_dump()} for d in data]
    return _bulk_upsert(GarminTrainingLoad, "uq_garmin_load_user_date", rows, force)


def import_fitness_metrics(user_id: int, data: list[GarminFitnessMetricsDTO], force: bool = False) -> int:
    rows = [{"user_id": user_id, **d.model_dump()} for d in data]
    return _bulk_upsert(GarminFitnessMetrics, "uq_garmin_fitness_user_date", rows, force)


def import_race_predictions(user_id: int, data: list[GarminRacePredictionsDTO], force: bool = False) -> int:
    rows = [{"user_id": user_id, **d.model_dump()} for d in data]
    return _bulk_upsert(GarminRacePredictions, "uq_garmin_race_user_date", rows, force)


def import_bio_metrics(user_id: int, data: list[GarminBioMetricsDTO], force: bool = False) -> int:
    rows = [{"user_id": user_id, **d.model_dump()} for d in data]
    return _bulk_upsert(GarminBioMetrics, "uq_garmin_bio_user_date", rows, force)


def import_abnormal_hr_events(user_id: int, data: list[GarminAbnormalHrEventDTO], force: bool = False) -> int:
    rows = [{"user_id": user_id, **d.model_dump()} for d in data]
    return _bulk_upsert(GarminAbnormalHrEvents, "uq_garmin_abnormal_hr_user_ts", rows, force)


def import_all(
    user_id: int,
    *,
    sleep: list[GarminSleepDTO] | None = None,
    daily: list[GarminDailySummaryDTO] | None = None,
    readiness: list[GarminTrainingReadinessDTO] | None = None,
    health: list[GarminHealthStatusDTO] | None = None,
    load: list[GarminTrainingLoadDTO] | None = None,
    fitness: list[GarminFitnessMetricsDTO] | None = None,
    race: list[GarminRacePredictionsDTO] | None = None,
    bio: list[GarminBioMetricsDTO] | None = None,
    abnormal_hr: list[GarminAbnormalHrEventDTO] | None = None,
    force: bool = False,
) -> dict[str, int]:
    """Import all provided data types. Returns counts per type."""
    counts = {}
    if sleep is not None:
        counts["sleep"] = import_sleep(user_id, sleep, force)
    if daily is not None:
        counts["daily"] = import_daily_summary(user_id, daily, force)
    if readiness is not None:
        counts["readiness"] = import_training_readiness(user_id, readiness, force)
    if health is not None:
        counts["health"] = import_health_status(user_id, health, force)
    if load is not None:
        counts["load"] = import_training_load(user_id, load, force)
    if fitness is not None:
        counts["fitness"] = import_fitness_metrics(user_id, fitness, force)
    if race is not None:
        counts["race"] = import_race_predictions(user_id, race, force)
    if bio is not None:
        counts["bio"] = import_bio_metrics(user_id, bio, force)
    if abnormal_hr is not None:
        counts["abnormal_hr"] = import_abnormal_hr_events(user_id, abnormal_hr, force)
    return counts
