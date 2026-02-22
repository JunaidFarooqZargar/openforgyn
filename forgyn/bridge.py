"""Message bridge: routes external messages to the reasoner and back."""

from __future__ import annotations

import logging
import sqlite3
from typing import Protocol, runtime_checkable

import click

from forgyn.db import drain_outbox, push_outbox

log = logging.getLogger(__name__)


@runtime_checkable
class Channel(Protocol):
    """Interface that messaging channel implementations must follow."""

    name: str

    async def connect(self) -> None: ...
    async def send_message(self, recipient: str, text: str) -> None: ...
    async def disconnect(self) -> None: ...


class CLIChannel:
    """Channel for the interactive CLI — proactive messages print to terminal."""

    name = "cli"

    async def connect(self) -> None:
        pass  # CLI is always connected

    async def send_message(self, recipient: str, text: str) -> None:
        click.echo(f"\n[Forgyn] {text}\n")

    async def disconnect(self) -> None:
        pass


class Bridge:
    """Routes incoming messages from channels to the reasoner and responses back."""

    def __init__(self, on_message_fn=None, db: sqlite3.Connection | None = None):
        self.on_message_fn = on_message_fn  # async fn(sender, text) -> str
        self.db = db
        self.channels: dict[str, Channel] = {}

    def register_channel(self, channel: Channel) -> None:
        """Register a messaging channel."""
        self.channels[channel.name] = channel

    async def push(self, text: str, channel: str | None = None, recipient: str | None = None) -> None:
        """Write a proactive message to the outbox."""
        if self.db is not None:
            push_outbox(self.db, text, channel, recipient)

    def drain(self, channel_name: str | None = None) -> list[str]:
        """Fetch pending outbox messages for a channel. Returns list of text strings."""
        if self.db is None:
            return []
        rows = drain_outbox(self.db, channel_name)
        return [r["text"] for r in rows]

    async def start(self) -> None:
        """Connect all registered channels."""
        for ch in self.channels.values():
            try:
                await ch.connect()
            except Exception as e:
                log.error("Channel %s failed to connect: %s", ch.name, e)

    async def stop(self) -> None:
        """Disconnect all registered channels."""
        for ch in self.channels.values():
            try:
                await ch.disconnect()
            except Exception as e:
                log.error("Channel %s failed to disconnect: %s", ch.name, e)

    async def handle_message(self, channel_name: str, sender: str, text: str) -> str:
        """Process an incoming message and return the response."""
        if not self.on_message_fn:
            return "No message handler configured."

        try:
            response = await self.on_message_fn(sender, text)
        except Exception as e:
            log.error("Message handling failed for %s: %s", channel_name, e)
            return f"Error processing message: {e}"

        # Send response back through the channel
        channel = self.channels.get(channel_name)
        if channel:
            try:
                await channel.send_message(sender, response)
            except Exception as e:
                log.error("Failed to send response on %s: %s", channel_name, e)

        return response
