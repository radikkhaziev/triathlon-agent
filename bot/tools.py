"""Async MCP client for Telegram bot — proxies tool calls to /mcp via HTTP.

MCP Streamable HTTP protocol: initialize → notifications/initialized → tools/list | tools/call.
Responses are SSE (text/event-stream). Session ID is required after initialize.
"""

import json
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


class MCPClient:
    """Async MCP client that communicates with /mcp via Streamable HTTP.

    Tool list is cached at class level (same for all users).
    Session is per-instance (per-token).
    """

    # NB: class-level cache assumes all users see the same tool list.
    # If per-user tool visibility is added, switch to per-token caching.
    _tools_cache: list[dict] | None = None

    def __init__(
        self,
        mcp_url: str | None = None,
        token: str | None = None,
    ):
        self.mcp_url = (mcp_url or f"{settings.API_BASE_URL}/mcp").rstrip("/") + "/"
        self._token = token or settings.MCP_AUTH_TOKEN.get_secret_value()
        self._session_id: str | None = None

    def _headers(self, *, with_session: bool = True) -> dict:
        h = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if with_session and self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    @staticmethod
    def _parse_sse(text: str) -> dict:
        """Extract JSON-RPC result from SSE response body."""
        for line in text.split("\n"):
            if line.startswith("data: "):
                return json.loads(line[6:])
        return {}

    @staticmethod
    def _to_anthropic_tool(tool: dict) -> dict:
        """Convert MCP tool schema (inputSchema) to Anthropic format (input_schema)."""
        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("inputSchema", {"type": "object", "properties": {}}),
        }

    async def _ensure_session(self) -> None:
        """Initialize MCP session if not already established."""
        if self._session_id is not None:
            return

        init_payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "triathlon-bot", "version": "1.0"},
            },
            "id": 1,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self.mcp_url, json=init_payload, headers=self._headers(with_session=False))
            resp.raise_for_status()
            self._session_id = resp.headers.get("mcp-session-id")

            notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            await client.post(self.mcp_url, json=notif, headers=self._headers())

        logger.info("MCP session initialized: %s", self._session_id)

    async def _post(self, client: httpx.AsyncClient, payload: dict) -> httpx.Response:
        """POST to MCP with automatic session recovery on 409 Conflict."""
        resp = await client.post(
            self.mcp_url,
            json=payload,
            headers=self._headers(),
        )
        if resp.status_code == 409:
            logger.warning("MCP session expired (409), re-initializing")
            self._session_id = None
            await self._ensure_session()
            resp = await client.post(self.mcp_url, json=payload, headers=self._headers())
            if resp.status_code == 409:
                logger.error("MCP 409 persists after re-init, session_id=%s", self._session_id)
        resp.raise_for_status()
        return resp

    async def list_tools(self, *, force_refresh: bool = False) -> list[dict]:
        """Fetch available tools from MCP. Returns Anthropic tool-use format.

        Cached at class level — tool list is the same for all users.
        """
        if MCPClient._tools_cache is not None and not force_refresh:
            return MCPClient._tools_cache

        await self._ensure_session()

        payload = {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await self._post(client, payload)

        data = self._parse_sse(resp.text)
        if "error" in data:
            logger.error("MCP tools/list error: %s", data["error"])
            return []

        mcp_tools = data.get("result", {}).get("tools", [])
        MCPClient._tools_cache = [self._to_anthropic_tool(t) for t in mcp_tools]

        logger.info("Loaded %d tools from MCP", len(MCPClient._tools_cache))
        return MCPClient._tools_cache

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Call an MCP tool via JSON-RPC tools/call. Returns parsed result dict."""
        await self._ensure_session()

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
            "id": 3,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await self._post(client, payload)

        data = self._parse_sse(resp.text)

        if "error" in data:
            logger.warning("MCP tool %s error: %s", name, data["error"])
            return {"error": str(data["error"])}

        result = data.get("result", {})
        content = result.get("content", [])
        for block in content:
            if block.get("type") != "text":
                continue
            try:
                return json.loads(block["text"])
            except (ValueError, KeyError):
                return {"text": block["text"]}
        return {}

    def reset(self) -> None:
        """Reset session (e.g., after server restart)."""
        self._session_id = None
