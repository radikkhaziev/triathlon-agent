"""Dramatiq actor — weekly retrain of race-projection models (Run/Ride/Swim).

Co-tenant of `actor_retrain_progression_model`: same Sun 16:00 slot, separate
actor so a `InsufficientDataError` for one discipline doesn't poison the
progression model's run. Each discipline persisted independently to
`static/models/race_{user_id}_{discipline}.joblib`.
"""

import logging

import dramatiq
import sentry_sdk
from pydantic import validate_call

from data.db import UserDTO
from data.ml.race_features import DISCIPLINE_TO_SPORT, InsufficientDataError
from data.ml.race_train import train_user_model

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default", time_limit=600_000, max_retries=0)
@validate_call
def actor_retrain_race_models(user: UserDTO) -> None:
    """Retrain race-projection models for all three disciplines.

    `max_retries=0` mirrors the progression actor — a transient failure is
    cheaper to skip until next Sunday than to retry mid-week with stale data.
    """
    for discipline in DISCIPLINE_TO_SPORT:
        try:
            result = train_user_model(user.id, discipline)
            logger.info(
                "Race model trained user_id=%d discipline=%s: n=%d MAE=%.3f R²=%.3f",
                user.id,
                discipline,
                result["n_examples"],
                result["mae"],
                result["r2"],
            )
        except InsufficientDataError:
            logger.info("Race model skipped user_id=%d discipline=%s — not enough data", user.id, discipline)
        except Exception:
            # Per-discipline isolation: keep training the other two. Dramatiq's
            # outer handler only sees Sentry events for *unhandled* exceptions,
            # so we capture explicitly here — silent rot is worse than noise.
            logger.exception(
                "Race model failed user_id=%d discipline=%s — continuing to next discipline",
                user.id,
                discipline,
            )
            sentry_sdk.capture_exception()
