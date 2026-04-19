"""Dramatiq actor — weekly retrain of progression model."""

import logging

import dramatiq
from pydantic import validate_call

from data.db import ProgressionModelRun, UserDTO
from data.ml.progression import train_model

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default", time_limit=300_000)
@validate_call
def actor_retrain_progression_model(user: UserDTO, sport: str = "Ride") -> None:
    """Retrain progression model for a user+sport and save results."""
    result = train_model(user.id, sport)
    if result is None:
        logger.info("Progression model skipped for user %d %s — not enough data", user.id, sport)
        return

    ProgressionModelRun.save_run(
        user_id=user.id,
        sport=sport,
        n_examples=result["n_examples"],
        mae=result["mae"],
        r2=result["r2"],
        model_path=result["model_path"],
        shap_global_json=result["shap_global"],
    )
    logger.info(
        "Progression model trained for user %d %s: R²=%.3f corr=%.3f examples=%d",
        user.id,
        sport,
        result["r2"],
        result["correlation"],
        result["n_examples"],
    )
