"""Tests for model adapter (Step 1.3)."""

from unittest.mock import patch

import pytest

from forgyn.models import (
    Message,
    ModelConfig,
    ToolDef,
    _build_anthropic_request,
    _build_openai_request,
    _parse_anthropic_response,
    _parse_openai_response,
    chat,
    parse_model_string,
)


# --- parse_model_string ---


def test_parse_openai():
    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        config = parse_model_string("openai/gpt-4o")
    assert config.provider == "openai"
    assert config.model == "gpt-4o"
    assert config.api_key == "sk-test"


def test_parse_ollama():
    config = parse_model_string("ollama/llama3")
    assert config.provider == "ollama"
    assert config.model == "llama3"
    assert config.api_key is None


def test_parse_invalid_format():
    with pytest.raises(ValueError, match="provider/model"):
        parse_model_string("just-a-model-name")


def test_parse_missing_api_key():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="Missing API key"):
            parse_model_string("openai/gpt-4o")


# --- OpenAI request building ---


def test_build_openai_request():
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="sk-test")
    messages = [Message(role="user", content="Hello")]
    body = _build_openai_request(config, messages, None, 0.7)

    assert body["model"] == "gpt-4o"
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"
    assert "tools" not in body


def test_build_openai_request_with_tools():
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="sk-test")
    messages = [Message(role="user", content="Hello")]
    tools = [ToolDef(name="test_tool", description="A test", parameters={"type": "object"})]
    body = _build_openai_request(config, messages, tools, 0.7)

    assert len(body["tools"]) == 1
    assert body["tools"][0]["function"]["name"] == "test_tool"


# --- OpenAI response parsing ---


def test_parse_openai_response():
    data = {
        "choices": [{"message": {"content": "Hello!", "role": "assistant"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    resp = _parse_openai_response(data)
    assert resp.content == "Hello!"
    assert resp.tool_calls is None
    assert resp.usage["prompt_tokens"] == 10


def test_parse_openai_tool_calls():
    data = {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "tc_1",
                    "type": "function",
                    "function": {"name": "list_skills", "arguments": "{}"},
                }],
            },
        }],
        "usage": {},
    }
    resp = _parse_openai_response(data)
    assert resp.content is None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0]["function"]["name"] == "list_skills"


# --- Anthropic request building ---


def test_build_anthropic_request():
    config = ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514", api_key="sk-ant-test")
    messages = [
        Message(role="system", content="You are helpful."),
        Message(role="user", content="Hello"),
    ]
    body = _build_anthropic_request(config, messages, None, 0.7)

    assert body["model"] == "claude-sonnet-4-20250514"
    assert body["system"] == "You are helpful."
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"


def test_build_anthropic_tool_result():
    config = ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514", api_key="sk-ant-test")
    messages = [
        Message(role="user", content="List skills"),
        Message(role="assistant", content=None, tool_calls=[{
            "id": "tc_1", "type": "function",
            "function": {"name": "list_skills", "arguments": "{}"},
        }]),
        Message(role="tool", content="No skills installed.", tool_call_id="tc_1"),
    ]
    body = _build_anthropic_request(config, messages, None, 0.7)

    assert len(body["messages"]) == 3
    # Tool result should be converted to user message with tool_result content
    tool_msg = body["messages"][2]
    assert tool_msg["role"] == "user"
    assert tool_msg["content"][0]["type"] == "tool_result"


# --- Anthropic response parsing ---


def test_parse_anthropic_response():
    data = {
        "content": [{"type": "text", "text": "Hello!"}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    resp = _parse_anthropic_response(data)
    assert resp.content == "Hello!"
    assert resp.tool_calls is None


def test_parse_anthropic_tool_use():
    data = {
        "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "tu_1", "name": "list_skills", "input": {}},
        ],
        "usage": {},
    }
    resp = _parse_anthropic_response(data)
    assert resp.content == "Let me check."
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0]["function"]["name"] == "list_skills"


# --- Connection error handling ---


@pytest.mark.asyncio
async def test_connection_error_handling():
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="sk-test",
                         base_url="http://localhost:1")  # unreachable
    messages = [Message(role="user", content="Hello")]
    with pytest.raises(ConnectionError, match="Cannot connect"):
        await chat(config, messages)
