"""Tests for Intervals.icu client retry logic and endpoint specs."""

from unittest.mock import MagicMock

import httpx
import pytest

from data.intervals.client import (
    BASE_URL,
    MAX_RETRIES,
    RETRY_MAX_DELAY,
    IntervalsAsyncClient,
    IntervalsClientBase,
    IntervalsSyncClient,
)


@pytest.fixture
def client():
    return IntervalsClientBase(api_key="test", athlete_id="i123")


class TestComputeRetryDelay:
    """_compute_retry_delay: exponential backoff with cap."""

    def test_exponential_backoff_attempts(self, client):
        """Delay doubles each attempt: 10, 20, 40, 60, 60."""
        resp = MagicMock()
        resp.headers = {}
        expected = [10, 20, 40, 60, 60]
        for attempt, exp in enumerate(expected):
            assert client._compute_retry_delay(resp, attempt) == exp

    def test_respects_retry_after_header(self, client):
        """Retry-After header takes priority over computed delay."""
        resp = MagicMock()
        resp.headers = {"Retry-After": "25"}
        assert client._compute_retry_delay(resp, 0) == 25.0

    def test_retry_after_capped_at_max(self, client):
        """Retry-After > RETRY_MAX_DELAY is capped."""
        resp = MagicMock()
        resp.headers = {"Retry-After": "300"}
        assert client._compute_retry_delay(resp, 0) == RETRY_MAX_DELAY

    def test_computed_delay_capped_at_max(self, client):
        """High attempt numbers don't exceed RETRY_MAX_DELAY."""
        resp = MagicMock()
        resp.headers = {}
        assert client._compute_retry_delay(resp, 10) == RETRY_MAX_DELAY

    def test_total_worst_case_under_200s(self, client):
        """Sum of all 5 retry delays stays under 200s."""
        resp = MagicMock()
        resp.headers = {}
        total = sum(client._compute_retry_delay(resp, i) for i in range(5))
        assert total == 190  # 10 + 20 + 40 + 60 + 60
        assert total < 200


# ---------------------------------------------------------------------------
#  _spec_get_activity_streams
# ---------------------------------------------------------------------------


class TestSpecGetActivityStreams:
    """_spec_get_activity_streams: builds correct URL path and query params."""

    def test_method_is_get(self, client):
        spec = client._spec_get_activity_streams("abc123")
        assert spec.method == "GET"

    def test_path_contains_activity_id(self, client):
        spec = client._spec_get_activity_streams("abc123")
        assert "/activity/abc123/streams" in spec.path

    def test_handle_404_is_true(self, client):
        """Streams endpoint should silently swallow 404 (activity may lack streams)."""
        spec = client._spec_get_activity_streams("abc123")
        assert spec.handle_404 is True

    def test_no_params_when_types_is_none(self, client):
        spec = client._spec_get_activity_streams("abc123", types=None)
        assert spec.kwargs == {}

    def test_no_params_when_types_is_empty(self, client):
        spec = client._spec_get_activity_streams("abc123", types=[])
        assert spec.kwargs == {}

    def test_single_type_joined_as_csv(self, client):
        spec = client._spec_get_activity_streams("abc123", types=["latlng"])
        assert spec.kwargs == {"params": {"types": "latlng"}}

    def test_multiple_types_joined_as_csv(self, client):
        spec = client._spec_get_activity_streams("abc123", types=["latlng", "altitude", "heartrate"])
        params = spec.kwargs["params"]["types"]
        assert params == "latlng,altitude,heartrate"

    def test_different_activity_id(self, client):
        spec = client._spec_get_activity_streams("xyz999")
        assert "xyz999" in spec.path


# ---------------------------------------------------------------------------
#  Transport-error retry (TLS handshake, connect reset, read error)
# ---------------------------------------------------------------------------


class TestTransportErrorRetry:
    """_request retries transient transport errors, not just bad HTTP statuses."""

    def test_sync_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr("data.intervals.client.time.sleep", lambda s: None)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("tls handshake failed")
            return httpx.Response(200, json={"ok": True})

        client = IntervalsSyncClient(athlete_id="i1", api_key="k")
        client._client.close()
        client._client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
        try:
            resp = client._request("GET", "/ping")
            assert resp.status_code == 200
            assert calls["n"] == 2
        finally:
            client._client.close()

    def test_sync_raises_after_all_attempts_fail(self, monkeypatch):
        monkeypatch.setattr("data.intervals.client.time.sleep", lambda s: None)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ConnectError("tls handshake failed")

        client = IntervalsSyncClient(athlete_id="i1", api_key="k")
        client._client.close()
        client._client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(httpx.ConnectError):
                client._request("GET", "/ping")
            assert calls["n"] == MAX_RETRIES
        finally:
            client._client.close()

    async def test_async_retries_then_succeeds(self, monkeypatch):
        async def _no_sleep(_):
            return None

        monkeypatch.setattr("data.intervals.client.asyncio.sleep", _no_sleep)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("tls handshake failed")
            return httpx.Response(200, json={"ok": True})

        client = IntervalsAsyncClient(athlete_id="i1", api_key="k")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
        try:
            resp = await client._request("GET", "/ping")
            assert resp.status_code == 200
            assert calls["n"] == 2
        finally:
            await client._client.aclose()
