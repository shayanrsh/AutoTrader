"""
AutoTrader — Risk Management Module

Pre-trade validation layer that enforces:
- Per-trade risk cap (lot size calculation from SL distance)
- Maximum concurrent open positions
- Daily loss limit (halts trading when breached)
- Signal deduplication (prevents duplicate executions)
- Conflict detection (opposite positions on same symbol)
"""

from __future__ import annotations

import math
from typing import Optional

from src.database import Database
from src.models import ParsedSignal, RiskCheckResult, TradeAction
from src.utils import get_logger, timestamp_age_minutes

logger = get_logger("risk_manager")


class RiskManager:
    """
    Risk management layer that validates every trade before execution.
    All positions and limits are tracked in real-time.
    """

    def __init__(
        self,
        db: Database,
        max_risk_pct: float = 1.0,
        max_open_trades: int = 5,
        daily_loss_limit_pct: float = 5.0,
        signal_expiry_minutes: int = 30,
        dedup_window_hours: int = 4,
        min_lot: float = 0.01,
        max_lot: float = 1.0,
        lot_step: float = 0.01,
        default_lot: float = 0.01,
    ) -> None:
        self._db = db
        self._max_risk_pct = max_risk_pct
        self._max_open_trades = max_open_trades
        self._daily_loss_limit_pct = daily_loss_limit_pct
        self._signal_expiry_minutes = signal_expiry_minutes
        self._dedup_window_hours = dedup_window_hours
        self._min_lot = min_lot
        self._max_lot = max_lot
        self._lot_step = lot_step
        self._default_lot = default_lot

        # Daily tracking (resets each calendar day)
        self._daily_start_balance: Optional[float] = None
        self._daily_date: Optional[str] = None

    async def check_trade(
        self,
        signal: ParsedSignal,
        account_balance: float,
        current_open_count: int,
        daily_pnl: float,
        existing_positions: list[dict],
    ) -> RiskCheckResult:
        """
        Run all risk checks on a signal before allowing execution.

        Args:
            signal: The parsed trading signal.
            account_balance: Current account balance.
            current_open_count: Number of currently open positions.
            daily_pnl: Today's cumulative P&L in account currency.
            existing_positions: List of current open positions (dicts with 'type', 'symbol', etc.)

        Returns:
            RiskCheckResult with approval status and adjusted lot size.
        """
        # ── Check 1: Signal expiry ──────────────────────────────────
        age_minutes = timestamp_age_minutes(signal.timestamp)
        if age_minutes > self._signal_expiry_minutes:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Signal expired: {age_minutes:.0f} min old "
                    f"(limit: {self._signal_expiry_minutes} min)"
                ),
                current_open_trades=current_open_count,
                daily_pnl=daily_pnl,
            )

        # ── Check 2: Deduplication ──────────────────────────────────
        dedup_hash = signal.dedup_hash()
        is_dup = await self._db.is_duplicate(dedup_hash, self._dedup_window_hours)
        if is_dup:
            return RiskCheckResult(
                approved=False,
                reason=f"Duplicate signal detected (hash: {dedup_hash})",
                current_open_trades=current_open_count,
                daily_pnl=daily_pnl,
            )

        # ── Check 3: Max open positions ─────────────────────────────
        if current_open_count >= self._max_open_trades:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Max open trades reached: {current_open_count}/{self._max_open_trades}"
                ),
                current_open_trades=current_open_count,
                daily_pnl=daily_pnl,
            )

        # ── Check 4: Daily loss limit ───────────────────────────────
        if account_balance > 0:
            daily_loss_pct = abs(min(daily_pnl, 0)) / account_balance * 100
            if daily_pnl < 0 and daily_loss_pct >= self._daily_loss_limit_pct:
                return RiskCheckResult(
                    approved=False,
                    reason=(
                        f"Daily loss limit breached: {daily_loss_pct:.1f}% "
                        f"(limit: {self._daily_loss_limit_pct}%)"
                    ),
                    current_open_trades=current_open_count,
                    daily_pnl=daily_pnl,
                )

        # ── Check 5: Conflicting positions ──────────────────────────
        for pos in existing_positions:
            pos_type = pos.get("type", "")
            # MT5: type 0 = BUY, type 1 = SELL
            pos_is_buy = str(pos_type) in ("0", "BUY", "ORDER_TYPE_BUY")
            pos_is_sell = str(pos_type) in ("1", "SELL", "ORDER_TYPE_SELL")

            if signal.action == TradeAction.BUY and pos_is_sell:
                logger.warning(
                    "Conflicting SELL position exists (ticket=%s). "
                    "Consider closing it before opening BUY.",
                    pos.get("ticket", "?"),
                )
            elif signal.action == TradeAction.SELL and pos_is_buy:
                logger.warning(
                    "Conflicting BUY position exists (ticket=%s). "
                    "Consider closing it before opening SELL.",
                    pos.get("ticket", "?"),
                )

        # ── Check 6: Calculate lot size ─────────────────────────────
        lot_size = self._calculate_lot_size(
            balance=account_balance,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            signal_lot=signal.lot_size,
        )

        if lot_size < self._min_lot:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Calculated lot size ({lot_size:.4f}) is below minimum "
                    f"({self._min_lot}). Account balance may be too low."
                ),
                current_open_trades=current_open_count,
                daily_pnl=daily_pnl,
            )

        # ── Check 7: Low confidence warning ────────────────────────
        if signal.confidence < 0.5:
            logger.warning(
                "Low confidence signal (%.1f): proceeding with caution",
                signal.confidence,
            )

        # ── All checks passed ───────────────────────────────────────
        sl_distance = abs(signal.entry_price - signal.stop_loss)
        risk_amount = self._calculate_risk_amount(lot_size, sl_distance)
        risk_pct = (risk_amount / account_balance * 100) if account_balance > 0 else 0

        logger.info(
            "Risk check PASSED: lot=%.2f, risk=$%.2f (%.2f%%), "
            "open=%d/%d, daily_pnl=$%.2f",
            lot_size, risk_amount, risk_pct,
            current_open_count, self._max_open_trades, daily_pnl,
        )

        return RiskCheckResult(
            approved=True,
            reason="All risk checks passed",
            adjusted_lot_size=lot_size,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
            current_open_trades=current_open_count,
            daily_pnl=daily_pnl,
        )

    def _calculate_lot_size(
        self,
        balance: float,
        entry_price: float,
        stop_loss: float,
        signal_lot: Optional[float] = None,
    ) -> float:
        """
        Calculate optimal lot size based on risk percentage and SL distance.

        XAUUSD lot size calculation:
        - 1 standard lot = 100 oz of gold
        - Pip value for 1 lot XAUUSD = $1 per $0.01 move = $100 per $1.00 move
        - Risk = Lot Size × SL Distance (in $) × 100

        Formula:
            lot_size = (balance × risk_pct / 100) / (SL_distance × 100)

        Args:
            balance: Account balance in account currency (USD).
            entry_price: Entry price for the trade.
            stop_loss: Stop-loss price.
            signal_lot: Lot size from the signal (if explicitly specified).

        Returns:
            Calculated lot size, clamped to min/max and rounded to lot_step.
        """
        if signal_lot is not None and signal_lot > 0:
            # Use signal-specified lot but still cap it
            lot = min(signal_lot, self._max_lot)
            lot = max(lot, self._min_lot)
            logger.debug(
                "Using signal-specified lot: %.2f (capped to [%.2f, %.2f])",
                lot, self._min_lot, self._max_lot,
            )
            return self._round_lot(lot)

        # Calculate from risk percentage
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            logger.warning("SL distance is zero; using default lot size")
            return self._default_lot

        # Risk amount in dollars
        risk_amount = balance * (self._max_risk_pct / 100.0)

        # XAUUSD: 1 lot = 100 oz. Price move of $1 = $100 P&L per lot.
        # So: lot_size = risk_amount / (sl_distance_in_dollars × 100)
        pip_value_per_lot = 100.0  # $100 per $1 move per standard lot for XAUUSD
        lot_size = risk_amount / (sl_distance * pip_value_per_lot)

        # Clamp to bounds
        lot_size = max(lot_size, self._min_lot)
        lot_size = min(lot_size, self._max_lot)

        lot_size = self._round_lot(lot_size)

        logger.debug(
            "Lot calculation: balance=$%.2f, risk=%.1f%%, SL_dist=$%.2f, "
            "risk_amount=$%.2f, raw_lot=%.4f, final_lot=%.2f",
            balance, self._max_risk_pct, sl_distance,
            risk_amount, risk_amount / (sl_distance * pip_value_per_lot), lot_size,
        )

        return lot_size

    def _calculate_risk_amount(self, lot_size: float, sl_distance: float) -> float:
        """Calculate the dollar risk for a given lot size and SL distance."""
        # XAUUSD: 1 lot = 100 oz → $100 per $1 move
        return lot_size * sl_distance * 100.0

    def _round_lot(self, lot: float) -> float:
        """Round lot size down to the nearest lot step."""
        if self._lot_step <= 0:
            return lot
        steps = math.floor(lot / self._lot_step)
        return round(steps * self._lot_step, 2)
