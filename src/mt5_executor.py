"""
AutoTrader — MetaTrader 5 Trade Executor Module

Connects to MT5 via the mt5linux RPC bridge (running in Wine on the same server).
Handles order placement, position queries, symbol resolution, and retry logic.

Architecture:
    Native Python app → (localhost TCP) → mt5linux server → Wine MT5 Terminal → Broker
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from src.models import ParsedSignal, TradeAction, TradeResult, TradeStatus
from src.utils import get_logger

logger = get_logger("mt5_executor")

# MT5 trade return codes we handle specifically
_RETCODE_DONE = 10009
_RETCODE_PLACED = 10008
_RETCODE_REQUOTE = 10004
_RETCODE_REJECT = 10006
_RETCODE_INVALID = 10013
_RETCODE_INVALID_VOLUME = 10014
_RETCODE_NO_MONEY = 10019
_RETCODE_MARKET_CLOSED = 10018
_RETCODE_TRADE_DISABLED = 10017
_RETCODE_TOO_MANY = 10024
_RETCODE_CONNECTION_LOST = 10031

# Alpari symbol suffix candidates to try
_SYMBOL_SUFFIXES = ["", "m", ".", "#", "_", "i"]


class MT5Executor:
    """
    Manages MT5 connection and trade execution via the mt5linux RPC bridge.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 18812,
        account: int = 0,
        password: str = "",
        server: str = "",
        symbol_base: str = "XAUUSD",
        magic_number: int = 240001,
        max_slippage: int = 50,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self._host = host
        self._port = port
        self._account = account
        self._password = password
        self._server = server
        self._symbol_base = symbol_base
        self._magic_number = magic_number
        self._max_slippage = max_slippage
        self._max_retries = max_retries
        self._retry_delay = retry_delay

        self._mt5 = None
        self._connected = False
        self._resolved_symbol: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def resolved_symbol(self) -> str:
        return self._resolved_symbol or self._symbol_base

    async def initialize(self) -> bool:
        """
        Connect to the MT5 bridge and log into the trading account.

        Returns:
            True if connection and login succeeded.
        """
        try:
            # Import mt5linux here so the rest of the app can load
            # even if mt5linux isn't installed (e.g., on Windows dev machines)
            from mt5linux import MetaTrader5

            self._mt5 = MetaTrader5(host=self._host, port=self._port)

            # Initialize connection to the bridge
            init_result = await asyncio.to_thread(self._mt5.initialize)
            if not init_result:
                error = await asyncio.to_thread(self._mt5.last_error)
                logger.error("MT5 initialization failed: %s", error)
                return False

            # Login to account
            login_result = await asyncio.to_thread(
                self._mt5.login,
                self._account,
                password=self._password,
                server=self._server,
            )
            if not login_result:
                error = await asyncio.to_thread(self._mt5.last_error)
                logger.error("MT5 login failed: %s", error)
                return False

            # Get account info
            account_info = await asyncio.to_thread(self._mt5.account_info)
            if account_info:
                logger.info(
                    "MT5 connected: account=%d, name=%s, balance=%.2f %s, "
                    "server=%s, leverage=1:%d",
                    account_info.login,
                    account_info.name,
                    account_info.balance,
                    account_info.currency,
                    account_info.server,
                    account_info.leverage,
                )

            # Resolve the correct symbol name for this broker
            await self._resolve_symbol()

            self._connected = True
            return True

        except ImportError:
            logger.error(
                "mt5linux package not installed. Install it with: pip install mt5linux"
            )
            return False
        except Exception as e:
            logger.error("MT5 initialization error: %s", e)
            return False

    async def shutdown(self) -> None:
        """Cleanly shut down the MT5 connection."""
        if self._mt5:
            try:
                await asyncio.to_thread(self._mt5.shutdown)
            except Exception as e:
                logger.warning("MT5 shutdown error: %s", e)
            self._mt5 = None
            self._connected = False
            logger.info("MT5 connection closed")

    async def _resolve_symbol(self) -> None:
        """
        Find the correct symbol name on this broker by trying common suffixes.
        Alpari uses different suffixes for different account types.
        """
        if not self._mt5:
            return

        for suffix in _SYMBOL_SUFFIXES:
            candidate = f"{self._symbol_base}{suffix}"
            info = await asyncio.to_thread(self._mt5.symbol_info, candidate)
            if info is not None:
                self._resolved_symbol = candidate
                # Ensure symbol is visible in Market Watch
                await asyncio.to_thread(self._mt5.symbol_select, candidate, True)
                logger.info(
                    "Symbol resolved: %s → %s (digits=%d, trade_mode=%s, "
                    "volume_min=%.2f, volume_max=%.2f, volume_step=%.2f)",
                    self._symbol_base, candidate,
                    info.digits, info.trade_mode,
                    info.volume_min, info.volume_max, info.volume_step,
                )
                return

        logger.warning(
            "Could not resolve symbol %s with any suffix. "
            "Using base name. Available symbols can be checked in MT5 terminal.",
            self._symbol_base,
        )
        self._resolved_symbol = self._symbol_base

    async def get_account_info(self) -> Optional[dict]:
        """Get current account information."""
        if not self._mt5 or not self._connected:
            return None
        try:
            info = await asyncio.to_thread(self._mt5.account_info)
            if info:
                return {
                    "login": info.login,
                    "balance": info.balance,
                    "equity": info.equity,
                    "margin": info.margin,
                    "free_margin": info.margin_free,
                    "profit": info.profit,
                    "currency": info.currency,
                    "leverage": info.leverage,
                    "server": info.server,
                }
        except Exception as e:
            logger.error("Failed to get account info: %s", e)
        return None

    async def get_symbol_info(self) -> Optional[dict]:
        """Get information about the trading symbol."""
        if not self._mt5 or not self._connected:
            return None
        try:
            info = await asyncio.to_thread(
                self._mt5.symbol_info, self.resolved_symbol
            )
            if info:
                return {
                    "name": info.name,
                    "digits": info.digits,
                    "point": info.point,
                    "spread": info.spread,
                    "volume_min": info.volume_min,
                    "volume_max": info.volume_max,
                    "volume_step": info.volume_step,
                    "trade_mode": info.trade_mode,
                    "bid": info.bid,
                    "ask": info.ask,
                }
        except Exception as e:
            logger.error("Failed to get symbol info: %s", e)
        return None

    async def get_open_positions(self) -> list[dict]:
        """Get all open positions for this magic number."""
        if not self._mt5 or not self._connected:
            return []
        try:
            positions = await asyncio.to_thread(
                self._mt5.positions_get, symbol=self.resolved_symbol
            )
            if positions is None:
                return []

            result = []
            for pos in positions:
                if pos.magic == self._magic_number:
                    result.append({
                        "ticket": pos.ticket,
                        "symbol": pos.symbol,
                        "type": pos.type,
                        "volume": pos.volume,
                        "price_open": pos.price_open,
                        "price_current": pos.price_current,
                        "sl": pos.sl,
                        "tp": pos.tp,
                        "profit": pos.profit,
                        "magic": pos.magic,
                        "comment": pos.comment,
                        "time": pos.time,
                    })

            return result

        except Exception as e:
            logger.error("Failed to get positions: %s", e)
            return []

    async def place_order(
        self, signal: ParsedSignal, lot_size: float
    ) -> TradeResult:
        """
        Place a market order based on the parsed signal.

        Args:
            signal: The parsed trading signal.
            lot_size: Risk-adjusted lot size.

        Returns:
            TradeResult with execution outcome.
        """
        if not self._mt5 or not self._connected:
            return TradeResult(
                status=TradeStatus.ERROR,
                symbol=self.resolved_symbol,
                action=signal.action,
                volume=lot_size,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profits,
                error_message="MT5 not connected",
                signal_hash=signal.dedup_hash(),
            )

        # Import MT5 constants
        from mt5linux import MetaTrader5

        # Determine order type
        if signal.action == TradeAction.BUY:
            order_type = MetaTrader5.ORDER_TYPE_BUY
            # BUY at ask price
            tick = await asyncio.to_thread(
                self._mt5.symbol_info_tick, self.resolved_symbol
            )
            price = tick.ask if tick else signal.entry_price
        else:
            order_type = MetaTrader5.ORDER_TYPE_SELL
            # SELL at bid price
            tick = await asyncio.to_thread(
                self._mt5.symbol_info_tick, self.resolved_symbol
            )
            price = tick.bid if tick else signal.entry_price

        take_profit = signal.take_profits

        # Build order request
        request = {
            "action": MetaTrader5.TRADE_ACTION_DEAL,
            "symbol": self.resolved_symbol,
            "volume": lot_size,
            "type": order_type,
            "price": price,
            "sl": signal.stop_loss,
            "tp": take_profit,
            "deviation": self._max_slippage,
            "magic": self._magic_number,
            "comment": f"AT|{signal.parser_source}|{signal.dedup_hash()[:8]}",
            "type_time": MetaTrader5.ORDER_TIME_GTC,
            "type_filling": MetaTrader5.ORDER_FILLING_IOC,
        }

        # Retry loop for requotes
        last_result = None
        for attempt in range(1, self._max_retries + 1):
            logger.info(
                "Placing %s order (attempt %d/%d): symbol=%s, lot=%.2f, "
                "price=%.2f, SL=%.2f, TP=%.2f",
                signal.action.value, attempt, self._max_retries,
                self.resolved_symbol, lot_size, price,
                signal.stop_loss, take_profit,
            )

            try:
                result = await asyncio.to_thread(
                    self._mt5.order_send, request
                )
            except Exception as e:
                logger.error("order_send exception on attempt %d: %s", attempt, e)
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_delay)
                    continue
                return TradeResult(
                    status=TradeStatus.ERROR,
                    symbol=self.resolved_symbol,
                    action=signal.action,
                    volume=lot_size,
                    stop_loss=signal.stop_loss,
                    take_profit=take_profit,
                    error_message=str(e),
                    signal_hash=signal.dedup_hash(),
                    retries=attempt,
                )

            if result is None:
                error = await asyncio.to_thread(self._mt5.last_error)
                logger.error("order_send returned None. Last error: %s", error)
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_delay)
                    continue
                return TradeResult(
                    status=TradeStatus.ERROR,
                    symbol=self.resolved_symbol,
                    action=signal.action,
                    volume=lot_size,
                    stop_loss=signal.stop_loss,
                    take_profit=take_profit,
                    error_message=f"order_send returned None: {error}",
                    signal_hash=signal.dedup_hash(),
                    retries=attempt,
                )

            retcode = result.retcode
            last_result = result

            # ── Success ─────────────────────────────────────────────
            if retcode in (_RETCODE_DONE, _RETCODE_PLACED):
                trade_result = TradeResult(
                    status=TradeStatus.SUCCESS,
                    order_ticket=result.order,
                    symbol=self.resolved_symbol,
                    action=signal.action,
                    volume=result.volume,
                    price=result.price,
                    stop_loss=signal.stop_loss,
                    take_profit=take_profit,
                    signal_hash=signal.dedup_hash(),
                    retries=attempt - 1,
                )
                logger.info(
                    "Order FILLED: ticket=%d, price=%.2f, volume=%.2f",
                    result.order, result.price, result.volume,
                )
                return trade_result

            # ── Requote ─────────────────────────────────────────────
            if retcode == _RETCODE_REQUOTE:
                logger.warning(
                    "Requote on attempt %d: requested=%.2f, new_bid=%.5f, new_ask=%.5f",
                    attempt, price,
                    getattr(result, "bid", 0),
                    getattr(result, "ask", 0),
                )
                # Update price for next attempt
                tick = await asyncio.to_thread(
                    self._mt5.symbol_info_tick, self.resolved_symbol
                )
                if tick:
                    price = tick.ask if signal.action == TradeAction.BUY else tick.bid
                    request["price"] = price
                await asyncio.sleep(self._retry_delay)
                continue

            # ── Non-retryable errors ────────────────────────────────
            error_msg = f"retcode={retcode}, comment={getattr(result, 'comment', 'unknown')}"

            if retcode == _RETCODE_NO_MONEY:
                logger.error("Insufficient funds: %s", error_msg)
            elif retcode == _RETCODE_MARKET_CLOSED:
                logger.error("Market closed: %s", error_msg)
            elif retcode == _RETCODE_TRADE_DISABLED:
                logger.error("Trading disabled: %s", error_msg)
            elif retcode == _RETCODE_INVALID_VOLUME:
                logger.error("Invalid volume: %s", error_msg)
            elif retcode == _RETCODE_INVALID:
                logger.error("Invalid request: %s", error_msg)
            else:
                logger.error("Order rejected: %s", error_msg)

            return TradeResult(
                status=TradeStatus.REJECTED,
                symbol=self.resolved_symbol,
                action=signal.action,
                volume=lot_size,
                stop_loss=signal.stop_loss,
                take_profit=take_profit,
                error_code=retcode,
                error_message=error_msg,
                signal_hash=signal.dedup_hash(),
                retries=attempt - 1,
            )

        # All retries exhausted
        return TradeResult(
            status=TradeStatus.REQUOTE,
            symbol=self.resolved_symbol,
            action=signal.action,
            volume=lot_size,
            stop_loss=signal.stop_loss,
            take_profit=take_profit,
            error_message=f"Max retries ({self._max_retries}) exhausted due to requotes",
            signal_hash=signal.dedup_hash(),
            retries=self._max_retries,
        )

    async def close_position(self, ticket: int) -> bool:
        """
        Close a specific open position by ticket number.

        Args:
            ticket: The position ticket to close.

        Returns:
            True if the position was closed successfully.
        """
        if not self._mt5 or not self._connected:
            logger.error("Cannot close position: MT5 not connected")
            return False

        from mt5linux import MetaTrader5

        try:
            # Get position details
            positions = await asyncio.to_thread(
                self._mt5.positions_get, ticket=ticket
            )
            if not positions or len(positions) == 0:
                logger.warning("Position ticket %d not found", ticket)
                return False

            pos = positions[0]

            # Determine close direction (opposite of position)
            if pos.type == 0:  # BUY position → close with SELL
                close_type = MetaTrader5.ORDER_TYPE_SELL
                tick = await asyncio.to_thread(
                    self._mt5.symbol_info_tick, pos.symbol
                )
                price = tick.bid if tick else pos.price_current
            else:  # SELL position → close with BUY
                close_type = MetaTrader5.ORDER_TYPE_BUY
                tick = await asyncio.to_thread(
                    self._mt5.symbol_info_tick, pos.symbol
                )
                price = tick.ask if tick else pos.price_current

            request = {
                "action": MetaTrader5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "position": ticket,
                "price": price,
                "deviation": self._max_slippage,
                "magic": self._magic_number,
                "comment": "AT|close",
                "type_time": MetaTrader5.ORDER_TIME_GTC,
                "type_filling": MetaTrader5.ORDER_FILLING_IOC,
            }

            result = await asyncio.to_thread(self._mt5.order_send, request)
            if result and result.retcode in (_RETCODE_DONE, _RETCODE_PLACED):
                logger.info(
                    "Position %d closed: price=%.2f, profit=%.2f",
                    ticket, result.price, pos.profit,
                )
                return True
            else:
                error = getattr(result, "comment", "unknown") if result else "null result"
                logger.error("Failed to close position %d: %s", ticket, error)
                return False

        except Exception as e:
            logger.error("Error closing position %d: %s", ticket, e)
            return False

    async def get_daily_pnl(self) -> float:
        """
        Calculate today's realized + unrealized P&L.

        Returns:
            Total P&L for today in account currency.
        """
        if not self._mt5 or not self._connected:
            return 0.0

        try:
            account = await asyncio.to_thread(self._mt5.account_info)
            if account:
                # profit field includes unrealized P&L on open positions
                return account.profit
        except Exception as e:
            logger.error("Failed to get daily P&L: %s", e)
        return 0.0

    async def health_check(self) -> bool:
        """Check if MT5 bridge is responsive."""
        if not self._mt5:
            return False
        try:
            info = await asyncio.to_thread(self._mt5.terminal_info)
            return info is not None
        except Exception:
            self._connected = False
            return False
