"""Tests for mcp_server/tools/ctl_prediction.py — thin wrapper over
``data.metrics.project_ctl_target``.

The wrapper's job is fetching wellness rows + sport-filtering, then mapping
the shared projector's ``{reason}`` output back to the legacy response shape
the morning-report prompt expects (``estimated_date`` / ``confidence`` /
``note`` / ``error``). We mock the DB layer; the projection math itself is
covered by ``tests/metrics/test_ctl_projection.py``.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _wellness_rows(today: date, ctls: list[float]) -> list[tuple[str, float, list | None]]:
    """Build wellness rows in DESC order (newest first) — matches the SQL
    ``ORDER BY date DESC LIMIT 15`` shape inside :func:`predict_ctl`.

    ``ctls`` is in chronological order (oldest → newest); the last element
    becomes today's CTL.
    """
    n = len(ctls)
    # date for ctls[i] is `today - (n-1-i)` days; output list reversed so newest first.
    ordered = [((today - timedelta(days=(n - 1 - i))).isoformat(), float(c), None) for i, c in enumerate(ctls)]
    return list(reversed(ordered))


def _patch_session(rows: list[tuple]):
    """Build an async context manager whose session.execute(...).all() returns
    ``rows``. Mirrors the pattern in ``tests/mcp/test_weight_compliance.py``."""
    execute_result = MagicMock()
    execute_result.all.return_value = rows
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=execute_result)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


@pytest.fixture
def today():
    return date(2026, 5, 11)


@pytest.fixture
def patch_user_context():
    with patch("mcp_server.tools.ctl_prediction.get_current_user_id", return_value=1):
        yield


# ---------------------------------------------------------------------------
# Error envelopes
# ---------------------------------------------------------------------------


class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_under_two_rows_returns_error(self, patch_user_context, today):
        from mcp_server.tools import ctl_prediction

        rows = _wellness_rows(today, [50.0])  # only 1 row
        with patch.object(ctl_prediction, "get_session", lambda: _patch_session(rows)):
            result = await ctl_prediction.predict_ctl(target_ctl=75)
        assert "error" in result
        assert "at least 2 days" in result["error"]

    @pytest.mark.asyncio
    async def test_seven_day_span_insufficient_maps_to_error(self, patch_user_context, today):
        """Series with 2 rows but only 6-day span — `project_ctl_target` returns
        ``reason=insufficient_data``; the wrapper must surface as ``{error}``,
        not silently drop into the happy path."""
        from mcp_server.tools import ctl_prediction

        # Two rows, 6 days apart — under the 7-day threshold
        rows = [
            (today.isoformat(), 52.0, None),
            ((today - timedelta(days=6)).isoformat(), 50.0, None),
        ]
        with patch.object(ctl_prediction, "get_session", lambda: _patch_session(rows)):
            result = await ctl_prediction.predict_ctl(target_ctl=75)
        assert "error" in result
        assert "7+ days" in result["error"]

    @pytest.mark.asyncio
    async def test_sport_with_no_data_returns_error(self, patch_user_context, today):
        from mcp_server.tools import ctl_prediction

        # 14 rows but sport_info=None → no per-sport CTL available
        rows = [((today - timedelta(days=i)).isoformat(), 50.0 + i, None) for i in range(14)]
        with patch.object(ctl_prediction, "get_session", lambda: _patch_session(rows)):
            result = await ctl_prediction.predict_ctl(target_ctl=75, sport="run")
        assert "error" in result
        assert "run" in result["error"]


# ---------------------------------------------------------------------------
# Reason-mapping (success / flat / declining / already_at_target)
# ---------------------------------------------------------------------------


class TestReasonMapping:
    @pytest.mark.asyncio
    async def test_happy_path_returns_estimated_date(self, patch_user_context, today):
        from mcp_server.tools import ctl_prediction

        # Linear climb 60 → 67 over 14 days, target 80 → ETA in ~27 days
        ctls = [60.0 + 0.5 * i for i in range(14)]
        rows = _wellness_rows(today, ctls)
        with patch.object(ctl_prediction, "get_session", lambda: _patch_session(rows)):
            result = await ctl_prediction.predict_ctl(target_ctl=80)
        assert "estimated_date" in result and result["estimated_date"] is not None
        assert result["target_ctl"] == 80
        assert result["current_ctl"] == 66.5
        assert result["ramp_rate_per_week"] == 3.5
        assert result["sport"] == "total"
        # 14 CTL values → 13-day span → "medium" (need ≥14 days for "high")
        assert result["confidence"] == "medium"
        assert result["data_days"] == 13

    @pytest.mark.asyncio
    async def test_already_at_target_returns_note(self, patch_user_context, today):
        from mcp_server.tools import ctl_prediction

        # 14-day span ending above target
        ctls = [70.0] + [75.0] * 12 + [82.0]
        rows = _wellness_rows(today, ctls)
        with patch.object(ctl_prediction, "get_session", lambda: _patch_session(rows)):
            result = await ctl_prediction.predict_ctl(target_ctl=80)
        assert result["estimated_date"] is None
        assert "note" in result
        assert "already" in result["note"].lower()

    @pytest.mark.asyncio
    async def test_declining_returns_note(self, patch_user_context, today):
        from mcp_server.tools import ctl_prediction

        ctls = [70.0 - i for i in range(14)]
        rows = _wellness_rows(today, ctls)
        with patch.object(ctl_prediction, "get_session", lambda: _patch_session(rows)):
            result = await ctl_prediction.predict_ctl(target_ctl=80)
        assert result["estimated_date"] is None
        assert "note" in result
        assert "declining" in result["note"].lower() or "flat" in result["note"].lower()

    @pytest.mark.asyncio
    async def test_flat_returns_note(self, patch_user_context, today):
        from mcp_server.tools import ctl_prediction

        rows = _wellness_rows(today, [60.0] * 14)
        with patch.object(ctl_prediction, "get_session", lambda: _patch_session(rows)):
            result = await ctl_prediction.predict_ctl(target_ctl=80)
        assert result["estimated_date"] is None
        assert "note" in result


# ---------------------------------------------------------------------------
# Confidence heuristic
# ---------------------------------------------------------------------------


class TestConfidence:
    @pytest.mark.asyncio
    async def test_low_when_ramp_above_seven(self, patch_user_context, today):
        from mcp_server.tools import ctl_prediction

        # Steep climb 50 → 75 over 14 days → ramp ≈ 12.5 CTL/wk > 7 → low
        ctls = [50.0 + i * (25 / 13) for i in range(14)]
        rows = _wellness_rows(today, ctls)
        with patch.object(ctl_prediction, "get_session", lambda: _patch_session(rows)):
            result = await ctl_prediction.predict_ctl(target_ctl=100)
        assert result["ramp_rate_per_week"] > 7
        assert result["confidence"] == "low"

    @pytest.mark.asyncio
    async def test_medium_when_span_under_fourteen(self, patch_user_context, today):
        from mcp_server.tools import ctl_prediction

        # 10-day span — span<14 → medium (assuming ramp < 7)
        ctls = [60.0 + 0.3 * i for i in range(10)]
        rows = _wellness_rows(today, ctls)
        with patch.object(ctl_prediction, "get_session", lambda: _patch_session(rows)):
            result = await ctl_prediction.predict_ctl(target_ctl=80)
        assert result["data_days"] == 9
        assert result["confidence"] == "medium"
