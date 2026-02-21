"""Multi-provider LLM adapter. All providers behind one async chat() call."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import httpx

# --- Data types ---


@dataclass
class ModelConfig:
    provider: str  # 'openai', 'anthropic', 'ollama', 'custom'
    model: str  # e.g. 'gpt-4o', 'claude-sonnet-4-20250514', 'llama3'
    api_key: str | None = None
    base_url: str | None = None


@dataclass
class Message:
    role: str  # 'user', 'assistant', 'system', 'tool'
    content: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass
class ChatResponse:
    content: str | None = None
    tool_calls: list[dict] | None = None
    usage: dict = field(default_factory=dict)


# --- Provider endpoints ---

PROVIDER_DEFAULTS = {
    "openai": {"base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"},
    "anthropic": {"base_url": "https://api.anthropic.com", "key_env": "ANTHROPIC_API_KEY"},
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GEMINI_API_KEY"},
}


def parse_model_string(model_str: str) -> ModelConfig:
    """Parse 'provider/model' string into ModelConfig, resolving API key from env."""
    if "/" not in model_str:
        raise ValueError(f"Model must be provider/model (e.g. openai/gpt-4o), got: {model_str}")

    provider, model = model_str.split("/", 1)
    defaults = PROVIDER_DEFAULTS.get(provider, {})

    key_env = defaults.get("key_env")
    api_key = os.environ.get(key_env) if key_env else None

    if key_env and not api_key and provider != "ollama":
        raise ValueError(f"Missing API key: set {key_env} environment variable for {provider}")

    return ModelConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=defaults.get("base_url"),
    )


# --- Chat function ---


async def chat(
    config: ModelConfig,
    messages: list[Message],
    tools: list[ToolDef] | None = None,
    temperature: float = 0.7,
) -> ChatResponse:
    """Send a chat completion request to the configured provider."""
    if config.provider == "anthropic" and not config.base_url:
        return await _chat_anthropic(config, messages, tools, temperature)
    return await _chat_openai_compat(config, messages, tools, temperature)


# --- OpenAI-compatible path (OpenAI, Ollama, Gemini, custom) ---


def _build_openai_request(
    config: ModelConfig,
    messages: list[Message],
    tools: list[ToolDef] | None,
    temperature: float,
) -> dict:
    """Build the request body for OpenAI-compatible endpoints."""
    body: dict = {
        "model": config.model,
        "messages": [_message_to_openai(m) for m in messages],
        "temperature": temperature,
    }
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]
    return body


def _message_to_openai(m: Message) -> dict:
    msg: dict = {"role": m.role}
    if m.content is not None:
        msg["content"] = m.content
    if m.tool_calls:
        msg["tool_calls"] = m.tool_calls
    if m.tool_call_id:
        msg["tool_call_id"] = m.tool_call_id
    return msg


def _parse_openai_response(data: dict) -> ChatResponse:
    choice = data["choices"][0]
    msg = choice["message"]
    return ChatResponse(
        content=msg.get("content"),
        tool_calls=msg.get("tool_calls"),
        usage=data.get("usage", {}),
    )


async def _chat_openai_compat(
    config: ModelConfig,
    messages: list[Message],
    tools: list[ToolDef] | None,
    temperature: float,
) -> ChatResponse:
    base_url = config.base_url or "https://api.openai.com/v1"
    url = f"{base_url}/chat/completions"

    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    body = _build_openai_request(config, messages, tools, temperature)

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
        except httpx.ConnectError:
            raise ConnectionError(
                f"Cannot connect to {config.provider} at {base_url}. "
                f"Is the service running?"
            )
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"{config.provider} API error {e.response.status_code}: {e.response.text}")

    return _parse_openai_response(resp.json())


# --- Anthropic native path ---


def _build_anthropic_request(
    config: ModelConfig,
    messages: list[Message],
    tools: list[ToolDef] | None,
    temperature: float,
) -> dict:
    """Build Anthropic Messages API request body."""
    # Separate system message from conversation
    system_text = None
    conv_messages = []
    for m in messages:
        if m.role == "system":
            system_text = m.content
        elif m.role == "tool":
            conv_messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}],
            })
        elif m.role == "assistant" and m.tool_calls:
            content = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                fn = tc["function"]
                content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": fn["name"],
                    "input": json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"],
                })
            conv_messages.append({"role": "assistant", "content": content})
        else:
            conv_messages.append({"role": m.role, "content": m.content or ""})

    body: dict = {
        "model": config.model,
        "max_tokens": 4096,
        "messages": conv_messages,
        "temperature": temperature,
    }
    if system_text:
        body["system"] = system_text
    if tools:
        body["tools"] = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]
    return body


def _parse_anthropic_response(data: dict) -> ChatResponse:
    content_text = None
    tool_calls = None
    for block in data.get("content", []):
        if block["type"] == "text":
            content_text = (content_text or "") + block["text"]
        elif block["type"] == "tool_use":
            if tool_calls is None:
                tool_calls = []
            tool_calls.append({
                "id": block["id"],
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block["input"]),
                },
            })
    return ChatResponse(
        content=content_text,
        tool_calls=tool_calls,
        usage=data.get("usage", {}),
    )


async def _chat_anthropic(
    config: ModelConfig,
    messages: list[Message],
    tools: list[ToolDef] | None,
    temperature: float,
) -> ChatResponse:
    base_url = config.base_url or "https://api.anthropic.com"
    url = f"{base_url}/v1/messages"

    headers = {
        "Content-Type": "application/json",
        "x-api-key": config.api_key or "",
        "anthropic-version": "2023-06-01",
    }

    body = _build_anthropic_request(config, messages, tools, temperature)

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
        except httpx.ConnectError:
            raise ConnectionError(
                f"Cannot connect to Anthropic at {base_url}. Check your connection."
            )
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Anthropic API error {e.response.status_code}: {e.response.text}")

    return _parse_anthropic_response(resp.json())
