"""
AutoTrader — Telegram Listener Module

Persistent Telethon client that monitors a specific Telegram channel
for new messages and pushes raw signal text to an asyncio queue.

Handles:
- Auto-reconnection on network failures
- FloodWait errors with proper backoff
- Message history catch-up on startup
- Graceful shutdown
"""

from __future__ import annotations

import asyncio
from typing import Optional, Union

from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    ConnectionError as TelethonConnectionError,
    AuthKeyUnregisteredError,
    SessionPasswordNeededError,
)
from telethon.tl.types import Channel, Message

from src.utils import get_logger, utc_now

logger = get_logger("telegram_listener")


class TelegramListener:
    """
    Monitors a Telegram channel for new messages using a user account (Telethon).
    Pushes received message data to an asyncio.Queue for downstream processing.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        phone: str,
        channel_id: Union[int, str],
        session_path: str,
        signal_queue: asyncio.Queue,
        catchup_messages: int = 10,
    ) -> None:
        """
        Args:
            api_id: Telegram API ID
            api_hash: Telegram API hash
            phone: Phone number for auth
            channel_id: Numeric channel ID or channel username to monitor
            session_path: Path to .session file
            signal_queue: Queue to push received messages into
            catchup_messages: Number of recent messages to fetch on startup
        """
        self._api_id = api_id
        self._api_hash = api_hash
        self._phone = phone
        self._channel_id = channel_id
        self._session_path = session_path
        self._signal_queue = signal_queue
        self._catchup_messages = catchup_messages
        self._client: Optional[TelegramClient] = None
        self._connected = False
        self._running = False
        self._channel_entity: Optional[Channel] = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    async def start(self) -> None:
        """
        Initialize and start the Telegram client.
        Registers event handlers and performs message catch-up.
        """
        logger.info("Initializing Telegram listener...")

        self._client = TelegramClient(
            self._session_path,
            self._api_id,
            self._api_hash,
            auto_reconnect=True,
            retry_delay=5,
            connection_retries=10,
            request_retries=5,
        )

        # Start the client — will prompt for code on first run
        try:
            await self._client.start(phone=self._phone)
        except SessionPasswordNeededError:
            logger.error(
                "Two-factor authentication is enabled on this Telegram account. "
                "Run the bot interactively first to enter your 2FA password."
            )
            raise
        except AuthKeyUnregisteredError:
            logger.error(
                "Session is invalid or expired. Delete the .session file and re-authenticate."
            )
            raise

        me = await self._client.get_me()
        logger.info("Logged in as: %s (id=%d)", me.first_name, me.id)

        # Resolve channel entity
        try:
            self._channel_entity = await self._client.get_entity(self._channel_id)
            channel_name = getattr(self._channel_entity, "title", str(self._channel_id))
            logger.info("Monitoring channel: '%s' (id=%d)", channel_name, self._channel_id)
        except Exception as e:
            logger.error(
                "Failed to resolve channel '%s'. Ensure you've joined the channel. Error: %s",
                self._channel_id,
                e,
            )
            raise

        # Register new message handler
        self._client.add_event_handler(
            self._on_new_message,
            events.NewMessage(chats=self._channel_id),
        )

        self._connected = True
        self._running = True

        # Catch-up: fetch recent messages
        await self._catchup()

        logger.info("Telegram listener started — waiting for signals...")

    async def _catchup(self) -> None:
        """Fetch recent messages from the channel on startup for catch-up."""
        if not self._client or self._catchup_messages <= 0:
            return

        logger.info("Fetching last %d messages for catch-up...", self._catchup_messages)
        try:
            messages = await self._client.get_messages(
                self._channel_id,
                limit=self._catchup_messages,
            )
            # Process oldest first
            for msg in reversed(messages):
                if msg.text:
                    await self._enqueue_message(msg)
            logger.info("Catch-up complete: processed %d messages", len(messages))
        except FloodWaitError as e:
            logger.warning(
                "FloodWait during catch-up: sleeping %d seconds", e.seconds
            )
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.warning("Catch-up failed (non-critical): %s", e)

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        """Handler for new messages in the monitored channel."""
        message: Message = event.message

        if not message.text:
            logger.debug("Skipping non-text message (id=%d)", message.id)
            return

        logger.info(
            "New message received: id=%d, length=%d chars",
            message.id, len(message.text),
        )
        logger.debug("Message text: %s", message.text[:200])

        await self._enqueue_message(message)

    async def _enqueue_message(self, message: Message) -> None:
        """Push a message's data to the signal queue."""
        payload = {
            "text": message.text,
            "message_id": message.id,
            "timestamp": message.date or utc_now(),
            "received_at": utc_now(),
        }

        try:
            self._signal_queue.put_nowait(payload)
            logger.debug("Message queued: id=%d, queue_size=%d",
                         message.id, self._signal_queue.qsize())
        except asyncio.QueueFull:
            logger.warning(
                "Signal queue is full (size=%d). Dropping message id=%d. "
                "This indicates the parser/executor can't keep up.",
                self._signal_queue.qsize(), message.id,
            )

    async def run_forever(self) -> None:
        """
        Run the Telegram client until stopped.
        This keeps the event loop alive and processing updates.
        """
        if not self._client:
            raise RuntimeError("Listener not started. Call start() first.")

        try:
            await self._client.run_until_disconnected()
        except Exception as e:
            logger.error("Telegram client disconnected with error: %s", e)
            self._connected = False
            raise

    async def stop(self) -> None:
        """Gracefully disconnect the Telegram client."""
        self._running = False
        self._connected = False
        if self._client:
            logger.info("Disconnecting Telegram client...")
            await self._client.disconnect()
            self._client = None
            logger.info("Telegram client disconnected")

    async def reconnect(self, max_retries: int = 5) -> bool:
        """
        Attempt to reconnect with exponential backoff.

        Returns:
            True if reconnection succeeded, False otherwise.
        """
        for attempt in range(1, max_retries + 1):
            delay = min(2 ** attempt, 60)  # 2, 4, 8, 16, 32, 60, 60...
            logger.warning(
                "Reconnection attempt %d/%d in %ds...",
                attempt, max_retries, delay,
            )
            await asyncio.sleep(delay)

            try:
                if self._client:
                    await self._client.disconnect()
                await self.start()
                logger.info("Reconnected successfully on attempt %d", attempt)
                return True
            except FloodWaitError as e:
                logger.warning("FloodWait on reconnect: sleeping %ds", e.seconds)
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error("Reconnection attempt %d failed: %s", attempt, e)

        logger.critical("All %d reconnection attempts failed", max_retries)
        return False
