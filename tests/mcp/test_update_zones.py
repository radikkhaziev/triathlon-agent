"""Tests for ``mcp_server/tools/update_zones.py``.

Closes the coverage gap surfaced in the ZONES_FIX_SPEC §2 Q4 audit. Before
this file there were zero tests on the tool that pushes LTHR/FTP into
Intervals.icu directly (the manual user-initiated path, distinct from the
drift-detector actor flow).
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

_MODULE = "mcp_server.tools.update_zones"


@asynccontextmanager
async def _client_cm(client):
    yield client


def _patch_client(mock_client):
    """Patch ``IntervalsAsyncClient.for_user`` to yield ``mock_client`` from an
    async context manager (the tool uses ``async with for_user(...) as client``)."""
    return patch(
        f"{_MODULE}.IntervalsAsyncClient.for_user",
        return_value=_client_cm(mock_client),
    )


class TestUpdateZones:
    async def test_rejects_missing_inputs(self):
        from mcp_server.tools.update_zones import update_zones

        with patch(f"{_MODULE}.get_current_user_id", return_value=1):
            result = await update_zones(sport="Ride")
        assert "error" in result

    async def test_rejects_unknown_sport(self):
        from mcp_server.tools.update_zones import update_zones

        with patch(f"{_MODULE}.get_current_user_id", return_value=1):
            result = await update_zones(sport="Yoga", ftp=200)
        assert "error" in result

    async def test_rejects_unrealistic_lthr(self):
        from mcp_server.tools.update_zones import update_zones

        with patch(f"{_MODULE}.get_current_user_id", return_value=1):
            result = await update_zones(sport="Run", lthr=300)
        assert "error" in result

    async def test_rejects_unrealistic_ftp(self):
        from mcp_server.tools.update_zones import update_zones

        with patch(f"{_MODULE}.get_current_user_id", return_value=1):
            result = await update_zones(sport="Ride", ftp=20)
        assert "error" in result

    async def test_pushes_ftp_only(self):
        """FTP-only update — payload contains only ``ftp``, not ``lthr``."""
        from mcp_server.tools.update_zones import update_zones

        mock_client = MagicMock()
        mock_client.get_sport_settings = AsyncMock(return_value=MagicMock(ftp=208, lthr=165))
        mock_client.update_sport_settings = AsyncMock(return_value={})

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_client(mock_client),
        ):
            result = await update_zones(sport="Ride", ftp=240)

        mock_client.update_sport_settings.assert_awaited_once_with("Ride", {"ftp": 240})
        assert result["sport"] == "Ride"
        assert result["updated"]["ftp"] == {"old": 208, "new": 240}
        assert "lthr" not in result["updated"]

    async def test_pushes_lthr_only(self):
        from mcp_server.tools.update_zones import update_zones

        mock_client = MagicMock()
        mock_client.get_sport_settings = AsyncMock(return_value=MagicMock(ftp=208, lthr=165))
        mock_client.update_sport_settings = AsyncMock(return_value={})

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_client(mock_client),
        ):
            result = await update_zones(sport="Ride", lthr=172)

        mock_client.update_sport_settings.assert_awaited_once_with("Ride", {"lthr": 172})
        assert result["updated"]["lthr"] == {"old": 165, "new": 172}
        assert "ftp" not in result["updated"]

    async def test_pushes_both(self):
        from mcp_server.tools.update_zones import update_zones

        mock_client = MagicMock()
        mock_client.get_sport_settings = AsyncMock(return_value=MagicMock(ftp=208, lthr=165))
        mock_client.update_sport_settings = AsyncMock(return_value={})

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_client(mock_client),
        ):
            result = await update_zones(sport="Ride", lthr=172, ftp=240)

        mock_client.update_sport_settings.assert_awaited_once_with("Ride", {"lthr": 172, "ftp": 240})
        assert result["updated"]["lthr"]["new"] == 172
        assert result["updated"]["ftp"]["new"] == 240

    async def test_api_failure_returns_error(self):
        from mcp_server.tools.update_zones import update_zones

        mock_client = MagicMock()
        mock_client.get_sport_settings = AsyncMock(return_value=MagicMock(ftp=208, lthr=165))
        mock_client.update_sport_settings = AsyncMock(side_effect=RuntimeError("HTTP 500"))

        with (
            patch(f"{_MODULE}.get_current_user_id", return_value=1),
            _patch_client(mock_client),
        ):
            result = await update_zones(sport="Ride", ftp=240)

        assert "error" in result
        assert "HTTP 500" in result["error"]
