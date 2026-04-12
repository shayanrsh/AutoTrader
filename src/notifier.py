"""
AutoTrader — Telegram Notification Module

Sends trade confirmations, error alerts, and daily summaries to the admin
via a Telegram bot (separate from the listener's user account).

Uses python-telegram-bot with async support.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from telegram import Bot
from telegram.error import TelegramError, RetryAfter

from src.models import ParsedSignal, TradeResult, TradeStatus, RiskCheckResult
from src.utils import get_logger, format_price, format_lot, format_pct, format_timestamp

logger = get_logger("notifier")


class TelegramNotifier:
    """
    Sends formatted notifications to the admin via Telegram Bot API.
    Rate-limited to avoid hitting Telegram's message throttling.
    """

    def __init__(self, bot_token: str, chat_id: int) -> None:
        self._bot = Bot(token=bot_token)
        self._chat_id = chat_id
        self._send_lock = asyncio.Lock()
        self._min_interval = 1.0  # Min seconds between messages
        self._last_send_time = 0.0

    async def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Send a message with rate limiting and retry on flood-wait.

        Args:
            text: Message text (HTML formatted).
            parse_mode: Parse mode for Telegram API.

        Returns:
            True if message was sent successfully.
        """
        async with self._send_lock:
            # Rate limiting
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_send_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)

            for attempt in range(3):
                try:
                    await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=text,
                        parse_mode=parse_mode,
                        disable_web_page_preview=True,
                    )
                    self._last_send_time = asyncio.get_event_loop().time()
                    return True

                except RetryAfter as e:
                    logger.warning(
                        "Telegram rate limit: retrying in %d seconds", e.retry_after
                    )
                    await asyncio.sleep(e.retry_after)

                except TelegramError as e:
                    logger.error("Telegram send error (attempt %d): %s", attempt + 1, e)
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)

            logger.error("Failed to send notification after 3 attempts")
            return False

    async def send_trade_alert(
        self, signal: ParsedSignal, result: TradeResult, risk: RiskCheckResult
    ) -> None:
        """Send a formatted trade execution notification."""
        if result.status == TradeStatus.SUCCESS:
            emoji = "✅"
            status_text = "EXECUTED"
        elif result.status == TradeStatus.DRY_RUN:
            emoji = "🔵"
            status_text = "DRY RUN"
        else:
            emoji = "❌"
            status_text = f"FAILED ({result.status.value})"

        text = (
            f"{emoji} <b>Trade {status_text}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>{signal.action.value} {result.symbol}</b>\n"
            f"💰 Volume: <code>{format_lot(result.volume)}</code> lots\n"
            f"📈 Price: <code>{format_price(result.price or signal.entry_price)}</code>\n"
            f"🛑 SL: <code>{format_price(result.stop_loss)}</code>\n"
            f"🎯 TP: <code>{format_price(result.take_profit)}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💸 Risk: <code>${risk.risk_amount:.2f}</code> ({format_pct(risk.risk_pct)})\n"
            f"📂 Open Trades: <code>{risk.current_open_trades}</code>\n"
            f"📉 Daily P&L: <code>${risk.daily_pnl:.2f}</code>\n"
        )

        if result.order_ticket:
            text += f"🎫 Ticket: <code>{result.order_ticket}</code>\n"

        if result.retries > 0:
            text += f"🔄 Retries: <code>{result.retries}</code>\n"

        if result.error_message:
            text += f"⚠️ Error: <code>{result.error_message}</code>\n"

        text += (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Parser: <code>{signal.parser_source}</code> "
            f"(confidence: {signal.confidence:.0%})\n"
            f"⏱ {format_timestamp(result.timestamp)}"
        )

        await self._send(text)

    async def send_error_alert(self, error: str, context: str = "") -> None:
        """Send an error notification to the admin."""
        text = (
            f"🚨 <b>AutoTrader Error</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
        )
        if context:
            text += f"📍 Context: <code>{context}</code>\n"
        text += (
            f"❌ Error: <code>{error[:500]}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Check logs for full details."
        )
        await self._send(text)

    async def send_risk_rejection(
        self, signal: ParsedSignal, risk: RiskCheckResult
    ) -> None:
        """Notify admin when a signal is rejected by risk management."""
        text = (
            f"⛔ <b>Signal Rejected (Risk)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {signal.action.value} @ {format_price(signal.entry_price)}\n"
            f"❓ Reason: <code>{risk.reason}</code>\n"
            f"📂 Open: {risk.current_open_trades} | "
            f"P&L: ${risk.daily_pnl:.2f}"
        )
        await self._send(text)

    async def send_startup_message(self, dry_run: bool, account_info: Optional[dict]) -> None:
        """Send a notification when the bot starts up."""
        mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
        text = (
            f"🚀 <b>AutoTrader Started</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Mode: <b>{mode}</b>\n"
        )
        if account_info:
            text += (
                f"Account: <code>{account_info.get('login', '?')}</code>\n"
                f"Balance: <code>${account_info.get('balance', 0):.2f}</code>\n"
                f"Server: <code>{account_info.get('server', '?')}</code>\n"
                f"Leverage: <code>1:{account_info.get('leverage', '?')}</code>\n"
            )
        text += "━━━━━━━━━━━━━━━━━━━━━━"
        await self._send(text)

    async def send_daily_summary(
        self,
        balance: float,
        equity: float,
        daily_pnl: float,
        trades_today: int,
        signals_today: int,
    ) -> None:
        """Send an end-of-day summary."""
        pnl_emoji = "📈" if daily_pnl >= 0 else "📉"
        text = (
            f"📋 <b>Daily Summary</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <code>${balance:.2f}</code>\n"
            f"💎 Equity: <code>${equity:.2f}</code>\n"
            f"{pnl_emoji} Daily P&L: <code>${daily_pnl:+.2f}</code>\n"
            f"📊 Trades: <code>{trades_today}</code>\n"
            f"📡 Signals: <code>{signals_today}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        await self._send(text)

    async def send_shutdown_message(self, reason: str = "graceful") -> None:
        """Send a notification when the bot is shutting down."""
        text = (
            f"🔴 <b>AutoTrader Stopped</b>\n"
            f"Reason: <code>{reason}</code>"
        )
        await self._send(text)
