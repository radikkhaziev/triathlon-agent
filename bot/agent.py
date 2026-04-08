"""Claude AI agent for Telegram bot — thin client over MCP.

Tools are fetched dynamically from MCP server and tool calls are proxied via HTTP.
"""

import base64
import json
import logging

import anthropic
import sentry_sdk

from bot.prompts import get_system_prompt_chat
from bot.tools import MCPClient
from config import settings

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
    ) -> str:
        """Run Claude API with tool-use loop. Returns final text response.

        Tool calls are proxied to MCP server via HTTP.
        """
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )

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
                system=system,
                messages=messages,
                tools=tools,
            )
            iterations += 1

        text_blocks = [b.text for b in response.content if b.type == "text"]
        return "\n".join(text_blocks)

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
        tools = await mcp.list_tools()
        sentry_sdk.set_tag("user_id", user_id)

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

        result = await self._run_tool_use_loop(mcp, system, messages, tools, max_tokens=2048)
        return result or "Не удалось обработать запрос."
