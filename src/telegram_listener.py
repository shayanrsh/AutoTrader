"""
AutoTrader — Telegram Listener Module

Persistent Telethon client that monitors one or more Telegram channels
for new messages and pushes raw signal text to an asyncio queue.

Uses shared setup utilities from src.telegram_setup for channel parsing
and consistent client creation settings.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Optional, Union

from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    AuthKeyUnregisteredError,
    SessionPasswordNeededError,
)
from telethon.tl.types import Message

try:
    from src.telegram_setup import (
        channel_tokens_to_targets,
        create_telegram_client,
        parse_channel_ids,
    )
    from src.utils import get_logger, setup_logging, utc_now
except ModuleNotFoundError:
    # Supports direct execution from src/ (python3 telegram_listener.py).
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from src.telegram_setup import (
        channel_tokens_to_targets,
        create_telegram_client,
        parse_channel_ids,
    )
    from src.utils import get_logger, setup_logging, utc_now

try:
    from src.config import get_settings
    from src.database import Database
except ModuleNotFoundError:
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from src.config import get_settings
    from src.database import Database

logger = get_logger("telegram_listener")


class TelegramListener:
    """
    Monitors one or more Telegram channels for new messages using a user account (Telethon).
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
            channel_id: Numeric channel ID / username or comma-separated list
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

        channel_tokens = parse_channel_ids(str(channel_id))
        if not channel_tokens:
            raise ValueError("At least one channel id/username must be provided")
        self._channel_tokens = channel_tokens
        self._channel_targets = channel_tokens_to_targets(channel_tokens)
        self._resolved_channels: list[str] = []
        self._message_text_cache: dict[int, str] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    async def start(self) -> None:
        """
        Initialize and start the Telegram client.
        Registers event handlers and performs message catch-up.
        """
        logger.info("Initializing Telegram listener...")

        self._client = create_telegram_client(
            api_id=self._api_id,
            api_hash=self._api_hash,
            session_path=self._session_path,
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

        # Resolve all configured channels
        self._resolved_channels.clear()
        for target in self._channel_targets:
            try:
                entity = await self._client.get_entity(target)
                channel_name = getattr(entity, "title", str(target))
                channel_id = getattr(entity, "id", target)
                label = f"{channel_name} ({channel_id})"
                self._resolved_channels.append(label)
                logger.info("Monitoring channel: %s", label)
            except Exception as e:
                logger.error(
                    "Failed to resolve channel '%s'. Ensure you've joined it. Error: %s",
                    target,
                    e,
                )
                raise

        # Register new message handler
        self._client.add_event_handler(
            self._on_new_message,
            events.NewMessage(chats=self._channel_targets),
        )
        self._client.add_event_handler(
            self._on_edited_message,
            events.MessageEdited(chats=self._channel_targets),
        )
        self._client.add_event_handler(
            self._on_deleted_message,
            events.MessageDeleted(chats=self._channel_targets),
        )

        self._connected = True
        self._running = True

        # Catch-up: fetch recent messages
        await self._catchup()

        logger.info(
            "Telegram listener started — waiting for signals from %d channel(s)",
            len(self._channel_targets),
        )

    async def _catchup(self) -> None:
        """Fetch recent messages from the channel on startup for catch-up."""
        if not self._client or self._catchup_messages <= 0:
            return

        logger.info(
            "Fetching last %d messages per channel for catch-up...",
            self._catchup_messages,
        )
        total_processed = 0
        for target in self._channel_targets:
            try:
                messages = await self._client.get_messages(
                    target,
                    limit=self._catchup_messages,
                )
                # Process oldest first per channel
                for msg in reversed(messages):
                    if msg.text:
                        await self._enqueue_message(msg, event_type="new")
                        total_processed += 1
                logger.info("Catch-up processed %d messages for channel %s", len(messages), target)
            except FloodWaitError as e:
                logger.warning(
                    "FloodWait during catch-up for channel %s: sleeping %d seconds",
                    target,
                    e.seconds,
                )
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.warning("Catch-up failed for channel %s (non-critical): %s", target, e)

        logger.info("Catch-up complete: total processed %d messages", total_processed)

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

        await self._enqueue_message(message, event_type="new")

    async def _on_edited_message(self, event: events.MessageEdited.Event) -> None:
        """Handler for edited messages in the monitored channel(s)."""
        message: Message = event.message

        logger.info(
            "Edited message received: id=%d, length=%d chars",
            message.id,
            len(message.text or ""),
        )
        await self._enqueue_message(message, event_type="edit")

    async def _on_deleted_message(self, event: events.MessageDeleted.Event) -> None:
        """Handler for deleted messages in the monitored channel(s)."""
        deleted_ids = list(getattr(event, "deleted_ids", []) or [])
        channel_id = getattr(event, "chat_id", None)
        if not deleted_ids:
            logger.debug("Received delete event with no message ids")
            return

        for msg_id in deleted_ids:
            old_text = self._message_text_cache.pop(msg_id, None)
            payload = {
                "event_type": "delete",
                "text": "",
                "text_before": old_text,
                "text_after": "[deleted]",
                "message_id": msg_id,
                "channel_id": str(channel_id) if channel_id is not None else None,
                "timestamp": utc_now(),
                "received_at": utc_now(),
            }
            try:
                self._signal_queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning(
                    "Signal queue is full (size=%d). Dropping delete event id=%d",
                    self._signal_queue.qsize(),
                    msg_id,
                )

    def _extract_channel_id(self, message: Message) -> Optional[str]:
        """Extract channel/chat id from a Telethon message object."""
        chat_id = getattr(message, "chat_id", None)
        if chat_id is not None:
            return str(chat_id)

        peer = getattr(message, "peer_id", None)
        if peer is None:
            return None

        for attr in ("channel_id", "chat_id", "user_id"):
            value = getattr(peer, attr, None)
            if value is not None:
                return str(value)
        return None

    async def _enqueue_message(self, message: Message, event_type: str = "new") -> None:
        """Push a message event to the signal queue."""
        text_value = message.text or ""
        previous_text = self._message_text_cache.get(message.id)
        if event_type in {"new", "edit"}:
            self._message_text_cache[message.id] = text_value

        payload = {
            "event_type": event_type,
            "text": text_value,
            "text_before": previous_text if event_type == "edit" else None,
            "text_after": text_value,
            "message_id": message.id,
            "channel_id": self._extract_channel_id(message),
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


def _parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Telegram listener directly with optional live output."
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show background listener status and exit.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop background listener process and exit.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run in foreground (default behavior is background daemon).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Connect, validate channel subscription, print success, and exit.",
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Print incoming telegram events in real time.",
    )
    parser.add_argument(
        "--persist-db",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Persist new/edit/delete events to sqlite telegram_messages table "
            "(default: enabled; use --no-persist-db to disable)."
        ),
    )
    parser.add_argument(
        "--catchup",
        type=int,
        default=10,
        help="Catch-up messages per channel on startup (default: 10).",
    )
    return parser.parse_args()


