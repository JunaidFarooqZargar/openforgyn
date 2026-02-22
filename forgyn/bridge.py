"""Message bridge: routes external messages to the reasoner and back."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class Channel(Protocol):
    """Interface that messaging channel implementations must follow."""

    name: str

    async def connect(self) -> None: ...
    async def send_message(self, recipient: str, text: str) -> None: ...
    async def disconnect(self) -> None: ...


class Bridge:
    """Routes incoming messages from channels to the reasoner and responses back."""

    def __init__(self, on_message_fn=None):
        self.on_message_fn = on_message_fn  # async fn(sender, text) -> str
        self.channels: dict[str, Channel] = {}

    def register_channel(self, channel: Channel) -> None:
        """Register a messaging channel."""
        self.channels[channel.name] = channel

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
