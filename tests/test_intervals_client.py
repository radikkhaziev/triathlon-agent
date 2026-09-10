"""Tests for Intervals.icu client retry logic and endpoint specs."""

from datetime import date
from unittest.mock import MagicMock, patch

import httpx
import pytest
from dramatiq import Retry

from data.intervals.client import (
    BASE_URL,
    MAX_RETRIES,
    RETRY_MAX_DELAY,
    IntervalsAccessError,
    IntervalsAsyncClient,
    IntervalsClientBase,
    IntervalsCredsMissingError,
    IntervalsRateLimitError,
    IntervalsScopeError,
    IntervalsSyncClient,
    QuotaSnapshot,
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


# ---------------------------------------------------------------------------
#  Rate limit / quota — 429 with a long Retry-After raises instead of sleeping
# ---------------------------------------------------------------------------

_QUOTA_HEADERS = {"X-RateLimit-Limit": "2500,8000", "X-RateLimit-Remaining": "2499,0"}


def _sync_client_with(handler) -> IntervalsSyncClient:
    client = IntervalsSyncClient(athlete_id="i1", access_token="k")
    client._client.close()
    client._client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    return client


class TestParseRetryAfter:
    def test_header_wins_over_body(self, client):
        resp = httpx.Response(429, headers={"Retry-After": "24173"}, json={"retry_after_seconds": 5})
        assert client._parse_retry_after(resp) == 24173

    def test_body_fallback(self, client):
        resp = httpx.Response(429, json={"retry_after_seconds": 812})
        assert client._parse_retry_after(resp) == 812

    def test_absent(self, client):
        assert client._parse_retry_after(httpx.Response(429, content=b"")) is None

    def test_zero_floors_to_one_second(self, client):
        """«Retry-After: 0» must never turn into a no-sleep hammer loop."""
        assert client._parse_retry_after(httpx.Response(429, headers={"Retry-After": "0"}, content=b"")) == 1
        assert client._parse_retry_after(httpx.Response(429, json={"retry_after_seconds": 0})) == 1

    def test_http_date_header_ignored(self, client):
        resp = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, content=b"")
        assert client._parse_retry_after(resp) is None


class TestRecordQuota:
    def test_parses_headers(self, client):
        client._record_quota(httpx.Response(200, headers=_QUOTA_HEADERS))
        assert client.quota == QuotaSnapshot(remaining_15m=2499, remaining_day=0, limit_15m=2500, limit_day=8000)

    def test_missing_headers_reset_stale_snapshot(self, client):
        """Per-second per-IP 429s carry no headers — the previous snapshot must
        not survive and masquerade as the current response's quota."""
        client._record_quota(httpx.Response(200, headers=_QUOTA_HEADERS))
        client._record_quota(httpx.Response(429, content=b""))
        assert client.quota is None

    def test_malformed_headers_reset_snapshot(self, client):
        client._record_quota(httpx.Response(200, headers=_QUOTA_HEADERS))
        client._record_quota(httpx.Response(200, headers={"X-RateLimit-Remaining": "lots"}))
        assert client.quota is None

    # ``logger.warning`` is mocked rather than caplog'd: the autouse test-DB
    # fixture runs alembic, whose ``fileConfig`` disables pre-existing loggers.
    def test_low_daily_quota_warns_once_per_day(self, client, monkeypatch):
        monkeypatch.setattr("data.intervals.client._low_quota_warned_on", None)
        with patch("data.intervals.client.logger.warning") as warn:
            client._record_quota(httpx.Response(200, headers={"X-RateLimit-Remaining": "2400,1499"}))
            client._record_quota(httpx.Response(200, headers={"X-RateLimit-Remaining": "2399,1498"}))
        assert warn.call_count == 1
        assert "daily quota low" in warn.call_args.args[0]

    def test_healthy_quota_does_not_warn(self, client, monkeypatch):
        monkeypatch.setattr("data.intervals.client._low_quota_warned_on", None)
        with patch("data.intervals.client.logger.warning") as warn:
            client._record_quota(httpx.Response(200, headers={"X-RateLimit-Remaining": "2400,6000"}))
        warn.assert_not_called()


