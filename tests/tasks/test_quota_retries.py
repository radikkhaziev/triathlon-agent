"""QuotaAwareRetries — an Intervals.icu quota hit defers the message without
burning a retry slot; everything else goes through stock ``Retries``."""

from unittest.mock import MagicMock

import dramatiq
from dramatiq.broker import MessageProxy

from data.intervals.client import IntervalsRateLimitError
from tasks.middleware import QUOTA_DEFER_JITTER_SEC, QUOTA_MAX_DEFER_TOTAL_SEC, QuotaAwareRetries


def _message(**options) -> MessageProxy:
    msg = dramatiq.Message(queue_name="default", actor_name="actor_x", args=(), kwargs={}, options=options)
    return MessageProxy(msg)


def _broker() -> MagicMock:
    broker = MagicMock()
    broker.get_actor.return_value.options = {}
    return broker


def _middleware() -> QuotaAwareRetries:
    return QuotaAwareRetries(min_backoff=1000, max_backoff=60_000, max_retries=3)


class TestQuotaAwareRetries:
    def test_rate_limit_defers_by_retry_after_without_counting(self):
        broker = _broker()
        msg = _message(retries=2)

        _middleware().after_process_message(
            broker, msg, exception=IntervalsRateLimitError(24173, method="GET", path="/x")
        )

        broker.enqueue.assert_called_once()
        enqueued = broker.enqueue.call_args.args[0]
        delay = broker.enqueue.call_args.kwargs["delay"]
        assert enqueued is msg
        assert 24173 * 1000 <= delay <= (24173 + QUOTA_DEFER_JITTER_SEC) * 1000
        assert msg.options["retries"] == 2
        assert not msg.failed
        assert 24173 <= msg.options["quota_deferred_sec"] <= 24173 + QUOTA_DEFER_JITTER_SEC

    def test_deferred_time_accumulates_across_deferrals(self):
        broker = _broker()
        msg = _message(quota_deferred_sec=1000)

        _middleware().after_process_message(
            broker, msg, exception=IntervalsRateLimitError(900, method="GET", path="/x")
        )

        assert 1900 <= msg.options["quota_deferred_sec"] <= 1900 + QUOTA_DEFER_JITTER_SEC

    def test_projected_total_over_budget_dead_letters(self):
        """The cap is enforced on already-deferred + next deferral — a message
        just under the budget must not be granted one more multi-hour window."""
        broker = _broker()
        msg = _message(quota_deferred_sec=QUOTA_MAX_DEFER_TOTAL_SEC - 100)

        _middleware().after_process_message(
            broker, msg, exception=IntervalsRateLimitError(900, method="GET", path="/x")
        )

        broker.enqueue.assert_not_called()
        assert msg.failed

    def test_projected_total_within_budget_still_defers(self):
        broker = _broker()
        msg = _message(quota_deferred_sec=QUOTA_MAX_DEFER_TOTAL_SEC - 900 - QUOTA_DEFER_JITTER_SEC - 1)

        _middleware().after_process_message(
            broker, msg, exception=IntervalsRateLimitError(900, method="GET", path="/x")
        )

        broker.enqueue.assert_called_once()
        assert not msg.failed
        assert msg.options["quota_deferred_sec"] <= QUOTA_MAX_DEFER_TOTAL_SEC

    def test_total_deferral_budget_exhausted_dead_letters(self):
        """A structural shortfall must surface, not loop forever in the delay queue."""
        broker = _broker()
        msg = _message(quota_deferred_sec=QUOTA_MAX_DEFER_TOTAL_SEC)

        _middleware().after_process_message(
            broker, msg, exception=IntervalsRateLimitError(900, method="GET", path="/x")
        )

        broker.enqueue.assert_not_called()
        assert msg.failed

    def test_rate_limit_ignores_exhausted_retries(self):
        """A message already at max_retries is still deferred — quota says nothing about it."""
        broker = _broker()
        msg = _message(retries=3)

        err = IntervalsRateLimitError(900, method="GET", path="/x")
        _middleware().after_process_message(broker, msg, exception=err)

        broker.enqueue.assert_called_once()
        assert not msg.failed

    def test_other_exceptions_use_stock_retries(self):
        broker = _broker()
        msg = _message()

        _middleware().after_process_message(broker, msg, exception=RuntimeError("boom"))

        broker.enqueue.assert_called_once()
        assert msg.options["retries"] == 1

    def test_other_exceptions_still_exhaust(self):
        broker = _broker()
        msg = _message(retries=3)

        _middleware().after_process_message(broker, msg, exception=RuntimeError("boom"))

        broker.enqueue.assert_not_called()
        assert msg.failed

    def test_success_is_noop(self):
        broker = _broker()
        _middleware().after_process_message(broker, _message(), result=None, exception=None)
        broker.enqueue.assert_not_called()