def _project_root_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _pid_file_path() -> Path:
    return _project_root_dir() / "data" / "telegram_listener.pid"


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid_file() -> Optional[int]:
    pid_file = _pid_file_path()
    if not pid_file.exists():
        return None

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None
    return pid if pid > 0 else None


def _status_background_worker() -> str:
    pid = _read_pid_file()
    if pid is None:
        return "Telegram listener is not running (no pid file)."
    if _is_pid_running(pid):
        return f"Telegram listener is running in background (pid={pid})."

    # Stale pid file
    try:
        _pid_file_path().unlink(missing_ok=True)
    except Exception:
        pass
    return "Telegram listener is not running (stale pid file cleaned)."


def _stop_background_worker() -> str:
    pid = _read_pid_file()
    if pid is None:
        return "Telegram listener is not running."

    if not _is_pid_running(pid):
        try:
            _pid_file_path().unlink(missing_ok=True)
        except Exception:
            pass
        return "Telegram listener was already stopped (stale pid file removed)."

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return f"Failed to stop listener pid={pid}: {exc}"

    try:
        _pid_file_path().unlink(missing_ok=True)
    except Exception:
        pass

    return f"Stop signal sent to telegram listener (pid={pid})."


def _launch_background_worker(args: argparse.Namespace) -> int:
    script_path = Path(__file__).resolve()
    pid_file = _pid_file_path()
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if existing_pid > 0 and _is_pid_running(existing_pid):
                return existing_pid
        except Exception:
            pass

    cmd = [
        sys.executable,
        str(script_path),
        "--worker",
        "--catchup",
        str(max(0, args.catchup)),
    ]
    if args.persist_db:
        cmd.append("--persist-db")
    else:
        cmd.append("--no-persist-db")
    if args.live:
        cmd.append("--live")

    process = subprocess.Popen(  # noqa: S603
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(_project_root_dir()),
        preexec_fn=os.setsid,
        close_fds=True,
    )
    pid_file.write_text(str(process.pid), encoding="utf-8")
    return process.pid


