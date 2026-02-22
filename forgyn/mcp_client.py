"""MCP (Model Context Protocol) client for external tool discovery."""

from __future__ import annotations

import json
import logging

import httpx

from forgyn.models import ToolDef

log = logging.getLogger(__name__)


class MCPClient:
    """Connects to MCP servers and exposes their tools as ToolDefs."""

    def __init__(self):
        self._servers: dict[str, str] = {}  # name -> url
        self._tools: dict[str, list[ToolDef]] = {}  # server_name -> tools
        self._client: httpx.AsyncClient | None = None

    async def connect(self, name: str, server_url: str) -> list[ToolDef]:
        """Connect to an MCP server and discover its tools."""
        self._servers[name] = server_url
        self._client = self._client or httpx.AsyncClient(timeout=30)

        try:
            resp = await self._client.post(
                server_url,
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            log.error("Failed to connect to MCP server %s: %s", name, e)
            raise ConnectionError(f"Cannot connect to MCP server '{name}': {e}") from e

        tools = []
        for tool_data in data.get("result", {}).get("tools", []):
            tools.append(ToolDef(
                name=f"mcp_{name}_{tool_data['name']}",
                description=tool_data.get("description", ""),
                parameters=tool_data.get("inputSchema", {"type": "object", "properties": {}}),
            ))

        self._tools[name] = tools
        return tools

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict
    ) -> str:
        """Call a tool on an MCP server and return the result."""
        url = self._servers.get(server_name)
        if not url:
            return f"Error: MCP server '{server_name}' not connected."

        self._client = self._client or httpx.AsyncClient(timeout=30)

        try:
            resp = await self._client.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                    "id": 2,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            return f"Error calling tool '{tool_name}': {e}"

        result = data.get("result", {})
        content = result.get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", json.dumps(result))
        return json.dumps(result)

    def get_all_tools(self) -> list[ToolDef]:
        """Return all discovered tools from all connected servers."""
        tools = []
        for server_tools in self._tools.values():
            tools.extend(server_tools)
        return tools

    async def disconnect(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._servers.clear()
        self._tools.clear()
