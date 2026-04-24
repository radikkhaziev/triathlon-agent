"""MCP tools for long-term user memory (``user_facts`` table).

Thin wrappers over ``data.db.UserFact``. All tenant scoping goes through
``get_current_user_id()`` — no ``user_id`` parameter on any tool. See
``docs/USER_CONTEXT_SPEC.md`` for semantics, caps, and race protection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select

from data.db import ApiUsageDaily, User, UserFact
from data.db.common import get_session
from mcp_server.app import mcp
from mcp_server.context import get_current_user_id
from mcp_server.sentry import sentry_tool


def _parse_expires_at(raw: str | None) -> datetime | None:
    """Accept either ``"YYYY-MM-DD"`` or a full ISO-8601 datetime.

    Bare dates are pinned to end-of-day UTC so a fact tagged
    ``expires_at='2026-10-31'`` stays active through the whole day.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as e:
        raise ValueError(f"expires_at must be ISO-8601 date or datetime, got {raw!r}") from e
    if parsed.tzinfo is None:
        # Date-only inputs come back as midnight naive — push to EoD UTC.
        if len(raw) == 10:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fact_to_dict(row: UserFact) -> dict:
    return {
        "id": row.id,
        "topic": row.topic,
        "fact": row.fact,
        "fact_language": row.fact_language,
        "source": row.source,
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "deactivated_at": row.deactivated_at.isoformat() if row.deactivated_at else None,
        "deactivated_reason": row.deactivated_reason,
    }


@mcp.tool()
@sentry_tool
async def save_fact(topic: str, fact: str, expires_at: str | None = None) -> dict:
    """Save ONE lasting trait about the user (still relevant in 2+ weeks):
    injuries, schedule, family, preferences, equipment, travel, job, health.
    Transient moods → save_mood_checkin_tool. Data already in athlete_goals /
    athlete_settings → skip. One fact per call.

    topic: short slot (injury, health, schedule, family, preference, equipment,
           travel, job — or a new one, stay consistent with prior).
    fact:  ≤300 chars, first-person-about-user, include date if time-bound.
           e.g. "right knee hurts after 10K on 2026-04-12".
    expires_at: ISO date/datetime, null = indefinite.

    Call list_facts first if you may duplicate an existing fact.
    Returns {fact_id, evicted_ids, warning}. If `warning` is non-null the
    user is close to the hard cap — call list_facts and prune stale ones
    via deactivate_fact before saving more.
    """
    user_id = get_current_user_id()
    try:
        expires_dt = _parse_expires_at(expires_at)
    except ValueError as e:
        return {"error": str(e)}

    # Pull the user's current language so Phase 3 Settings UI can group /
    # translate facts later without a backfill pass (see spec §11.1).
    user = await User.get_by_id(user_id)
    fact_language = getattr(user, "language", None) if user else None

    try:
        result = await UserFact.save_with_cap(
            user_id=user_id,
            topic=topic,
            fact=fact,
            fact_language=fact_language,
            source="tool",
            expires_at=expires_dt,
        )
    except ValueError as e:
        return {"error": str(e)}

    return {
        "fact_id": result["fact_id"],
        "evicted_ids": result["evicted_ids"],
        "warning": result["warning"],
    }


@mcp.tool()
async def list_facts(include_inactive: bool = False) -> dict:
    """List facts the bot remembers about the user. Default: active only.
    Pass include_inactive=True for the full audit trail ("what did you forget?").
    """
    user_id = get_current_user_id()
    rows = await UserFact.list_all(user_id=user_id, include_inactive=include_inactive)
    return {
        "facts": [_fact_to_dict(r) for r in rows],
        "count": len(rows),
        "include_inactive": include_inactive,
    }


_MODEL_ALLOWED_DEACTIVATE_REASONS = {"user_request", "contradicted"}


@mcp.tool()
@sentry_tool
async def deactivate_fact(fact_id: int, reason: str = "user_request") -> dict:
    """Forget a fact. Call when the user asks to drop it, or before saving a
    clear duplicate. reason: "user_request" (default) or "contradicted".
    Reserved — don't pass: topic_cap, hard_cap, expired.
    """
    user_id = get_current_user_id()
    # Server-side whitelist: reserved reasons (topic_cap/hard_cap/expired) are
    # produced only by automatic eviction paths. If the model passes them —
    # hallucination, prompt injection, or drift — they'd pollute
    # ``get_fact_metrics.cap_evictions_7d`` and miscount ``undo_tap_rate_7d``.
    # Coerce silently back to the default; don't leak the rejection to the
    # model (no tool-error that might confuse the chat flow).
    safe_reason = reason if reason in _MODEL_ALLOWED_DEACTIVATE_REASONS else "user_request"
    ok = await UserFact.deactivate(user_id=user_id, fact_id=fact_id, reason=safe_reason)
    return {"fact_id": fact_id, "deactivated": ok}


