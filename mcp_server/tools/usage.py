"""MCP tool for API token usage tracking."""

from datetime import date, timedelta

from data.db import ApiUsageDaily
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id


@mcp.tool()
async def get_api_usage(target_date: str = "", days_back: int = 30) -> dict:
    """Get API token usage for the current user.

    Returns daily breakdown with input/output/cache tokens and request count.
    Useful for monitoring consumption and cost estimation.

    Args:
        target_date: Date in YYYY-MM-DD format. Default: today.
        days_back: Number of days to look back (default: 30).
    """
    user_id = get_current_user_id()
    ref = date.fromisoformat(target_date) if target_date else date.today()
    from_date = ref - timedelta(days=days_back - 1)

    rows = await ApiUsageDaily.get_range(user_id=user_id, target_date=str(ref), days_back=days_back)

    total_input = sum(r.input_tokens for r in rows)
    total_output = sum(r.output_tokens for r in rows)
    total_cache_read = sum(r.cache_read_tokens for r in rows)
    total_cache_creation = sum(r.cache_creation_tokens for r in rows)
    total_requests = sum(r.request_count for r in rows)

    return {
        "period": {"from": str(from_date), "to": str(ref)},
        "totals": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": total_cache_read,
            "cache_creation_tokens": total_cache_creation,
            "total_tokens": total_input + total_output + total_cache_read + total_cache_creation,
            "request_count": total_requests,
        },
        "days_with_data": len(rows),
        "daily": [
            {
                "date": r.date,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cache_read_tokens": r.cache_read_tokens,
                "cache_creation_tokens": r.cache_creation_tokens,
                "request_count": r.request_count,
            }
            for r in rows
        ],
    }
