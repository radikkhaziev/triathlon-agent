"""Tests for Intervals.icu client retry logic."""

from unittest.mock import MagicMock

import pytest

from data.intervals.client import RETRY_MAX_DELAY, IntervalsClientBase


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
