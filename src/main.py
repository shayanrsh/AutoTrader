"""
AutoTrader — Main Orchestrator

Entry point that ties all modules together:
1. Loads configuration
2. Initializes database, MT5, Telegram listener, AI parser, notifier
3. Runs the async event loop:
   - Telegram listener pushes messages to a queue
   - Orchestrator dequeues → parses → risk-checks → executes → notifies
4. Handles graceful shutdown on SIGTERM/SIGINT

Usage:
    python -m src.main
"""

from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError

from src.config import get_settings, Settings
from src.database import Database
from src.telegram_listener import TelegramListener
from src.ai_parser import AISignalParser
from src.risk_manager import RiskManager
from src.mt5_executor import MT5Executor
from src.notifier import TelegramNotifier
from src.health import HealthCheckServer
from src.models import (
    ParsedSignal,
    SignalRecord,
    TradeResult,
    TradeStatus,
)
from src.utils import (
    setup_logging,
    get_logger,
    format_price,
    format_lot,
    format_timestamp,
    utc_now,
    timestamp_age_minutes,
)

logger = get_logger("main")


class AutoTrader:
    """
    Main application orchestrator.
    Coordinates all modules in an async pipeline:
        Telegram → Queue → Parser → Risk Manager → MT5 Executor → Notifier
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._signal_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._shutdown_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        # ── Initialize modules ────────────────────────────────────────
        self._db = Database(db_path=settings.database_path)

        self._listener = TelegramListener(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            phone=settings.telegram_phone,
            channel_id=settings.telegram_channel_id,
            session_path=settings.telegram_session_path,
            signal_queue=self._signal_queue,
            catchup_messages=10,
        )

        self._parser = AISignalParser(
            ollama_enabled=settings.ollama_enabled,
            ollama_base_url=settings.ollama_base_url,
            ollama_model=settings.ollama_model,
            gemini_api_key=settings.gemini_api_key,
            gemini_model=settings.gemini_model,
            xai_api_key=settings.xai_api_key,
            xai_model=settings.xai_model,
            ollama_rate_limits=settings.ollama_rate_limits_map(),
            gemini_rate_limits=settings.gemini_rate_limits_map(),
            xai_rate_limits=settings.xai_rate_limits_map(),
        )

        self._risk_manager = RiskManager(
            db=self._db,
            max_risk_pct=settings.max_risk_per_trade_pct,
            max_open_trades=settings.max_open_trades,
            daily_loss_limit_pct=settings.daily_loss_limit_pct,
            signal_expiry_minutes=settings.signal_expiry_minutes,
            dedup_window_hours=settings.dedup_window_hours,
            min_lot=settings.min_lot_size,
            max_lot=settings.max_lot_size,
            lot_step=settings.lot_step,
            default_lot=settings.default_lot_size,
        )

        self._executor = MT5Executor(
            host=settings.mt5_host,
            port=settings.mt5_port,
            account=settings.mt5_account,
            password=settings.mt5_password,
            server=settings.mt5_server,
            symbol_base=settings.symbol,
            magic_number=settings.magic_number,
            max_slippage=settings.max_slippage_points,
        )

        self._notifier = TelegramNotifier(
            bot_token=settings.notify_bot_token,
            chat_id=settings.notify_chat_id,
        )

        self._health = HealthCheckServer(port=settings.health_check_port)
        self._health.dry_run = settings.dry_run

    async def run(self) -> None:
        """Main entry point — start all services and run until shutdown."""
        logger.info("=" * 60)
        logger.info("AutoTrader v1.0.0 starting...")
        logger.info("Mode: %s", "DRY RUN" if self._settings.dry_run else "LIVE")
        logger.info("Symbol: %s", self._settings.symbol)
        logger.info("Risk: %.1f%% per trade, max %d positions",
                     self._settings.max_risk_per_trade_pct,
                     self._settings.max_open_trades)
        logger.info("=" * 60)

        try:
            # Step 1: Connect database
            await self._db.connect()

            # Step 2: Start health check server
            await self._health.start()

            # Step 3: Initialize MT5 connection
            if not self._settings.dry_run:
                mt5_ok = await self._executor.initialize()
                if not mt5_ok:
                    logger.error("MT5 connection failed — starting in degraded mode")
                    await self._notifier.send_error_alert(
                        "MT5 connection failed on startup",
                        context="initialization",
                    )
                else:
                    self._health.mt5_connected = True
            else:
                logger.info("Dry-run mode: skipping MT5 connection")

            # Step 4: Send startup notification
            account_info = None
            if self._executor.is_connected:
                account_info = await self._executor.get_account_info()
            await self._notifier.send_startup_message(
                dry_run=self._settings.dry_run, account_info=account_info
            )

            # Step 5: Start Telegram listener
            await self._listener.start()
            self._health.telegram_connected = True

            # Step 6: Launch background tasks
            self._tasks = [
                asyncio.create_task(
                    self._signal_processing_loop(),
                    name="signal_processor",
                ),
                asyncio.create_task(
                    self._listener.run_forever(),
                    name="telegram_listener",
                ),
                asyncio.create_task(
                    self._periodic_tasks(),
                    name="periodic_tasks",
                ),
            ]

            # Wait for shutdown signal
            await self._shutdown_event.wait()

        except Exception as e:
            logger.critical("Fatal error in main loop: %s", e, exc_info=True)
            await self._notifier.send_error_alert(str(e), context="fatal")
        finally:
            await self._shutdown()

    async def _signal_processing_loop(self) -> None:
        """
        Main processing loop: dequeue signals, parse, risk-check, execute.
        Runs continuously until shutdown.
        """
        logger.info("Signal processing loop started")

        while not self._shutdown_event.is_set():
            try:
                # Wait for a message with a timeout (allows periodic shutdown checks)
                try:
                    payload = await asyncio.wait_for(
                        self._signal_queue.get(), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    continue

                await self._process_signal(payload)

            except asyncio.CancelledError:
                logger.info("Signal processing loop cancelled")
                break
            except Exception as e:
                logger.error("Error in signal processing loop: %s", e, exc_info=True)
                self._health.errors_count += 1
                await self._notifier.send_error_alert(str(e), context="signal_processing")
                await asyncio.sleep(1)  # Prevent tight error loop

    async def _process_signal(self, payload: dict) -> None:
        """Process a single signal through the full pipeline."""
        event_type = str(payload.get("event_type", "new"))
        raw_text = payload.get("text", "")
        message_id = payload.get("message_id")
        channel_id = payload.get("channel_id")
        text_before = payload.get("text_before")
        text_after = payload.get("text_after", raw_text)
        msg_timestamp = payload.get("timestamp", utc_now())
        event_row_id: Optional[int] = None

        if message_id is not None:
            event_row_id = await self._db.insert_telegram_message_event(
                message_id=int(message_id),
                event_type=event_type,
                channel_id=str(channel_id) if channel_id is not None else None,
                text_before=(str(text_before)[:2000] if text_before else None),
                text_after=(str(text_after)[:2000] if text_after else None),
                created_at=msg_timestamp,
            )

        logger.info(
            "Processing telegram event type=%s id=%s (%d chars)",
            event_type,
            message_id,
            len(raw_text),
        )

        # Deleted events and empty-text events are recorded above for dashboard/audit,
        # but should not pass through parser/trading pipeline.
        if event_type == "delete" or not raw_text:
            if event_row_id is not None:
                await self._db.mark_telegram_event_parse_status(
                    event_row_id,
                    "SKIPPED",
                    parser_source="none",
                    parse_error="non-tradeable event",
                )
            logger.debug("Skipping non-tradeable event: type=%s id=%s", event_type, message_id)
            return

        if event_row_id is not None:
            existing_status = await self._db.get_telegram_event_parse_status(event_row_id)
            if existing_status in {"PROCESSED", "SKIPPED"}:
                logger.info(
                    "Skipping already parsed telegram event row id=%s status=%s",
                    event_row_id,
                    existing_status,
                )
                return
            await self._db.mark_telegram_event_parse_status(event_row_id, "PROCESSING")

        try:
            # ── Step 1: Parse ───────────────────────────────────────────
            signal = await self._parser.parse(raw_text, message_id)

            if signal is None:
                logger.debug("Message id=%s is not a trading signal — skipping", message_id)
                if event_row_id is not None:
                    await self._db.mark_telegram_event_parse_status(
                        event_row_id,
                        "PROCESSED",
                        parser_source="none",
                        parse_error="non-signal or parse failed",
                    )
                return

            # Override timestamp from the actual message
            signal.timestamp = msg_timestamp

            self._health.signals_processed += 1
            self._health.last_signal_time = format_timestamp(utc_now())

            # Update parser stats
            self._health.parser_stats = self._parser.get_stats()

            logger.info(
                "Signal parsed: %s @ %s, SL=%s, TP=%s (by %s, confidence=%.0f%%)",
                signal.action.value,
                format_price(signal.entry_price),
                format_price(signal.stop_loss),
                format_price(signal.take_profits),
                signal.parser_source,
                signal.confidence * 100,
            )

            # ── Step 2: Risk Check ──────────────────────────────────────
            account_balance = 0.0
            daily_pnl = 0.0
            existing_positions: list[dict] = []

            if self._executor.is_connected:
                acct = await self._executor.get_account_info()
                if acct:
                    account_balance = acct.get("balance", 0.0)
                daily_pnl = await self._executor.get_daily_pnl()
                existing_positions = await self._executor.get_open_positions()
            elif self._settings.dry_run:
                # Use a simulated balance for dry-run risk calculations
                account_balance = 10000.0

            current_open = len(existing_positions)
            self._health.open_trades = current_open
            self._health.daily_pnl = daily_pnl

            risk_result = await self._risk_manager.check_trade(
                signal=signal,
                account_balance=account_balance,
                current_open_count=current_open,
                daily_pnl=daily_pnl,
                existing_positions=existing_positions,
            )

            if not risk_result.approved:
                logger.warning("Signal REJECTED by risk manager: %s", risk_result.reason)
                # Record the rejected signal
                record = SignalRecord(
                    dedup_hash=signal.dedup_hash(),
                    raw_text=raw_text[:500],
                    parsed_action=signal.action.value,
                    parsed_entry=signal.entry_price,
                    parsed_sl=signal.stop_loss,
                    parsed_tp1=signal.take_profits,
                    parser_source=signal.parser_source,
                    trade_status="REJECTED",
                    message_id=message_id,
                )
                await self._db.insert_signal(record)
                if event_row_id is not None:
                    await self._db.mark_telegram_event_parse_status(
                        event_row_id,
                        "PROCESSED",
                        parser_source=signal.parser_source,
                    )
                await self._notifier.send_risk_rejection(signal, risk_result)
                return

            lot_size = risk_result.adjusted_lot_size
            logger.info(
                "Risk check PASSED: lot=%s, risk=$%.2f (%s)",
                format_lot(lot_size),
                risk_result.risk_amount,
                f"{risk_result.risk_pct:.2f}%",
            )

            # ── Step 3: Execute ─────────────────────────────────────────
            if self._settings.dry_run:
                # Dry-run: log intent but don't trade
                trade_result = TradeResult(
                    status=TradeStatus.DRY_RUN,
                    symbol=self._executor.resolved_symbol,
                    action=signal.action,
                    volume=lot_size,
                    price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profits,
                    signal_hash=signal.dedup_hash(),
                )
                logger.info(
                    "DRY RUN: Would place %s %s %s lots @ %s, SL=%s, TP=%s",
                    signal.action.value,
                    self._executor.resolved_symbol,
                    format_lot(lot_size),
                    format_price(signal.entry_price),
                    format_price(signal.stop_loss),
                    format_price(signal.take_profits),
                )
            else:
                # Live execution
                trade_result = await self._executor.place_order(signal, lot_size)

            self._health.trades_executed += 1

            # ── Step 4: Record & Notify ─────────────────────────────────
            # Save signal record
            signal_record = SignalRecord(
                dedup_hash=signal.dedup_hash(),
                raw_text=raw_text[:500],
                parsed_action=signal.action.value,
                parsed_entry=signal.entry_price,
                parsed_sl=signal.stop_loss,
                parsed_tp1=signal.take_profits,
                parser_source=signal.parser_source,
                trade_ticket=trade_result.order_ticket,
                trade_status=trade_result.status.value,
                message_id=message_id,
            )
            await self._db.insert_signal(signal_record)

            # Save trade record
            await self._db.insert_trade(trade_result)

            # Send notification
            await self._notifier.send_trade_alert(signal, trade_result, risk_result)

            if event_row_id is not None:
                await self._db.mark_telegram_event_parse_status(
                    event_row_id,
                    "PROCESSED",
                    parser_source=signal.parser_source,
                )

            if trade_result.status == TradeStatus.SUCCESS:
                logger.info(
                    "✅ Trade executed: ticket=%s, %s %s lots @ %s",
                    trade_result.order_ticket,
                    signal.action.value,
                    format_lot(trade_result.volume),
                    format_price(trade_result.price or 0),
                )
            elif trade_result.status == TradeStatus.DRY_RUN:
                logger.info("🔵 Dry-run trade logged successfully")
            else:
                logger.warning(
                    "❌ Trade failed: %s — %s",
                    trade_result.status.value,
                    trade_result.error_message,
                )
                self._health.errors_count += 1
        except Exception as e:
            if event_row_id is not None:
                await self._db.mark_telegram_event_parse_status(
                    event_row_id,
                    "FAILED",
                    parser_source="none",
                    parse_error=str(e)[:400],
                )
            raise

    async def _periodic_tasks(self) -> None:
        """
        Background tasks that run on a schedule:
        - MT5 health check (every 60s)
        - Database cleanup (daily)
        - Daily P&L recording (every 5 min)
        """
        logger.info("Periodic tasks started")
        mt5_check_interval = 60    # seconds
        pnl_record_interval = 300  # 5 minutes
        non_signal_cleanup_interval = 3600  # 1 hour
        cleanup_interval = 86400   # 24 hours

        last_mt5_check = 0.0
        last_pnl_record = 0.0
        last_non_signal_cleanup = 0.0
        last_cleanup = 0.0

        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(10)  # Base loop interval
                now = asyncio.get_event_loop().time()

                # MT5 health check
                if now - last_mt5_check >= mt5_check_interval:
                    last_mt5_check = now
                    if not self._settings.dry_run and self._executor.is_connected:
                        healthy = await self._executor.health_check()
                        self._health.mt5_connected = healthy
                        if not healthy:
                            logger.warning("MT5 health check failed — attempting reconnect")
                            await self._executor.initialize()
                            self._health.mt5_connected = self._executor.is_connected
                            if not self._executor.is_connected:
                                await self._notifier.send_error_alert(
                                    "MT5 connection lost — reconnect failed",
                                    context="health_check",
                                )

                # Daily P&L recording
                if now - last_pnl_record >= pnl_record_interval:
                    last_pnl_record = now
                    if self._executor.is_connected:
                        pnl = await self._executor.get_daily_pnl()
                        acct = await self._executor.get_account_info()
                        if acct:
                            await self._db.record_daily_pnl(
                                pnl=pnl,
                                starting_balance=acct.get("balance", 0),
                                ending_balance=acct.get("equity", 0),
                            )

                # Remove non-signal channel messages older than 24h
                if now - last_non_signal_cleanup >= non_signal_cleanup_interval:
                    last_non_signal_cleanup = now
                    await self._db.cleanup_non_signal_telegram_messages(max_age_hours=24)

                # Database cleanup
                if now - last_cleanup >= cleanup_interval:
                    last_cleanup = now
                    await self._db.cleanup_old_records(days=90)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Periodic task error: %s", e)
                await asyncio.sleep(30)

    async def _shutdown(self) -> None:
        """Graceful shutdown sequence."""
        logger.info("Shutting down AutoTrader...")

        # Cancel background tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Send shutdown notification
        try:
            await self._notifier.send_shutdown_message("graceful")
        except Exception:
            pass

        # Stop services
        await self._health.stop()
        await self._listener.stop()

        if self._executor.is_connected:
            await self._executor.shutdown()

        await self._db.close()

        logger.info("AutoTrader shutdown complete")

    def request_shutdown(self) -> None:
        """Signal the main loop to shut down."""
        logger.info("Shutdown requested")
        self._shutdown_event.set()


def _setup_signal_handlers(trader: AutoTrader, loop: asyncio.AbstractEventLoop) -> None:
    """Register OS signal handlers for graceful shutdown."""
    def handle_signal(sig_name: str) -> None:
        logger.info("Received %s — initiating shutdown", sig_name)
        trader.request_shutdown()

    # On Windows, SIGTERM isn't fully supported. Only register if possible.
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, handle_signal, sig.name)
    except NotImplementedError:
        # Windows fallback
        signal.signal(signal.SIGINT, lambda s, f: trader.request_shutdown())


async def main() -> None:
    """Application entry point."""
    # Load settings
    try:
        settings = get_settings()
    except ValidationError as e:
        print("❌ Configuration validation failed. Fix the following fields in config.env:")
        for issue in e.errors():
            field = ".".join(str(part) for part in issue.get("loc", []))
            message = issue.get("msg", "invalid value")
            print(f"   - {field}: {message}")
        print("   Hint: run the installer setup wizard to regenerate required values.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        print("   Copy config.env.example to config.env and fill in your values.")
        sys.exit(1)

    # Setup logging
    setup_logging(
        log_level=settings.log_level,
        log_file_path=settings.log_file_path,
        max_size_mb=settings.log_max_size_mb,
        backup_count=settings.log_backup_count,
    )

    # Create and run the trader
    trader = AutoTrader(settings)

    # Setup signal handlers
    loop = asyncio.get_running_loop()
    _setup_signal_handlers(trader, loop)

    await trader.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown requested by user.")
    except Exception as e:
        print(f"❌ Fatal startup error: {e}")
        raise
