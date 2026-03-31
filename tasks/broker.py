"""Dramatiq broker configuration — Redis backend."""

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import (
    AgeLimit,
    CurrentMessage,
    GroupCallbacks,
    Pipelines,
    Retries,
    ShutdownNotifications,
    TimeLimit,
)
from dramatiq.rate_limits.backends import RedisBackend as RedisRateLimiterBackend
from dramatiq.results import Results
from dramatiq.results.backends import RedisBackend as RedisResultBackend

import tasks.middleware  # noqa: F401 — patches Actor.message_with_options for Pydantic auto-serialization
from config import settings


def setup_broker() -> RedisBroker:
    """Create and set the global Dramatiq broker backed by Redis."""
    result_backend = RedisResultBackend(url=settings.REDIS_URL)
    rate_limiter_backend = RedisRateLimiterBackend(url=settings.REDIS_URL)

    broker = RedisBroker(url=settings.REDIS_URL)

    broker.middleware = [
        AgeLimit(),
        TimeLimit(),
        ShutdownNotifications(),  # ← graceful shutdown worker
        Retries(min_backoff=1000, max_backoff=60_000, max_retries=3),
        CurrentMessage(),
        Pipelines(),  # ← without pipeline it does not work
        Results(backend=result_backend, store_results=True),
        GroupCallbacks(rate_limiter_backend),  # ← rate_limiter, NOT result
    ]

    dramatiq.set_broker(broker)

    return broker


broker = setup_broker()
