"""Tests for MCP client (Step 3.3)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from forgyn.mcp_client import MCPClient


MOCK_TOOLS_RESPONSE = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "tools": [
            {
                "name": "get_weather",
                "description": "Get current weather for a location",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City name"},
                    },
                    "required": ["location"],
                },
            },
            {
                "name": "search",
                "description": "Search the web",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                },
            },
        ]
    },
}

MOCK_CALL_RESPONSE = {
    "jsonrpc": "2.0",
    "id": 2,
    "result": {
        "content": [
            {"type": "text", "text": "Sunny, 72°F in San Francisco"}
        ]
    },
}


def _mock_response(data, status=200):
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status,
        json=data,
        request=httpx.Request("POST", "http://test"),
    )


# --- connect ---


@pytest.mark.asyncio
async def test_list_tools():
    client = MCPClient()
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(MOCK_TOOLS_RESPONSE)
        tools = await client.connect("weather_server", "http://localhost:3000/mcp")

    assert len(tools) == 2
    assert tools[0].name == "mcp_weather_server_get_weather"
    assert tools[1].name == "mcp_weather_server_search"
    assert "location" in tools[0].parameters["properties"]
    await client.disconnect()


@pytest.mark.asyncio
async def test_get_all_tools():
    client = MCPClient()
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(MOCK_TOOLS_RESPONSE)
        await client.connect("server1", "http://localhost:3000/mcp")
        await client.connect("server2", "http://localhost:3001/mcp")

    all_tools = client.get_all_tools()
    assert len(all_tools) == 4  # 2 tools per server
    await client.disconnect()


# --- call_tool ---


@pytest.mark.asyncio
async def test_call_tool():
    client = MCPClient()
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [
            _mock_response(MOCK_TOOLS_RESPONSE),
            _mock_response(MOCK_CALL_RESPONSE),
        ]
        await client.connect("weather", "http://localhost:3000/mcp")
        result = await client.call_tool("weather", "get_weather", {"location": "SF"})

    assert "Sunny" in result
    assert "72°F" in result
    await client.disconnect()


@pytest.mark.asyncio
async def test_call_tool_unknown_server():
    client = MCPClient()
    result = await client.call_tool("nonexistent", "get_weather", {})
    assert "Error" in result
    await client.disconnect()


# --- connection errors ---


@pytest.mark.asyncio
async def test_connection_error():
    client = MCPClient()
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("Connection refused")
        with pytest.raises(ConnectionError, match="Cannot connect"):
            await client.connect("bad", "http://localhost:9999/mcp")
    await client.disconnect()


# --- disconnect ---


@pytest.mark.asyncio
async def test_disconnect_clears_state():
    client = MCPClient()
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_response(MOCK_TOOLS_RESPONSE)
        await client.connect("server", "http://localhost:3000/mcp")

    assert len(client.get_all_tools()) == 2
    await client.disconnect()
    assert len(client.get_all_tools()) == 0
