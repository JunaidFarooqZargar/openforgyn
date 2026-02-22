"""Tests for message bridge (Step 3.2)."""

import pytest

from forgyn.bridge import Bridge, CLIChannel
from forgyn.db import init_db


class MockChannel:
    """A simple mock channel for testing."""

    def __init__(self, name: str):
        self.name = name
        self.connected = False
        self.sent_messages = []

    async def connect(self):
        self.connected = True

    async def send_message(self, recipient: str, text: str):
        self.sent_messages.append((recipient, text))

    async def disconnect(self):
        self.connected = False


class FailingChannel:
    """A channel that raises on connect/send."""

    def __init__(self, name: str):
        self.name = name

    async def connect(self):
        raise ConnectionError("Cannot connect")

    async def send_message(self, recipient: str, text: str):
        raise ConnectionError("Cannot send")

    async def disconnect(self):
        raise ConnectionError("Cannot disconnect")


# --- Registration ---


def test_register_channel():
    bridge = Bridge()
    ch = MockChannel("test")
    bridge.register_channel(ch)
    assert "test" in bridge.channels
    assert bridge.channels["test"] is ch


def test_register_multiple_channels():
    bridge = Bridge()
    ch1 = MockChannel("whatsapp")
    ch2 = MockChannel("telegram")
    bridge.register_channel(ch1)
    bridge.register_channel(ch2)
    assert len(bridge.channels) == 2


# --- Start / Stop ---


@pytest.mark.asyncio
async def test_start_connects_channels():
    bridge = Bridge()
    ch = MockChannel("test")
    bridge.register_channel(ch)
    await bridge.start()
    assert ch.connected is True


@pytest.mark.asyncio
async def test_stop_disconnects_channels():
    bridge = Bridge()
    ch = MockChannel("test")
    bridge.register_channel(ch)
    await bridge.start()
    await bridge.stop()
    assert ch.connected is False


@pytest.mark.asyncio
async def test_failing_channel_doesnt_crash_start():
    bridge = Bridge()
    good = MockChannel("good")
    bad = FailingChannel("bad")
    bridge.register_channel(good)
    bridge.register_channel(bad)
    await bridge.start()  # Should not raise
    assert good.connected is True


# --- Message routing ---


@pytest.mark.asyncio
async def test_message_routed_to_handler():
    calls = []

    async def handler(sender, text):
        calls.append((sender, text))
        return f"Reply to {text}"

    bridge = Bridge(on_message_fn=handler)
    ch = MockChannel("test")
    bridge.register_channel(ch)

    response = await bridge.handle_message("test", "alice", "hello")
    assert response == "Reply to hello"
    assert calls == [("alice", "hello")]


@pytest.mark.asyncio
async def test_response_sent_back():
    async def handler(sender, text):
        return "Got it!"

    bridge = Bridge(on_message_fn=handler)
    ch = MockChannel("test")
    bridge.register_channel(ch)

    await bridge.handle_message("test", "alice", "hello")
    assert ch.sent_messages == [("alice", "Got it!")]


@pytest.mark.asyncio
async def test_multiple_channels_routed():
    async def handler(sender, text):
        return f"Echo: {text}"

    bridge = Bridge(on_message_fn=handler)
    wa = MockChannel("whatsapp")
    tg = MockChannel("telegram")
    bridge.register_channel(wa)
    bridge.register_channel(tg)

    await bridge.handle_message("whatsapp", "alice", "hi")
    await bridge.handle_message("telegram", "bob", "yo")

    assert wa.sent_messages == [("alice", "Echo: hi")]
    assert tg.sent_messages == [("bob", "Echo: yo")]


@pytest.mark.asyncio
async def test_handler_error_returns_error():
    async def failing_handler(sender, text):
        raise RuntimeError("Handler crash")

    bridge = Bridge(on_message_fn=failing_handler)
    ch = MockChannel("test")
    bridge.register_channel(ch)

    response = await bridge.handle_message("test", "alice", "hello")
    assert "Error" in response


@pytest.mark.asyncio
async def test_no_handler_returns_message():
    bridge = Bridge()  # No handler
    ch = MockChannel("test")
    bridge.register_channel(ch)

    response = await bridge.handle_message("test", "alice", "hello")
    assert "No message handler" in response


# --- Outbox integration ---


@pytest.mark.asyncio
async def test_bridge_push_and_drain(tmp_db_path):
    db = init_db(tmp_db_path)
    bridge = Bridge(db=db)
    await bridge.push("Hello from scheduler")
    msgs = bridge.drain("cli")
    assert msgs == ["Hello from scheduler"]


@pytest.mark.asyncio
async def test_bridge_drain_marks_delivered(tmp_db_path):
    db = init_db(tmp_db_path)
    bridge = Bridge(db=db)
    await bridge.push("Message one")
    bridge.drain("cli")
    # Second drain returns empty
    assert bridge.drain("cli") == []


@pytest.mark.asyncio
async def test_bridge_push_without_db():
    """Bridge without db should silently ignore push/drain."""
    bridge = Bridge()
    await bridge.push("No DB")
    assert bridge.drain("cli") == []


# --- CLIChannel ---


def test_cli_channel_name():
    ch = CLIChannel()
    assert ch.name == "cli"


@pytest.mark.asyncio
async def test_cli_channel_send_message(capsys):
    ch = CLIChannel()
    await ch.send_message("user", "Reminder: drink water")
    captured = capsys.readouterr()
    assert "Reminder: drink water" in captured.out
