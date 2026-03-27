"""MCP tools for mood check-in data."""

from datetime import date, timedelta

from data.database import get_mood_checkins, save_mood_checkin
from mcp_server.app import mcp


@mcp.tool()
async def save_mood_checkin_tool(
    energy: int | None = None,
    mood: int | None = None,
    anxiety: int | None = None,
    social: int | None = None,
    note: str | None = None,
) -> dict:
    """Record a mood check-in with optional emotion ratings and note.

    At least one field must be provided. All numeric fields use a 1-5 scale.
    Scale meanings:
    - energy: 1=very low, 5=very high
    - mood: 1=very poor, 5=excellent
    - anxiety: 1=very calm, 5=very anxious
    - social: 1=withdrawn, 5=very social

    Args:
        energy: Energy level (1-5)
        mood: Mood (1-5)
        anxiety: Anxiety level (1-5)
        social: Social desire (1-5)
        note: Optional text note
    """
    try:
        row = await save_mood_checkin(energy=energy, mood=mood, anxiety=anxiety, social=social, note=note)
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
    """Get mood check-ins for a date range.

    Returns check-ins from the last N days (inclusive) with all recorded ratings and notes.

    Args:
        date_str: Reference date in YYYY-MM-DD format. Defaults to today.
        days_back: Number of days to look back (inclusive). Default is 7.
    """
    checkins = await get_mood_checkins(target_date=date_str, days_back=days_back)

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
