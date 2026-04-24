"""Claude AI agent for Telegram bot — thin client over MCP.

Tools are fetched dynamically from MCP server and tool calls are proxied via HTTP.
"""

import base64
import copy
import json
import logging
from dataclasses import dataclass, field

import anthropic
import sentry_sdk

from bot.prompts import get_system_prompt_chat  # noqa: F401 — kept for tests that patch this symbol
from bot.prompts import get_static_system_prompt, render_athlete_block
from bot.tool_filter import filter_tools, select_tool_groups
from bot.tools import MCPClient
from config import settings
from data.db import ApiUsageDaily

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    """Return type for `ClaudeAgent.chat()`.

    - `text` — assistant response. Never empty: if Claude returned no text
      blocks, `chat()` supplies the fallback `"Не удалось обработать запрос."`.
    - `tool_calls` — every filtered `tool_use` block the model emitted, in
      order. Each entry: ``{"name": str, "input": dict, "result": dict | Any}``.
      ``result`` is the deep-copied tool-return (mainly needed for post-commit
      tools whose id lives in the result, not the input — e.g. ``save_fact``
      returns ``{"fact_id": ...}`` that the undo button reads). Callers that
      replay a dry-run (e.g. ``/workout`` preview → push) read from ``input``
      and ignore ``result``.
    - `nudge_boundary` — raw signal: today's request_count divides evenly by
      `DONATE_NUDGE_EVERY_N`. Agent does NOT apply donate policy — the handler
      gates this via `bot.donate_nudge.should_show_nudge`. See DONATE_SPEC §11.4.
    - `request_count` — number of `chat()` calls by this user today (post-increment).
      Exposed so the handler's nudge gate can enforce the daily cap.
    """

    text: str
    tool_calls: list[dict] = field(default_factory=list)
    nudge_boundary: bool = False
    request_count: int = 0


