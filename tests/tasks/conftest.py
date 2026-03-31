"""Dramatiq test fixtures: StubBroker replaces RedisBroker at module level."""

import sys
import types

import dramatiq
import pytest
from dotenv import load_dotenv
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import CurrentMessage, GroupCallbacks, Pipelines
from dramatiq.rate_limits.backends.stub import StubBackend as StubRateLimiterBackend
from dramatiq.results import Results
from dramatiq.results.backends import StubBackend

import tasks.middleware  # noqa: F401 — patches Actor.message_with_options for Pydantic auto-serialization

load_dotenv()

# Create StubBroker BEFORE any actor import
_broker = StubBroker()
_result_backend = StubBackend()
_broker.add_middleware(CurrentMessage())
_broker.add_middleware(Pipelines())
_broker.add_middleware(Results(backend=_result_backend, store_results=True))
_broker.add_middleware(GroupCallbacks(StubRateLimiterBackend()))
dramatiq.set_broker(_broker)

# Replace tasks.broker module so `from tasks.broker import broker` gets StubBroker
_fake_broker_module = types.ModuleType("tasks.broker")
_fake_broker_module.broker = _broker
_fake_broker_module.setup_broker = lambda: _broker
sys.modules["tasks.broker"] = _fake_broker_module

# Now safe to import actors — they bind to StubBroker
_broker.emit_after("process_boot")
import tasks.actors  # noqa


@pytest.fixture(scope="session")
def stub_broker():
    return _broker


@pytest.fixture(autouse=True)
def clean_broker():
    _broker.flush_all()
    yield
    _broker.flush_all()


@pytest.fixture()
def stub_worker():
    """Create a worker that processes messages from the StubBroker."""
    worker = dramatiq.Worker(_broker, worker_timeout=1000)
    worker.start()
    yield worker
    worker.stop()
