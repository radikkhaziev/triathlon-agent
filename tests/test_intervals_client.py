"""Tests for Intervals.icu client retry logic and endpoint specs."""

from unittest.mock import MagicMock

import httpx
import pytest

from data.intervals.client import (
    BASE_URL,
    MAX_RETRIES,
    RETRY_MAX_DELAY,
    IntervalsAccessError,
    IntervalsAsyncClient,
    IntervalsClientBase,
    IntervalsCredsMissingError,
    IntervalsScopeError,
    IntervalsSyncClient,
)


@pytest.fixture
def client():
    return IntervalsClientBase(access_token="test", athlete_id="i123")


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

        client = IntervalsSyncClient(athlete_id="i1", access_token="k")
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

        client = IntervalsSyncClient(athlete_id="i1", access_token="k")
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

        client = IntervalsAsyncClient(athlete_id="i1", access_token="k")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
        try:
            resp = await client._request("GET", "/ping")
            assert resp.status_code == 200
            assert calls["n"] == 2
        finally:
            await client._client.aclose()


class TestCredsMissing:
    """A user with no OAuth access_token (full revoke / never connected) must
    raise a typed error that subclasses ``IntervalsAccessError`` so actors
    catch and skip uniformly with 401/403 paths.

    Exercised through the **public** ``IntervalsSyncClient.for_user`` factory
    rather than the private ``_resolve_credentials`` helper, so the contract
    survives any future refactor of the helper layout.
    """

    def _stub_user(self, **overrides):
        class _StubUser:
            id = 25
            athlete_id = "i376855"
            intervals_access_token = None
            intervals_access_token_encrypted = None

        u = _StubUser()
        for k, v in overrides.items():
            setattr(u, k, v)
        return u

    def test_no_creds_raises_typed_error(self):
        # `for_user` is a `@contextmanager` — the body (including
        # `_resolve_credentials`) only runs on `__enter__`, so the `with`
        # is what surfaces the raise. Bare call would return a CM, not raise.
        with pytest.raises(IntervalsCredsMissingError) as exc:
            with IntervalsSyncClient.for_user(self._stub_user()):
                pass
        assert exc.value.user_id == 25
        # Must be catchable as the base type — that's how actors swallow it.
        assert isinstance(exc.value, IntervalsAccessError)

    def test_no_athlete_id_raises_typed_error(self):
        with pytest.raises(IntervalsCredsMissingError):
            with IntervalsSyncClient.for_user(self._stub_user(athlete_id=None)):
                pass


class TestScopeRevoked:
    """403 from Intervals.icu = scope revoked. Token stays (other scopes still work),
    but the failing call raises IntervalsScopeError so Dramatiq actors can catch and
    skip without retry-looping on a permanent user-action denial."""

    def test_sync_403_raises_scope_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            # Attach `request` so `raise_for_status()` can build the HTTPStatusError
            # with proper request context (httpx requires it; MockTransport doesn't
            # auto-attach, unlike the real httpx client transport path).
            return httpx.Response(403, text="Forbidden", request=request)

        client = IntervalsSyncClient(athlete_id="i1", access_token="k")
        client._client.close()
        client._client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(IntervalsScopeError) as exc:
                client._execute(client._spec_list_sport_settings())
            assert exc.value.method == "GET"
            assert "/athlete/i1/sport-settings" in exc.value.path
        finally:
            client._client.close()

    async def test_async_403_raises_scope_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            # Attach `request` so `raise_for_status()` can build the HTTPStatusError
            # with proper request context (httpx requires it; MockTransport doesn't
            # auto-attach, unlike the real httpx client transport path).
            return httpx.Response(403, text="Forbidden", request=request)

        client = IntervalsAsyncClient(athlete_id="i1", access_token="k")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(IntervalsScopeError):
                await client._execute(client._spec_list_sport_settings())
        finally:
            await client._client.aclose()
