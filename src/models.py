"""
AutoTrader — Data Models

Pydantic models for structured data flowing through the system.
Every signal, trade, and risk check is represented as a validated model.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class TradeAction(str, Enum):
    """Trade direction."""
    BUY = "BUY"
    SELL = "SELL"


class ParsedSignal(BaseModel):
    """
    Structured trading signal extracted from raw Telegram message text.
    Produced by the AI parser or regex fallback parser.
    """

    action: TradeAction = Field(..., description="BUY or SELL")
    entry_price: float = Field(..., gt=0, description="Entry price level")
    stop_loss: float = Field(..., gt=0, description="Stop-loss price level")
    take_profits: list[float] = Field(
        ..., min_length=1, description="Take-profit levels (TP1, TP2, ...)"
    )
    lot_size: Optional[float] = Field(
        None, gt=0, description="Explicit lot size from signal (if provided)"
    )
    confidence: float = Field(
        1.0, ge=0.0, le=1.0, description="AI confidence score (1.0 = certain)"
    )
    raw_text: str = Field(..., description="Original message text")
    message_id: Optional[int] = Field(None, description="Telegram message ID")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the signal was received",
    )
    parser_source: str = Field(
        "unknown", description="Which parser produced this: gemini, groq, regex"
    )

    @field_validator("take_profits")
    @classmethod
    def validate_take_profits(cls, v: list[float]) -> list[float]:
        for tp in v:
            if tp <= 0:
                raise ValueError(f"Take-profit must be positive, got {tp}")
        return sorted(v)  # Always sort ascending

    @model_validator(mode="after")
    def validate_sl_tp_direction(self) -> "ParsedSignal":
        """Ensure SL and TP are on correct sides of entry for the action."""
        if self.action == TradeAction.BUY:
            if self.stop_loss >= self.entry_price:
                raise ValueError(
                    f"BUY signal: SL ({self.stop_loss}) must be below "
                    f"entry ({self.entry_price})"
                )
            if self.take_profits[0] <= self.entry_price:
                raise ValueError(
                    f"BUY signal: TP1 ({self.take_profits[0]}) must be above "
                    f"entry ({self.entry_price})"
                )
        elif self.action == TradeAction.SELL:
            if self.stop_loss <= self.entry_price:
                raise ValueError(
                    f"SELL signal: SL ({self.stop_loss}) must be above "
                    f"entry ({self.entry_price})"
                )
            if self.take_profits[0] >= self.entry_price:
                raise ValueError(
                    f"SELL signal: TP1 ({self.take_profits[0]}) must be below "
                    f"entry ({self.entry_price})"
                )
        return self

    def dedup_hash(self) -> str:
        """
        Generate a SHA-256 hash for deduplication.
        Two signals with the same action, entry, SL, and TP1 are considered duplicates.
        """
        key = (
            f"{self.action.value}|"
            f"{self.entry_price:.2f}|"
            f"{self.stop_loss:.2f}|"
            f"{self.take_profits[0]:.2f}"
        )
        return hashlib.sha256(key.encode()).hexdigest()[:16]


class TradeStatus(str, Enum):
    """Outcome of a trade execution attempt."""
    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    REQUOTE = "REQUOTE"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    DRY_RUN = "DRY_RUN"


class TradeResult(BaseModel):
    """Result of attempting to execute a trade on MT5."""

    status: TradeStatus
    order_ticket: Optional[int] = Field(None, description="MT5 order ticket number")
    symbol: str = Field(..., description="Actual symbol used (with broker suffix)")
    action: TradeAction = Field(..., description="BUY or SELL")
    volume: float = Field(..., ge=0, description="Actual volume executed")
    price: Optional[float] = Field(None, description="Fill price")
    stop_loss: float = Field(..., description="SL price set")
    take_profit: float = Field(..., description="TP price set (TP1 used)")
    error_code: Optional[int] = Field(None, description="MT5 retcode on failure")
    error_message: Optional[str] = Field(None, description="Human-readable error")
    signal_hash: str = Field(..., description="Dedup hash of originating signal")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Execution timestamp",
    )
    retries: int = Field(0, ge=0, description="Number of retry attempts made")


class RiskCheckResult(BaseModel):
    """Result of the risk manager's pre-trade validation."""

    approved: bool = Field(..., description="Whether the trade is allowed")
    reason: str = Field("", description="Explanation if rejected")
    adjusted_lot_size: float = Field(
        0.0, ge=0, description="Risk-adjusted lot size (0 if rejected)"
    )
    risk_amount: float = Field(
        0.0, ge=0, description="Dollar risk if trade hits SL"
    )
    risk_pct: float = Field(
        0.0, ge=0, description="Risk as % of account balance"
    )
    current_open_trades: int = Field(0, ge=0)
    daily_pnl: float = Field(0.0, description="Current day P&L in account currency")


class SignalRecord(BaseModel):
    """Persisted signal record for SQLite storage."""

    id: Optional[int] = None
    dedup_hash: str
    raw_text: str
    parsed_action: Optional[str] = None
    parsed_entry: Optional[float] = None
    parsed_sl: Optional[float] = None
    parsed_tp1: Optional[float] = None
    parser_source: str = "unknown"
    trade_ticket: Optional[int] = None
    trade_status: Optional[str] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    message_id: Optional[int] = None


class HealthStatus(BaseModel):
    """Health check response structure."""

    status: str = "ok"
    uptime_seconds: float = 0.0
    last_signal_time: Optional[str] = None
    open_trades: int = 0
    daily_pnl: float = 0.0
    dry_run: bool = True
    version: str = "1.0.0"
    mt5_connected: bool = False
    telegram_connected: bool = False
