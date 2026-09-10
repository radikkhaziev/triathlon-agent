"""Custom Dramatiq helpers — Pydantic auto-serialization + quota-aware retries."""

import json
import random
from datetime import date, datetime

import dramatiq
from dramatiq.encoder import JSONEncoder
from dramatiq.middleware import Retries
from pydantic import BaseModel

from data.intervals.client import IntervalsRateLimitError

# --- 1. Patch message_with_options: auto-dump Pydantic in kwargs ---

_original_message_with_options = dramatiq.Actor.message_with_options


def _patched_message_with_options(self, *, args=(), kwargs=None, **options):
    """Wrap message_with_options to auto-dump Pydantic models in kwargs."""
    if kwargs:
        kwargs = {k: v.model_dump() if isinstance(v, BaseModel) else v for k, v in kwargs.items()}
    return _original_message_with_options(self, args=args, kwargs=kwargs, **options)


dramatiq.Actor.message_with_options = _patched_message_with_options


# --- 2. Custom encoder: auto-dump Pydantic in pipeline results ---


class _PydanticJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, BaseModel):
            return o.model_dump()
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)


class PydanticEncoder(JSONEncoder):
    """Dramatiq encoder that serializes Pydantic models in results."""

    def encode(self, data: dict) -> bytes:
        return json.dumps(data, separators=(",", ":"), cls=_PydanticJSONEncoder).encode("utf-8")


dramatiq.set_encoder(PydanticEncoder())


# --- 3. Quota-aware retries: an Intervals.icu quota hit is a deferral, not a failure ---

# Spread re-enqueued messages over the first minutes of the fresh window so
# a backlog doesn't slam the 15-minute limit the moment the day resets.
QUOTA_DEFER_JITTER_SEC = 120

# Total time one message may spend quota-deferred before it dead-letters —
# enforced on the *projected* total (already deferred + the deferral about to
# be scheduled), so the bound is never overshot by one more window. Daily
# quota deferrals are ~24 h each, 15-minute-window ones ≤ 15 min, so this
# allows two full daily deferrals (a third would land past the cap). Past
# that the shortfall is structural (quota = 100 × authorized athletes) and
# needs a human, not another re-enqueue — the ERROR log below is the alarm.
QUOTA_MAX_DEFER_TOTAL_SEC = 3 * 24 * 3600


class QuotaAwareRetries(Retries):
    """``Retries`` that re-enqueues a message failed by ``IntervalsRateLimitError``
    after the upstream ``Retry-After`` **without** consuming a retry slot.

    Stock ``Retries`` counts every attempt against ``max_retries`` (3 here),
    so one daily-quota outage would dead-letter the message after three
    deferrals. A quota hit says nothing about the message — the same work
    succeeds verbatim once the window reopens — so it must not burn retries.
    For this one exception class ``max_retries`` / ``retry_when`` / ``throws``
    are deliberately bypassed; the only bound is ``QUOTA_MAX_DEFER_TOTAL_SEC``.
    Everything else (5xx, bugs, transport errors) goes through the parent.

    ``broker.enqueue(message, delay=…)`` is exactly what stock ``Retries``
    does, so ``pipe_target`` / group-completion options survive. Caveat: the
    ``GroupCallbacks`` barrier lives 24 h in Redis — an Intervals-calling
    actor inside a group with a completion callback would lose the callback
    if its deferral chain outlives that (none does today).
    """

    def after_process_message(self, broker, message, *, result=None, exception=None):
        if not isinstance(exception, IntervalsRateLimitError):
            super().after_process_message(broker, message, result=result, exception=exception)
            return
        deferred = message.options.get("quota_deferred_sec", 0)
        delay_sec = exception.retry_after + random.randint(0, QUOTA_DEFER_JITTER_SEC)
        if deferred + delay_sec > QUOTA_MAX_DEFER_TOTAL_SEC:
            self.logger.error(
                "Intervals.icu quota: %s (%r) already deferred %ds, next +%ds would exceed the %ds budget "
                "— giving up, dead-lettering",
                message.actor_name,
                message.message_id,
                deferred,
                delay_sec,
                QUOTA_MAX_DEFER_TOTAL_SEC,
            )
            message.fail()
            return
        # Deliberately NOT writing ``retries`` / ``traceback`` /
        # ``requeue_timestamp`` (stock Retries does): a deferral is not an attempt.
        message.options["quota_deferred_sec"] = deferred + delay_sec
        self.logger.warning(
            "Intervals.icu quota exhausted — deferring %s (%r) by %ds",
            message.actor_name,
            message.message_id,
            delay_sec,
        )
        broker.enqueue(message, delay=delay_sec * 1000)