class ClaudeAgent:
    def __init__(self) -> None:
        self.client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY.get_secret_value(),
            max_retries=5,
        )
        self.model = "claude-sonnet-4-6"

    async def _run_tool_use_loop(
        self,
        mcp: MCPClient,
        system: str | list[dict],
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 4096,
        max_iterations: int = 10,
        tool_calls_filter: set[str] | None = None,
    ) -> tuple[str, dict, list[dict]]:
        """Run Claude API with tool-use loop. Returns (text, usage_totals, tool_calls).

        ``system`` can be a plain string (wrapped into a single ephemeral cache
        segment — legacy tests patch it as a string) or the already-shaped
        ``list[{"type": "text", "text": ..., "cache_control": ...}]`` that the
        live chat path passes in so the static prompt and the per-user athlete
        block each get their own cache marker (USER_CONTEXT_SPEC §6).

        Tool calls are proxied to MCP server via HTTP. Every ``tool_use`` block
        is recorded (deep-copied) into the returned list so callers can replay
        a dry-run as a real push without re-inference — e.g. ``/workout``
        preview → "Отправить в Intervals" button. ``tool_calls_filter`` narrows
        which tool names are recorded (pass ``{"suggest_workout",
        "compose_workout"}`` to skip deep-copies of unrelated large inputs).
        ``None`` records everything.
        """
        total_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
        tool_calls: list[dict] = []

        if isinstance(system, str):
            cached_system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        else:
            cached_system = system

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=cached_system,
            messages=messages,
            tools=tools,
        )
        self._accumulate_usage(total_usage, response)

        iterations = 0
        while response.stop_reason == "tool_use" and iterations < max_iterations:
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    recorded = tool_calls_filter is None or block.name in tool_calls_filter
                    # Deep-copy the input before dispatch — the tool side or the
                    # caller may mutate either the recorded snapshot or the live
                    # input later, and we want the recording frozen.
                    input_snapshot = copy.deepcopy(block.input) if recorded else None
                    result = await mcp.call_tool(block.name, block.input)
                    if recorded:
                        # Freeze any JSON-like mutable result (dict / list /
                        # tuple / set). Scalars are immutable, skip the copy.
                        frozen_result = (
                            copy.deepcopy(result) if isinstance(result, (dict, list, tuple, set)) else result
                        )
                        tool_calls.append(
                            {
                                "name": block.name,
                                "input": input_snapshot,
                                # `result` is mainly for post-commit ids (save_fact.fact_id)
                                # — replay-a-preview callers ignore it.
                                "result": frozen_result,
                            }
                        )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        }
                    )

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            response = await self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=cached_system,
                messages=messages,
                tools=tools,
            )
            self._accumulate_usage(total_usage, response)
            iterations += 1

        text_blocks = [b.text for b in response.content if b.type == "text"]
        return "\n".join(text_blocks), total_usage, tool_calls

    @staticmethod
    def _accumulate_usage(totals: dict, response) -> None:
        totals["input_tokens"] += response.usage.input_tokens
        totals["output_tokens"] += response.usage.output_tokens
        totals["cache_read_tokens"] += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        totals["cache_creation_tokens"] += getattr(response.usage, "cache_creation_input_tokens", 0) or 0

    async def chat(
        self,
        user_message: str,
        mcp_token: str | None = None,
        user_id: int = 1,
        language: str = "ru",
        image_data: bytes | None = None,
        image_media_type: str = "image/jpeg",
        image_url: str | None = None,
        tool_calls_filter: set[str] | None = None,
    ) -> ChatResult:
        """Handle a free-form chat message. Stateless: no conversation history.

        Returns `ChatResult`. Tool calls are accumulated internally and exposed
        via `ChatResult.tool_calls` (replaces the previous `tool_calls_out`
        out-param). Nudge policy lives in `bot.donate_nudge` — agent only
        reports the raw boundary signal; see DONATE_SPEC §11.4.

        mcp_token: per-user MCP Bearer token. Falls back to global MCP_AUTH_TOKEN.
        tool_calls_filter: optional set of tool names — only matching calls
            are recorded. Use to avoid deep-copying unrelated large inputs.
        """
        mcp = MCPClient(token=mcp_token)
        # Two-segment cache: static prompt stays hot across users/days, per-user
        # athlete block invalidates only on profile/goal/fact updates.
        # See USER_CONTEXT_SPEC §6 for the prefix-hash rationale.
        static_prompt = get_static_system_prompt()
        athlete_block = await render_athlete_block(user_id=user_id, language=language)
        system: list[dict] = [
            {"type": "text", "text": static_prompt, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": athlete_block, "cache_control": {"type": "ephemeral"}},
        ]
        all_tools = await mcp.list_tools()
        sentry_sdk.set_tag("user_id", user_id)

        groups = select_tool_groups(user_message or "")
        tools = filter_tools(all_tools, groups)
        logger.info("Tool filtering: %d/%d tools, groups=%s", len(tools), len(all_tools), sorted(groups))

        if image_data:
            content_blocks: list[dict] = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_media_type,
                        "data": base64.standard_b64encode(image_data).decode("ascii"),
                    },
                },
            ]
            text_part = user_message or ""
            if image_url:
                text_part += f"\n\n[Uploaded screenshot]({image_url})"
            if text_part.strip():
                content_blocks.append({"type": "text", "text": text_part})
            else:
                content_blocks.append(
                    {"type": "text", "text": "Пользователь отправил скриншот. Опиши что видишь и спроси чем помочь."}
                )
            messages: list[dict] = [{"role": "user", "content": content_blocks}]
        else:
            messages = [{"role": "user", "content": user_message}]

        text, usage, tool_calls = await self._run_tool_use_loop(
            mcp,
            system,
            messages,
            tools,
            max_tokens=2048,
            tool_calls_filter=tool_calls_filter,
        )

        nudge_boundary = False
        request_count = 0
        try:
            row = await ApiUsageDaily.increment(user_id=user_id, **usage)
            raw_count = getattr(row, "request_count", 0)
            # Defensive: if a caller mocks `increment()` without a real integer
            # (e.g. bare AsyncMock), don't let arithmetic blow up the chat flow.
            request_count = raw_count if isinstance(raw_count, int) else 0
            nudge_boundary = request_count > 0 and request_count % settings.DONATE_NUDGE_EVERY_N == 0
        except Exception:
            logger.warning("Failed to track token usage for user %s", user_id, exc_info=True)

        return ChatResult(
            text=text or "Не удалось обработать запрос.",
            tool_calls=tool_calls,
            nudge_boundary=nudge_boundary,
            request_count=request_count,
        )
