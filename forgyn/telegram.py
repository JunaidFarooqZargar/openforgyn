"""Telegram Bot API channel using long-polling."""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"
MAX_MESSAGE_LENGTH = 4096
POLL_TIMEOUT = 30  # seconds — Telegram holds the connection open this long
DRAIN_INTERVAL = 10  # seconds between outbox drain checks


class TelegramChannel:
    """Receives and sends messages via the Telegram Bot API.

    Uses long-polling (getUpdates) — no webhooks, no exposed ports.
    """

    name = "telegram"

    def __init__(self, token: str):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        self._base_url = TELEGRAM_API.format(token=token)
        self._client: httpx.AsyncClient | None = None
        self._offset: int = 0
        self._bridge = None  # set via set_bridge() after registration
        self._poll_task: asyncio.Task | None = None
        self._drain_task: asyncio.Task | None = None
        self._running = False

    def set_bridge(self, bridge) -> None:
        """Inject bridge reference after registration (avoids circular dependency)."""
        self._bridge = bridge

    async def connect(self) -> None:
        """Validate the bot token and start the polling loop."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0),
        )
        # Validate token — fail fast on bad credentials
        resp = await self._client.get(f"{self._base_url}/getMe")
        resp.raise_for_status()
        bot_info = resp.json().get("result", {})
        bot_name = bot_info.get("username", "unknown")
        log.info("Connected as @%s", bot_name)

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._drain_task = asyncio.create_task(self._drain_loop())

    async def send_message(self, recipient: str, text: str) -> None:
        """Send a message to a Telegram chat, splitting if too long."""
        if not self._client:
            return
        for chunk in _split_message(text):
            try:
                resp = await self._client.post(
                    f"{self._base_url}/sendMessage",
                    json={"chat_id": int(recipient), "text": chunk},
                )
                if resp.status_code == 429:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                    log.warning("Rate limited, retrying after %ds", retry_after)
                    await asyncio.sleep(retry_after)
                    await self._client.post(
                        f"{self._base_url}/sendMessage",
                        json={"chat_id": int(recipient), "text": chunk},
                    )
            except httpx.HTTPError as e:
                log.error("Failed to send message to %s: %s", recipient, e)

    async def disconnect(self) -> None:
        """Stop polling and close the HTTP client."""
        self._running = False
        for task in (self._poll_task, self._drain_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._poll_task = None
        self._drain_task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _poll_loop(self) -> None:
        """Long-poll Telegram for new messages and route them through the bridge."""
        while self._running:
            try:
                resp = await self._client.get(
                    f"{self._base_url}/getUpdates",
                    params={"offset": self._offset, "timeout": POLL_TIMEOUT},
                )
                if resp.status_code != 200:
                    log.warning("getUpdates returned %d", resp.status_code)
                    await asyncio.sleep(5)
                    continue

                for update in resp.json().get("result", []):
                    self._offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    text = msg.get("text")
                    chat_id = msg.get("chat", {}).get("id")
                    if text and chat_id and self._bridge:
                        await self._bridge.handle_message("telegram", str(chat_id), text)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("Poll error: %s", e)
                await asyncio.sleep(5)

    async def _drain_loop(self) -> None:
        """Periodically send proactive outbox messages to Telegram chats."""
        while self._running:
            await asyncio.sleep(DRAIN_INTERVAL)
            if not self._bridge:
                continue
            try:
                for row in self._bridge.drain_full("telegram"):
                    recipient = row.get("recipient")
                    text = row.get("text", "")
                    if recipient and text:
                        await self.send_message(recipient, text)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("Drain error: %s", e)


def _split_message(text: str) -> list[str]:
    """Split text into chunks that fit Telegram's 4096-char limit."""
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]
    chunks = []
    while text:
        if len(text) <= MAX_MESSAGE_LENGTH:
            chunks.append(text)
            break
        # Try to split at the last newline within the limit
        cut = text.rfind("\n", 0, MAX_MESSAGE_LENGTH)
        if cut <= 0:
            cut = MAX_MESSAGE_LENGTH
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
