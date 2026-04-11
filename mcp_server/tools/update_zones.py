"""MCP tool for updating HR/power zones in Intervals.icu."""

from data.intervals.client import IntervalsAsyncClient
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def update_zones(sport: str, lthr: int | None = None, ftp: int | None = None) -> dict:
    """Update LTHR and/or FTP in Intervals.icu. Zones auto-recalculate. Returns old and new values."""
    user_id = get_current_user_id()

    if lthr is None and ftp is None:
        return {"error": "Provide at least one of lthr or ftp to update."}

    if sport not in ("Ride", "Run", "Swim"):
        return {"error": f"Sport must be Ride, Run, or Swim. Got: {sport}"}

    if lthr is not None and (lthr < 80 or lthr > 220):
        return {"error": f"LTHR {lthr} seems unrealistic (expected 80-220 bpm)."}

    if ftp is not None and (ftp < 50 or ftp > 500):
        return {"error": f"FTP {ftp} seems unrealistic (expected 50-500 W)."}

    try:
        async with IntervalsAsyncClient.for_user(user_id) as client:
            current = await client.get_sport_settings(sport)

            update_payload: dict = {}
            changes: dict = {}

            if lthr is not None:
                changes["lthr"] = {"old": current.lthr, "new": lthr}
                update_payload["lthr"] = lthr

            if ftp is not None:
                changes["ftp"] = {"old": current.ftp, "new": ftp}
                update_payload["ftp"] = ftp

            await client.update_sport_settings(sport, update_payload)
    except Exception as e:
        return {"error": f"Intervals.icu API error: {e}"}

    return {
        "sport": sport,
        "updated": changes,
        "zones_recalculated": True,
    }
