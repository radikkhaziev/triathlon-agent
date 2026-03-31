"""Redis client for caching and session storage."""

import logging

import redis.asyncio as redis

from config import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def get_redis() -> redis.Redis | None:
    """Return the shared Redis client, or None if Redis is disabled."""
    return _client


async def init_redis() -> redis.Redis | None:
    """Initialize Redis connection. Returns client or None if disabled/unreachable."""
    global _client

    if _client is not None:
        return _client

    if not settings.REDIS_URL:
        logger.info("Redis disabled (REDIS_URL is empty)")
        return None

    try:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=5)
        await _client.ping()
        logger.info("Redis connected: %s", settings.REDIS_URL.split("@")[-1])
        return _client
    except Exception as exc:
        logger.warning("Redis unavailable: %s", exc)
        _client = None
        return None


async def close_redis() -> None:
    """Close Redis connection."""
    global _client
    if _client:
        await _client.aclose()
        _client = None
        logger.info("Redis connection closed")