@mcp.tool()
@sentry_tool
async def reactivate_fact(fact_id: int) -> dict:
    """Internal: restore a deactivated fact. Called by the undo UI only.
    For user-driven re-saves use save_fact (fresh audit entry).
    """
    user_id = get_current_user_id()
    ok = await UserFact.reactivate(user_id=user_id, fact_id=fact_id)
    return {"fact_id": fact_id, "reactivated": ok}


@mcp.tool()
async def get_fact_metrics() -> dict:
    """Fact-memory stats for the current user. Pair with list_facts to answer
    "what have you learned about me lately?". Returns: active_total,
    active_by_topic, facts_written_7d, undo_tap_rate_7d, cap_evictions_7d,
    tool_facts_per_100_msgs_30d, cache_hit_rate_chat_7d.
    """
    user_id = get_current_user_id()
    now = datetime.now(timezone.utc)
    since_7d = now - timedelta(days=7)

    async with get_session() as session:
        # --- Active totals ---
        active_total = int(
            (
                await session.execute(
                    select(func.count(UserFact.id)).where(
                        UserFact.user_id == user_id, UserFact.deactivated_at.is_(None)
                    )
                )
            ).scalar_one()
        )
        topic_rows = (
            await session.execute(
                select(UserFact.topic, func.count(UserFact.id))
                .where(UserFact.user_id == user_id, UserFact.deactivated_at.is_(None))
                .group_by(UserFact.topic)
                .order_by(func.count(UserFact.id).desc())
            )
        ).all()
        active_by_topic = {topic: int(cnt) for topic, cnt in topic_rows}

        # --- 7-day write activity (source='tool' is the only writer in Phase 1,
        #     keeping the filter makes the metric extractor-proof in Phase 2). ---
        writes_7d = int(
            (
                await session.execute(
                    select(func.count(UserFact.id)).where(
                        UserFact.user_id == user_id,
                        UserFact.source == "tool",
                        UserFact.created_at >= since_7d,
                    )
                )
            ).scalar_one()
        )

        # --- Undo-tap rate: user_request deactivations that fired within 10 min
        #     of the save (typical "🗑 Забудь это" tap). Wider windows would
        #     include deliberate cleanup hours/days later — not the signal we
        #     want for the "Claude too jealous" gauge. ---
        undo_fast = int(
            (
                await session.execute(
                    select(func.count(UserFact.id)).where(
                        and_(
                            UserFact.user_id == user_id,
                            UserFact.deactivated_reason == "user_request",
                            UserFact.deactivated_at >= since_7d,
                            (func.extract("epoch", UserFact.deactivated_at - UserFact.created_at)) <= 600,
                        )
                    )
                )
            ).scalar_one()
        )
        undo_tap_rate_7d = round(undo_fast / writes_7d, 3) if writes_7d else None

        # --- Cap evictions (topic_cap + hard_cap over 7d) ---
        cap_evictions_7d = int(
            (
                await session.execute(
                    select(func.count(UserFact.id)).where(
                        UserFact.user_id == user_id,
                        UserFact.deactivated_reason.in_(("topic_cap", "hard_cap")),
                        UserFact.deactivated_at >= since_7d,
                    )
                )
            ).scalar_one()
        )

        # --- Chat-volume gauges (30d and 7d) from api_usage_daily ---
        cutoff_30d_iso = (now - timedelta(days=30)).date().isoformat()
        cutoff_7d_iso = since_7d.date().isoformat()
        usage_30d_row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(ApiUsageDaily.request_count), 0),
                ).where(ApiUsageDaily.user_id == user_id, ApiUsageDaily.date >= cutoff_30d_iso)
            )
        ).one()
        requests_30d = int(usage_30d_row[0] or 0)
        # 30d window from UserFact — write activity used for the Phase 2
        # trigger ratio (spec §11.3), separate from the 7d counter above.
        writes_30d = int(
            (
                await session.execute(
                    select(func.count(UserFact.id)).where(
                        UserFact.user_id == user_id,
                        UserFact.source == "tool",
                        UserFact.created_at >= now - timedelta(days=30),
                    )
                )
            ).scalar_one()
        )
        tool_facts_per_100_msgs_30d = round(writes_30d * 100 / requests_30d, 2) if requests_30d >= 100 else None

        usage_7d = (
            await session.execute(
                select(
                    func.coalesce(func.sum(ApiUsageDaily.input_tokens), 0),
                    func.coalesce(func.sum(ApiUsageDaily.cache_read_tokens), 0),
                ).where(ApiUsageDaily.user_id == user_id, ApiUsageDaily.date >= cutoff_7d_iso)
            )
        ).one()
        input_7d = int(usage_7d[0] or 0)
        cache_read_7d = int(usage_7d[1] or 0)
        cache_hit_rate_chat_7d = round(cache_read_7d / input_7d, 3) if input_7d else None

    return {
        "active_total": active_total,
        "active_by_topic": active_by_topic,
        "facts_written_7d": writes_7d,
        "undo_tap_rate_7d": undo_tap_rate_7d,
        "cap_evictions_7d": cap_evictions_7d,
        "tool_facts_per_100_msgs_30d": tool_facts_per_100_msgs_30d,
        "cache_hit_rate_chat_7d": cache_hit_rate_chat_7d,
    }
