"""Issue #348 — ML retrain actors must declare `queue_name='ml_retrain'`.

These actors run on a dedicated single-threaded ml-worker container so CPU-heavy
XGBoost training doesn't compete with the default worker pool (Telegram /
wellness / webhooks). The queue_name on the decorator is the **only** thing
that routes messages to the right worker — if it drifts back to "default", the
isolation silently breaks and CPU spikes return.
"""

from __future__ import annotations

from tasks.actors.progression import actor_retrain_progression_model
from tasks.actors.race_models import actor_retrain_race_models


class TestMlRetrainQueueIsolation:
    def test_progression_actor_uses_ml_retrain_queue(self):
        assert actor_retrain_progression_model.queue_name == "ml_retrain"

    def test_race_models_actor_uses_ml_retrain_queue(self):
        assert actor_retrain_race_models.queue_name == "ml_retrain"

    def test_race_models_actor_no_retries(self):
        """`max_retries=0` because mid-week retry on stale data is worse than
        skipping until next Sunday. Pinned to catch accidental enablement.
        """
        assert actor_retrain_race_models.options.get("max_retries") == 0

    def test_progression_actor_no_retries(self):
        """Same rationale as race_models — mid-week stale data > skip."""
        assert actor_retrain_progression_model.options.get("max_retries") == 0
