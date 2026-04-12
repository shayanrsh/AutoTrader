"""
AutoTrader — Database Module

Async SQLite operations for signal logging, trade tracking, deduplication,
and daily P&L aggregation. Uses aiosqlite for non-blocking I/O.
"""

from __future__ import annotations

import aiosqlite
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.models import SignalRecord, TradeResult
from src.utils import get_logger, utc_now

logger = get_logger("database")

# ── SQL Schema ──────────────────────────────────────────────────────────────

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_hash      TEXT    NOT NULL,
    raw_text        TEXT    NOT NULL,
    parsed_action   TEXT,
    parsed_entry    REAL,
    parsed_sl       REAL,
    parsed_tp1      REAL,
    parser_source   TEXT    DEFAULT 'unknown',
    trade_ticket    INTEGER,
    trade_status    TEXT,
    message_id      INTEGER,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_signals_hash ON signals(dedup_hash);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_ticket    INTEGER,
    symbol          TEXT    NOT NULL,
    action          TEXT    NOT NULL,
    volume          REAL    NOT NULL,
    price           REAL,
    stop_loss       REAL    NOT NULL,
    take_profit     REAL    NOT NULL,
    status          TEXT    NOT NULL,
    error_code      INTEGER,
    error_message   TEXT,
    signal_hash     TEXT    NOT NULL,
    retries         INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_signal ON trades(signal_hash);

CREATE TABLE IF NOT EXISTS daily_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT    NOT NULL UNIQUE,
    starting_balance REAL,
    ending_balance  REAL,
    total_pnl       REAL    DEFAULT 0.0,
    trades_opened   INTEGER DEFAULT 0,
    trades_closed   INTEGER DEFAULT 0,
    signals_received INTEGER DEFAULT 0,
    signals_skipped INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_stats(trade_date);
"""


class Database:
    """Async SQLite database wrapper for AutoTrader."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Open database connection and create tables if needed."""
        db_file = Path(self._db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(str(db_file))
        self._conn.row_factory = aiosqlite.Row

        # Enable WAL mode for better concurrent read performance
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")

        # Create tables
        await self._conn.executescript(_CREATE_TABLES_SQL)
        await self._conn.commit()
        logger.info("Database connected: %s", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    # ── Signal Operations ───────────────────────────────────────────────

    async def insert_signal(self, record: SignalRecord) -> int:
        """
        Insert a signal record and return the new row ID.
        """
        cursor = await self.conn.execute(
            """
            INSERT INTO signals
                (dedup_hash, raw_text, parsed_action, parsed_entry,
                 parsed_sl, parsed_tp1, parser_source, trade_ticket,
                 trade_status, message_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.dedup_hash,
                record.raw_text,
                record.parsed_action,
                record.parsed_entry,
                record.parsed_sl,
                record.parsed_tp1,
                record.parser_source,
                record.trade_ticket,
                record.trade_status,
                record.message_id,
                record.created_at.isoformat(),
            ),
        )
        await self.conn.commit()
        row_id = cursor.lastrowid
        logger.debug("Inserted signal record id=%d hash=%s", row_id, record.dedup_hash)
        return row_id  # type: ignore[return-value]

    async def is_duplicate(self, dedup_hash: str, window_hours: int = 4) -> bool:
        """
        Check if a signal with the same hash exists within the dedup window.

        Args:
            dedup_hash: The signal's deduplication hash.
            window_hours: How far back to look for duplicates.

        Returns:
            True if a matching signal exists within the window.
        """
        cutoff = (utc_now() - timedelta(hours=window_hours)).isoformat()
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) as cnt
            FROM signals
            WHERE dedup_hash = ? AND created_at >= ?
            """,
            (dedup_hash, cutoff),
        )
        row = await cursor.fetchone()
        count = row["cnt"] if row else 0
        if count > 0:
            logger.info(
                "Duplicate signal detected: hash=%s (found %d within %dh)",
                dedup_hash, count, window_hours,
            )
        return count > 0

    async def update_signal_trade(
        self, signal_id: int, ticket: Optional[int], status: str
    ) -> None:
        """Update a signal record with the trade execution result."""
        await self.conn.execute(
            """
            UPDATE signals SET trade_ticket = ?, trade_status = ?
            WHERE id = ?
            """,
            (ticket, status, signal_id),
        )
        await self.conn.commit()

    # ── Trade Operations ────────────────────────────────────────────────

    async def insert_trade(self, result: TradeResult) -> int:
        """Insert a trade execution record."""
        cursor = await self.conn.execute(
            """
            INSERT INTO trades
                (order_ticket, symbol, action, volume, price,
                 stop_loss, take_profit, status, error_code,
                 error_message, signal_hash, retries, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.order_ticket,
                result.symbol,
                result.action.value,
                result.volume,
                result.price,
                result.stop_loss,
                result.take_profit,
                result.status.value,
                result.error_code,
                result.error_message,
                result.signal_hash,
                result.retries,
                result.timestamp.isoformat(),
            ),
        )
        await self.conn.commit()
        row_id = cursor.lastrowid
        logger.debug("Inserted trade record id=%d ticket=%s", row_id, result.order_ticket)
        return row_id  # type: ignore[return-value]

    # ── Daily Stats ─────────────────────────────────────────────────────

    async def get_today_trade_count(self) -> int:
        """Get the number of trades opened today."""
        today = utc_now().strftime("%Y-%m-%d")
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) as cnt FROM trades
            WHERE created_at >= ? AND status = 'SUCCESS'
            """,
            (today,),
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def get_today_signals_count(self) -> int:
        """Get the number of signals received today."""
        today = utc_now().strftime("%Y-%m-%d")
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) as cnt FROM signals
            WHERE created_at >= ?
            """,
            (today,),
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def record_daily_pnl(
        self,
        pnl: float,
        starting_balance: Optional[float] = None,
        ending_balance: Optional[float] = None,
    ) -> None:
        """Upsert today's P&L record."""
        today = utc_now().strftime("%Y-%m-%d")
        trades_count = await self.get_today_trade_count()
        signals_count = await self.get_today_signals_count()

        await self.conn.execute(
            """
            INSERT INTO daily_stats
                (trade_date, starting_balance, ending_balance, total_pnl,
                 trades_opened, signals_received)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                ending_balance = excluded.ending_balance,
                total_pnl = excluded.total_pnl,
                trades_opened = excluded.trades_opened,
                signals_received = excluded.signals_received
            """,
            (today, starting_balance, ending_balance, pnl, trades_count, signals_count),
        )
        await self.conn.commit()

    async def get_recent_signals(self, limit: int = 20) -> list[dict]:
        """Get the most recent signal records."""
        cursor = await self.conn.execute(
            """
            SELECT * FROM signals
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Cleanup ─────────────────────────────────────────────────────────

    async def cleanup_old_records(self, days: int = 90) -> int:
        """Delete signal and trade records older than N days."""
        cutoff = (utc_now() - timedelta(days=days)).isoformat()

        cursor = await self.conn.execute(
            "DELETE FROM signals WHERE created_at < ?", (cutoff,)
        )
        signals_deleted = cursor.rowcount

        cursor = await self.conn.execute(
            "DELETE FROM trades WHERE created_at < ?", (cutoff,)
        )
        trades_deleted = cursor.rowcount

        await self.conn.commit()
        total = signals_deleted + trades_deleted
        if total > 0:
            logger.info(
                "Cleaned up %d old records (%d signals, %d trades)",
                total, signals_deleted, trades_deleted,
            )
        return total
