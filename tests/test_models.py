"""Tests for model adapter (Step 1.3)."""

from unittest.mock import patch

import pytest

from forgyn.models import (
    Message,
    ModelConfig,
    ToolDef,
    _build_anthropic_request,
    _build_openai_request,
    _build_openai_responses_request,
    _is_reasoning_model,
    _parse_anthropic_response,
    _parse_openai_response,
    _parse_openai_responses_response,
    _sanitize_tool_parameters,
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


# --- OpenAI Responses API request building ---


def test_build_openai_responses_request():
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="sk-test")
    messages = [
        Message(role="system", content="You are helpful."),
        Message(role="user", content="Hello"),
    ]
    body = _build_openai_responses_request(config, messages, None, 0.7)

    assert body["model"] == "gpt-4o"
    assert body["instructions"] == "You are helpful."
    assert len(body["input"]) == 1
    assert body["input"][0] == {"role": "user", "content": "Hello"}
    assert body["store"] is False
    assert body["temperature"] == 0.7
    assert "tools" not in body


def test_build_openai_responses_request_with_tools():
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="sk-test")
    messages = [Message(role="user", content="Hello")]
    tools = [ToolDef(name="test_tool", description="A test", parameters={"type": "object"})]
    body = _build_openai_responses_request(config, messages, tools, 0.7)

    assert len(body["tools"]) == 1
    assert body["tools"][0]["name"] == "test_tool"
    assert body["tools"][0]["type"] == "function"
    # Flattened — no nested "function" wrapper
    assert "function" not in body["tools"][0]


def test_build_openai_responses_with_tool_calls():
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="sk-test")
    messages = [
        Message(role="user", content="List skills"),
        Message(role="assistant", content=None, tool_calls=[{
            "id": "call_1", "type": "function",
            "function": {"name": "list_skills", "arguments": "{}"},
        }]),
        Message(role="tool", content="No skills installed.", tool_call_id="call_1"),
    ]
    body = _build_openai_responses_request(config, messages, None, 0.7)

    assert len(body["input"]) == 3
    # User message
    assert body["input"][0] == {"role": "user", "content": "List skills"}
    # function_call item
    fc = body["input"][1]
    assert fc["type"] == "function_call"
    assert fc["call_id"] == "call_1"
    assert fc["name"] == "list_skills"
    assert fc["arguments"] == "{}"
    # function_call_output item
    fco = body["input"][2]
    assert fco["type"] == "function_call_output"
    assert fco["call_id"] == "call_1"
    assert fco["output"] == "No skills installed."


# --- OpenAI Responses API response parsing ---


def test_parse_openai_responses_response():
    data = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Hello!"}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    resp = _parse_openai_responses_response(data)
    assert resp.content == "Hello!"
    assert resp.tool_calls is None
    assert resp.usage["input_tokens"] == 10


def test_parse_openai_responses_tool_calls():
    data = {
        "output": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "list_skills",
                "arguments": "{}",
            }
        ],
        "usage": {},
    }
    resp = _parse_openai_responses_response(data)
    assert resp.content is None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0]["id"] == "call_1"
    assert resp.tool_calls[0]["function"]["name"] == "list_skills"
    assert resp.tool_calls[0]["function"]["arguments"] == "{}"


# --- Reasoning model detection ---


def test_is_reasoning_model():
    assert _is_reasoning_model("o1") is True
    assert _is_reasoning_model("o3") is True
    assert _is_reasoning_model("o3-mini") is True
    assert _is_reasoning_model("o4-mini") is True
    assert _is_reasoning_model("gpt-5.2-codex") is True
    assert _is_reasoning_model("gpt-5-mini-2025-08-07") is True
    assert _is_reasoning_model("gpt-5-mini") is True
    assert _is_reasoning_model("gpt-4o") is False
    assert _is_reasoning_model("gpt-4o-mini") is False
    assert _is_reasoning_model("gpt-4.1") is False
    assert _is_reasoning_model("gpt-4.1-mini") is False


def test_build_openai_responses_omits_temperature_for_reasoning():
    config = ModelConfig(provider="openai", model="gpt-5.2-codex", api_key="sk-test")
    messages = [Message(role="user", content="Hello")]
    body = _build_openai_responses_request(config, messages, None, 0.3)

    assert "temperature" not in body
    assert body["model"] == "gpt-5.2-codex"


def test_build_openai_responses_omits_temperature_for_gpt5_mini():
    config = ModelConfig(provider="openai", model="gpt-5-mini-2025-08-07", api_key="sk-test")
    messages = [Message(role="user", content="Hello")]
    body = _build_openai_responses_request(config, messages, None, 0.7)

    assert "temperature" not in body


# --- Tool parameter sanitization ---


def test_sanitize_tool_parameters_strips_forbidden_keys():
    params = {
        "type": "object",
        "properties": {"action": {"type": "string"}},
        "required": ["action"],
        "allOf": [{"if": {"properties": {"action": {"const": "add"}}}, "then": {"required": ["text"]}}],
    }
    cleaned = _sanitize_tool_parameters(params)
    assert "allOf" not in cleaned
    assert cleaned["type"] == "object"
    assert cleaned["properties"] == {"action": {"type": "string"}}
    assert cleaned["required"] == ["action"]


def test_sanitize_tool_parameters_ensures_type_object():
    params = {"properties": {"x": {"type": "string"}}}
    cleaned = _sanitize_tool_parameters(params)
    assert cleaned["type"] == "object"


def test_sanitize_tool_parameters_passes_clean_schema():
    params = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    cleaned = _sanitize_tool_parameters(params)
    assert cleaned == params


def test_build_openai_responses_sanitizes_tool_schemas():
    """Tool schemas with forbidden top-level keys should be sanitized in the request."""
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="sk-test")
    messages = [Message(role="user", content="Hello")]
    tools = [ToolDef(
        name="reminder",
        description="Manage reminders",
        parameters={
            "type": "object",
            "properties": {"action": {"type": "string"}},
            "required": ["action"],
            "anyOf": [{"required": ["text"]}],
        },
    )]
    body = _build_openai_responses_request(config, messages, tools, 0.7)

    assert "anyOf" not in body["tools"][0]["parameters"]
    assert body["tools"][0]["parameters"]["type"] == "object"


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
    """OpenAI routes through Responses API — connection error still raised."""
    config = ModelConfig(provider="openai", model="gpt-4o", api_key="sk-test",
                         base_url="http://localhost:1")  # unreachable
    messages = [Message(role="user", content="Hello")]
    with pytest.raises(ConnectionError, match="Cannot connect"):
        await chat(config, messages)


@pytest.mark.asyncio
async def test_connection_error_handling_compat():
    """Ollama routes through Chat Completions — connection error still raised."""
    config = ModelConfig(provider="ollama", model="llama3",
                         base_url="http://localhost:1")  # unreachable
    messages = [Message(role="user", content="Hello")]
    with pytest.raises(ConnectionError, match="Cannot connect"):
        await chat(config, messages)
