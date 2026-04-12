"""
AutoTrader — Risk Manager Unit Tests

Tests lot size calculation, risk limit enforcement, and signal deduplication.
Run with: python -m pytest tests/test_risk_manager.py -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.models import ParsedSignal, TradeAction, RiskCheckResult
from src.risk_manager import RiskManager


# ── Helpers ─────────────────────────────────────────────────────────────────

def make_signal(
    action: TradeAction = TradeAction.BUY,
    entry: float = 2345.0,
    sl: float = 2338.0,
    tps: list[float] | None = None,
    lot: float | None = None,
    timestamp: datetime | None = None,
) -> ParsedSignal:
    """Factory for test signals."""
    if tps is None:
        tps = [2355.0]
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    return ParsedSignal(
        action=action,
        entry_price=entry,
        stop_loss=sl,
        take_profits=tps,
        lot_size=lot,
        raw_text="test signal",
        timestamp=timestamp,
    )


def make_risk_manager(**kwargs) -> RiskManager:
    """Create a RiskManager with a mocked database."""
    db = AsyncMock()
    db.is_duplicate = AsyncMock(return_value=False)

    defaults = {
        "db": db,
        "max_risk_pct": 1.0,
        "max_open_trades": 5,
        "daily_loss_limit_pct": 5.0,
        "signal_expiry_minutes": 30,
        "dedup_window_hours": 4,
        "min_lot": 0.01,
        "max_lot": 1.0,
        "lot_step": 0.01,
        "default_lot": 0.01,
    }
    defaults.update(kwargs)
    return RiskManager(**defaults)


# ── Tests: Lot Size Calculation ─────────────────────────────────────────────

class TestLotSizeCalculation:
    def test_standard_lot_calculation(self) -> None:
        """
        With $10,000 balance, 1% risk, and $7 SL distance:
        Risk amount = $100
        Lot = $100 / ($7 × 100) = 0.14 lots
        """
        rm = make_risk_manager(max_risk_pct=1.0)
        lot = rm._calculate_lot_size(
            balance=10000.0, entry_price=2345.0, stop_loss=2338.0, signal_lot=None
        )
        assert lot == 0.14  # $100 / (7 * 100) = 0.1428 → floor to 0.14

    def test_2pct_risk(self) -> None:
        """2% risk with same parameters should give 2× the lot size."""
        rm = make_risk_manager(max_risk_pct=2.0)
        lot = rm._calculate_lot_size(
            balance=10000.0, entry_price=2345.0, stop_loss=2338.0, signal_lot=None
        )
        assert lot == 0.28

    def test_small_balance(self) -> None:
        """Small balance should produce minimum lot size."""
        rm = make_risk_manager(max_risk_pct=1.0)
        lot = rm._calculate_lot_size(
            balance=100.0, entry_price=2345.0, stop_loss=2338.0, signal_lot=None
        )
        # $1 risk / $700 = 0.0014 → clamped to min 0.01
        assert lot == 0.01

    def test_large_balance_capped(self) -> None:
        """Large balance should be capped at max lot size."""
        rm = make_risk_manager(max_risk_pct=1.0, max_lot=1.0)
        lot = rm._calculate_lot_size(
            balance=1000000.0, entry_price=2345.0, stop_loss=2338.0, signal_lot=None
        )
        assert lot == 1.0  # Capped at max_lot

    def test_signal_specified_lot_used(self) -> None:
        """When signal specifies a lot, use it (but still cap)."""
        rm = make_risk_manager(max_lot=1.0)
        lot = rm._calculate_lot_size(
            balance=10000.0, entry_price=2345.0, stop_loss=2338.0, signal_lot=0.5
        )
        assert lot == 0.5

    def test_signal_lot_capped(self) -> None:
        """Signal-specified lot should still be capped at max."""
        rm = make_risk_manager(max_lot=0.2)
        lot = rm._calculate_lot_size(
            balance=10000.0, entry_price=2345.0, stop_loss=2338.0, signal_lot=5.0
        )
        assert lot == 0.2

    def test_lot_step_rounding(self) -> None:
        """Lot should be rounded down to nearest step."""
        rm = make_risk_manager(max_risk_pct=1.0, lot_step=0.05)
        lot = rm._calculate_lot_size(
            balance=10000.0, entry_price=2345.0, stop_loss=2338.0, signal_lot=None
        )
        # 0.1428 → floor to nearest 0.05 = 0.10
        assert lot == 0.10

    def test_tight_sl_gives_larger_lot(self) -> None:
        """Tighter SL should give larger lot size (same risk, less distance)."""
        rm = make_risk_manager(max_risk_pct=1.0)
        lot_tight = rm._calculate_lot_size(
            balance=10000.0, entry_price=2345.0, stop_loss=2342.0, signal_lot=None
        )
        lot_wide = rm._calculate_lot_size(
            balance=10000.0, entry_price=2345.0, stop_loss=2335.0, signal_lot=None
        )
        assert lot_tight > lot_wide

    def test_sell_lot_calculation(self) -> None:
        """Lot calculation should work for SELL (SL above entry)."""
        rm = make_risk_manager(max_risk_pct=1.0)
        lot = rm._calculate_lot_size(
            balance=10000.0, entry_price=2345.0, stop_loss=2352.0, signal_lot=None
        )
        # SL distance = $7 → same as BUY test
        assert lot == 0.14


# ── Tests: Risk Checks ─────────────────────────────────────────────────────

class TestRiskChecks:
    @pytest.mark.asyncio
    async def test_signal_expiry(self) -> None:
        """Expired signals should be rejected."""
        rm = make_risk_manager(signal_expiry_minutes=30)
        old_signal = make_signal(
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=45)
        )
        result = await rm.check_trade(
            signal=old_signal,
            account_balance=10000.0,
            current_open_count=0,
            daily_pnl=0.0,
            existing_positions=[],
        )
        assert not result.approved
        assert "expired" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_max_open_trades(self) -> None:
        """Should reject when max open trades reached."""
        rm = make_risk_manager(max_open_trades=3)
        signal = make_signal()
        result = await rm.check_trade(
            signal=signal,
            account_balance=10000.0,
            current_open_count=3,
            daily_pnl=0.0,
            existing_positions=[],
        )
        assert not result.approved
        assert "max open trades" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_daily_loss_limit(self) -> None:
        """Should reject when daily loss limit breached."""
        rm = make_risk_manager(daily_loss_limit_pct=5.0)
        signal = make_signal()
        result = await rm.check_trade(
            signal=signal,
            account_balance=10000.0,
            current_open_count=0,
            daily_pnl=-600.0,  # 6% loss
            existing_positions=[],
        )
        assert not result.approved
        assert "daily loss limit" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_duplicate_rejected(self) -> None:
        """Duplicate signals should be rejected."""
        db = AsyncMock()
        db.is_duplicate = AsyncMock(return_value=True)  # Simulate duplicate
        rm = make_risk_manager(db=db)
        signal = make_signal()
        result = await rm.check_trade(
            signal=signal,
            account_balance=10000.0,
            current_open_count=0,
            daily_pnl=0.0,
            existing_positions=[],
        )
        assert not result.approved
        assert "duplicate" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_all_checks_pass(self) -> None:
        """Normal signal should pass all checks."""
        rm = make_risk_manager()
        signal = make_signal()
        result = await rm.check_trade(
            signal=signal,
            account_balance=10000.0,
            current_open_count=2,
            daily_pnl=-100.0,
            existing_positions=[],
        )
        assert result.approved
        assert result.adjusted_lot_size > 0
        assert result.risk_amount > 0
        assert result.risk_pct > 0

    @pytest.mark.asyncio
    async def test_risk_percentage_correct(self) -> None:
        """Risk percentage should be within configured limit."""
        rm = make_risk_manager(max_risk_pct=1.0)
        signal = make_signal()
        result = await rm.check_trade(
            signal=signal,
            account_balance=10000.0,
            current_open_count=0,
            daily_pnl=0.0,
            existing_positions=[],
        )
        assert result.approved
        # Risk should be ≤ 1% of balance (allowing for lot step rounding)
        assert result.risk_pct <= 1.1  # Small tolerance for rounding

    @pytest.mark.asyncio
    async def test_low_balance_still_allowed(self) -> None:
        """Low balance should still allow min lot trades."""
        rm = make_risk_manager()
        signal = make_signal()
        result = await rm.check_trade(
            signal=signal,
            account_balance=500.0,  # Low balance
            current_open_count=0,
            daily_pnl=0.0,
            existing_positions=[],
        )
        assert result.approved
        assert result.adjusted_lot_size == 0.01  # Min lot


# ── Tests: Risk Amount Calculation ──────────────────────────────────────────

class TestRiskAmount:
    def test_risk_amount_buy(self) -> None:
        """Risk amount for BUY: lot × SL_distance × 100 (XAUUSD)."""
        rm = make_risk_manager()
        # 0.1 lot, $7 SL distance → 0.1 × 7 × 100 = $70
        risk = rm._calculate_risk_amount(lot_size=0.1, sl_distance=7.0)
        assert risk == 70.0

    def test_risk_amount_micro_lot(self) -> None:
        """Risk amount for micro lot (0.01)."""
        rm = make_risk_manager()
        # 0.01 lot, $10 SL → 0.01 × 10 × 100 = $10
        risk = rm._calculate_risk_amount(lot_size=0.01, sl_distance=10.0)
        assert risk == 10.0
