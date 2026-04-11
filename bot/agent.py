"""Claude AI agent for Telegram bot — thin client over MCP.

Tools are fetched dynamically from MCP server and tool calls are proxied via HTTP.
"""

import base64
import json
import logging

import anthropic
import sentry_sdk

from bot.prompts import get_system_prompt_chat
from bot.tool_filter import filter_tools, select_tool_groups
from bot.tools import MCPClient
from config import settings
from data.db import ApiUsageDaily

logger = logging.getLogger(__name__)


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
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 4096,
        max_iterations: int = 10,
    ) -> tuple[str, dict]:
        """Run Claude API with tool-use loop. Returns (text, usage_totals).

        Tool calls are proxied to MCP server via HTTP.
        """
        total_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}

        cached_system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

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
                    result = await mcp.call_tool(block.name, block.input)
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
        return "\n".join(text_blocks), total_usage

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
        image_data: bytes | None = None,
        image_media_type: str = "image/jpeg",
        image_url: str | None = None,
    ) -> str:
        """Handle a free-form chat message. Stateless: no conversation history.

        Tools are fetched dynamically from MCP server.
        mcp_token: per-user MCP Bearer token. Falls back to global MCP_AUTH_TOKEN.
        """
        mcp = MCPClient(token=mcp_token)
        system = await get_system_prompt_chat(user_id=user_id)
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

        text, usage = await self._run_tool_use_loop(mcp, system, messages, tools, max_tokens=2048)

        try:
            await ApiUsageDaily.increment(user_id=user_id, **usage)
        except Exception:
            logger.warning("Failed to track token usage for user %s", user_id, exc_info=True)

        return text or "Не удалось обработать запрос."
