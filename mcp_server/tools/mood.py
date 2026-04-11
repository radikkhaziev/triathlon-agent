"""MCP tools for mood check-in data."""

from datetime import date, timedelta

from data.db import MoodCheckin
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def save_mood_checkin_tool(
    energy: int | None = None,
    mood: int | None = None,
    anxiety: int | None = None,
    social: int | None = None,
    note: str | None = None,
) -> dict:
    """Record a mood check-in with emotion ratings (1-5 scale) and optional note."""
    user_id = get_current_user_id()
    try:
        row = await MoodCheckin.save(
            user_id=user_id, energy=energy, mood=mood, anxiety=anxiety, social=social, note=note
        )
        return {
            "id": row.id,
            "timestamp": row.timestamp.isoformat(),
            "energy": row.energy,
            "mood": row.mood,
            "anxiety": row.anxiety,
            "social": row.social,
            "note": row.note,
        }
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_mood_checkins_tool(date_str: str | None = None, days_back: int = 7) -> dict:
    """Get mood check-ins for a date range with all ratings and notes."""
    user_id = get_current_user_id()
    checkins = await MoodCheckin.get_range(user_id=user_id, target_date=date_str, days_back=days_back)

    ref = date.fromisoformat(date_str) if date_str else date.today()
    from_date = ref - timedelta(days=days_back - 1)

    if not checkins:
        return {
            "checkins": [],
            "count": 0,
            "period": {"from": str(from_date), "to": str(ref)},
        }

    return {
        "checkins": [
            {
                "id": row.id,
                "timestamp": row.timestamp.isoformat(),
                "energy": row.energy,
                "mood": row.mood,
                "anxiety": row.anxiety,
                "social": row.social,
                "note": row.note,
            }
            for row in checkins
        ],
        "count": len(checkins),
        "period": {"from": str(from_date), "to": str(ref)},
    }