class TestRateLimitRaise:
    """The 2026-09-10 outage: 8000/day exhausted → every call slept 5×60s then
    failed, 3 Dramatiq retries on top. Now: raise on the first 429, no sleep."""

    def test_sync_long_retry_after_raises_without_sleeping(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr("data.intervals.client.time.sleep", lambda s: sleeps.append(s))
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(
                429,
                headers={"Retry-After": "24173", **_QUOTA_HEADERS},
                json={"error": "Rate limit exceeded", "retry_after_seconds": 24173},
            )

        client = _sync_client_with(handler)
        try:
            with pytest.raises(IntervalsRateLimitError) as exc_info:
                client._request("GET", "/athlete/i1/wellness")
        finally:
            client._client.close()

        assert calls["n"] == 1
        assert sleeps == []
        err = exc_info.value
        assert err.retry_after == 24173
        assert err.delay == 24173 * 1000  # dramatiq.Retry contract
        assert err.quota == QuotaSnapshot(remaining_15m=2499, remaining_day=0, limit_15m=2500, limit_day=8000)
        assert "6h 42m" in str(err)

    def test_sync_body_only_retry_after_raises(self, monkeypatch):
        """No header, only the JSON ``retry_after_seconds`` — still a deferral."""
        sleeps: list[float] = []
        monkeypatch.setattr("data.intervals.client.time.sleep", lambda s: sleeps.append(s))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "Rate limit exceeded", "retry_after_seconds": 812})

        client = _sync_client_with(handler)
        try:
            with pytest.raises(IntervalsRateLimitError) as exc_info:
                client._request("GET", "/ping")
        finally:
            client._client.close()
        assert sleeps == []
        assert exc_info.value.retry_after == 812

    def test_sync_short_retry_after_still_sleeps_and_retries(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr("data.intervals.client.time.sleep", lambda s: sleeps.append(s))
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "30"}, content=b"")
            return httpx.Response(200, json={"ok": True})

        client = _sync_client_with(handler)
        try:
            assert client._request("GET", "/ping").status_code == 200
        finally:
            client._client.close()
        assert calls["n"] == 2
        assert sleeps == [30.0]

    def test_sync_429_without_headers_uses_backoff(self, monkeypatch):
        """Per-second per-IP limit sends no Retry-After — exponential backoff as before."""
        sleeps: list[float] = []
        monkeypatch.setattr("data.intervals.client.time.sleep", lambda s: sleeps.append(s))
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, content=b"")
            return httpx.Response(200, json={"ok": True})

        client = _sync_client_with(handler)
        try:
            assert client._request("GET", "/ping").status_code == 200
        finally:
            client._client.close()
        assert calls["n"] == 2
        assert sleeps == [10]

    def test_execute_does_not_swallow_rate_limit(self):
        """``_execute`` only special-cases HTTPStatusError (401/403/404) — the
        typed rate-limit error must reach the actor untouched."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "24173"}, content=b"")

        client = _sync_client_with(handler)
        try:
            with pytest.raises(IntervalsRateLimitError):
                client.get_wellness(date(2026, 9, 10))
        finally:
            client._client.close()

    async def test_async_long_retry_after_raises_without_sleeping(self, monkeypatch):
        sleeps: list[float] = []

        async def _sleep(s):
            sleeps.append(s)

        monkeypatch.setattr("data.intervals.client.asyncio.sleep", _sleep)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "812"}, content=b"")

        client = IntervalsAsyncClient(athlete_id="i1", access_token="k")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(IntervalsRateLimitError) as exc_info:
                await client._request("GET", "/ping")
        finally:
            await client._client.aclose()
        assert calls["n"] == 1
        assert sleeps == []
        assert exc_info.value.retry_after == 812

    def test_is_retry_not_access_error(self):
        """Actors swallow IntervalsAccessError as «skip this user» — a quota hit
        must NOT be swallowed; it must propagate to QuotaAwareRetries."""
        err = IntervalsRateLimitError(900, method="GET", path="/x")
        assert isinstance(err, Retry)
        assert not isinstance(err, IntervalsAccessError)