def _format_live_line(payload: dict) -> str:
    event_type = str(payload.get("event_type", "new")).upper()
    msg_id = payload.get("message_id")
    channel_id = payload.get("channel_id")
    text = str(payload.get("text") or "")
    preview = text.replace("\n", " ").strip()
    if len(preview) > 120:
        preview = preview[:120] + "..."
    return f"[{event_type}] channel={channel_id} id={msg_id} text={preview}"


async def _run_standalone_listener() -> None:
    args = _parse_cli_args()

    if args.status:
        print(_status_background_worker())
        return

    if args.stop:
        print(_stop_background_worker())
        return

    # Default UX: start detached background worker and return shell immediately.
    if not args.check and not args.foreground and not args.worker:
        pid = _launch_background_worker(args)
        print(f"[OK] Telegram listener is running in background (pid={pid}).")
        print("You can continue using the terminal while it writes to the database.")
        return

    settings = get_settings()

    setup_logging(
        log_level=settings.log_level,
        log_file_path=settings.log_file_path,
        max_size_mb=settings.log_max_size_mb,
        backup_count=settings.log_backup_count,
    )

    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    listener = TelegramListener(
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        phone=settings.telegram_phone,
        channel_id=settings.telegram_channel_id,
        session_path=settings.telegram_session_path,
        signal_queue=queue,
        catchup_messages=max(0, args.catchup),
    )

    db: Optional[Database] = None
    if args.persist_db:
        db = Database(db_path=settings.database_path)
        await db.connect()
        removed = await db.deduplicate_telegram_events()
        if removed > 0:
            logger.info("Startup deduplication removed %d duplicate rows", removed)

    if args.worker:
        _pid_file_path().write_text(str(os.getpid()), encoding="utf-8")

    await listener.start()
    if args.check:
        print("[OK] Telegram listener startup successful.")
        print("Connected, authenticated, and channel subscription validated.")
        await listener.stop()
        if db is not None:
            await db.close()
        return

    logger.info(
        "Standalone listener is running continuously. live=%s persist_db=%s",
        args.live,
        args.persist_db,
    )
    if args.live:
        print("Live mode enabled. Press Ctrl+C to stop.")
    else:
        print("Listener is running in background mode. Use --live to print events.")
        print("Press Ctrl+C to stop.")

    run_task = asyncio.create_task(listener.run_forever(), name="listener_forever")

    try:
        while True:
            payload = await queue.get()

            if args.live:
                print(_format_live_line(payload))

            if db is not None and payload.get("message_id") is not None:
                await db.insert_telegram_message_event(
                    message_id=int(payload["message_id"]),
                    event_type=str(payload.get("event_type", "new")),
                    channel_id=(
                        str(payload.get("channel_id"))
                        if payload.get("channel_id") is not None
                        else None
                    ),
                    text_before=(
                        str(payload.get("text_before"))
                        if payload.get("text_before") is not None
                        else None
                    ),
                    text_after=(
                        str(payload.get("text_after"))
                        if payload.get("text_after") is not None
                        else None
                    ),
                    created_at=payload.get("timestamp"),
                )
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down listener...")
    finally:
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass

        await listener.stop()
        if db is not None:
            await db.close()


def main() -> None:
    asyncio.run(_run_standalone_listener())


if __name__ == "__main__":
    main()
