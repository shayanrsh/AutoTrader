"""
AutoTrader — MetaTrader 5 Trade Executor Module

Connects to MT5 via the mt5linux RPC bridge (running in Wine on the same server).
Handles order placement, position queries, symbol resolution, and retry logic.

Architecture:
    Native Python app → (localhost TCP) → mt5linux server → Wine MT5 Terminal → Broker
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

try:
    from src.utils import get_logger
except ModuleNotFoundError:
    # Supports direct execution from src/ (python3 mt5_executor.py).
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from src.utils import get_logger

# Lazy model imports: allow CLI health-checks without loading full app deps.
ParsedSignal = None  # type: ignore[assignment]
TradeAction = None  # type: ignore[assignment]
TradeResult = None  # type: ignore[assignment]
TradeStatus = None  # type: ignore[assignment]


def _ensure_model_imports() -> None:
    global ParsedSignal, TradeAction, TradeResult, TradeStatus

    if all(symbol is not None for symbol in (ParsedSignal, TradeAction, TradeResult, TradeStatus)):
        return

    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from src.models import ParsedSignal as _ParsedSignal
    from src.models import TradeAction as _TradeAction
    from src.models import TradeResult as _TradeResult
    from src.models import TradeStatus as _TradeStatus

    ParsedSignal = _ParsedSignal
    TradeAction = _TradeAction
    TradeResult = _TradeResult
    TradeStatus = _TradeStatus

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
_LAST_BRIDGE_LOG_PATH: Optional[Path] = None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MT5 executor utility: verify MT5 bridge/account connectivity."
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Connect to MT5 and print account/symbol/tick info (default behavior).",
    )
    parser.add_argument(
        "--no-auto-start-bridge",
        action="store_true",
        help="Do not auto-start mt5-bridge service when connection is refused.",
    )
    return parser.parse_args()


def _is_local_host(host: str) -> bool:
    return host.strip().lower() in {"localhost", "127.0.0.1", "::1"}


def _attempt_start_mt5_bridge_service() -> bool:
    """Try to start mt5-bridge service for local troubleshooting flows."""
    global _LAST_BRIDGE_LOG_PATH

    project_root = Path(__file__).resolve().parent.parent
    installed_root = Path("/home/trader/autotrader")

    # Prefer the installed path that trader can access over /root worktrees.
    bridge_root = installed_root if (installed_root / "systemd" / "start_mt5_bridge.sh").exists() else project_root
    fallback_script = bridge_root / "systemd" / "start_mt5_bridge.sh"

    # Prevent overlapping manual bridge launchers from fighting over Xvfb/MT5/wineserver.
    stale_cleanup_cmd = (
        "pkill -f 'start_mt5_bridge.sh' 2>/dev/null || true; "
        "pkill -f 'mt5linux' 2>/dev/null || true; "
        "wineserver -k 2>/dev/null || true; "
        "rm -f /tmp/.X99-lock 2>/dev/null || true"
    )

    try:
        if os.geteuid() == 0:
            subprocess.run(
                ["sudo", "-u", "trader", "bash", "-lc", stale_cleanup_cmd],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
        else:
            subprocess.run(
                ["bash", "-lc", stale_cleanup_cmd],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
    except Exception as exc:
        print(f"[WARN] Stale bridge cleanup command failed: {exc}")

    cmd = ["systemctl", "start", "mt5-bridge"]
    if os.geteuid() != 0:
        cmd = ["sudo", *cmd]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except Exception as exc:
        print(f"[WARN] Failed to run {' '.join(cmd)}: {exc}")
        return False

    if result.returncode == 0:
        print("[INFO] Started mt5-bridge service. Retrying connection...")
        return True

    stderr = result.stderr.strip() or result.stdout.strip() or "unknown error"
    if "Unit mt5-bridge.service not found" not in stderr:
        print(f"[WARN] Could not start mt5-bridge service: {stderr}")
        return False

    print("[WARN] mt5-bridge.service is not installed. Trying local bridge script fallback...")
    if not fallback_script.exists():
        print(f"[WARN] Bridge fallback script not found: {fallback_script}")
        return False

    log_path = bridge_root / "data" / "mt5-bridge-manual.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_path = Path("/tmp/mt5-bridge-manual.log")

    _LAST_BRIDGE_LOG_PATH = log_path

    run_script = fallback_script
    run_cwd = bridge_root

    # If executing from a root-owned worktree, trader cannot access it.
    # Copy the script to /tmp so it can run as trader.
    if os.geteuid() == 0 and str(fallback_script).startswith("/root/"):
        tmp_script = Path("/tmp/autotrader_start_mt5_bridge.sh")
        try:
            tmp_script.write_text(fallback_script.read_text(encoding="utf-8"), encoding="utf-8")
            os.chmod(tmp_script, 0o755)
            run_script = tmp_script
            run_cwd = Path("/home/trader")
        except OSError as exc:
            print(f"[WARN] Failed to prepare /tmp bridge script fallback: {exc}")

    run_cmd: list[str]
    if os.geteuid() == 0:
        # Wine prefix belongs to trader user; launching as root breaks Wine startup.
        run_cmd = [
            "sudo",
            "-u",
            "trader",
            "bash",
            "-lc",
            f"cd '{run_cwd}' && bash '{run_script}'",
        ]
    else:
        run_cmd = ["bash", str(run_script)]

    try:
        with open(log_path, "a", encoding="utf-8") as log_file:
            subprocess.Popen(  # noqa: S603
                run_cmd,
                cwd=str(run_cwd),
                stdout=log_file,
                stderr=log_file,
                start_new_session=True,
            )
        print(
            "[INFO] Started local bridge script in background. "
            f"Logs: {log_path}"
        )
        return True
    except Exception as exc:
        print(f"[WARN] Failed to launch local bridge script fallback: {exc}")
        return False


def _wait_for_bridge(host: str, port: int, timeout_seconds: int = 60) -> bool:
    """Wait until bridge TCP port is reachable."""
    deadline = time.time() + max(1, timeout_seconds)

    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            time.sleep(1)

    return False


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

    async def check_bridge_connectivity(self) -> bool:
        """
        Check if the MT5 RPC bridge port is accessible without initializing MT5.
        Useful for diagnostics when full initialization is timing out.

        Returns:
            True if bridge port is reachable, False otherwise.
        """
        try:
            import socket

            loop = asyncio.get_event_loop()
            sock = await loop.run_in_executor(
                None,
                lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            )
            sock.settimeout(5)

            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: sock.connect_ex((self._host, self._port))
                    ),
                    timeout=10,
                )
                sock.close()

                if result == 0:
                    logger.info(
                        "MT5 RPC bridge is reachable at %s:%d", self._host, self._port
                    )
                    return True
                else:
                    logger.warning(
                        "MT5 RPC bridge port %d not reachable. Status: %d",
                        self._port,
                        result,
                    )
                    return False
            except Exception as e:
                sock.close()
                logger.warning("Bridge connectivity check failed: %s", e)
                return False

        except Exception as e:
            logger.error("Cannot check bridge connectivity: %s", e)
            return False

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

            # Keep RPC timeout generous - MT5 in Wine can take a long time to respond
            logger.info(
                "Connecting to MT5 bridge at %s:%d with 120s timeout...",
                self._host,
                self._port,
            )
            self._mt5 = await asyncio.wait_for(
                asyncio.to_thread(
                    MetaTrader5,
                    host=self._host,
                    port=self._port,
                    timeout=120,
                ),
                timeout=125,
            )

            # Initialize connection to the bridge
            logger.info("Calling initialize() on MT5 bridge (may take 30-120s)...")
            try:
                init_result = await asyncio.wait_for(
                    asyncio.to_thread(self._mt5.initialize),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "MT5 initialize() timed out after 120s. "
                    "MT5 terminal may be: 1) Stuck initializing, 2) Waiting for broker authentication, "
                    "3) Unresponsive to RPC. Check: 1) Port %d listening (ss -tln|grep %d), "
                    "2) MT5 process running (ps aux|grep terminal64), "
                    "3) Wine configuration OK (echo $WINEPREFIX, $DISPLAY)",
                    self._port,
                    self._port,
                )
                return False
            
            if not init_result:
                error = await asyncio.to_thread(self._mt5.last_error)
                logger.error(
                    "MT5 initialization failed: %s. "
                    "MT5 terminal may not be running or is unresponsive. "
                    "Check: 1) Port %d listening, 2) MT5 process running, "
                    "3) Wine/X11 display OK",
                    error,
                    self._port,
                )
                return False

            # Login to account when credentials are configured.
            # Some broker terminals are already logged in via GUI profile; in that case
            # we can continue with the active terminal session if account_info is available.
            has_explicit_login = bool(self._account and self._password and self._server)
            if has_explicit_login:
                try:
                    login_result = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._mt5.login,
                            self._account,
                            password=self._password,
                            server=self._server,
                        ),
                        timeout=120,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "MT5 login timed out; trying to use currently active terminal session"
                    )
                    login_result = False

                if not login_result:
                    error = await asyncio.to_thread(self._mt5.last_error)
                    logger.warning("MT5 login failed: %s", error)

            # Get account info
            account_info = await asyncio.wait_for(
                asyncio.to_thread(self._mt5.account_info),
                timeout=60,
            )
            if account_info is None:
                logger.error("MT5 account_info is unavailable after initialize/login")
                return False

            if has_explicit_login and account_info.login != self._account:
                logger.error(
                    "MT5 connected to unexpected account: expected=%d got=%d",
                    self._account,
                    account_info.login,
                )
                return False

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
            logger.info("MT5 initialization complete and connection successful")
            return True

        except ImportError:
            logger.error(
                "mt5linux package not installed. Install it with: pip install mt5linux"
            )
            return False
        except asyncio.TimeoutError:
            logger.error(
                "MT5 operation timed out waiting for terminal RPC response. "
                "This usually means: 1) MT5 terminal is not running in Wine, "
                "2) MT5 is stuck/unresponsive, or 3) Bridge-to-MT5 communication failed. "
                "Check bridge logs at /tmp/mt5linux-bridge-18812.typescript "
                "or /home/trader/autotrader/data/mt5-bridge-manual.log"
            )
            return False
        except Exception as e:
            logger.error("MT5 initialization error: %s", e)
            import traceback
            logger.debug(traceback.format_exc())
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
        _ensure_model_imports()

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


async def _run_cli() -> int:
    args = _parse_args()

    try:
        from src.config import get_settings
    except ModuleNotFoundError:
        project_root = Path(__file__).resolve().parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        from src.config import get_settings

    settings = get_settings()

    executor = MT5Executor(
        host=settings.mt5_host,
        port=settings.mt5_port,
        account=settings.mt5_account,
        password=settings.mt5_password,
        server=settings.mt5_server,
        symbol_base=settings.symbol,
        magic_number=settings.magic_number,
        max_slippage=settings.max_slippage_points,
    )

    async def _connect_with_retries(
        attempts: int = 6,
        delay_seconds: float = 8.0,
        attempt_timeout_seconds: float = 90.0,
    ) -> bool:
        for attempt in range(1, attempts + 1):
            try:
                connected_now = await asyncio.wait_for(
                    executor.initialize(),
                    timeout=attempt_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "MT5 initialize attempt timed out after %.0fs",
                    attempt_timeout_seconds,
                )
                connected_now = False

            if not connected_now:
                await executor.shutdown()

            if connected_now:
                return True

            if attempt < attempts:
                print(
                    "[INFO] MT5 bridge is up but terminal is still warming up "
                    f"(attempt {attempt}/{attempts}). Retrying in {delay_seconds:.0f}s..."
                )
                await asyncio.sleep(delay_seconds)

        return False

    connected = await _connect_with_retries(
        attempts=12,
        delay_seconds=8.0,
        attempt_timeout_seconds=90.0,
    )
    if not connected:
        if not args.no_auto_start_bridge and _is_local_host(settings.mt5_host):
            started = _attempt_start_mt5_bridge_service()
            if started:
                await executor.shutdown()
                print("[INFO] Waiting for MT5 bridge to become ready on port 18812...")
                ready = await asyncio.to_thread(
                    _wait_for_bridge,
                    settings.mt5_host,
                    settings.mt5_port,
                    70,
                )
                if not ready:
                    print("[WARN] Bridge port did not open in time.")
                connected = await _connect_with_retries(
                    attempts=20,
                    delay_seconds=10.0,
                    attempt_timeout_seconds=90.0,
                )

        if not connected:
            print("[ERROR] MT5 connection/login failed.")
            print("Try these checks:")
            print("  1) systemctl status mt5-bridge")
            print("  2) ss -tlnp | grep 18812")
            print("  3) journalctl -u mt5-bridge -n 80 --no-pager")
            if _LAST_BRIDGE_LOG_PATH is not None:
                print(f"  4) tail -n 80 {_LAST_BRIDGE_LOG_PATH}")
            return 1

    try:
        account = await executor.get_account_info()
        symbol = await executor.get_symbol_info()

        if account is None:
            print("[ERROR] Connected, but failed to fetch account info.")
            return 1

        if symbol is None:
            print(
                f"[OK] MT5 connected: account={account['login']} server={account['server']} "
                "(symbol info unavailable)"
            )
            return 0

        print(
            "[OK] MT5 connected successfully | "
            f"account={account['login']} server={account['server']} | "
            f"symbol={symbol['name']} bid={symbol['bid']:.5f} ask={symbol['ask']:.5f} "
            f"spread={symbol['spread']}"
        )
        return 0
    finally:
        await executor.shutdown()


def main() -> None:
    raise SystemExit(asyncio.run(_run_cli()))


if __name__ == "__main__":
    main()
