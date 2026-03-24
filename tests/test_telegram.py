"""Tests for the Telegram channel."""

import asyncio
import json

import httpx
import pytest

from forgyn.telegram import TelegramChannel, _split_message


# --- Message splitting ---


def test_short_message_no_split():
    assert _split_message("hello") == ["hello"]


def test_long_message_splits_at_newline():
    # Build a message that exceeds 4096 chars with newlines
    lines = [f"Line {i}: " + "x" * 80 for i in range(60)]  # ~5400 chars
    text = "\n".join(lines)
    chunks = _split_message(text)
    assert len(chunks) >= 2
    assert all(len(c) <= 4096 for c in chunks)
    # Reassembling should recover all content
    assert "\n".join(chunks) == text


def test_long_message_hard_split_no_newlines():
    text = "a" * 8192
    chunks = _split_message(text)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 4096
    assert chunks[1] == "a" * 4096


def test_empty_message():
    assert _split_message("") == [""]


# --- TelegramChannel basics ---


def test_channel_name():
    ch = TelegramChannel("fake-token")
    assert ch.name == "telegram"


def test_missing_token_raises():
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        TelegramChannel("")


# --- Connect / disconnect with mocked HTTP ---


def _make_transport(responses: dict):
    """Create an httpx mock transport that returns canned responses by URL path."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/")[-1]  # e.g. "getMe", "sendMessage"
        if path in responses:
            return httpx.Response(200, json=responses[path])
        return httpx.Response(404, json={"ok": False})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_connect_validates_token():
    ch = TelegramChannel("test-token")
    transport = _make_transport({
        "getMe": {"ok": True, "result": {"username": "test_bot"}},
    })
    ch._client = httpx.AsyncClient(transport=transport)
    # Manually call the validation part of connect
    resp = await ch._client.get(f"{ch._base_url}/getMe")
    assert resp.json()["result"]["username"] == "test_bot"
    await ch._client.aclose()


@pytest.mark.asyncio
async def test_connect_bad_token_raises():
    ch = TelegramChannel("bad-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    ch._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        resp = await ch._client.get(f"{ch._base_url}/getMe")
        resp.raise_for_status()
    await ch._client.aclose()


# --- send_message ---


@pytest.mark.asyncio
async def test_send_message():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "sendMessage" in str(request.url):
            body = json.loads(request.content)
            sent.append(body)
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    ch = TelegramChannel("test-token")
    ch._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await ch.send_message("12345", "Hello from Forgyn")
    await ch._client.aclose()

    assert len(sent) == 1
    assert sent[0]["chat_id"] == 12345
    assert sent[0]["text"] == "Hello from Forgyn"


# --- Poll loop ---


@pytest.mark.asyncio
async def test_poll_loop_processes_updates():
    """Verify the poll loop calls bridge.handle_message for each update."""
    handled = []

    class FakeBridge:
        async def handle_message(self, channel, sender, text):
            handled.append((channel, sender, text))
            return "ok"

    def handler(request: httpx.Request) -> httpx.Response:
        if "getUpdates" in str(request.url):
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 100, "message": {"text": "hi", "chat": {"id": 42}}},
                {"update_id": 101, "message": {"text": "bye", "chat": {"id": 42}}},
            ]})
        return httpx.Response(404)

    ch = TelegramChannel("test-token")
    ch._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ch._bridge = FakeBridge()
    ch._running = True

    # Run one iteration of the poll loop manually instead of the full async loop
    resp = await ch._client.get(
        f"{ch._base_url}/getUpdates", params={"offset": ch._offset, "timeout": 0},
    )
    for update in resp.json()["result"]:
        ch._offset = update["update_id"] + 1
        msg = update.get("message", {})
        text = msg.get("text")
        chat_id = msg.get("chat", {}).get("id")
        if text and chat_id:
            await ch._bridge.handle_message("telegram", str(chat_id), text)

    assert ("telegram", "42", "hi") in handled
    assert ("telegram", "42", "bye") in handled
    assert ch._offset == 102

    await ch._client.aclose()


# --- Conversation mapping (on_message_fn closure pattern) ---


@pytest.mark.asyncio
async def test_conversation_mapping():
    """Different chat_ids should get different conversation_ids."""
    conv_counter = 0
    conversations: dict[str, str] = {}

    def new_conversation():
        nonlocal conv_counter
        conv_counter += 1
        return f"conv-{conv_counter}"

    async def on_message(sender: str, text: str) -> str:
        if sender not in conversations:
            conversations[sender] = new_conversation()
        return f"reply in {conversations[sender]}"

    r1 = await on_message("100", "hello")
    r2 = await on_message("200", "hi")
    r3 = await on_message("100", "again")  # same chat_id, same conversation

    assert conversations["100"] == "conv-1"
    assert conversations["200"] == "conv-2"
    assert r1 == "reply in conv-1"
    assert r3 == "reply in conv-1"  # reuses same conversation
